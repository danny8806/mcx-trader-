"""Main trading engine — event-driven architecture with per-strategy isolation.

Architecture:
    CandleFetcher → NativeCandleDistributor → EventBus → StrategyInstance.on_candle()

Each StrategyInstance owns its own:
    - DEMAATR indicators (fast, mid, slow)
    - HTF state (mid_htf_state, slow_htf_state)
    - Strategy state (FLAT/LONG/SHORT, pending, stop, etc.)

Shared infrastructure:
    - Dhan REST/WebSocket
    - EventBus
    - ExecutionEngine
    - PositionManager
    - TradeLifecycleManager
    - Database (trading.db — single canonical source)
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from config import Config
from data.dhan import DhanDataAdapter
from core.timeframe_engine import Bar
from core.market_status import MarketStatus, MarketState, EngineStatus
from core.safe_mode import SafeModeManager
from core.fill_dedup import FillDeduplicator
from events.bus import EventBus
from data.native_streams import NativeCandleDistributor
from data.native_router import NativeCandleRouter
from strategies.instance import StrategyInstance
from strategies.types import SignalType, StrategyState
from strategies.gold import create_gold_5m, create_gold_15m
from strategies.silver import create_silver_5m, create_silver_15m
from indicators.shared import SharedNativeIndicatorEngine
from execution.paper_broker import PaperExecutionEngine, Fill
from execution.broker_router import BrokerEventRouter
from execution.fee_model import MCXFeeModel
from execution.order_manager import OrderManager, OrderManagerFacade
from portfolio.position_manager import PositionManager, PositionManagerFacade, Position
from portfolio.pnl import PNLEngine
from portfolio.account import AccountEngine
from monitoring.health import HealthMonitor, SystemStatus
from notifications.telegram_router import TelegramRouter
from analytics.event_store import EventStore
from analytics.trade_ledger import TradeLedger
from core.lifecycle import TradeLifecycleManager
from core.risk_engine import RiskEngine
from core.trade_close import TradeCloseManager
from strategies.runtime import StrategyRuntime, StrategyRuntimeRegistry

log = logging.getLogger(__name__)


def _strategy_positions_for_risk(signal_type, open_positions) -> int:
    """Number of the strategy's open positions to apply against the per-strategy
    position cap for an incoming order."""
    open_held = [p for p in open_positions if getattr(p, "is_open", False)]
    holds_short = any(getattr(p, "is_short", False) for p in open_held)
    holds_long = any(getattr(p, "is_long", False) for p in open_held)
    if signal_type.name == "LONG" and holds_short:
        return max(0, len(open_held) - 1)
    if signal_type.name == "SHORT" and holds_long:
        return max(0, len(open_held) - 1)
    return len(open_held)


STRATEGY_FACTORIES = {
    "gold_01": create_gold_5m,
    "gold_02": create_gold_15m,
    "gold_03": lambda **kw: create_gold_5m(strategy_id="gold_03", **kw),
    "gold_04": lambda **kw: create_gold_5m(strategy_id="gold_04", **kw),
    "silver_01": create_silver_15m,
    "silver_02": create_silver_5m,
    "silver_03": lambda **kw: create_silver_5m(strategy_id="silver_03", **kw),
    "silver_04": lambda **kw: create_silver_5m(strategy_id="silver_04", **kw),
}


class TradingEngine:
    """Event-driven trading engine with per-strategy isolation.

    Event flow:
    Dhan WebSocket → Tick → EventBus → StrategyInstance.on_tick()
    Dhan REST → CandleFetcher → NativeCandleDistributor → EventBus → StrategyInstance.on_candle()
    """

    def __init__(self, config_path: Optional[str] = None, event_callback=None):
        self.config = Config()
        if config_path:
            self.config.load(Path(config_path))
        else:
            self.config.load()

        self._event_callback = event_callback

        # ── Shared infrastructure ──
        self.event_bus = EventBus()
        self.candle_distributor = NativeCandleDistributor(self.event_bus)
        # §7 — NativeCandleRouter is the single native-candle choke point:
        # dedup by (security_id, timeframe, candle_end_ts), out-of-order
        # detection, incomplete-candle guard, then forward to the distributor.
        self.candle_router = NativeCandleRouter(
            distributor=self.candle_distributor.on_candle_closed,
            instruments=self.config.get("instruments", {}),
        )
        indicator_cfg = self.config.get("indicators", {})
        self.indicator_engine = SharedNativeIndicatorEngine(
            dema_period=indicator_cfg.get("dema_period", 3),
            atr_period=indicator_cfg.get("atr_period", 6),
            atr_factor=indicator_cfg.get("atr_factor", 1.0),
        )

        # ── Initialize ──
        self._init_market_status()
        self._init_data_adapter()
        self._init_indicator_engines()
        self._init_htf_engine()
        self._init_strategies()
        self._init_candle_fetcher()
        self._init_execution()
        self._init_portfolio()
        self._init_risk()
        self._init_monitoring()
        self._init_notifications()

        # ── Database + lifecycle ──
        db_path = Config.resolve_path(
            self.config.get("system", {}).get("db_path", "data/db/trading.db")
        )
        try:
            self.event_store = EventStore(db_path=db_path)
        except Exception:
            self.event_store = None
        try:
            self.trade_ledger = TradeLedger(db_path=db_path)
        except Exception:
            self.trade_ledger = None
        self.fill_dedup = FillDeduplicator(db_path=db_path)
        self.safe_mode = SafeModeManager(self.market_status)
        # §34 — cross-strategy protection: rejected/quarantined events are
        # logged as ERROR and recorded here; they never mutate lifecycle state.
        self.quarantine_count: int = 0
        self._quarantined_events: list[dict] = []
        # No global TradeLifecycleManager: one per StrategyRuntime.
        self._lifecycle = None
        self._trade_close_manager = None

        # ── State ──
        self._running = False
        self._lock = threading.RLock()
        self._persistence = None

        # ── Per-strategy runtimes (rebuilt with persistence by set_persistence) ──
        self.runtimes = StrategyRuntimeRegistry()
        self._build_runtimes(persistence=None)

        # Live vs bar-model signal routing
        self.tick_signal_processing = True

        log.info("[Engine] Initialized with %d strategies", len(self.strategies))

    # ═══════════════════════════════════════════════════════════════════
    # INIT METHODS (kept for backward compatibility with tests/scripts)
    # ═══════════════════════════════════════════════════════════════════

    def _init_market_status(self) -> None:
        self.market_status = MarketStatus()

    def _init_data_adapter(self) -> None:
        dhan_config = self.config.get("dhan")
        self.data_adapter = DhanDataAdapter(
            client_id=dhan_config["client_id"],
            token_file=dhan_config.get("token_file", "dhan_token.json"),
            pin=dhan_config.get("pin", ""),
            totp_secret=dhan_config.get("totp_secret", ""),
            on_tick=self._on_tick,
            on_status=self._on_status,
        )
        instruments = self.config.get("instruments", {})
        self.data_adapter.register_instruments(instruments)

    def _init_indicator_engines(self) -> None:
        """No longer needed — indicators are per-strategy. Kept as no-op for compat."""
        self.indicators: dict[str, Any] = {}

    def _init_htf_engine(self) -> None:
        """No longer needed — HTF state is per-strategy. Kept as no-op for compat."""
        self.htf_engine: Any = type("_FakeHTF", (), {"_engines": {}, "on_htf_bar_closed": lambda s, b: None, "map_to_fast_bar": lambda s, b, t: None, "map_mid_to_fast_bar": lambda s, b, t: None})()

    def _init_candle_fetcher(self) -> None:
        """Initialize CandleFetcher and wire to EventBus via NativeCandleRouter
        -> NativeCandleDistributor."""
        from core.candle_fetcher import CandleFetcher
        instruments = self.config.get("instruments", {})
        first_inst = list(instruments.values())[0] if instruments else {}
        session_open = first_inst.get("session_open", "09:00")
        session_close = first_inst.get("session_close", "23:30")
        if not hasattr(self, 'candle_router') or self.candle_router is None:
            self.candle_router = NativeCandleRouter(
                distributor=self.candle_distributor.on_candle_closed,
                instruments=instruments,
            )
        self.candle_fetcher = CandleFetcher(
            data_adapter=self.data_adapter,
            instruments=instruments,
            on_candle_closed=self.candle_router.on_candle_closed,
            session_open=session_open,
            session_close=session_close,
            market_status=self.market_status,
        )

    def _init_timeframe_engine(self) -> None:
        """Initialize the REST CandleFetcher as the strategy candle source.

        Backward-compatible alias for _init_candle_fetcher: closed bars flow
        from the CandleFetcher through EventBus to per-strategy handlers.
        """
        self._init_candle_fetcher()

    def _init_strategies(self) -> None:
        """Initialize four independent StrategyInstances and subscribe to EventBus."""
        if not hasattr(self, 'event_bus') or self.event_bus is None:
            self.event_bus = EventBus()
        if not hasattr(self, 'candle_distributor') or self.candle_distributor is None:
            self.candle_distributor = NativeCandleDistributor(self.event_bus)
        if not hasattr(self, 'indicator_engine') or self.indicator_engine is None:
            indicator_cfg = self.config.get("indicators", {})
            self.indicator_engine = SharedNativeIndicatorEngine(
                dema_period=indicator_cfg.get("dema_period", 3),
                atr_period=indicator_cfg.get("atr_period", 6),
                atr_factor=indicator_cfg.get("atr_factor", 1.0),
            )
        self.strategies: dict[str, StrategyInstance] = {}
        strategies_config = self.config.get("strategies", {})
        instruments_config = self.config.get("instruments", {})

        for strat_name, strat_config in strategies_config.items():
            if not strat_config.get("enabled", True):
                continue
            factory = STRATEGY_FACTORIES.get(strat_name)
            if not factory:
                log.warning("[Engine] Unknown strategy: %s", strat_name)
                continue

            instrument = strat_config.get("instrument", "GOLDM")
            inst_cfg = instruments_config.get(instrument, {})
            strategy = factory(
                strategy_id=strat_name,
                instrument=instrument,
                quantity=strat_config.get("quantity", 1),
                capital=strat_config.get("capital", 300_000.0),
                multiplier=inst_cfg.get("multiplier", 10.0),
            )
            self.strategies[strat_name] = strategy

            # Bind this strategy's indicator slots to the shared indicator
            # engine so each (security_id, timeframe) DEMA-ATR is computed once
            # (mission §7–§12). The strategy's own math objects are replaced
            # with shared views; on_bar behavior is unchanged.
            strategy.bind_shared_indicators(self.indicator_engine)

            # Subscribe to candle events
            for sub in strategy.subscriptions:
                self.event_bus.subscribe(f"candle:{sub}", self._make_candle_handler(strategy))
            # Subscribe to tick events for pending/SL
            tick_topic = f"tick:{instrument}"
            self.event_bus.subscribe(tick_topic, self._make_tick_handler(strategy))

    def _init_execution(self) -> None:
        paper_config = self.config.get("paper_execution", {})
        # Single shared broker transport (PaperExecutionEngine) underneath
        # per-strategy OrderManagers. The facade keeps external consumers
        # stable while execution state stays per-runtime.
        self.execution_engine = PaperExecutionEngine(
            slippage_ticks=paper_config.get("slippage_ticks", 1),
            latency_ms=paper_config.get("latency_ms", 100),
            partial_fill_probability=paper_config.get("partial_fill_probability", 0.0),
        )
        self.order_manager = OrderManagerFacade(execution_engine=self.execution_engine)
        # §39–40 — BrokerEventRouter is the single broker-event choke point:
        # every fill/order event routes by EXPLICIT broker_order_id -> strategy
        # mapping (never symbol/side/latest order); unmappable events are
        # quarantined. The paper broker registers each order's mapping here.
        self.broker_router = BrokerEventRouter(persistence=None)
        self.execution_engine.broker_router = self.broker_router

    def _init_portfolio(self) -> None:
        account_config = self.config.get("account", {})
        self.position_manager = PositionManagerFacade()
        default_capital = account_config.get("starting_capital_per_strategy", 300_000.0)
        margin_pct = self.config.get("risk", {}).get("margin_per_trade_pct", 6.5)

        self.pnl_engines = {}
        self.account_engines = {}
        charges_config = self.config.get("charges", {})
        for strat_name in self.strategies:
            instrument = self.strategies[strat_name].instrument
            # Fee model is derived from the instrument's own charges config
            # (e.g. stamp_duty_pct) — never global defaults that contradict it.
            inst_charges = charges_config.get(instrument, {})
            fee_model = MCXFeeModel.from_config(inst_charges) if inst_charges else MCXFeeModel()
            self.pnl_engines[strat_name] = PNLEngine(fee_model=fee_model)
            self.account_engines[strat_name] = AccountEngine(
                starting_capital=default_capital,
                margin_per_trade_pct=margin_pct,
            )
        # Global account engine (compat aggregate) — seeded with TOTAL capital
        # (account.starting_capital), NOT the per-strategy allocation.
        global_capital = account_config.get("starting_capital", 600_000.0)
        self.account_engine = AccountEngine(starting_capital=global_capital, margin_per_trade_pct=margin_pct)

    def _init_risk(self) -> None:
        risk_config = self.config.get("risk", {})
        self.risk_engine = RiskEngine(
            max_positions_per_strategy=risk_config.get("max_positions_per_strategy", 1),
        )

    def _init_monitoring(self) -> None:
        self.health = HealthMonitor()

    def _init_notifications(self) -> None:
        telegram_config = self.config.get("telegram", {})
        bot_token = telegram_config.get("bot_token", "")
        chat_id = telegram_config.get("chat_id", "")
        if bot_token and chat_id:
            from notifications.telegram_client import TelegramClient
            client = TelegramClient(bot_token=bot_token, chat_id=chat_id)
            self.telegram = TelegramRouter(client=client)
        else:
            self.telegram = TelegramRouter()

    def _build_runtimes(self, persistence) -> None:
        """Build (or rebuild) one isolated StrategyRuntime per strategy.

        Each runtime owns its own TradeLifecycleManager (scoped to the
        strategy), OrderManager (over the shared broker transport), and
        PositionManager. When persistence is available the lifecycle caches
        are restored from trading.db filtered by strategy_id. This is the
        ONLY place StrategyRuntime objects (and their lifecycle caches for
        the current persistence) are created — set_persistence() no longer
        wipes strategy/position state.
        """
        self.runtimes = StrategyRuntimeRegistry()
        for sid, strategy in self.strategies.items():
            lifecycle = TradeLifecycleManager(
                persistence=persistence,
                event_store=self.event_store,
                trade_ledger=self.trade_ledger,
                strategy_id=sid,
            )
            if persistence is not None:
                lifecycle.restore_from_db()
            order_manager = OrderManager(execution_engine=self.execution_engine)
            position_manager = PositionManager()
            runtime = StrategyRuntime(
                strategy_id=sid,
                strategy=strategy,
                lifecycle=lifecycle,
                order_manager=order_manager,
                position_manager=position_manager,
            )
            runtime.current_trade_id = getattr(strategy, "current_trade_id", None)
            self.runtimes.register(runtime)
            self.order_manager.register(sid, order_manager)
            self.position_manager.register(sid, position_manager)

        # Compat view: indicator components are owned per-strategy now; expose
        # them globally so boot audits / dashboards still find 'engine.indicators'.
        self.indicators = {}
        for sid, strategy in self.strategies.items():
            self.indicators[f"{sid}_fast"] = strategy.fast_indicator
            self.indicators[f"{sid}_mid"] = getattr(
                strategy, "mid_indicator", strategy.mid_htf_state)
            self.indicators[f"{sid}_slow"] = strategy.slow_htf_state

    def _runtime(self, strategy_id: str) -> StrategyRuntime:
        """Resolve the isolated runtime for a strategy (authoritative)."""
        return self.runtimes.require(strategy_id)

    # ═══════════════════════════════════════════════════════════════════
    # CANDLE + TICK HANDLERS (EventBus-driven)
    # ═══════════════════════════════════════════════════════════════════

    def _make_candle_handler(self, strategy: StrategyInstance):
        def handler(event):
            if not self._running:
                return
            with self._lock:
                self.health.record_bar()
                self.market_status.mark_rest_data_fresh()
                self._maybe_enable_trading()

                bar = Bar(
                    instrument=event.instrument,
                    timeframe=event.timeframe,
                    start_ts=event.start_ts,
                    end_ts=event.end_ts,
                    open=event.open, high=event.high,
                    low=event.low, close=event.close,
                    volume=int(event.volume),
                )
                # Deferred reversal exits only fire on the strategy's FAST
                # timeframe (next fast bar open). HTF/mid candles must NOT
                # consume a scheduled exit.
                is_fast = (event.timeframe == strategy.fast_timeframe)
                if is_fast:
                    self._process_deferred_exit(strategy, bar)
                signal = strategy.on_candle(event)
                if signal and is_fast:
                    self._process_signal(signal)
                    stop2 = strategy._consume_same_bar_stop(bar)
                    if stop2 is not None:
                        self._process_signal(stop2)
        return handler

    def _make_tick_handler(self, strategy: StrategyInstance):
        def handler(event):
            if not self._running or not self.tick_signal_processing:
                return
            if strategy.instrument != event.instrument:
                return
            if not (strategy.pending_entry is not None or strategy.position_side is not None):
                return
            tick_signal = strategy.on_tick(event.ltp, event.timestamp)
            if tick_signal:
                self._process_signal(tick_signal)
        return handler

    def _on_tick(self, tick) -> None:
        """Handle WebSocket tick — update execution price + position marks + publish to EventBus.

        Accepts both dict ticks (Dhan adapter canonical format, and test
        harness) and dataclass/object ticks.
        """
        from events.types import TickEvent

        if isinstance(tick, dict):
            instrument = tick.get("instrument")
            ltp = tick.get("ltp", 0.0)
            timestamp = tick.get("event_timestamp") or tick.get("timestamp") or time.time()
            volume = tick.get("volume", 0.0)
        else:
            instrument = getattr(tick, "instrument", None)
            ltp = getattr(tick, "ltp", 0.0)
            timestamp = (getattr(tick, "event_timestamp", None)
                         or getattr(tick, "timestamp", None) or time.time())
            volume = getattr(tick, "volume", 0.0)
        if not instrument:
            return

        valid_ltp = (isinstance(ltp, (int, float))
                     and ltp > 0.0
                     and not (isinstance(ltp, float) and (math.isnan(ltp) or math.isinf(ltp))))

        # Market-data bookkeeping (always, even for a bad-LTP sentinel tick)
        ws = getattr(self.data_adapter, "ws", None)
        ws_connected = bool(ws and ws.connected)
        self.market_status.update_data_status(
            connected=ws_connected,
            last_tick_time=(ws._last_tick_time if ws else 0.0),
        )
        if ws_connected:
            ws_stats = ws._stats if hasattr(ws, "_stats") else {}
            self.health.update_component(
                "data_adapter", SystemStatus.HEALTHY,
                f"{ws_stats.get('tick', 0) if ws_stats else 0} ticks")
            if ws.is_stale() and ws_stats.get("tick", 0) > 0:
                print("[Engine] WARNING: WebSocket stale - no ticks received recently", flush=True)
                if self.market_status.is_trading_allowed:
                    self.safe_mode.enter_safe_mode("market_data_uncertain",
                                                   "WebSocket stale during trading hours")
        else:
            self.health.update_component("data_adapter", SystemStatus.ERROR, "WebSocket disconnected")

        self.health.record_tick()
        self._maybe_enable_trading()

        with self._lock:
            if valid_ltp:
                self.execution_engine.update_price(instrument, ltp)
                for pos in self.position_manager.get_positions_by_instrument(instrument):
                    if pos.is_open:
                        pos.update_mark(ltp)

            # Always publish the tick — strategies guard on ltp <= 0/sentinels.
            event = TickEvent(
                instrument=instrument, ltp=float(ltp) if valid_ltp else 0.0,
                timestamp=float(timestamp or time.time()), volume=float(volume or 0.0),
            )
            self.event_bus.publish(f"tick:{instrument}", event)

    def _on_bar_closed(self, bar: Bar) -> None:
        """Handle closed bar — route through NativeCandleRouter to EventBus.

        Called by replay scripts and CandleFetcher callback. The router
        de-duplicates (security_id, timeframe, candle_end_ts) and drops
        out-of-order bars so replays can overlap live data safely.
        """
        if not self._running:
            return
        self.health.record_bar()
        self.market_status.mark_rest_data_fresh()

        router = getattr(self, "candle_router", None)
        if router is not None:
            router.on_candle(bar, is_complete=True)
            return

        from events.types import CandleEvent
        event = CandleEvent(
            instrument=bar.instrument, timeframe=bar.timeframe,
            start_ts=bar.start_ts, end_ts=bar.end_ts,
            open=bar.open, high=bar.high, low=bar.low,
            close=bar.close, volume=float(bar.volume),
            source="rest",
        )
        self.event_bus.publish(f"candle:{bar.instrument}:{bar.timeframe}", event)

    def _on_status(self, status) -> None:
        pass

    def _on_fill(self, fill) -> None:
        """Compatibility callback. Routes through the broker router by explicit
        broker_order_id mapping (§39); order submission passes the signal id."""
        router = getattr(self, "broker_router", None)
        if router is not None:
            router.route_fill(fill, self._handle_fill,
                              entry_signal_id=getattr(fill, "entry_signal_id", None))
        else:
            self._handle_fill(fill, getattr(fill, "entry_signal_id", None))

    # ═══════════════════════════════════════════════════════════════════
    # SIGNAL / EXIT / TRADE LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def _process_deferred_exit(self, strategy, bar, ltp=None, fill_price=None) -> bool:
        if not getattr(strategy, "pending_exit_at_open", False):
            return False
        if strategy.position_side is None:
            strategy.pending_exit_at_open = False
            strategy.pending_exit_reason = None
            return False

        exit_price = fill_price if fill_price is not None else (ltp if ltp is not None else bar.open)
        strategy.pending_exit_at_open = False
        strategy.pending_exit_bar_start = None
        reason = strategy.pending_exit_reason or "reversal"

        from strategies.types import Signal as StratSignal, SignalType
        exit_signal = StratSignal(
            signal_type=SignalType.SHORT if strategy.position_side == "LONG" else SignalType.LONG,
            instrument=strategy.instrument,
            strategy_id=strategy.strategy_id,
            # Offset AFTER the bar open: the exit is consumed at the open, so
            # ordering is preserved with other same-bar signals.
            timestamp=(bar.start_ts or time.time()) + 0.5,
            trigger_price=exit_price,
            stop_price=strategy.stop_price,
            quantity=strategy.quantity,
        )
        exit_signal.metadata = {
            "exit": True, "exit_reason": reason, "exit_price": exit_price,
            "deferred_exit": True, "source": "next_open", "fill_price": exit_price,
        }
        # The reversal exit and the opposite re-entry share ONE signal id —
        # the SIG-X lineage invariant (one trade id per pending reversal).
        if getattr(strategy, "pending_entry", None) is not None:
            exit_signal.signal_id = strategy.pending_entry.signal.signal_id
        strategy._close_position(reason)
        self._process_signal(exit_signal)
        return True

    def _process_signal(self, signal) -> None:
        """Move one strategy signal through the explicit durable lifecycle.

        Signal creation and breakout execution are deliberately separate: a
        pending breakout only writes the immutable signal; a trade id is born
        only after a trigger has actually occurred.

        All mutable lifecycle/execution/position state is resolved from the
        signal's OWN StrategyRuntime — a signal can never touch another
        strategy's lifecycle caches, order state, or positions.
        """
        metadata = signal.metadata or {}
        is_exit = bool(metadata.get("exit"))
        is_pending = bool(metadata.get("pending")) and not bool(metadata.get("triggered"))
        strategy = self.strategies.get(signal.strategy_id)
        if strategy is None:
            log.error("Dropping signal for unknown strategy %s", signal.strategy_id)
            self._quarantine_event(
                "unknown_strategy_signal",
                {"signal_id": signal.signal_id, "strategy_id": signal.strategy_id})
            return
        runtime = self._runtime(signal.strategy_id)
        if runtime is None:
            self._quarantine_event(
                "no_runtime_for_strategy",
                {"signal_id": signal.signal_id, "strategy_id": signal.strategy_id})
            return
        lifecycle = runtime.lifecycle
        order_manager = runtime.order_manager
        position_manager = runtime.position_manager

        # A bare opposite-side signal while this strategy holds an open
        # position is a REVERSAL: it closes the held position (never opens a
        # phantom/duplicate trade). Re-entry on the opposite side happens only
        # via a later breakout trigger armed by the strategy.
        if not is_exit and not is_pending:
            sig_side = (signal.side or getattr(signal.signal_type, "value", "")).upper()
            if sig_side in ("LONG", "SHORT"):
                open_pos = next((
                    p for p in position_manager.get_positions_by_strategy(signal.strategy_id)
                    if p.is_open and p.instrument == signal.instrument), None)
                if open_pos is not None:
                    held_side = "LONG" if open_pos.is_long else "SHORT"
                    if held_side != sig_side:
                        from strategies.types import Signal as StratSignal
                        reversal = StratSignal(
                            signal_type=signal.signal_type,
                            instrument=signal.instrument,
                            strategy_id=signal.strategy_id,
                            timestamp=signal.timestamp,
                            trigger_price=signal.trigger_price,
                            stop_price=signal.stop_price,
                            quantity=signal.quantity,
                        )
                        reversal.signal_id = signal.signal_id
                        reversal.metadata = dict(signal.metadata or {})
                        reversal.metadata.update({
                            "exit": True,
                            "exit_reason": f"{held_side.lower()}_reversal",
                            "is_reversal": True,
                        })
                        signal = reversal
                        is_exit = True

        self._persist_signal(signal, "exit" if is_exit else "entry")
        self.publish_event("signal_created", {
            "signal_id": signal.signal_id, "strategy_id": signal.strategy_id,
            "instrument": signal.instrument, "signal_type": signal.signal_type.name,
            "trigger_price": signal.trigger_price, "stop_price": signal.stop_price,
            "pending": is_pending,
        })
        if is_pending:
            return

        # Exits reduce risk and remain available during a safety halt. Entries
        # must pass both the session/data gate and the risk gate.
        if not is_exit:
            if self.safe_mode.is_active or not self.market_status.is_trading_allowed:
                self._reset_strategy_state(signal.strategy_id)
                return
            account = self.account_engines.get(signal.strategy_id)
            multiplier = self.config.instrument(signal.instrument).get("multiplier", 1.0)
            required_margin = self._calculate_margin(signal.instrument, signal.trigger_price, signal.quantity)
            held = position_manager.get_positions_by_strategy(signal.strategy_id)
            allowed, reason = self.risk_engine.check_order(
                signal, len(self.position_manager.open_positions),
                _strategy_positions_for_risk(signal.signal_type, held),
                account.available_margin if account else 0.0, required_margin,
                account.equity if account else 0.0,
            )
            if not allowed:
                log.warning("Order rejected for %s: %s", signal.strategy_id, reason)
                self._reset_strategy_state(signal.strategy_id)
                self.publish_event("order_rejected", {"signal_id": signal.signal_id,
                    "strategy_id": signal.strategy_id, "instrument": signal.instrument, "reason": reason})
                return

        multiplier = self.config.instrument(signal.instrument).get("multiplier", 1.0)
        if is_exit:
            position = next((p for p in position_manager.get_positions_by_strategy(signal.strategy_id)
                             if p.instrument == signal.instrument and p.is_open), None)
            trade = lifecycle.get_trade(position.trade_id) if position else None
            if trade is None:
                log.error("Exit signal %s has no explicit open trade", signal.signal_id)
                return
        else:
            trade = lifecycle.resolve_trade_from_signal(signal.signal_id)
            if trade is None:
                trade = lifecycle.create_trade_from_signal(
                    signal, signal.strategy_id, signal.strategy_id, signal.instrument,
                    signal.quantity, multiplier,
                )
            strategy.current_trade_id = trade.trade_id
            runtime.current_trade_id = trade.trade_id

        order = order_manager.submit_signal(signal, multiplier=multiplier, trade_id=trade.trade_id)
        if order is None:
            if not is_exit:
                self._reset_strategy_state(signal.strategy_id)
            return
        lifecycle.register_order(trade.trade_id, order.order_id, "EXIT" if is_exit else "ENTRY")
        self._persist_order(order, signal)
        self.publish_event("order_created", {"trade_id": trade.trade_id, "order_id": order.order_id,
            "signal_id": signal.signal_id, "strategy_id": signal.strategy_id,
            "instrument": signal.instrument, "state": order.state.value})
        for fill in order_manager.drain_fills():
            # §39 — every broker fill routes by explicit broker_order_id ->
            # strategy mapping (never symbol/side/latest order). Unmappable or
            # conflicting fills are quarantined, never applied.
            router = getattr(self, "broker_router", None)
            if router is not None:
                router.route_fill(
                    fill, self._handle_fill,
                    entry_signal_id=signal.signal_id, is_exit=is_exit)
            else:
                self._handle_fill(fill, signal.signal_id, is_exit=is_exit)

    def _persist_signal(self, signal, signal_type: str) -> None:
        if not self._persistence:
            return
        self._persistence.save_signal({
            "signal_id": signal.signal_id, "strategy_id": signal.strategy_id,
            "instrument": signal.instrument, "side": signal.signal_type.value,
            "signal_type": signal_type, "timestamp": signal.timestamp,
            "trigger_price": signal.trigger_price, "stop_price": signal.stop_price,
            "quantity": signal.quantity,
        })

    def _persist_order(self, order, signal) -> None:
        if self._persistence:
            self._persistence.save_order({
                "order_id": order.order_id, "strategy_id": order.strategy_id,
                "instrument": order.instrument, "side": order.side, "quantity": order.quantity,
                "order_type": order.order_type, "price": signal.trigger_price,
                "state": order.state.value, "filled_quantity": order.filled_quantity,
                "average_fill_price": order.average_fill_price,
                "created_at": datetime.fromtimestamp(order.created_at, tz=timezone.utc).isoformat(),
                "updated_at": datetime.fromtimestamp(order.updated_at, tz=timezone.utc).isoformat(),
                "signal_id": signal.signal_id, "trade_id": order.trade_id,
            })

    def _handle_fill(self, fill, signal_id: str | None, is_exit: bool | None = None) -> None:
        """Apply a fill exactly once, using explicit IDs throughout."""
        if self.fill_dedup.is_duplicate(fill.fill_id):
            return
        self.fill_dedup.note_processed(fill.fill_id)
        if fill.price <= 0 or (isinstance(fill.price, float) and not math.isfinite(fill.price)):
            self.fill_dedup.mark_processed(fill.fill_id)
            return

        # §34 — validate fill strategy identity via its owning runtime.
        # Unknown strategy ids are quarantined: the fill is never applied.
        runtime = None
        if hasattr(self, 'runtimes') and self.runtimes:
            try:
                runtime = self._runtime(fill.strategy_id)
            except (KeyError, ValueError):
                runtime = None
        if runtime is None:
            self._quarantine_event("fill_unknown_strategy", {
                "fill_id": fill.fill_id, "order_id": fill.order_id,
                "strategy_id": fill.strategy_id, "trade_id": getattr(fill, "trade_id", "")})
            self.fill_dedup.mark_processed(fill.fill_id)
            return
        lifecycle = runtime.lifecycle
        position_manager = runtime.position_manager
        current = next((p for p in position_manager.get_positions_by_strategy(fill.strategy_id)
                        if p.instrument == fill.instrument and p.is_open), None)
        is_exit = bool(is_exit) if is_exit is not None else current is not None
        if not is_exit:
            trade = lifecycle.get_trade(fill.trade_id) or lifecycle.resolve_trade_from_signal(signal_id)
            # §34 — entry fill must have an explicit trade reference in this
            # strategy's scope. A fill that resolves to a cross-strategy trade
            # (different strategy_id) is quarantined — never applied.
            if trade is None or trade.strategy_id != fill.strategy_id:
                self._quarantine_event("entry_fill_no_trade_or_mismatch", {
                    "fill_id": fill.fill_id, "order_id": fill.order_id,
                    "trade_id": getattr(fill, "trade_id", None),
                    "resolved_trade_id": getattr(trade, "trade_id", None) if trade else None,
                    "fill_strategy_id": fill.strategy_id,
                    "trade_strategy_id": getattr(trade, "strategy_id", None) if trade else None,
                    "signal_id": signal_id})
                self.fill_dedup.mark_processed(fill.fill_id)
                return
            account = self.account_engines[fill.strategy_id]
            margin = self._calculate_margin(fill.instrument, fill.price, fill.quantity)
            account_blocked = account.block_margin(margin)
            # Only block the global account if the per-strategy block
            # succeeded (avoids a double-release of the same margin).
            global_blocked = self.account_engine.block_margin(margin) if account_blocked else False
            if not (account_blocked and global_blocked):
                if account_blocked:
                    account.release_margin(margin)
                self._reset_strategy_state(fill.strategy_id)
                return
            position = position_manager.open_position(
                fill, multiplier=fill.multiplier, margin=margin,
                stop_price=self.strategies[fill.strategy_id].stop_price,
                entry_signal_id=signal_id, trade_id=trade.trade_id,
            )
            self._persist_fill(fill, trade.trade_id, signal_id)
            self._persist_position(position)
            lifecycle.register_entry_fill(trade.trade_id, fill.fill_id, fill.price, fill.timestamp)
            lifecycle.register_position(trade.trade_id, position.position_id)
            # Keep the analytics read-model (trade ledger) in lock-step at entry:
            # the OPEN projection must exist as soon as the position opens so a
            # crash/restart never has a position without a ledger trade.
            if self.trade_ledger is not None:
                try:
                    if self.trade_ledger.get_trade(trade.trade_id) is None:
                        self.trade_ledger.create_trade(
                            strategy_id=fill.strategy_id,
                            instrument=fill.instrument,
                            side="LONG" if position.is_long else "SHORT",
                            entry_quantity=position.quantity,
                            signal_time=fill.timestamp,
                            trigger_price=position.average_entry,
                            stop_price=getattr(position, "stop_price", None) or 0.0,
                            multiplier=fill.multiplier,
                            entry_reason="signal",
                            trade_id=trade.trade_id,
                            position_id=position.position_id,
                        )
                    self.trade_ledger.record_fill(
                        trade_id=trade.trade_id,
                        fill_id=fill.fill_id,
                        order_id=fill.order_id,
                        side="BUY" if position.is_long else "SELL",
                        quantity=fill.quantity,
                        price=fill.price,
                        timestamp=fill.timestamp,
                        is_entry=True,
                    )
                except Exception as e:
                    log.error("[Engine] ledger projection write failed for %s: %s",
                              trade.trade_id, e)
            self.publish_event("position_opened", {"trade_id": trade.trade_id,
                "position_id": position.position_id, "fill_id": fill.fill_id,
                "strategy_id": fill.strategy_id, "instrument": fill.instrument})
        else:
            if current is None or not current.trade_id:
                self._quarantine_event("exit_fill_no_position", {
                    "fill_id": fill.fill_id, "order_id": fill.order_id,
                    "strategy_id": fill.strategy_id,
                    "instrument": fill.instrument, "trade_id": getattr(fill, "trade_id", "")})
                self.fill_dedup.mark_processed(fill.fill_id)
                return
            if self._trade_close_manager is None:
                raise RuntimeError("trade close manager is not initialized")
            result = self._trade_close_manager.close_position(
                fill, current, fill.strategy_id, fill.multiplier,
                exit_reason=(self.strategies[fill.strategy_id].last_exit_reason or "signal_exit"),
                exit_signal_id=signal_id,
            )
            if result is False:
                return
            if self._persistence is not None and hasattr(self._persistence, "close_position_record"):
                try:
                    self._persistence.close_position_record(current)
                except Exception as e:
                    log.error("[Engine] close_position_record failed for %s: %s",
                              current.position_id, e)
            lifecycle.register_exit_fill(current.trade_id, fill.fill_id, fill.price,
                fill.timestamp, signal_id or "", exit_reason=self.strategies[fill.strategy_id].last_exit_reason or "signal_exit")
            lifecycle.close_trade(current.trade_id, result["gross_pnl"], result["charges"], result["net_pnl"])
            # Reversal exits arm an OPPOSITE pending breakout entry which must
            # survive the close: keep it if the strategy has one armed.
            pending_armed = self.strategies[fill.strategy_id].pending_entry is not None
            self._reset_strategy_state(fill.strategy_id, keep_pending=pending_armed)
        self.fill_dedup.mark_processed(fill.fill_id)

    def _persist_fill(self, fill, trade_id: str, signal_id: str | None) -> None:
        if self._persistence:
            self._persistence.save_fill({"fill_id": fill.fill_id, "order_id": fill.order_id,
                "strategy_id": fill.strategy_id, "instrument": fill.instrument, "side": fill.side,
                "quantity": fill.quantity, "price": fill.price,
                "timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
                "trade_id": trade_id, "entry_signal_id": signal_id})

    def _persist_position(self, position) -> None:
        """Persist a position row into the canonical positions table.

        position_id is the row key; trade_id is the separate canonical trade
        identity (position_id != trade_id, enforced by the DB trigger).
        """
        if self._persistence is not None and hasattr(self._persistence, "save_position"):
            try:
                self._persistence.save_position(position)
            except Exception as e:
                log.error("[Engine] save_position failed for %s: %s",
                          getattr(position, "position_id", "?"), e)

    def _calculate_margin(self, instrument: str, price: float, quantity: int) -> float:
        model = self.config.instrument(instrument).get("margin_model", {})
        if model:
            return quantity * (model.get("slope", 0.0) * price + model.get("intercept", 0.0))
        return price * quantity * self.config.instrument(instrument).get("multiplier", 1.0) * 0.065

    def _reset_strategy_state(self, strategy_id: str, keep_pending: bool = False) -> None:
        strategy = self.strategies.get(strategy_id)
        if strategy:
            keep = keep_pending and strategy.pending_entry is not None
            if keep:
                pen = strategy.pending_entry
                strategy.state = (StrategyState.PENDING_LONG if pen.side == "LONG"
                                  else StrategyState.PENDING_SHORT)
            else:
                strategy.state = StrategyState.FLAT
            strategy.position_side = strategy.stop_price = None
            if not keep:
                strategy.pending_entry = None
            strategy.current_trade_id = None
        runtime = self.runtimes.get(strategy_id)
        if runtime is not None:
            runtime.current_trade_id = None

    # ═══════════════════════════════════════════════════════════════════
    # PERSISTENCE / EVENTS
    # ═══════════════════════════════════════════════════════════════════

    def set_persistence(self, persistence) -> None:
        self._persistence = persistence
        if getattr(self, "broker_router", None) is not None:
            # §40 — broker mappings survive restart: reload the explicit
            # broker_order_id -> strategy mapping from canonical persistence so
            # late-arriving broker fills still route to the correct strategy.
            self.broker_router.set_persistence(persistence)
            try:
                self.broker_router.restore()
            except Exception as e:
                log.error("[Engine] broker router restore failed: %s", e)
        # Rebuild every StrategyRuntime lifecycle with the real persistence
        # and restore that strategy's OWN trades from trading.db. Rebuilding
        # on set_persistence() is safe now (the old wipe bug): runtimes only
        # carry lifecycle caches; strategy state, positions, orders and fills
        # live elsewhere and are preserved.
        self._build_runtimes(persistence)

    def publish_event(self, event_type: str, data: dict) -> None:
        if self._event_callback:
            try:
                self._event_callback(event_type, data)
            except Exception:
                pass
        if self._persistence:
            try:
                self._persistence.save_event({
                    "event_type": event_type,
                    "strategy_id": data.get("strategy_id", ""),
                    "instrument": data.get("instrument", ""),
                    "details": data,
                })
            except Exception:
                pass

    # ── §34 cross-strategy quarantine ───────────────────────────────────

    def _quarantine_event(self, reason: str, details: dict, persist: bool = True) -> None:
        """Reject a cross-strategy/mismatched lifecycle event.

        Logs ERROR, records the event, counts it, and — when persistence is
        available — writes a quarantine_records row. NEVER mutates lifecycle
        state (that is the caller's contract).
        """
        if not hasattr(self, "quarantine_count"):
            self.quarantine_count = 0
        if not hasattr(self, "_quarantined_events"):
            self._quarantined_events = []
        record = {"reason": reason, "details": dict(details), "timestamp": time.time()}
        self.quarantine_count += 1
        self._quarantined_events.append(record)
        if len(self._quarantined_events) > 500:
            self._quarantined_events = self._quarantined_events[-500:]
        log.error("[Engine] QUARANTINE %s details=%s", reason, details)
        if persist and self._persistence is not None:
            try:
                self._persistence.save_quarantine_record({
                    "original_type": "lifecycle_event",
                    "original_id": str(details.get("trade_id") or details.get("fill_id")
                                       or details.get("signal_id") or "?"),
                    "reason": reason,
                    "payload": details,
                })
            except Exception as e:
                log.error("[Engine] quarantine persist failed: %s", e)
        try:
            self.publish_event("quarantine_event", {"reason": reason, **details})
        except Exception:
            pass

    def quarantine_snapshot(self) -> dict:
        return {
            "count": getattr(self, "quarantine_count", 0),
            "events": list(getattr(self, "_quarantined_events", [])[-100:]),
        }

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self) -> None:
        self._running = True
        self.market_status.set_engine_status(EngineStatus.RECONCILING)

        # Wire TradeCloseManager
        self._trade_close_manager = TradeCloseManager(
            position_manager=self.position_manager,
            pnl_engines=self.pnl_engines,
            account_engines=self.account_engines,
            global_account=self.account_engine,
            risk_engine=self.risk_engine,
            persistence=self._persistence,
            event_store=self.event_store,
            telegram=self.telegram,
            event_callback=self._event_callback,
            trade_ledger=self.trade_ledger,
        )

        self.market_status.set_engine_status(EngineStatus.WARMING_UP)
        self._warmup_from_rest()
        self.candle_fetcher.start()
        self.data_adapter.connect()
        # READY: no trading yet. The first fresh candle/tick transitions
        # READY -> TRADING via _maybe_enable_trading().
        self.market_status.set_engine_status(EngineStatus.READY)

        log.info("[Engine] Started — %d strategies active", len(self.strategies))
        print(f"[Engine] Started — {len(self.strategies)} strategies active", flush=True)
        for name, strat in self.strategies.items():
            print(f"  {name}: {strat.instrument} {strat.fast_timeframe}/{strat.mid_timeframe}/{strat.htf_timeframe}", flush=True)

    def stop(self) -> None:
        self._running = False
        if hasattr(self, 'candle_fetcher'):
            self.candle_fetcher.stop()
        if hasattr(self, 'data_adapter'):
            self.data_adapter.disconnect()
        log.info("[Engine] Stopped")

    def _warmup_from_rest(self) -> None:
        """Warm up each strategy from REST historical data."""
        import pandas as pd
        warmup_cfg = self.config.get("warmup", {})
        last_days = int(warmup_cfg.get("last_trading_days", 5))
        fetch_days = int(warmup_cfg.get("fetch_calendar_days", 14))
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        base_from = (now - timedelta(days=fetch_days)).date()
        to_date = now.date()

        for name, strategy in self.strategies.items():
            try:
                # Per-strategy FAST timeframe warmup (5m and 15m strategies
                # each warm their own fast indicator) — never hardcode "5".
                fast_id = {"5m": "5", "15m": "15"}.get(strategy.fast_timeframe, "5")
                fast_minutes = strategy._tf_to_minutes(strategy.fast_timeframe)
                candles = self.data_adapter.fetch_historical_candles(
                    strategy.instrument, fast_id, base_from, to_date)
                if not candles:
                    continue
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                df = df.sort_values("datetime").reset_index(drop=True)
                if last_days > 0:
                    dates = sorted(df["datetime"].dt.date.unique())
                    keep = set(dates[-last_days:])
                    df = df[df["datetime"].dt.date.isin(keep)].reset_index(drop=True)
                for _, row in df.iterrows():
                    strategy.warmup_indicator(Bar(
                        instrument=strategy.instrument, timeframe=strategy.fast_timeframe,
                        start_ts=row["datetime"].timestamp(),
                        end_ts=row["datetime"].timestamp() + fast_minutes * 60,
                        open=row["open"], high=row["high"], low=row["low"], close=row["close"],
                        volume=int(row["volume"]),
                    ))
                for tf_id, tf_name in [("15", strategy.mid_timeframe), ("60", strategy.htf_timeframe)]:
                    try:
                        htf_candles = self.data_adapter.fetch_historical_candles(
                            strategy.instrument, tf_id, base_from, to_date)
                        if htf_candles:
                            for c in htf_candles:
                                bar = Bar(instrument=strategy.instrument, timeframe=tf_name,
                                          start_ts=c[0], end_ts=c[0] + int(tf_id) * 60,
                                          open=c[1], high=c[2], low=c[3], close=c[4], volume=int(c[5]))
                                strategy.warmup_htf(bar)
                                strategy.warmup_indicator_htf(bar)
                    except Exception as e:
                        log.warning("[Engine] %s: HTF warmup failed: %s", name, e)
                log.info("[Engine] %s warmed: fast=%d bars, mid_htf=%d, slow_htf=%d",
                         name, strategy.fast_indicator._count,
                         strategy.mid_htf_state.bar_count(), strategy.slow_htf_state.bar_count())
            except Exception as e:
                log.error("[Engine] %s warmup failed: %s", name, e)

    def restore(self, saved_state: dict) -> None:
        """Restore engine state from a saved snapshot.

        Restores per-strategy indicator/HTF/pending state AND the per-strategy
        position managers (each position back into its OWN runtime). Account
        margin is then reconstituted from the restored open positions so the
        startup reconciliation margin check is exactly consistent.
        """
        if not saved_state:
            return
        strategies_state = saved_state.get("strategies", {})
        for name, strat in self.strategies.items():
            if name in strategies_state:
                try:
                    strat.restore(strategies_state[name])
                except Exception:
                    pass
        positions_state = saved_state.get("positions")
        if positions_state:
            try:
                self.position_manager.restore(positions_state)
            except Exception:
                pass
        # Reconstitute per-strategy + global used_margin from restored open
        # positions so reconciliation (account vs position margins) is exact.
        for strat_id, account in self.account_engines.items():
            account.used_margin = sum(
                p.margin for p in self.position_manager.get_positions_by_strategy(strat_id)
                if p.is_open
            )
        self.account_engine.used_margin = sum(
            p.margin for p in self.position_manager.open_positions if p.is_open
        )
        # Mirror current trade ids into each runtime from the restored strategy.
        for rt in self.runtimes.all():
            rt.current_trade_id = getattr(rt.strategy, "current_trade_id", None)

    def snapshot(self) -> dict:
        router_stats = getattr(self, "candle_router", None)
        router_stats = router_stats.stats() if router_stats is not None else {}
        return {
            "running": self._running,
            "strategies": {name: strat.snapshot() for name, strat in self.strategies.items()},
            "positions": self.position_manager.snapshot(),
            "event_bus": self.event_bus.snapshot(),
            "candle_distributor": self.candle_distributor.candle_count,
            "candle_router": router_stats,
        }

    # ── Aggregate lifecycle views for shared API/WS infrastructure ──
    # These are read-only aggregations over the per-strategy lifecycles; no
    # shared mutable lifecycle state exists.

    def get_trade(self, trade_id: str) -> Optional[Any]:
        """Find a trade across the per-strategy lifecycles (read-only)."""
        for rt in self.runtimes.all():
            trade = rt.lifecycle.get_trade(trade_id)
            if trade is not None:
                return trade
        return None

    def reconcile_trades(self) -> dict:
        """Aggregate lifecycle.reconcile() across per-strategy lifecycles."""
        errors: list[Any] = []
        warnings: list[Any] = []
        stats = {"total_trades": 0, "open": 0, "closed": 0, "pending": 0}
        for rt in self.runtimes.all():
            res = rt.lifecycle.reconcile()
            errors.extend(res.get("errors", []))
            warnings.extend(res.get("warnings", []))
            for k, v in res.get("stats", {}).items():
                stats[k] = stats.get(k, 0) + v
        return {"errors": errors, "warnings": warnings, "stats": stats}

    def orphan_scan(self) -> dict:
        """Aggregate orphan_scan() across per-strategy lifecycles."""
        merged = {
            "orphan_fills": [], "orphan_orders": [], "orphan_positions": [],
            "orphan_pending_orders": [], "trades_without_signals": [],
            "trades_without_positions": [], "trades_with_wrong_exit_state": [],
            "mismatched_memory_db": [], "total_orphans": 0, "is_clean": False,
        }
        for rt in self.runtimes.all():
            try:
                res = rt.lifecycle.orphan_scan()
            except Exception:
                continue
            for key in ("orphan_fills", "orphan_orders", "orphan_positions",
                        "orphan_pending_orders", "trades_without_signals",
                        "trades_without_positions", "trades_with_wrong_exit_state",
                        "mismatched_memory_db"):
                merged[key].extend(res.get(key, []))
            merged["total_orphans"] += res.get("total_orphans", 0)
        merged["is_clean"] = merged["total_orphans"] == 0
        return merged

    def notify_settings_refreshed(self) -> None:
        self.publish_event("settings_refreshed", {"timestamp": time.time()})

    @property
    def tick_signal_processing(self) -> bool:
        return getattr(self, '_tick_signal_processing', True)

    @tick_signal_processing.setter
    def tick_signal_processing(self, value: bool):
        self._tick_signal_processing = value

    def _reconcile_strategy_positions(self) -> None:
        """Reconcile strategy state with actual positions.

        Heals the crash/REST restart gap where a strategy may be persisted as
        FLAT while the (per-strategy) position manager still holds an open
        position: re-derive the strategy's state/side/stop from the live open
        position so a restart never double-entries into a held position.
        """
        if not hasattr(self, "position_manager") or self.position_manager is None:
            return
        with self._lock:
            for sid, strategy in list(getattr(self, "strategies", {}).items()):
                open_pos = next((
                    p for p in self.position_manager.get_positions_by_strategy(sid)
                    if p.is_open), None)
                if open_pos is None:
                    continue
                if strategy.state not in (StrategyState.LONG_POSITION, StrategyState.SHORT_POSITION):
                    side_val = getattr(getattr(open_pos, "side", None), "value", None)
                    if side_val is None:
                        side_val = "LONG" if bool(getattr(open_pos, "is_long", False)) else "SHORT"
                    is_long = (side_val == "LONG")
                    strategy.state = (StrategyState.LONG_POSITION if is_long
                                      else StrategyState.SHORT_POSITION)
                    strategy.position_side = "LONG" if is_long else "SHORT"
                    if getattr(strategy, "stop_price", None) is None:
                        strategy.stop_price = getattr(open_pos, "stop_price", None)

    def _maybe_enable_trading(self) -> None:
        """Transition READY -> TRADING when the market is open and live market
        data is confirmed (via WebSocket ticks OR fresh REST candles).

        Called after every tick/candle. Trading becomes allowed only when:
        engine READY, MarketState LIVE_TRADING, and data is live.
        """
        if (self.market_status.engine_status == EngineStatus.READY
                and self.market_status.state == MarketState.LIVE_TRADING
                and self.market_status.has_live_market_data):
            self.market_status.set_engine_status(EngineStatus.TRADING)
