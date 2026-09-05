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
from strategies.instance import StrategyInstance
from strategies.gold import create_gold_5m, create_gold_15m
from strategies.silver import create_silver_5m, create_silver_15m
from execution.paper_broker import PaperExecutionEngine, Fill
from execution.fee_model import MCXFeeModel
from execution.order_manager import OrderManager
from portfolio.position_manager import PositionManager, Position
from portfolio.pnl import PNLEngine
from portfolio.account import AccountEngine
from monitoring.health import HealthMonitor, SystemStatus
from notifications.telegram_router import TelegramRouter
from analytics.event_store import EventStore
from analytics.trade_ledger import TradeLedger
from core.lifecycle import TradeLifecycleManager
from core.risk_engine import RiskEngine
from core.trade_close import TradeCloseManager

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
        self._lifecycle = TradeLifecycleManager()
        self._trade_close_manager = None

        # ── State ──
        self._running = False
        self._lock = threading.RLock()
        self._persistence = None

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
        """Initialize CandleFetcher and wire to EventBus via NativeCandleDistributor."""
        from core.candle_fetcher import CandleFetcher
        instruments = self.config.get("instruments", {})
        first_inst = list(instruments.values())[0] if instruments else {}
        session_open = first_inst.get("session_open", "09:00")
        session_close = first_inst.get("session_close", "23:30")
        self.candle_fetcher = CandleFetcher(
            data_adapter=self.data_adapter,
            instruments=instruments,
            on_candle_closed=self.candle_distributor.on_candle_closed,
            session_open=session_open,
            session_close=session_close,
            market_status=self.market_status,
        )

    def _init_timeframe_engine(self) -> None:
        """Alias for _init_candle_fetcher (backward compat with tests/scripts)."""
        self._init_candle_fetcher()

    def _init_strategies(self) -> None:
        """Initialize four independent StrategyInstances and subscribe to EventBus."""
        if not hasattr(self, 'event_bus') or self.event_bus is None:
            self.event_bus = EventBus()
        if not hasattr(self, 'candle_distributor') or self.candle_distributor is None:
            self.candle_distributor = NativeCandleDistributor(self.event_bus)
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

            # Subscribe to candle events
            for sub in strategy.subscriptions:
                self.event_bus.subscribe(f"candle:{sub}", self._make_candle_handler(strategy))
            # Subscribe to tick events for pending/SL
            tick_topic = f"tick:{instrument}"
            self.event_bus.subscribe(tick_topic, self._make_tick_handler(strategy))

    def _init_execution(self) -> None:
        paper_config = self.config.get("paper_execution", {})
        self.execution_engine = PaperExecutionEngine(
            slippage_ticks=paper_config.get("slippage_ticks", 1),
            latency_ms=paper_config.get("latency_ms", 100),
            partial_fill_probability=paper_config.get("partial_fill_probability", 0.0),
        )
        self.order_manager = OrderManager(execution_engine=self.execution_engine)

    def _init_portfolio(self) -> None:
        account_config = self.config.get("account", {})
        self.position_manager = PositionManager()
        default_capital = account_config.get("starting_capital_per_strategy", 300_000.0)
        margin_pct = self.config.get("risk", {}).get("margin_per_trade_pct", 6.5)

        self.pnl_engines = {}
        self.account_engines = {}
        fee_model = MCXFeeModel()
        for strat_name in self.strategies:
            self.pnl_engines[strat_name] = PNLEngine(fee_model=fee_model)
            self.account_engines[strat_name] = AccountEngine(
                starting_capital=default_capital,
                margin_per_trade_pct=margin_pct,
            )
        # Global account engine (compat)
        self.account_engine = AccountEngine(starting_capital=default_capital, margin_per_trade_pct=margin_pct)

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

                bar = Bar(
                    instrument=event.instrument,
                    timeframe=event.timeframe,
                    start_ts=event.start_ts,
                    end_ts=event.end_ts,
                    open=event.open, high=event.high,
                    low=event.low, close=event.close,
                    volume=int(event.volume),
                )
                self._process_deferred_exit(strategy, bar)
                signal = strategy.on_candle(event)
                if signal:
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
        """Handle WebSocket tick — update execution price + position marks + publish to EventBus."""
        from events.types import TickEvent
        instrument = getattr(tick, "instrument", None)
        if not instrument:
            return

        ltp = tick.ltp
        timestamp = tick.timestamp

        # Update execution engine
        self.execution_engine.update_price(instrument, ltp)

        # Mark positions (LTP mark)
        for pos in self.position_manager.get_positions_by_instrument(instrument):
            if hasattr(pos, "update_mark"):
                pos.update_mark(ltp)

        # Publish tick event for strategy SL/trigger processing
        event = TickEvent(
            instrument=instrument, ltp=ltp,
            timestamp=timestamp, volume=getattr(tick, "volume", 0.0),
        )
        self.event_bus.publish(f"tick:{instrument}", event)

    def _on_bar_closed(self, bar: Bar) -> None:
        """Handle closed bar — publish to EventBus for per-strategy processing.

        Called by replay scripts and CandleFetcher callback.
        """
        if not self._running:
            return
        self.health.record_bar()
        self.market_status.mark_rest_data_fresh()

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
        """Handle fill from execution engine (used in dedup replay)."""
        pass

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
        reason = strategy.pending_exit_reason or "reversal"

        from strategies.types import Signal as StratSignal, SignalType
        exit_signal = StratSignal(
            signal_type=SignalType.SHORT if strategy.position_side == "LONG" else SignalType.LONG,
            instrument=strategy.instrument,
            strategy_id=strategy.strategy_id,
            timestamp=bar.start_ts,
            trigger_price=exit_price,
            stop_price=strategy.stop_price,
            quantity=strategy.quantity,
        )
        exit_signal.metadata = {"exit_reason": reason, "exit_price": exit_price, "deferred_exit": True}
        strategy._close_position(reason)
        self._process_signal(exit_signal)
        return True

    def _process_signal(self, signal) -> None:
        log.info("[Engine] Signal: %s %s %s trigger=%.0f SL=%.0f",
                 signal.strategy_id, signal.signal_type.name,
                 signal.instrument, signal.trigger_price, signal.stop_price)
        self.publish_event("signal_created", {
            "strategy_id": signal.strategy_id,
            "instrument": signal.instrument,
            "signal_type": signal.signal_type.name,
            "trigger_price": signal.trigger_price,
            "stop_price": signal.stop_price,
        })

    # ═══════════════════════════════════════════════════════════════════
    # PERSISTENCE / EVENTS
    # ═══════════════════════════════════════════════════════════════════

    def set_persistence(self, persistence) -> None:
        self._persistence = persistence

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

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self) -> None:
        self._running = True
        self.market_status.set_engine_status(EngineStatus.TRADING)

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

        self._warmup_from_rest()
        self.candle_fetcher.start()
        self.data_adapter.connect()
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
                candles = self.data_adapter.fetch_historical_candles(
                    strategy.instrument, "5", base_from, to_date)
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
                        start_ts=row["datetime"].timestamp(), end_ts=row["datetime"].timestamp() + 300,
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
        """Restore engine state from a saved snapshot."""
        if not saved_state:
            return
        strategies_state = saved_state.get("strategies", {})
        for name, strat in self.strategies.items():
            if name in strategies_state:
                try:
                    strat.restore(strategies_state[name])
                except Exception:
                    pass

    def snapshot(self) -> dict:
        return {
            "running": self._running,
            "strategies": {name: strat.snapshot() for name, strat in self.strategies.items()},
            "event_bus": self.event_bus.snapshot(),
            "candle_distributor": self.candle_distributor.candle_count,
        }

    def notify_settings_refreshed(self) -> None:
        self.publish_event("settings_refreshed", {"timestamp": time.time()})

    @property
    def tick_signal_processing(self) -> bool:
        return getattr(self, '_tick_signal_processing', True)

    @tick_signal_processing.setter
    def tick_signal_processing(self, value: bool):
        self._tick_signal_processing = value

    def _reconcile_strategy_positions(self) -> None:
        """Reconcile strategy state with actual positions."""
        pass

    def _maybe_enable_trading(self) -> None:
        """Check if trading should be enabled."""
        pass
