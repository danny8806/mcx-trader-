"""Main trading engine - orchestrates all components."""
from __future__ import annotations

import json
import math
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from config import Config
from data.dhan import DhanDataAdapter
from core.timeframe_engine import Bar
from core.risk_engine import RiskEngine
from core.market_status import MarketStatus, MarketState, EngineStatus
from core.safe_mode import SafeModeManager
from core.trade_close import TradeCloseManager
from core.fill_dedup import FillDeduplicator
from indicators.dema_atr import DEMAATR
from htf.backtest_style_htf import BacktestStyleHTFEngine
from strategies.base_dema_strategy import BaseDEMAStrategy, Signal, SignalType, StrategyState
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


def _strategy_positions_for_risk(signal_type: "SignalType", open_positions: list) -> int:
    """Number of the strategy's open positions to apply against the per-strategy
    position cap for an incoming order.

    A REVERSAL converts an existing position of the OPPOSITE side into the
    incoming side; it never ADDS to the strategy's position count. Counting the
    held opposite-side position would wrongly trip max_positions_per_strategy
    (e.g. long->short at a 1-position cap) and reject the reversal re-entry,
    stranding the stale leg. Net out the held position(s) that the reversal
    replaces so the cap is not tripped by the position being flipped."""
    open_held = [p for p in open_positions if getattr(p, "is_open", False)]
    holds_short = any(getattr(p, "is_short", False) for p in open_held)
    holds_long = any(getattr(p, "is_long", False) for p in open_held)
    if signal_type == SignalType.LONG and holds_short:
        return max(0, len(open_held) - 1)
    if signal_type == SignalType.SHORT and holds_long:
        return max(0, len(open_held) - 1)
    return len(open_held)


class TradingEngine:
    """Main trading engine that orchestrates all components.
    
    Event flow:
    Dhan WebSocket -> Tick -> TimeframeEngine -> Bar -> IndicatorEngine
    -> HTFEngine -> Strategy -> Signal -> OrderManager -> ExecutionEngine
    -> Fill -> PositionManager -> PNLEngine -> AccountEngine -> Persistence
    """

    def __init__(self, config_path: Optional[str] = None, event_callback=None):
        # Load configuration
        self.config = Config()
        if config_path:
            self.config.load(Path(config_path))
        else:
            self.config.load()

        # Dashboard EventBus callback
        self._event_callback = event_callback

        # Initialize components
        self._init_market_status()
        self._init_data_adapter()
        self._init_timeframe_engine()
        self._init_indicator_engines()
        self._init_htf_engine()
        self._init_strategies()
        self._init_execution()
        self._init_portfolio()
        self._init_risk()
        self._init_monitoring()
        self._init_notifications()

        # Analytics event store (writes to analytics.db).  Resolve the path
        # against the project root (not cwd) so a different launch cwd can never
        # silently fork a separate analytics DB.
        db_root = Path(Config.resolve_path(self.config.get("system", {}).get("db_path", "data/db/trading.db"))).parent
        analytics_db = str(db_root / "analytics.db")
        try:
            self.event_store = EventStore(db_path=analytics_db)
        except Exception:
            self.event_store = None

        # Trade ledger (rich trade lifecycle in analytics.db)
        try:
            self.trade_ledger = TradeLedger(db_path=analytics_db)
        except Exception:
            self.trade_ledger = None

        # Fill deduplication
        db_path = Config.resolve_path(self.config.get("system", {}).get("db_path", "trading.db"))
        self.fill_dedup = FillDeduplicator(db_path=db_path)

        # Safe mode manager
        self.safe_mode = SafeModeManager(self.market_status)

        # Atomic trade close manager (wired after portfolio init)
        self._trade_close_manager = None  # wired in start()

        # Central trade lifecycle manager (wired after persistence init)
        self._lifecycle = TradeLifecycleManager()  # fully wired in start()

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._persistence = None

        # Live vs bar-model signal routing.  In LIVE, WebSocket ticks carry the
        # pending-breakout trigger checks + tick SL (direct market order the
        # moment price crosses the trigger).  In the OFFLINE/SIM path the bars
        # are replayed and the per-bar closing tick is only a proxy, so tick
        # signal processing must be disabled to reproduce the bar-crossing model.
        self.tick_signal_processing = True

    def set_persistence(self, persistence) -> None:
        """Set persistence manager for trade logging."""
        self._persistence = persistence

    def notify_settings_refreshed(self) -> None:
        """Broadcast that settings were reloaded via the dashboard."""
        self.publish_event("settings_refreshed", {
            "timestamp": time.time(),
        })

    def publish_event(self, event_type: str, data: dict) -> None:
        """Publish event to dashboard EventBus + persistence."""
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

    def _init_data_adapter(self) -> None:
        """Initialize Dhan data adapter."""
        dhan_config = self.config.get("dhan")
        self.data_adapter = DhanDataAdapter(
            client_id=dhan_config["client_id"],
            token_file=dhan_config.get("token_file", "dhan_token.json"),
            pin=dhan_config.get("pin", ""),
            totp_secret=dhan_config.get("totp_secret", ""),
            on_tick=self._on_tick,
            on_status=self._on_status,
        )
        # Register instruments
        instruments = self.config.get("instruments", {})
        self.data_adapter.register_instruments(instruments)

    def _init_timeframe_engine(self) -> None:
        """Initialize candle fetcher (REST-based, not tick-based)."""
        from core.candle_fetcher import CandleFetcher
        instruments = self.config.get("instruments", {})
        first_inst = list(instruments.values())[0] if instruments else {}
        session_open = first_inst.get("session_open", "09:00")
        session_close = first_inst.get("session_close", "23:30")
        
        self.candle_fetcher = CandleFetcher(
            data_adapter=self.data_adapter,
            instruments=instruments,
            on_candle_closed=lambda bar: self._on_bar_closed(bar),
            session_open=session_open,
            session_close=session_close,
            market_status=self.market_status,
        )

    def _init_indicator_engines(self) -> None:
        """Initialize DEMA-ATR indicator engines for each instrument/timeframe."""
        indicators_config = self.config.get("indicators", {})
        self.indicators: dict[str, DEMAATR] = {}
        instruments = self.config.get("instruments", {})
        for inst_name in instruments:
            for tf in ["5m", "15m", "1h"]:
                key = f"{inst_name}:{tf}"
                self.indicators[key] = DEMAATR(
                    dema_period=indicators_config.get("dema_period", 3),
                    atr_period=indicators_config.get("atr_period", 6),
                    atr_factor=indicators_config.get("atr_factor", 1.0),
                )

    def _init_htf_engine(self) -> None:
        """Initialize HTF engine using EXACT backtest logic."""
        self.htf_engine = BacktestStyleHTFEngine()
        strategies_config = self.config.get("strategies", {})
        indicators_config = self.config.get("indicators", {})
        # Register unique instrument+htf combos
        registered = set()
        for strat_name, strat_config in strategies_config.items():
            if not strat_config.get("enabled", True):
                continue
            instrument = strat_config["instrument"]
            slow_tf = strat_config.get("htf_timeframe", "1h")
            mid_tf = strat_config.get("mid_timeframe", "15m")
            dema_p = indicators_config.get("dema_period", 3)
            atr_p = indicators_config.get("atr_period", 6)
            atr_f = indicators_config.get("atr_factor", 1.0)
            session_open = self.config.get("instruments", {}).get(instrument, {}).get("session_open", "09:00")
            # Register 1H signal line
            key_1h = f"{instrument}:1h"
            if key_1h not in registered:
                self.htf_engine.register(instrument, "1h", dema_p, atr_p, atr_f, session_open)
                registered.add(key_1h)
            # Register 15m confirmation line
            key_15m = f"{instrument}:15m"
            if key_15m not in registered:
                self.htf_engine.register(instrument, "15m", dema_p, atr_p, atr_f, session_open)
                registered.add(key_15m)

    def _init_strategies(self) -> None:
        """Initialize strategy instances."""
        from strategies.gold import (
            GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04,
        )
        from strategies.silver import (
            SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04,
        )

        strategy_classes = {
            "gold_01": GoldStrategy01,
            "gold_02": GoldStrategy02,
            "gold_03": GoldStrategy03,
            "gold_04": GoldStrategy04,
            "silver_01": SilverStrategy01,
            "silver_02": SilverStrategy02,
            "silver_03": SilverStrategy03,
            "silver_04": SilverStrategy04,
        }

        self.strategies: dict[str, BaseDEMAStrategy] = {}
        strategies_config = self.config.get("strategies", {})
        for strat_name, strat_config in strategies_config.items():
            if not strat_config.get("enabled", True):
                continue
            cls = strategy_classes.get(strat_name)
            if cls:
                self.strategies[strat_name] = cls(
                    strategy_id=strat_name,
                    instrument=strat_config.get("instrument", "GOLDM"),
                    fast_timeframe=strat_config.get("fast_timeframe", "5m"),
                    htf_timeframe=strat_config.get("htf_timeframe", "1h"),
                    quantity=strat_config.get("quantity", 1),
                )

    def _init_execution(self) -> None:
        """Initialize execution engine and order manager. PAPER MODE ONLY."""
        paper_config = self.config.get("paper_execution", {})
        exec_mode = self.config.get("execution_mode", "paper").lower()
        if exec_mode != "paper":
            raise RuntimeError(
                f"EXECUTION_MODE must be 'paper', got '{exec_mode}'. "
                "Live trading is not supported in this system."
            )
        self.execution_engine = PaperExecutionEngine(
            slippage_ticks=paper_config.get("slippage_ticks", 1),
            latency_ms=paper_config.get("latency_ms", 100),
            # NOTE: must default to 0.0 — PaperExecutionEngine raises ValueError
            # when partial_fill_probability != 0 (partial-close accounting does
            # not exist).  A nonzero default here would crash the engine on any
            # config omission of the key.
            partial_fill_probability=paper_config.get("partial_fill_probability", 0.0),
        )
        self.order_manager = OrderManager(
            execution_engine=self.execution_engine,
        )

    def _init_portfolio(self) -> None:
        """Initialize portfolio components with per-strategy capital isolation."""
        account_config = self.config.get("account", {})
        self.position_manager = PositionManager()

        default_capital = account_config.get("starting_capital_per_strategy", 300_000.0)
        margin_pct = self.config.get("risk", {}).get("margin_per_trade_pct", 6.5)

        self.pnl_engines: dict[str, PNLEngine] = {}
        self.account_engines: dict[str, AccountEngine] = {}
        strategies_config = self.config.get("strategies", {})
        for strat_name, strat_config in strategies_config.items():
            if not strat_config.get("enabled", True):
                continue
            instrument = strat_config.get("instrument", "GOLDM")
            # Per-strategy capital override: check strategy config first, then global
            per_strategy_capital = strat_config.get("capital", default_capital)
            self.pnl_engines[strat_name] = PNLEngine(
                fee_model=self._create_fee_model(instrument),
            )
            self.account_engines[strat_name] = AccountEngine(
                starting_capital=per_strategy_capital,
                margin_per_trade_pct=margin_pct,
            )

        # Global account engine for aggregate views (sum of all strategies)
        total_capital = sum(
            acct.starting_capital for acct in self.account_engines.values()
        )
        self.account_engine = AccountEngine(
            starting_capital=total_capital,
            margin_per_trade_pct=margin_pct,
        )

    def _init_risk(self) -> None:
        """Initialize risk engine."""
        risk_config = self.config.get("risk", {})
        self.risk_engine = RiskEngine(
            max_positions_per_strategy=risk_config.get("max_open_positions_per_strategy", 1),
            max_positions_total=risk_config.get("max_open_positions_total", 8),
            max_daily_loss=risk_config.get("max_daily_loss", 50_000.0),
            max_drawdown_pct=risk_config.get("max_drawdown_pct", 5.0),
            kill_switch_enabled=risk_config.get("kill_switch_enabled", True),
        )

    def _init_monitoring(self) -> None:
        """Initialize health monitoring."""
        self.health = HealthMonitor()
        self.health.register_component("data_adapter")
        self.health.register_component("timeframe_engine")
        self.health.register_component("htf_engine")
        self.health.register_component("strategy")
        self.health.register_component("execution")
        self.health.register_component("risk")

    def _init_notifications(self) -> None:
        """Initialize Telegram notification router."""
        self.telegram = TelegramRouter()
        try:
            self.telegram.start()
        except Exception:
            pass

    def _init_market_status(self) -> None:
        """Initialize market session lifecycle manager."""
        instruments = self.config.get("instruments", {})
        first_inst = list(instruments.values())[0] if instruments else {}
        session_open = first_inst.get("session_open", "09:00")
        session_close = first_inst.get("session_close", "23:30")
        self.market_status = MarketStatus(
            session_open=session_open,
            session_close=session_close,
        )

    def _create_fee_model(self, instrument: str) -> MCXFeeModel:
        """Create fee model for an instrument."""
        charges_config = self.config.get("charges", {}).get(instrument, {})
        return MCXFeeModel.from_config(charges_config)

    def start(self) -> None:
        """Start the trading engine with live verification report."""
        if self._running:
            return
        self._running = True
        self.market_status.set_engine_status(EngineStatus.INITIALIZING)
        self.health.mark_all(SystemStatus.HEALTHY, "initializing")
        print("[Engine] Starting...", flush=True)

        # Wire atomic trade close manager
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

        # Wire central trade lifecycle manager — single source of truth for all trade identity
        self._lifecycle = TradeLifecycleManager(
            persistence=self._persistence,
            event_store=self.event_store,
            trade_ledger=self.trade_ledger,
        )

        # Load fill dedup state from database
        try:
            count = self.fill_dedup.load_from_database()
            print(f"[Engine] Loaded {count} processed fills for dedup", flush=True)
        except Exception as e:
            print(f"[Engine] Warning: Could not load fill dedup state: {e}", flush=True)

        # Startup reconciliation
        self.market_status.set_engine_status(EngineStatus.RECONCILING)
        try:
            from reconciliation.engine import ReconciliationEngine
            recon = ReconciliationEngine(
                persistence=self._persistence,
                position_manager=self.position_manager,
                pnl_engines=self.pnl_engines,
                account_engines=self.account_engines,
                strategies=self.strategies,
                order_manager=self.order_manager,
            )
            result = recon.reconcile(phase="startup")
            print(result.summary(), flush=True)
            if not result.is_consistent:
                self.safe_mode.enter_safe_mode("reconciliation_failed", f"{len(result.errors)} errors")
            else:
                # Reconciliation passed — clear any stale safe_mode override
                # that was persisted from a previous session (e.g. WS disconnect).
                # If SafeModeManager has no active reasons, exit safe mode.
                if self.safe_mode.is_active:
                    # Clear all reasons that were from prior sessions, not fresh
                    for reason in list(self.safe_mode.active_reasons):
                        self.safe_mode.clear_reason(reason)
                if self.market_status.is_safe:
                    self.market_status.exit_safe_mode()
                    print("[Engine] Cleared stale safe_mode after successful reconciliation", flush=True)
            self.market_status.mark_reconcile_done()
        except Exception as e:
            print(f"[Engine] Reconciliation failed: {e}", flush=True)
            self.safe_mode.enter_safe_mode("reconciliation_failed", str(e))

        # Startup backfill: fetch historical candles, resample, pre-populate HTF engine
        self.market_status.set_engine_status(EngineStatus.WARMING_UP)
        self._warmup_from_rest()
        self.market_status.mark_warmup_done()

        # Start candle fetcher (fetches candles from REST when they close)
        self.candle_fetcher.start()

        self.data_adapter.connect()

        # Wait for connection and first ticks
        import time as _time
        print("[Engine] Waiting for data feed...", flush=True)
        for _ in range(10):
            _time.sleep(1)
            if self.data_adapter.ws and self.data_adapter.ws.connected:
                break

        # Collect LIVE status after data flowing
        _time.sleep(2)

        checks = []
        ws = self.data_adapter.ws

        # 1. WebSocket - actual live status
        ws_connected = ws.connected if ws else False
        ws_ticks = ws._stats.get("tick", 0) if ws else 0
        ws_subs = len(ws._instruments) if ws else 0
        checks.append(("WebSocket", ws_connected,
            f"{'Connected' if ws_connected else 'DISCONNECTED'} | {ws_ticks} ticks | {ws_subs} instruments"))

        # 2. Live LTP for each instrument
        for name, cfg in self.config.get("instruments", {}).items():
            price = self.execution_engine._current_prices.get(name, 0.0)
            checks.append((f"LTP {name}", price > 0, f"₹{price:,.1f}" if price > 0 else "No data"))

        # 3. Candle fetcher status
        candle_status = "RUNNING" if self.candle_fetcher._running else "STOPPED"
        checks.append(("CandleFetcher", self.candle_fetcher._running, f"Status: {candle_status}"))

        # 4. Indicators - initialized count
        init_count = sum(1 for ind in self.indicators.values() if ind.initialized)
        total_ind = len(self.indicators)
        checks.append(("DEMAATR Indicators", init_count > 0,
            f"{init_count}/{total_ind} initialized"))

        # 5. HTF engine
        htf_engines = len(self.htf_engine._engines)
        htf_confirmed = sum(1 for s in self.htf_engine._engines.values()
                           if s.values)
        checks.append(("HTF Engine", htf_engines > 0,
            f"{htf_engines} engines/{htf_confirmed} with DEMA-ATR values"))

        # 6. Strategies - actual state
        strat_details = []
        flat_count = 0
        pos_count = 0
        for name, strat in self.strategies.items():
            if strat.has_position:
                pos_count += 1
                strat_details.append(f"{name}:{strat.position_side}")
            else:
                flat_count += 1
        strat_state = f"{flat_count} flat"
        if pos_count > 0:
            strat_state += f" | {pos_count} in position: " + ", ".join(strat_details)
        checks.append(("Strategies", True, f"{len(self.strategies)} loaded | {strat_state}"))

        # 7. Per-strategy capital - actual equity
        for name, acct in self.account_engines.items():
            checks.append((f"Capital {name}", True,
                f"Equity ₹{acct.equity:,.0f} | Used ₹{acct.used_margin:,.0f} | Avail ₹{acct.available_margin:,.0f}"))

        # 8. P&L engines
        active_pnl = sum(1 for e in self.pnl_engines.values() if e.trade_count > 0)
        total_pnl = sum(e.realized_net for e in self.pnl_engines.values())
        checks.append(("P&L", True,
            f"{len(self.pnl_engines)} engines | {active_pnl} active | Realized ₹{total_pnl:,.0f}"))

        # 9. Risk engine
        risk_snap = self.risk_engine.snapshot()
        checks.append(("Risk Engine", True,
            f"Daily P&L ₹{risk_snap.get('daily_pnl', 0):,.0f} | Peak ₹{risk_snap.get('peak_equity', 0):,.0f}"))

        # 10. Execution mode
        checks.append(("Execution", True, "PAPER mode"))

        # 11. Telegram
        tg_stats = self.telegram.get_stats()
        checks.append(("Telegram", tg_stats.get("configured", False),
            f"Sent {tg_stats.get('sent_count', 0)} | Errors {tg_stats.get('error_count', 0)}"))

        # 12. Persistence
        pers_ok = self._persistence is not None
        checks.append(("Persistence", pers_ok, "trading.db" if pers_ok else "Not wired"))

        # 13. Analytics
        es_ok = self.event_store is not None
        es_count = 0
        if es_ok:
            try:
                es_count = self.event_store.count_events()
            except Exception:
                pass
        checks.append(("Analytics DB", es_ok, f"{es_count} events logged"))

        all_ok = all(c[1] for c in checks)

        now_str = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S IST")
        lines = []
        lines.append(f"SYSTEM STARTUP — {now_str}")
        lines.append("=" * 32)
        lines.append("")
        lines.append(f"Status: {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
        lines.append(f"Mode: PAPER ONLY")
        lines.append("")

        # Strategies
        lines.append("STRATEGIES:")
        for name, strat in self.strategies.items():
            acct = self.account_engines.get(name)
            cap = f"Rs{acct.starting_capital:,.0f}" if acct else "N/A"
            sid = self.config.get("instruments", {}).get(strat.instrument, {}).get("security_id", "?")
            lines.append(f"  {name}: {strat.instrument} [{sid}] {strat.fast_timeframe}+{strat.htf_timeframe} | {cap}")
        lines.append("")

        # Indicators
        lines.append("INDICATORS:")
        for key, ind in self.indicators.items():
            status = "OK" if ind.initialized else "WARMING"
            val = f"{ind.value:.1f}" if ind.value else "N/A"
            lines.append(f"  {key}: {status} | {val}")
        lines.append("")

        # HTF Engine
        lines.append("HTF ENGINE:")
        for key, state in self.htf_engine._engines.items():
            n = len(state.values) if state.values else 0
            last = f"{state.last_value:.1f}" if state.last_value else "N/A"
            lines.append(f"  {key}: {n} bars | {last}")
        lines.append("")

        # Live LTP
        lines.append("LIVE LTP:")
        for name, cfg_inst in self.config.get("instruments", {}).items():
            sid = cfg_inst.get("security_id", "?")
            price = self.execution_engine._current_prices.get(name, 0)
            if price > 0:
                lines.append(f"  {name} [{sid}]: Rs{price:,.1f}")
            else:
                lines.append(f"  {name} [{sid}]: waiting")
        lines.append("")

        # Accounts
        lines.append("ACCOUNTS:")
        total_eq = 0
        for name, acct in self.account_engines.items():
            lines.append(f"  {name}: Rs{acct.equity:,.0f}")
            total_eq += acct.equity
        lines.append(f"  TOTAL: Rs{total_eq:,.0f}")
        lines.append("")

        # Components
        lines.append("COMPONENTS:")
        for name, ok, detail in checks:
            lines.append(f"  {'OK' if ok else 'FAIL'} {name}: {detail}")
        lines.append("")
        lines.append("READY — waiting for market data")

        report = "\n".join(lines)

        try:
            self.telegram.send_sync(report)
            print(f"[Engine] Startup Telegram sent", flush=True)
        except Exception as e:
            print(f"[Telegram] Startup failed: {e}", flush=True)

        self.market_status.set_engine_status(EngineStatus.READY)

    def stop(self) -> None:
        """Stop the trading engine gracefully."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        self.market_status.set_engine_status(EngineStatus.STOPPED)
        self.health.mark_all(SystemStatus.STOPPED, "engine stopped")
        self.data_adapter.disconnect()
        # Stop candle fetcher
        self.candle_fetcher.stop()
        # Stop Telegram
        try:
            self.telegram.stop()
        except Exception:
            pass
        print("[Engine] Stopped", flush=True)

    def _maybe_enable_trading(self) -> None:
        """Transition READY -> TRADING when the market is open and live market
        data is confirmed (via WebSocket ticks OR fresh REST candles).

        Called from both _on_tick (WebSocket path) and _on_bar_closed (REST
        candle path) so that a silent WebSocket LTP feed does not stall trading
        while REST candles keep confirming real live prices.
        """
        if (self.market_status.state == MarketState.LIVE_TRADING
                and self.market_status.engine_status == EngineStatus.READY
                and self.market_status.has_live_market_data):
            self.market_status.set_engine_status(EngineStatus.TRADING)

    def _on_tick(self, tick: dict[str, Any]) -> None:
        """Handle incoming tick from WebSocket.
        
        WebSocket is ONLY for LTP (live price). Candles come from REST API.
        """
        if not self._running:
            return
        instrument = tick.get("instrument")
        ltp = tick.get("ltp", 0.0)
        timestamp = tick.get("event_timestamp") or tick.get("timestamp", time.time())
        # Guard: a bad LTP (Dhan `-1` no-data sentinel, NaN, inf, <=0) must not
        # be written into the execution price map or used for unrealized marks.
        # If the tick is invalid, still maintain data-status/health/lock bookkeeping
        # but skip all price-sensitive updates.
        valid_ltp = ltp is not None and not (isinstance(ltp, float) and (math.isnan(ltp) or math.isinf(ltp))) and ltp > 0.0

        # Update market data status
        ws_connected = self.data_adapter.ws.connected if self.data_adapter.ws else False
        self.market_status.update_data_status(
            connected=ws_connected,
            last_tick_time=self.data_adapter.ws._last_tick_time if self.data_adapter.ws else 0.0,
        )

        # Sync data_adapter health component to live feed state
        if ws_connected:
            self.health.update_component(
                "data_adapter", SystemStatus.HEALTHY,
                f"{self.data_adapter.ws._stats.get('tick', 0) if self.data_adapter.ws else 0} ticks",
            )
        else:
            self.health.update_component("data_adapter", SystemStatus.ERROR, "WebSocket disconnected")

        # Stale connection check — only if WS has received ticks before
        # (avoids false safe_mode during initial connection before first tick)
        ws_stats = self.data_adapter.ws._stats if self.data_adapter.ws else {}
        if (self.data_adapter.ws and self.data_adapter.ws.is_stale()
                and ws_stats.get("tick", 0) > 0):
            print("[Engine] WARNING: WebSocket stale - no ticks received recently", flush=True)
            if self.market_status.is_trading_allowed:
                self.safe_mode.enter_safe_mode("market_data_uncertain", "WebSocket stale during trading hours")

        self.health.record_tick()

        # Transition engine to TRADING if market is open and data is flowing
        self._maybe_enable_trading()

        with self._lock:
            # [1] Update execution engine price (for order fills)
            # Only with a valid (positive, finite) LTP; an invalid tick leaves
            # the last-known-good price in place so fills never use `-1`/NaN.
            if valid_ltp:
                self.execution_engine.update_price(instrument, ltp)

                # [2] Tick-level P&L marking for open positions
                open_positions = self.position_manager.get_positions_by_instrument(instrument)
                for pos in open_positions:
                    if pos.is_open:
                        pos.update_mark(ltp)
                        # Track MFE/MAE in the analytics trade ledger so
                        # max-favorable/adverse excursion is captured live.
                        if self.trade_ledger is not None:
                            try:
                                self.trade_ledger.update_mfe_mae(pos.position_id, ltp)
                            except Exception:
                                pass
            # Update per-strategy accounts — sum ALL open positions (not just current instrument)
            for strat_id in self.account_engines:
                strat_positions = self.position_manager.get_positions_by_strategy(strat_id)
                strat_unrealized = sum(p.unrealized_pnl for p in strat_positions if p.is_open)
                self.account_engines[strat_id].update_unrealized_pnl(strat_unrealized)
                pnl_eng = self.pnl_engines.get(strat_id)
                if pnl_eng is not None:
                    pnl_eng.update_unrealized_pnl(strat_unrealized)
            # Update global account
            all_unrealized = sum(p.unrealized_pnl for p in self.position_manager.open_positions)
            self.account_engine.update_unrealized_pnl(all_unrealized)

            # [3] Update peak equity for drawdown tracking
            current_equity = self.account_engine.equity if hasattr(self.account_engine, 'equity') else None
            if current_equity is not None:
                self.risk_engine.update_peak_equity(current_equity)

            # [4] Reversal-deferred exits (trigger-based) and
            # [5] tick-entry-trigger / stop-loss monitoring. Both are price
            # sensitive: skip entirely when the LTP is invalid.
            if self.tick_signal_processing and valid_ltp:
                for strat in self.strategies.values():
                    if strat.instrument != instrument:
                        continue
                    if not getattr(strat, "pending_exit_at_open", False):
                        continue

                    # Trigger-based exit: when LTP crosses the trigger price,
                    # fire the deferred exit IMMEDIATELY at the trigger price
                    # (not at LTP or next bar open).  This ensures the LONG
                    # exit and SHORT entry fire simultaneously at the same
                    # price level when the trigger is reached.
                    if strat.pending_entry is not None:
                        trigger = strat.pending_entry.trigger_price
                        side = strat.pending_entry.side
                        trigger_crossed = (
                            (side == "SHORT" and ltp <= trigger) or
                            (side == "LONG" and ltp >= trigger)
                        )
                        if trigger_crossed:
                            self._process_deferred_exit(
                                strat, None, fill_price=trigger)
                            # Deferred exit consumed — pending entry will
                            # fire in [5] on this same tick since
                            # pending_exit_at_open is now False.
                            continue

                    # Fallback: time-gate based exit (next bar's open)
                    if strat.pending_exit_bar_start is not None:
                        window = getattr(strat, "fast_window_seconds", 300)
                        if timestamp - strat.pending_exit_bar_start <= window:
                            continue
                    self._process_deferred_exit(strat, None, ltp=ltp)

                for strat in self.strategies.values():
                    if strat.instrument != instrument:
                        continue
                    if not (strat.pending_entry is not None
                            or strat.position_side is not None):
                        continue
                    tick_signal = strat.on_tick(ltp, timestamp)
                    if tick_signal:
                        self._process_signal(tick_signal)

    def _on_bar_closed(self, bar: Bar) -> None:
        """Handle closed bar from timeframe engine."""
        if not self._running:
            return
        key = f"{bar.instrument}:{bar.timeframe}"
        indicator = self.indicators.get(key)
        if indicator is None:
            return

        self.health.record_bar()

        # A successfully delivered REST candle confirms real live market data
        # (authoritative source of truth for signals). Mirror the WebSocket data
        # confirmation so trading is not stalled by a silent WS LTP feed.
        self.market_status.mark_rest_data_fresh()
        self._maybe_enable_trading()

        with self._lock:
            # Update indicator
            indicator.update(bar.open, bar.high, bar.low, bar.close)

            # Feed closed 1H/15m bars to backtest-style HTF engine
            if bar.timeframe in ("1h", "15m"):
                self.htf_engine.on_htf_bar_closed(bar)

            # Check if any strategies should process this 5m bar
            for strat in self.strategies.values():
                if strat.instrument != bar.instrument:
                    continue
                if strat.fast_timeframe != bar.timeframe:
                    continue

                # Get fast indicator value
                fast_key = f"{bar.instrument}:{strat.fast_timeframe}"
                fast_indicator = self.indicators.get(fast_key)
                if fast_indicator is None or not fast_indicator.initialized:
                    continue

                # Map 1H DEMA-ATR value to this 5m bar using EXACT backtest searchsorted
                htf_mapped = self.htf_engine.map_to_fast_bar(bar, strat.fast_timeframe)

                # Map 15m confirmation line
                mid_mapped = self.htf_engine.map_mid_to_fast_bar(bar, strat.fast_timeframe)

                # Backtest-placement model: consume a reversal-deferred exit at
                # this fast bar's OPEN (fills at bar.open), then let on_bar run
                # the armed opposite-side breakout re-entry for the same bar.
                self._process_deferred_exit(strat, bar)

                # Process bar with HTF value
                signal = strat.on_bar(bar, htf_mapped, fast_indicator.value, mid_mapped)
                if signal:
                    self._process_signal(signal)
                # Same-bar stop: entry AND stop-loss on one candle book as a
                # round-trip (reference goldm_dema_mtf_futures checks the SL
                # on the entry bar and exits at that bar's close).
                if signal:
                    stop2 = strat._consume_same_bar_stop(bar)
                    if stop2 is not None:
                        self._process_signal(stop2)

    def _process_deferred_exit(self, strat, bar, ltp: Optional[float] = None,
                               fill_price: Optional[float] = None) -> bool:
        """Execute a reversal-scheduled exit at the next fast bar's OPEN.

        Consumes strat.pending_exit_at_open (armed by _create_reversal_signal
        on the crossing bar): submits a market exit for the held position that
        fills at this bar's open.  The strategy's pending_entry (the opposite
        breakout) then routes through the normal signal/order/fill path once a
        bar crosses its trigger — the backtest exit-at-next-open + breakout
        re-entry model.

        When fill_price is provided (trigger-based exit), the exit fills at
        the specified price instead of bar.open or LTP.  This is used for
        simultaneous exit+entry when LTP crosses the trigger price.

        Returns True if a deferred exit was consumed.
        """
        if strat is None or not getattr(strat, "pending_exit_at_open", False):
            return False
        if strat.position_side is None:
            # Position already gone (e.g. tick SL) — drop the deferred
            # exit but KEEP the armed pending_entry for the opposite breakout.
            strat.pending_exit_at_open = False
            strat.pending_exit_reason = None
            strat.pending_exit_bar_start = None
            return False
        if fill_price is not None:
            exit_price = fill_price
        else:
            exit_price = ltp if ltp is not None else bar.open
        strat.pending_exit_at_open = False
        strat.pending_exit_reason = strat.pending_exit_reason or "reversal"
        strat.pending_exit_bar_start = None
        if bar is None:
            sig_ts = time.time()
        else:
            # Timestamp offset keeps this signal's dedup key distinct from the
            # same-bar breakout entry (bar.start_ts).
            sig_ts = (bar.start_ts or time.time()) + 0.5
        exit_signal = Signal(
            signal_type=SignalType.SHORT if strat.position_side == "LONG" else SignalType.LONG,
            instrument=strat.instrument,
            strategy_id=strat.strategy_id,
            timestamp=sig_ts,
            trigger_price=exit_price,
            stop_price=0.0,
            quantity=strat.quantity,
            metadata={"exit": True, "exit_reason": strat.pending_exit_reason,
                      "source": "next_open", "fill_price": exit_price},
        )
        self._process_signal(exit_signal)
        return True

    def _calculate_margin(self, instrument: str, price: float, quantity: int, side: str = "BUY") -> float:
        """Calculate margin required using Dhan's margin model.

        Formula: margin = quantity * (slope * price + intercept)
        Derived from Dhan /margincalculator API (linear fit, residuals < 0.06).
        Update slope/intercept in config when contracts roll over.
        """
        instrument_config = self.config.instrument(instrument)
        margin_model = instrument_config.get("margin_model")
        if margin_model:
            slope = margin_model.get("slope", 0.125)
            intercept = margin_model.get("intercept", 126930.0)
            margin = quantity * (slope * price + intercept)
            print(f"[Margin] {instrument}: {margin:.0f} (slope={slope}, intercept={intercept}, price={price}, qty={quantity})", flush=True)
            return margin

        # Fallback: percentage-based estimate
        multiplier = instrument_config.get("multiplier", 1.0)
        margin_pct = self.config.get("risk", {}).get("margin_per_trade_pct", 6.5)
        fallback = price * quantity * multiplier * margin_pct / 100.0
        print(f"[Margin] {instrument} fallback: {fallback:.0f} ({margin_pct}% of notional)", flush=True)
        return fallback

    def _process_signal(self, signal: Signal) -> None:
        """Process a trading signal."""
        metadata = signal.metadata or {}
        is_exit = bool(metadata.get("exit"))

        # An exit reduces exposure.  It must remain executable during safe mode,
        # stale data, risk-limit trips, and the market-close window.
        if not is_exit and self.safe_mode.is_active:
            print(f"[Signal] BLOCKED by safe mode: {signal.strategy_id} {signal.signal_type}", flush=True)
            # The armed entry never executed — clear the strategy's ghost
            # position/pending state so a later same-bar-stop exit cannot be
            # booked as an entry from nothing (and reconciliation stays clean).
            self._reset_strategy_state(signal.strategy_id)
            return
        if not is_exit and not self.market_status.is_trading_allowed:
            print(f"[Signal] BLOCKED by market state ({self.market_status.state.value}): {signal.strategy_id} {signal.signal_type}", flush=True)
            self._reset_strategy_state(signal.strategy_id)
            return
        self.health.record_signal()

        # ── Persist signal to signals table for audit trail ──
        if self._persistence:
            try:
                self._persistence.save_signal({
                    "signal_id": signal.signal_id,
                    "strategy_id": signal.strategy_id,
                    "instrument": signal.instrument,
                    "side": signal.signal_type.value,
                    "signal_type": "exit" if is_exit else "entry",
                    "timestamp": datetime.fromtimestamp(signal.timestamp, tz=timezone.utc).isoformat(),
                    "trigger_price": signal.trigger_price,
                    "stop_price": signal.stop_price,
                    "quantity": signal.quantity,
                    "candle_data": metadata.get("candle_data"),
                    "indicator_data": metadata.get("indicator_data"),
                })
            except Exception as e:
                print(f"[Signal] WARNING: failed to persist signal {signal.signal_id}: {e}", flush=True)

        # Risk check using per-strategy account
        instrument_config = self.config.instrument(signal.instrument)
        multiplier = instrument_config.get("multiplier", 1.0)
        strat_account = self.account_engines.get(signal.strategy_id)
        if strat_account is None:
            print(f"[Risk] No account engine for strategy {signal.strategy_id}", flush=True)
            self._reset_strategy_state(signal.strategy_id)
            return
        if is_exit:
            allowed, reason = True, None
        else:
            margin_required = self._calculate_margin(
                signal.instrument, signal.trigger_price, signal.quantity,
                side="BUY" if signal.signal_type in (SignalType.LONG, SignalType.REVERSAL) else "SELL",
            )
            held_positions = self.position_manager.get_positions_by_strategy(signal.strategy_id)
            open_held = [p for p in held_positions if getattr(p, "is_open", False)]
            strategy_positions = _strategy_positions_for_risk(signal.signal_type, open_held)
            allowed, reason = self.risk_engine.check_order(
                signal=signal,
                current_positions=len(self.position_manager.open_positions),
                strategy_positions=strategy_positions,
                available_margin=strat_account.available_margin,
                margin_required=margin_required,
                current_equity=strat_account.equity,
            )

        if not allowed:
            print(f"[Risk] Order rejected: {reason}", flush=True)
            print(f"  Strategy: {signal.strategy_id}  Instrument: {signal.instrument}  Side: {signal.signal_type}  Price: {signal.trigger_price}", flush=True)
            strat = self.strategies.get(signal.strategy_id)
            if strat:
                # This path is only for new entries.  It is safe to cancel their
                # pending state; exit signals bypass risk checks above.
                strat.state = StrategyState.FLAT
                strat.position_side = None
                strat.stop_price = None
                strat.pending_entry = None
            self.publish_event("order_rejected", {
                "strategy_id": signal.strategy_id,
                "instrument": signal.instrument,
                "side": str(signal.signal_type),
                "trigger_price": signal.trigger_price,
                "stop_price": signal.stop_price,
                "quantity": signal.quantity,
                "reason": reason,
            })
            if self.event_store:
                try:
                    self.event_store.record(
                        trade_id=f"rejected:{signal.strategy_id}:{signal.timestamp}",
                        strategy_id=signal.strategy_id,
                        instrument=signal.instrument,
                        event_type="ORDER_REJECTED",
                        payload={
                            "side": str(signal.signal_type),
                            "trigger_price": signal.trigger_price,
                            "stop_price": signal.stop_price,
                            "quantity": signal.quantity,
                            "reason": reason,
                        },
                    )
                except Exception:
                    pass
            return

        # Submit order
        fill_price = metadata.get("fill_price")
        if fill_price is not None:
            fp = float(fill_price)
            # Guard: a bad fill_price (<=0 / NaN / inf) must never be placed
            # into the execution price map, or it could poison later fills.
            if fp > 0.0 and not (isinstance(fp, float) and (math.isnan(fp) or math.isinf(fp))):
                # Backtest-placement model: fills at the exact model price level
                # (trigger breakout = trigger level, SL exit = bar close, reversal
                # exit = next bar open).  Override the broker's LTP for THIS order
                # only; the next order/bar re-establishes its own LTP.
                self.execution_engine.update_price(signal.instrument, fp)
        order = self.order_manager.submit_signal(signal, multiplier=multiplier)

        # ── Register trade in central lifecycle ──
        # For non-exit signals, create a new trade context
        if not is_exit and order:
            # Check if a trade already exists for this signal
            existing_trade = self._lifecycle.resolve_trade_from_signal(signal.signal_id)
            if not existing_trade:
                lifecycle_trade = self._lifecycle.create_trade_from_signal(
                    signal=signal,
                    strategy_id=signal.strategy_id,
                    strategy_name=signal.strategy_id,
                    instrument=signal.instrument,
                    quantity=signal.quantity,
                    multiplier=multiplier,
                )
                # Register the order with the trade
                self._lifecycle.register_order(lifecycle_trade.trade_id, order.order_id, role="ENTRY")
        elif is_exit and order:
            # Exit signal — find existing trade by signal or position
            existing_trade = self._lifecycle.resolve_trade_from_signal(signal.signal_id)
            if existing_trade:
                self._lifecycle.register_order(existing_trade.trade_id, order.order_id, role="EXIT")

        if order:
            # ── Persist the order row BEFORE dispensing its fills, so the DB
            # invariant "every fill references an existing order" holds even on
            # a crash between the two (reconciliation would otherwise flag
            # orphan fills / filled orders with no fill rows). ──
            if self._persistence:
                try:
                    self._persistence.save_order({
                        "order_id": order.order_id,
                        "strategy_id": signal.strategy_id,
                        "instrument": signal.instrument,
                        "side": str(order.side),
                        "quantity": signal.quantity,
                        "order_type": "MARKET",
                        "price": signal.trigger_price,
                        "state": order.state.value,
                        "filled_quantity": order.quantity,
                        "average_fill_price": order.average_fill_price,
                        "created_at": datetime.fromtimestamp(order.created_at or time.time(), tz=timezone.utc).isoformat(),
                        "updated_at": datetime.fromtimestamp(order.updated_at or time.time(), tz=timezone.utc).isoformat(),
                        "entry_signal_id": signal.signal_id,
                    })
                except Exception as e:
                    # LOUD, not silent: the order-before-fill DB invariant is
                    # otherwise broken — the fills dispatched below reference an
                    # order_id never persisted to trading.db, and reconciliation
                    # would flag orphan fills on restart. The fills still proceed
                    # (the paper broker already produced them), but the missing
                    # order row is visible instead of hidden.
                    print(f"[Order] WARNING: failed to persist order {order.order_id}: {e}", flush=True)
            # Dispatch fills produced by the order (each persists its own fill
            # row in _on_fill before any position references it).
            for fill in self.order_manager.drain_fills():
                try:
                    self._on_fill(fill, signal_id=signal.signal_id)
                except Exception as e:
                    # Broad guard: an unexpected throw inside _on_fill must not
                    # leave the strategy in a ghost long/short (blocked margin
                    # with no position, or un-reset state).  Log LOUD and reset
                    # the strategy to FLAT so the next bar starts clean instead
                    # of silently wedging the engine.
                    print(f"[Fill] CRITICAL: _on_fill unhandled for {fill.fill_id}: {e}", flush=True)
                    strat = self.strategies.get(fill.strategy_id)
                    if strat:
                        strat.state = StrategyState.FLAT
                        strat.position_side = None
                        strat.stop_price = None
                        strat.pending_entry = None
            print(f"[Order] Submitted: {order.order_id} {order.side} {order.instrument}", flush=True)
            self._notify_signal_alert(signal, order, metadata)
            if self.event_store:
                try:
                    self.event_store.record(
                        trade_id=order.order_id, strategy_id=signal.strategy_id,
                        instrument=signal.instrument, event_type="ORDER_CREATED",
                        payload={"order_id": order.order_id, "side": order.side,
                                 "trigger_price": signal.trigger_price, "stop_price": signal.stop_price,
                                 "signal_id": signal.signal_id},
                    )
                except Exception:
                    pass
            self.publish_event("order_submitted", {
                "order_id": order.order_id,
                "signal_id": signal.signal_id,
                "strategy_id": signal.strategy_id,
                "instrument": signal.instrument,
                "side": str(order.side),
                "trigger_price": signal.trigger_price,
                "stop_price": signal.stop_price,
                "quantity": signal.quantity,
            })
        else:
            # Order submission failed — reset strategy state to prevent stuck state
            print(f"[Order] SUBMISSION FAILED for {signal.strategy_id} - resetting strategy", flush=True)
            strat = self.strategies.get(signal.strategy_id)
            if strat:
                strat.state = StrategyState.FLAT
                strat.position_side = None
                strat.stop_price = None
                strat.pending_entry = None
                print(f"  Strategy state RESET to FLAT", flush=True)
            try:
                self.telegram.on_risk_alert({
                    "type": "order_failed",
                    "severity": "CRITICAL",
                    "message": f"Order submission FAILED for {signal.strategy_id} ({signal.instrument}) - strategy reset to FLAT",
                    "strategy_id": signal.strategy_id,
                    "instrument": signal.instrument,
                    "side": str(signal.signal_type),
                })
            except Exception:
                pass

    def _notify_signal_alert(self, signal: Signal, order, metadata: dict) -> None:
        """Send a Telegram SIGNAL CANDLE ALERT for entry signals: the candle
        that produced the cross plus the candle the trade was placed on."""
        try:
            if bool(metadata.get("exit")):
                return
            sig_md = metadata
            if not sig_md.get("signal_candle_start"):
                return
            ist = timezone(timedelta(hours=5, minutes=30))
            sig_candle = sig_md.get("signal_candle_start")
            place_candle = sig_md.get("placement_candle_start")
            fill_px = order.average_fill_price if order.average_fill_price else sig_md.get("fill_price")
            self.telegram.on_signal({
                "instrument": signal.instrument,
                "strategy_id": signal.strategy_id,
                "side": sig_md.get("signal_side") or (signal.side or signal.signal_type.value),
                "signal_candle_time": datetime.fromtimestamp(sig_candle, tz=ist).strftime("%Y-%m-%d %H:%M IST") if sig_candle else None,
                "signal_candle_open": sig_md.get("signal_candle_open"),
                "signal_candle_high": sig_md.get("signal_candle_high"),
                "signal_candle_low": sig_md.get("signal_candle_low"),
                "signal_candle_close": sig_md.get("signal_candle_close"),
                "signal_htf_dema_atr": sig_md.get("signal_htf_dema_atr"),
                "signal_mid_dema_atr": sig_md.get("signal_mid_dema_atr"),
                "signal_fast_dema_atr": sig_md.get("signal_fast_dema_atr"),
                "signal_trigger_price": signal.trigger_price,
                "placement_candle_time": datetime.fromtimestamp(place_candle, tz=ist).strftime("%Y-%m-%d %H:%M IST") if place_candle else None,
                "fill_price": fill_px,
            })
        except Exception:
            pass

    def _reset_strategy_state(self, strategy_id: str, *, clear_same_bar: bool = True) -> None:
        """Return an armed-but-never-executed strategy to a clean flat state.

        Called when an entry signal is blocked or its order never reaches the
        exchange.  Without this the strategy would hold a ghost
        LONG/SHORT_POSITION while the engine has no open position — and a later
        same-bar-stop "exit" would be booked by _on_fill as a brand-new entry.
        """
        strat = self.strategies.get(strategy_id)
        if not strat:
            return
        strat.state = StrategyState.FLAT
        strat.position_side = None
        strat.stop_price = None
        strat.pending_entry = None
        if clear_same_bar:
            strat.same_bar_stop = None
            strat.last_exit_reason = None

    def _on_fill(self, fill: Fill, signal_id: Optional[str] = None) -> None:
        """Handle order fill with dedup and atomic close."""
        # Fill deduplication — in-memory set + DB set fast path.
        if self.fill_dedup.is_duplicate(fill.fill_id):
            print(f"[Fill] DUPLICATE ignored: {fill.fill_id}", flush=True)
            return
        # DB-backed idempotency: if a prior process persisted this fill but
        # crashed before the durable mark at the end of this method, replay it
        # WITHOUT re-applying the financial effects (the fill row already
        # exists in the DB, so the trade/position rows were written too).
        if self._persistence:
            try:
                if self._persistence.get_fill(fill.fill_id) is not None:
                    print(f"[Fill] DB replay detected, skipping re-apply: {fill.fill_id}", flush=True)
                    self.fill_dedup.mark_processed(fill.fill_id)
                    return
            except Exception as e:
                # FAIL-SAFE on DB uncertainty: if we cannot confirm whether this
                # fill was already persisted by a prior session, we must NOT
                # re-apply its financial effects (that could double-open a
                # position or spuriously close one).  Mark it processed and skip
                # loudly so reconcile/telegram can flag it — never silently
                # reprocess into an inconsistent state.
                print(f"[Fill] CRITICAL: idempotency check failed for {fill.fill_id}, skipping (fail-safe): {e}", flush=True)
                self.fill_dedup.mark_processed(fill.fill_id)
                return
        # In-process dedup lock: protects against a single fill being delivered
        # twice inside this process before the durable DB mark at method end.
        self.fill_dedup.note_processed(fill.fill_id)

        self.health.record_fill()
        instrument_config = self.config.instrument(fill.instrument)
        multiplier = instrument_config.get("multiplier", 1.0)

        # Check if this is an entry or exit
        positions = self.position_manager.get_positions_by_strategy(fill.strategy_id)
        open_pos = [p for p in positions if p.instrument == fill.instrument and p.is_open]

        if not open_pos:
            # New entry
            strat_account = self.account_engines.get(fill.strategy_id)
            fill_side = fill.side.value if hasattr(fill.side, 'value') else str(fill.side)
            margin = self._calculate_margin(
                fill.instrument, fill.price, fill.quantity, side=fill_side,
            )
            if strat_account and strat_account.block_margin(margin):
                if not self.account_engine.block_margin(margin):
                    # Global account margin failed — rollback per-strategy
                    strat_account.release_margin(margin)
                    print(f"[Fill] GLOBAL MARGIN BLOCKED: {fill.strategy_id} - rolling back", flush=True)
                    self.fill_dedup.mark_processed(fill.fill_id)
                    strat = self.strategies.get(fill.strategy_id)
                    if strat:
                        strat.state = StrategyState.FLAT
                        strat.position_side = None
                        strat.stop_price = None
                        strat.pending_entry = None
                    return
                # ── Open position FIRST, then persist the fill.  If save_fill
                # fails or a crash lands between the two, the failure is LOUD:
                # the restored position references a fill_id missing from the DB,
                # which reconciliation catches and enters safe mode.  The old
                # order (save_fill → open_position) left the SAME window but
                # made it SILENT: an orphan fill in DB with no position,
                # skipping future re-applies while consuming blocked margin. ──
                try:
                    position = self.position_manager.open_position(
                        fill=fill, multiplier=multiplier, margin=margin,
                        stop_price=(self.strategies.get(fill.strategy_id).stop_price
                                    if fill.strategy_id in self.strategies else None),
                        entry_signal_id=signal_id,
                    )
                except Exception as e:
                    # Rollback margin on position open failure
                    print(f"[Fill] CRITICAL: Position open failed, rolling back margin: {e}", flush=True)
                    strat_account.release_margin(margin)
                    self.account_engine.release_margin(margin)
                    strat = self.strategies.get(fill.strategy_id)
                    if strat:
                        strat.state = StrategyState.FLAT
                        strat.position_side = None
                        strat.stop_price = None
                    self.fill_dedup.mark_processed(fill.fill_id)
                    return
                if self._persistence:
                    try:
                        self._persistence.save_fill({
                            "fill_id": fill.fill_id, "order_id": fill.order_id,
                            "strategy_id": fill.strategy_id, "instrument": fill.instrument,
                            "side": fill.side, "quantity": fill.quantity, "price": fill.price,
                            "timestamp": datetime.fromtimestamp(fill.timestamp or time.time(), tz=timezone.utc).isoformat(),
                            "entry_signal_id": signal_id,
                            "trade_id": position.position_id,
                        })
                    except Exception as exc:
                        # Fill persist failed — the in-memory position now
                        # references a fill_id absent from the DB.  This is
                        # LOUD (reconciliation error on next restart) rather
                        # than silent orphan.  Release margin so at least the
                        # account is not permanently locked.
                        print(f"[Fill] WARNING: fill persist failed ({exc}), margin released; position will be caught by reconciliation on next restart", flush=True)
                        self.position_manager.close_position(
                            position.position_id, fill,
                            reason="fill_persist_failed",
                        )
                        strat_account.release_margin(margin)
                        self.account_engine.release_margin(margin)
                        strat = self.strategies.get(fill.strategy_id)
                        if strat:
                            strat.state = StrategyState.FLAT
                            strat.position_side = None
                            strat.stop_price = None
                        self.fill_dedup.mark_processed(fill.fill_id)
                        return
                print(f"[Position] Opened: {position.side.value} {fill.instrument} @ {fill.price} (strategy={fill.strategy_id})", flush=True)

                # ── Register entry fill in central lifecycle ──
                lifecycle_trade = self._lifecycle.resolve_trade_from_signal(signal_id)
                if lifecycle_trade:
                    self._lifecycle.register_entry_fill(
                        trade_id=lifecycle_trade.trade_id,
                        fill_id=fill.fill_id,
                        price=fill.price,
                        timestamp=fill.timestamp or time.time(),
                    )
                    self._lifecycle.register_position(lifecycle_trade.trade_id, position.position_id)
                else:
                    # Fallback: create lifecycle trade from existing position
                    # This handles cases where signal wasn't registered earlier
                    print(f"[Lifecycle] WARNING: no trade found for signal {signal_id}, creating lifecycle entry", flush=True)

                if self.event_store:
                    try:
                        self.event_store.record(
                            trade_id=position.position_id, strategy_id=fill.strategy_id,
                            instrument=fill.instrument, event_type="POSITION_OPENED",
                            payload={"side": position.side.value, "price": fill.price,
                                     "quantity": fill.quantity, "margin": margin},
                        )
                    except Exception as e:
                        print(f"[Fill] WARNING: POSITION_OPENED event write failed for {position.position_id}: {e}", flush=True)
                self.publish_event("position_opened", {
                    "position_id": position.position_id, "strategy_id": fill.strategy_id,
                    "instrument": fill.instrument, "side": position.side.value,
                    "price": fill.price, "quantity": fill.quantity, "margin": margin,
                })
                # ── Persist trade-signal link (entry) ──
                if self._persistence and signal_id:
                    try:
                        self._persistence.save_trade_signal_link(
                            trade_id=position.position_id, signal_id=signal_id,
                            relationship_type="entry",
                        )
                    except Exception as e:
                        print(f"[Signal] WARNING: trade_signal_link save failed: {e}", flush=True)
                # Create trade in ledger (position-anchored 1:1: trade_id =
                # position_id) and record the entry fill leg.
                if self.trade_ledger:
                    try:
                        strat = self.strategies.get(fill.strategy_id)
                        stop_price = strat.stop_price if strat and hasattr(strat, 'stop_price') else None
                        # Capture entry-time indicator snapshot from the live
                        # DEMA-ATR engines so analytics records the context the
                        # signal fired under (entry_dema/atr/dema_atr/htf).
                        entry_dema = entry_atr = entry_dema_atr = None
                        entry_htf = None
                        if strat is not None:
                            fast_key = f"{fill.instrument}:{getattr(strat, 'fast_timeframe', '5m')}"
                            ind = self.indicators.get(fast_key)
                            if ind is not None:
                                entry_dema = ind.dema_value
                                entry_atr = ind.atr_value
                                entry_dema_atr = ind.value
                            entry_htf = getattr(strat, '_prev_htf_value', None)
                        self.trade_ledger.create_trade(
                            strategy_id=fill.strategy_id,
                            instrument=fill.instrument,
                            side=position.side.value,
                            entry_quantity=fill.quantity,
                            signal_time=fill.timestamp,
                            trigger_price=fill.price,
                            stop_price=stop_price or 0.0,
                            multiplier=multiplier,
                            trade_id=position.position_id,
                            position_id=position.position_id,
                            entry_dema=entry_dema,
                            entry_atr=entry_atr,
                            entry_dema_atr=entry_dema_atr,
                            entry_htf_value=entry_htf,
                        )
                        self.trade_ledger.record_fill(
                            trade_id=position.position_id,
                            fill_id=fill.fill_id,
                            order_id=fill.order_id,
                            side=fill.side,
                            quantity=fill.quantity,
                            price=fill.price,
                            timestamp=fill.timestamp,
                            is_entry=True,
                        )
                    except Exception as e:
                        # LOUD, not silent: the position is open in memory and
                        # persisted to trading.db regardless; if the analytics
                        # trade record fails here, the startup backfill
                        # (_backfill_ledger_for_open_positions) heals it on the
                        # next restart so analytics.db can never diverge silently.
                        print(f"[Fill] WARNING: analytics ledger create/leg failed for {position.position_id}: {e}", flush=True)
                # Telegram notification
                try:
                    strat_snap = self.strategies[fill.strategy_id].snapshot() if fill.strategy_id in self.strategies else {}
                    strat_acct_snap = self.account_engines[fill.strategy_id].snapshot() if fill.strategy_id in self.account_engines else {}
                    self.telegram.on_fill(
                        {"fill_id": fill.fill_id, "order_id": fill.order_id, "instrument": fill.instrument,
                         "side": fill.side, "price": fill.price, "quantity": fill.quantity, "strategy_id": fill.strategy_id,
                         "multiplier": multiplier, "stop_price": strat_snap.get("stop_price", 0)},
strat_snap, strat_acct_snap,
                    )
                except Exception:
                    pass
            else:
                print(f"[Fill] MARGIN BLOCKED: {fill.strategy_id} - no margin for {fill.instrument}", flush=True)
                # CRITICAL: Reset strategy state — _check_pending_entry already set
                # LONG_POSITION/SHORT_POSITION, but position was never opened.
                # Without this reset the strategy is stuck in a ghost position forever.
                strat = self.strategies.get(fill.strategy_id)
                if strat:
                    print(f"  Strategy state was: {strat.state}  position_side={strat.position_side}", flush=True)
                    strat.state = StrategyState.FLAT
                    strat.position_side = None
                    strat.stop_price = None
                    strat.pending_entry = None
                    print(f"  Strategy state RESET to FLAT (margin blocked)", flush=True)
                try:
                    self.telegram.on_risk_alert({
                        "type": "margin_blocked",
                        "severity": "WARNING",
                        "message": f"Margin BLOCKED for {fill.strategy_id} - no margin for {fill.instrument}",
                        "strategy_id": fill.strategy_id,
                        "instrument": fill.instrument,
                        "side": fill.side,
                        "price": fill.price,
                        "quantity": fill.quantity,
                    })
                except Exception:
                    pass
        else:
            # Exit — use atomic trade close manager
            position = open_pos[0]
            strategy_id = fill.strategy_id
            strat_account = self.account_engines.get(strategy_id)
            strat = self.strategies.get(strategy_id)
            exit_reason = getattr(strat, "last_exit_reason", None) or "signal_exit"
            if self._trade_close_manager:
                close_result = self._trade_close_manager.close_position(
                    fill=fill, position=position,
                    strategy_id=strategy_id, multiplier=multiplier,
                    exit_reason=exit_reason,
                    exit_signal_id=signal_id,
                )
                success = close_result is not False and close_result is not None
                # ── Persist trade-signal link (exit) ──
                if success and self._persistence and signal_id:
                    try:
                        self._persistence.save_trade_signal_link(
                            trade_id=position.position_id, signal_id=signal_id,
                            relationship_type="exit",
                        )
                    except Exception as e:
                        print(f"[Signal] WARNING: exit trade_signal_link save failed: {e}", flush=True)

                # ── Register exit fill in central lifecycle ──
                if success:
                    lifecycle_trade = self._lifecycle.get_trade(position.position_id)
                    if lifecycle_trade:
                        self._lifecycle.register_exit_fill(
                            trade_id=lifecycle_trade.trade_id,
                            fill_id=fill.fill_id,
                            price=fill.price,
                            timestamp=fill.timestamp or time.time(),
                            exit_signal_id=signal_id or "",
                            exit_type=lifecycle_trade.exit_type,
                            exit_reason=exit_reason,
                        )
                        # Close the trade in lifecycle with real P&L from TradeCloseManager
                        pnl_gross = close_result.get("gross_pnl", 0.0) if isinstance(close_result, dict) else 0.0
                        pnl_charges = close_result.get("charges", 0.0) if isinstance(close_result, dict) else 0.0
                        pnl_net = close_result.get("net_pnl", 0.0) if isinstance(close_result, dict) else 0.0
                        self._lifecycle.close_trade(
                            trade_id=lifecycle_trade.trade_id,
                            gross_pnl=pnl_gross,
                            charges=pnl_charges,
                            net_pnl=pnl_net,
                        )

                if not success:
                    print(f"[TradeClose] CRITICAL: Atomic close failed for {position.position_id}", flush=True)
                    self.safe_mode.enter_safe_mode("persistence_failure", f"Trade close persistence failed: {position.position_id}")
                    try:
                        self.telegram.on_error({
                            "component": "TradeCloseManager",
                            "message": f"CRITICAL: Atomic close FAILED for {position.position_id} ({fill.instrument} {strategy_id}). Entering SAFE MODE.",
                        })
                    except Exception:
                        pass
                else:
                    if strat is not None:
                        strat.last_exit_reason = None
                    # Stops and strategy state are cleared only after the exit
                    # is durably recorded and the in-memory position is closed.
                    if strat:
                        if strat.pending_entry is not None and getattr(strat.pending_entry, "immediate", False):
                            # Direct-market reversal: the opposite trade is
                            # placed immediately after the exit — buy/sell the
                            # new side at market, in the same bar.
                            pen = strat.pending_entry
                            strat.pending_entry = None
                            strat.position_side = pen.side
                            strat.stop_price = pen.signal.stop_price
                            strat.just_entered = True
                            strat.state = (StrategyState.LONG_POSITION if pen.side == "LONG"
                                           else StrategyState.SHORT_POSITION)
                            reentry = Signal(
                                signal_type=SignalType.LONG if pen.side == "LONG" else SignalType.SHORT,
                                instrument=fill.instrument,
                                strategy_id=strategy_id,
                                timestamp=fill.timestamp,
                                trigger_price=fill.price,
                                stop_price=pen.signal.stop_price,
                                quantity=pen.signal.quantity,
                                side=pen.side,
                                metadata={
                                    **dict(pen.signal.metadata or {}),
                                    "entry_price": fill.price,
                                    "executed": True,
                                    "market": True,
                                    "reversal_reentry": True,
                                    "placement_candle_start": fill.timestamp,
                                },
                            )
                            self._process_signal(reentry)
                        elif strat.pending_entry is not None:
                            strat.state = (StrategyState.PENDING_LONG
                                           if strat.pending_entry.side == "LONG"
                                           else StrategyState.PENDING_SHORT)
                            strat.position_side = None
                            strat.stop_price = None
                        else:
                            strat.state = StrategyState.FLAT
                            strat.position_side = None
                            strat.stop_price = None
            else:
                # Fallback: legacy non-atomic close (should not happen)
                print(f"[TradeClose] WARNING: TradeCloseManager not wired, using legacy close", flush=True)
                pnl_engine = self.pnl_engines.get(strategy_id)
                strat_account = self.account_engines.get(strategy_id)
                if pnl_engine:
                    entry_fill = Fill(
                        fill_id=position.entry_fill_ids[0] if position.entry_fill_ids else "",
                        order_id="", instrument=position.instrument,
                        side="BUY" if position.is_long else "SELL",
                        quantity=position.quantity, price=position.average_entry,
                        timestamp=position.entry_timestamp, strategy_id=position.strategy_id,
                        multiplier=multiplier,
                    )
                    gross_pnl, charges, net_pnl = pnl_engine.calculate_realized_pnl(
                        entry_fill=entry_fill, exit_fill=fill, multiplier=multiplier,
                    )
                    pnl_engine.record_trade(gross_pnl, charges, net_pnl)
                else:
                    gross_pnl, charges, net_pnl = 0.0, 0.0, 0.0
                self.position_manager.close_position(
                    position_id=position.position_id, fill=fill,
                    reason=position.exit_reason or exit_reason,
                    exit_signal_id=signal_id,
                )
                if strat_account:
                    strat_account.update_realized_pnl(net_pnl, charges)
                    strat_account.release_margin(position.margin)
                self.account_engine.update_realized_pnl(net_pnl, charges)
                self.account_engine.release_margin(position.margin)
                self.risk_engine.update_daily_pnl(net_pnl)
                print(f"[Trade] Closed (legacy): strategy={strategy_id} P&L={net_pnl:.2f}", flush=True)
                try:
                    entry_ts = position.entry_timestamp if hasattr(position, 'entry_timestamp') else 0
                    duration_s = fill.timestamp - entry_ts if entry_ts else 0
                    hrs, rem = divmod(int(duration_s), 3600)
                    mins = rem // 60
                    duration_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
                    self.telegram.on_trade_close({
                        "instrument": fill.instrument,
                        "strategy_id": strategy_id,
                        "side": position.side.value,
                        "entry_price": position.average_entry,
                        "exit_price": fill.price,
                        "net_pnl": net_pnl,
                        "exit_reason": position.exit_reason or exit_reason,
                        "duration": duration_str,
                    })
                except Exception:
                    pass

            # Reversal entries remain pending after the exit.  They will go
            # through the ordinary signal/order/fill path only when their
            # breakout trigger is reached, preserving a complete audit trail.

            # Durable dedup mark AFTER all financial effects are applied —
            # SQLite is the single written-to-DB source of truth, so a crash
            # between save_fill / save_trade_and_fill and this mark is
            # recovered by the get_fill() idempotency guard above.
            self.fill_dedup.mark_processed(fill.fill_id)

    def _on_status(self, status: str) -> None:
        """Handle data adapter status change."""
        print(f"[Data] Status: {status}", flush=True)

    def _fetch_history_with_session_guarantee(self, name, from_date, to_date, last_days, max_fetch_days, extend_step_days):
        """Fetch a FRESH 5m REST series, extending the window backward until the
        configured number of actual trading sessions is guaranteed.

        Gap closed by Phase-1 remediation (Part 2): a fixed 14-calendar-day
        window is not guaranteed to contain 5 real MCX sessions — weekend +
        multi-day holiday clusters can leave fewer trading dates, which the old
        code silently trimmed down to.  The window is extended in
        extend_step_days backward steps up to max_fetch_days calendar days until
        the fetched data contains >= last_days distinct trading dates.

        Returns (candles, final_from_date, extension_count).  Candles are always
        fresh Dhan REST data — never a cache (SOURCE=DHAN_REST).
        """
        import datetime as _dt
        import pandas as _pd

        def _trading_dates(rows):
            if not rows:
                return []
            d = _pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
            d["datetime"] = _pd.to_datetime(d["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            return d["datetime"].dt.date.unique().tolist()

        current_from = from_date
        candles = self.data_adapter.fetch_historical_candles(name, "5", current_from, to_date)
        extensions = 0

        if last_days > 0:
            dates = sorted(_trading_dates(candles))
            span = (to_date - current_from).days
            while len(dates) < last_days and span < max_fetch_days:
                extensions += 1
                current_from = to_date - _dt.timedelta(days=min(span + extend_step_days, max_fetch_days))
                span = (to_date - current_from).days
                candles = self.data_adapter.fetch_historical_candles(name, "5", current_from, to_date)
                dates = sorted(_trading_dates(candles))
            if candles and len(dates) < last_days:
                print(f"[Backfill] {name}: WARNING max_fetch_days cap ({max_fetch_days}) reached — "
                      f"only {len(dates)} trading dates (< {last_days}); using all available", flush=True)

        return candles, current_from, extensions

    def _fetch_native_htf_bars(self, name: str, timeframe: str, from_date, to_date):
        """Fetch NATIVE higher-TF candles directly from Dhan (no resampling).

        Mirrors the backtest native path (fetch_native_htf.py): Dhan returns
        native 15m/60m candles at their real exchange interval.  Candle
        timestamps are epoch seconds (absolute instant, timezone-independent),
        so each bar's start_ts is used directly and end_ts = start + tf*60 —
        exactly how the backtest's native_map_htf consumes native bars.

        Returns a list of CLOSED Bar objects (native 15m/60m), or [] on error.
        """
        from core.timeframe_engine import BarState
        try:
            candles = self.data_adapter.fetch_historical_candles(
                name, timeframe, from_date, to_date,
            )
        except Exception as e:
            print(f"[Backfill] {name} native {timeframe}: fetch error - {e}", flush=True)
            return []
        if not candles:
            return []

        tf_minutes = {"15m": 15, "1h": 60}.get(timeframe, int(timeframe))
        bars = []
        dropped = 0
        for candle in candles:
            try:
                ts, open_p, high, low, close, volume = candle
                start_ts = float(ts)
                if start_ts <= 0:
                    dropped += 1
                    continue
                bars.append(Bar(
                    instrument=name, timeframe="1h" if tf_minutes == 60 else "15m",
                    start_ts=start_ts, end_ts=start_ts + tf_minutes * 60,
                    open=float(open_p), high=float(high), low=float(low),
                    close=float(close), volume=int(volume or 0),
                    state=BarState.CLOSED,
                ))
            except Exception:
                dropped += 1
        bars.sort(key=lambda b: b.start_ts)
        if dropped:
            print(f"[Backfill] {name} native {timeframe}: dropped {dropped} malformed rows", flush=True)
        return bars

    def _warmup_from_rest(self) -> None:
        """Startup backfill: fetch 5m candles via REST, resample to 1H/15m, pre-populate HTF engine AND indicators.

        Every startup fetches previous day+ data to warm up indicators.
        Eliminates the cold-start penalty — indicators ready from first live tick.
        The fetch window is guaranteed to contain at least `last_trading_days`
        actual trading sessions (see _fetch_history_with_session_guarantee).
        """
        import datetime as _dt
        print("[Engine] Starting backfill from REST API...", flush=True)

        warmup_cfg = self.config.get("warmup", {})
        # Backtest-aligned warmup (option 2):
        #   last_trading_days  0 = no date filter (use the raw fetch window);
        #                      N = seed from the last N distinct trading dates
        #                          (identical to the backtest LAST5 window).
        #   keep_partial       True = keep KEEP-ALL buckets incl. the partial
        #                      23:00 1H slot, so the warm line matches the
        #                      backtest 1H resample exactly (D2 off).
        #   max_fetch_calendar_days / fetch_extend_step_days bound the window
        #   extension used to guarantee `last_trading_days` sessions exist.
        # DEMA-ATR needs 6+ bars to initialize; the fetch margin below must
        # always cover last_trading_days trading dates across weekends/holidays.
        last_days = int(warmup_cfg.get("last_trading_days", 0))
        keep_partial = bool(warmup_cfg.get("keep_partial", False))
        fetch_days = int(warmup_cfg.get("fetch_calendar_days", 7))
        max_fetch_days = int(warmup_cfg.get("max_fetch_calendar_days", 62))
        extend_step_days = int(warmup_cfg.get("fetch_extend_step_days", 7))

        now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
        base_from_date = (now - _dt.timedelta(days=fetch_days)).date()
        to_date = now.date()

        instruments = self.config.get("instruments", {})
        for name, cfg in instruments.items():
            try:
                # Fetch 5m candles via REST, guaranteeing the session count
                candles, fetch_from, extensions = self._fetch_history_with_session_guarantee(
                    name, base_from_date, to_date, last_days, max_fetch_days, extend_step_days,
                )
                if not candles:
                    print(f"[Backfill] {name}: no REST data in {fetch_from}..{to_date}, skipping", flush=True)
                    continue
                print(f"[Backfill] {name}: SOURCE=DHAN_REST range {fetch_from}..{to_date} "
                      f"({'extended x'+str(extensions) if extensions else 'clean window'})", flush=True)

                # Single authoritative source for candle-derived state: this fresh REST
                # series.  Indicator/HTF state is never restored from the session
                # DB (see restore()); resetting here also guards against a
                # re-warm (double feed) if start() is invoked twice.
                for key in (f"{name}:5m", f"{name}:15m", f"{name}:1h"):
                    ind = self.indicators.get(key)
                    if ind is not None:
                        ind.reset()
                self.htf_engine.reset_instrument(name)

                # Convert to DataFrame for resampling
                import pandas as pd
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                df = df.sort_values("datetime").reset_index(drop=True)
                if last_days > 0:
                    dates_sorted = sorted(df["datetime"].dt.date.unique())
                    keep_dates = set(dates_sorted[-last_days:])
                    df = df[df["datetime"].dt.date.isin(keep_dates)].reset_index(drop=True)
                    print(f"[Backfill] {name}: trimmed to last {last_days} trading dates {sorted(keep_dates)}", flush=True)
                print(f"[Backfill] {name}: {len(df)} 5m candles fetched ({df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]})", flush=True)

                session_open = cfg.get("session_open", "09:00")

                # --- Warm up 5m indicator from raw 5m candles ---
                key_5m = f"{name}:5m"
                if key_5m in self.indicators:
                    ind_5m = self.indicators[key_5m]
                    for _, row in df.iterrows():
                        ind_5m.update(
                            float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"]),
                        )
                    print(f"[Backfill] {name} 5m indicator: {ind_5m._count} bars, initialized={ind_5m.initialized}", flush=True)

                # --- Native higher-timeframe warmup (no resampling) ---
                # Fetch native 15m/60m candles directly from Dhan and compute
                # DEMA-ATR on those bars, mapping onto the 5m base exactly like
                # the backtest (dema_mtf.native_htf_dema_lines). This is the
                # primary path; the 5m->HTF resample below is the fallback when
                # native HTF candles are unavailable (e.g. offline replay).
                native_bars: dict[str, list] = {}
                for tf_id, tf_name in [("60", "1h"), ("15", "15m")]:
                    nb = self._fetch_native_htf_bars(name, tf_id, fetch_from, to_date)
                    if nb:
                        native_bars[tf_name] = nb
                        print(f"[Backfill] {name} native {tf_name}: {len(nb)} bars "
                              f"({datetime.fromtimestamp(nb[0].start_ts, timezone(timedelta(hours=5, minutes=30))):%Y-%m-%d %H:%M} "
                              f"-> {datetime.fromtimestamp(nb[-1].start_ts, timezone(timedelta(hours=5, minutes=30))):%Y-%m-%d %H:%M})",
                              flush=True)
                    else:
                        print(f"[Backfill] {name} native {tf_name}: none fetched, will resample from 5m", flush=True)

                use_native = bool(native_bars.get("1h") and native_bars.get("15m"))

                if use_native:
                    # Feed native HTF bars to the HTF engine + indicators.
                    # DEMA-ATR warms on native bars identically to the backtest.
                    for tf_name in ("15m", "1h"):
                        bars = native_bars[tf_name]
                        self.htf_engine.load_batch_htf(name, tf_name, bars)
                        key_tf = f"{name}:{tf_name}"
                        ind_tf = self.indicators.get(key_tf)
                        if ind_tf is not None:
                            for bar in bars:
                                ind_tf.update(bar.open, bar.high, bar.low, bar.close)
                            print(f"[Backfill] {name} native {tf_name} indicator: "
                                  f"{ind_tf._count} bars, initialized={ind_tf.initialized}", flush=True)
                else:
                    # --- Resample 5m to 1H and 15m (fallback) ---
                    from core.timeframe_engine import Bar, BarState
                    for tf, tf_minutes in [("1h", 60), ("15m", 15)]:
                        d = df.copy()
                        dt = d["datetime"]
                        dates = dt.dt.date.astype(str)
                        session_start = pd.to_datetime(dates + f" {session_open}")
                        mins = ((dt - session_start).dt.total_seconds() // 60).astype(int)
                        d["_bucket"] = session_start + pd.to_timedelta((mins // tf_minutes) * tf_minutes, unit="m")
                        # Phase-1 hardening (Parts 7/30): rows before the session
                        # anchor (mins < 0, e.g. a 00:10 next-day print against a
                        # 09:00 anchor) must never form buckets.  A calendar-date
                        # change must not create a new session nor merge across one.
                        d = d[mins >= 0]
                        if not keep_partial:
                            # ONLY complete aggregation windows — identical to
                            # CandleFetcher._fetch_candle (expected_count = tf_minutes//5).
                            # Partial end-of-session buckets (e.g. the 23:00 1H slot after
                            # a 23:30 MCX close) must NOT enter the HTF engine, else the
                            # backfilled DEMA-ATR line diverges from the backtest/live rule.
                            d = d[d.groupby("_bucket")["datetime"].transform("size") == tf_minutes // 5]
                        htf = d.groupby("_bucket", sort=True).agg({
                            "open": "first", "high": "max", "low": "min",
                            "close": "last", "volume": "sum",
                        }).reset_index().rename(columns={"_bucket": "datetime"})

                        # Convert to Bar objects and feed to HTF engine
                        bars = []
                        for _, row in htf.iterrows():
                            bar_dt = row["datetime"]
                            if bar_dt.tzinfo is None:
                                bar_dt = bar_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
                            start_ts = bar_dt.timestamp()
                            bar = Bar(
                                instrument=name, timeframe=tf,
                                start_ts=start_ts, end_ts=start_ts + tf_minutes * 60,
                                open=row["open"], high=row["high"], low=row["low"],
                                close=row["close"], volume=int(row["volume"]),
                                state=BarState.CLOSED,
                            )
                            bars.append(bar)

                        # Load HTF engine from backfill bars
                        self.htf_engine.load_batch_htf(name, tf, bars)

                        # --- Warm up indicator for this timeframe ---
                        key_tf = f"{name}:{tf}"
                        if key_tf in self.indicators:
                            ind_tf = self.indicators[key_tf]
                            for bar in bars:
                                ind_tf.update(bar.open, bar.high, bar.low, bar.close)
                            print(f"[Backfill] {name} {tf} indicator: {ind_tf._count} bars, initialized={ind_tf.initialized}", flush=True)

                        print(f"[Backfill] {name} {tf}: {len(bars)} bars loaded", flush=True)

                # --- Phase-1 diagnostics: session + readiness summary (Part 41/42) ---
                _sessions = sorted(set(df["datetime"].dt.date)) if len(df) else []
                _line = f"[Backfill] {name}: SESSIONS={len(_sessions)} " + \
                        (f"({_sessions[0]}..{_sessions[-1]}) " if _sessions else "") + \
                        f"LTF_5M={len(df)}"
                for _tf in ("15m", "1h"):
                    _eng = self.htf_engine._engines.get(f"{name}:{_tf}")
                    _cnt = len(_eng.end_times) if _eng else 0
                    _ind = self.indicators.get(f"{name}:{_tf}")
                    _dema = bool(_ind and _ind.dema_value is not None)
                    _atr = bool(_ind and _ind.atr_value is not None)
                    _line += f" | {_tf.upper()}={_cnt} DEMA={'Y' if _dema else 'N'} ATR={'Y' if _atr else 'N'}"
                _mapping_ready = all(
                    bool(self.htf_engine._engines.get(f"{name}:{_tf}"))
                    for _tf in ("15m", "1h")
                )
                _line += f" | MAPPING={'READY' if _mapping_ready else 'NOT_READY'}"
                _line += f" | STRATEGIES={sum(1 for s in self.strategies.values() if s.instrument == name)}"
                _line += f" | SOURCE={'DHAN_NATIVE' if use_native else 'DHAN_REST_RESAMPLED'}"
                print(_line, flush=True)

            except Exception as e:
                print(f"[Backfill] {name}: error - {e}", flush=True)

        print("[Engine] Backfill complete", flush=True)

    def snapshot(self) -> dict:
        """Get complete system state for persistence.

        Separates LIVE STATE (restorable) from HISTORICAL STATE (in DB only).
        Thread-safe: acquires lock to prevent inconsistent snapshots during mutations.
        """
        with self._lock:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                # Live state (restorable on restart)
                "market_status": self.market_status.snapshot(),
                "strategies": {
                    name: strat.snapshot()
                    for name, strat in self.strategies.items()
                },
                "positions": self.position_manager.snapshot(),
                "account": self.account_engine.snapshot(),
                "accounts_by_strategy": {
                    name: eng.snapshot()
                    for name, eng in self.account_engines.items()
                },
                "pnl": {
                    name: eng.snapshot()
                    for name, eng in self.pnl_engines.items()
                },
                "risk": self.risk_engine.snapshot(),
                "execution": self.execution_engine.snapshot(),
                # Central trade lifecycle — single source of truth for all trade identity
                "lifecycle": self._lifecycle.snapshot(),
                # NOTE: indicator & HTF (candle-derived) state is intentionally
                # NOT persisted — it is always recomputed from a fresh Dhan REST
                # series at startup (_warmup_from_rest).
                "health": self.health.snapshot(),
                # Historical state reference (authoritative source is trades DB)
                "historical_source": "trading.db",
            }

    def restore(self, state: dict) -> None:
        """Restore system state from persistence.

        Restore order: market status → strategies → positions → accounts → PnL → risk → execution
        NOTE: starting_capital is NOT restored from saved state - it always comes from config.
        NOTE: indicator/HTF (candle) state is NEVER restored from persistence —
        it is recomputed from a fresh Dhan REST series at startup (_warmup_from_rest).
        """
        with self._lock:
            self.market_status.restore(state.get("market_status", {}))
            for name, strat_state in state.get("strategies", {}).items():
                if name in self.strategies:
                    self.strategies[name].restore(strat_state)
            self.position_manager.restore(state.get("positions", {}))
            # Restore global account (P&L, charges, etc.) but NOT starting_capital
            acct_state = state.get("account", {})
            self.account_engine.realized_pnl = acct_state.get("realized_pnl", 0.0)
            self.account_engine.unrealized_pnl = acct_state.get("unrealized_pnl", 0.0)
            self.account_engine.charges = acct_state.get("charges", 0.0)
            self.account_engine.used_margin = acct_state.get("used_margin", 0.0)
            for name, acct_state in state.get("accounts_by_strategy", {}).items():
                if name in self.account_engines:
                    self.account_engines[name].restore(acct_state)
            for name, pnl_state in state.get("pnl", {}).items():
                if name in self.pnl_engines:
                    self.pnl_engines[name].restore(pnl_state)
            self.risk_engine.restore(state.get("risk", {}))
            self.execution_engine.restore(state.get("execution", {}))
            # Recalculate global account starting_capital from per-strategy accounts
            total = sum(a.starting_capital for a in self.account_engines.values())
            self.account_engine.starting_capital = total
            # Restore cash: match AccountEngine.restore() semantics.  Cash
            # follows starting_capital + cumulative realised P&L (the value
            # persisted in the state file; if missing, recompute).
            acct_cash = acct_state.get("cash")
            if acct_cash is not None:
                self.account_engine.cash = acct_cash
            else:
                self.account_engine.cash = (
                    self.account_engine.starting_capital
                    + self.account_engine.realized_pnl
                )
            # Restore central trade lifecycle from DB — rebuilds all identity maps
            self._lifecycle.restore_from_db()
            # Heal positions whose trades are already closed in DB.  This
            # catches the crash-desync case where _on_fill persisted the trade
            # close to DB but position_manager.close_position() failed silently
            # (exception caught by TradeCloseManager) — the position is still
            # open in memory while the DB says closed.
            self._heal_closed_trades()
            # Reconcile strategy state with position manager: if a strategy
            # was saved as flat but its position is still open (desync from
            # a crash/snapshot-race), restore the strategy to match.
            self._reconcile_strategy_positions()
            # Reconcile the analytics trade ledger against restored open
            # positions so a carried-over / crash-restored open position can
            # NEVER be missing from analytics.db (BUG-1 fix: previously the
            # ledger/event writes only ran at fresh-open time, so a position
            # restored from state had no trades_analytics row / entry leg).
            self._backfill_ledger_for_open_positions()

    def _heal_closed_trades(self) -> None:
        """Close positions whose trades are already closed in DB.

        Catches the crash-desync: _on_fill persisted the trade close to DB
        but position_manager.close_position() failed silently (exception
        caught by TradeCloseManager).  The position is still open in memory
        while the DB says closed.  This method heals the desync by closing
        the position in memory using the DB trade data.
        """
        if not self._persistence:
            print("[Heal] Skipped: no persistence manager", flush=True)
            return
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{self._persistence.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT trade_id, strategy_id, instrument, exit_price, "
                    "exit_reason, net_pnl, charges, multiplier "
                    "FROM trades WHERE status = 'closed'"
                ).fetchall()
                closed_trades = {r["trade_id"]: dict(r) for r in rows}
            finally:
                conn.close()
        except Exception as e:
            print(f"[Heal] WARNING: could not read closed trades from DB: {e}", flush=True)
            return

        open_positions = list(self.position_manager.open_positions)
        open_ids = {p.position_id for p in open_positions}
        print(f"[Heal] DB closed_trades={len(closed_trades)}, "
              f"open_positions={len(open_positions)}", flush=True)
        if closed_trades:
            print(f"[Heal] DB closed trade_ids: {list(closed_trades.keys())}", flush=True)
        if open_ids:
            print(f"[Heal] Memory position_ids: {list(open_ids)}", flush=True)

        healed = 0
        for pos in open_positions:
            trade_id = pos.position_id
            if trade_id not in closed_trades:
                continue
            trade = closed_trades[trade_id]
            print(f"[Heal] Healing position {trade_id} ({trade['strategy_id']} "
                  f"{trade['instrument']}): trade is closed in DB but position "
                  f"still open in memory", flush=True)

            strategy_id = trade["strategy_id"]
            multiplier = trade.get("multiplier", 1.0)
            net_pnl = trade.get("net_pnl", 0.0) or 0.0
            charges = trade.get("charges", 0.0) or 0.0
            exit_price = trade.get("exit_price", 0.0) or 0.0
            exit_reason = trade.get("exit_reason", "healed_closed_trade")

            # Create a synthetic exit fill for position_manager.close_position()
            exit_fill = Fill(
                fill_id=f"heal-{trade_id}",
                order_id="",
                instrument=pos.instrument,
                side="SELL" if pos.is_long else "BUY",
                quantity=pos.quantity,
                price=exit_price,
                timestamp=time.time(),
                strategy_id=strategy_id,
                multiplier=multiplier,
            )

            # Close position in memory
            try:
                self.position_manager.close_position(
                    position_id=trade_id,
                    fill=exit_fill,
                    reason=exit_reason,
                )
            except Exception as e:
                print(f"[Heal] CRITICAL: close_position failed for {trade_id}: {e}", flush=True)
                print(f"[Heal]   pos_keys_in_dict: {list(self.position_manager._positions.keys())[:5]}", flush=True)
                continue

            # Release margin
            strat_account = self.account_engines.get(strategy_id)
            if strat_account:
                strat_account.release_margin(pos.margin)
            if self.account_engine:
                self.account_engine.release_margin(pos.margin)

            # NOTE: Do NOT record P&L here — the P&L was already recorded
            # during the original session and persisted in system_state.json.
            # The heal only needs to close the position in memory, release
            # margin, and fix strategy state.  Recording P&L again would
            # double-count (visible as PNLEngine.realized_net = 2x DB sum
            # and trade_count = 2x DB count in reconciliation).

            # Fix strategy state: set to FLAT (pending_entry will be re-armed
            # by _reconcile_strategy_positions if a SHORT signal was detected)
            strat = self.strategies.get(strategy_id)
            if strat:
                strat.state = StrategyState.FLAT
                strat.position_side = None
                strat.stop_price = None
                strat.last_exit_reason = exit_reason

            print(f"[Heal] CLOSED position {trade_id}: {strategy_id} P&L={net_pnl:.2f}", flush=True)
            healed += 1

        # Recalculate used_margin from actual open positions to fix
        # the desync where state file saved used_margin=0 but positions
        # still have margin allocated.
        for strat_id, account in self.account_engines.items():
            used_margin = 0.0
            for p in self.position_manager.open_positions:
                if p.strategy_id == strat_id:
                    used_margin += p.margin
            account.used_margin = used_margin
        if self.account_engine:
            self.account_engine.used_margin = sum(
                p.margin for p in self.position_manager.open_positions
            )

        if healed:
            print(f"[Heal] Healed {healed} position(s) from DB closed trades", flush=True)
        else:
            print(f"[Heal] No positions need healing", flush=True)

    def _reconcile_strategy_positions(self) -> None:
        """Sync strategy state with position manager after restore.

        If a strategy was persisted as flat but the position manager has an
        open position for it (desync from crash/snapshot-race), restore the
        strategy to the correct state so the position is not orphaned.
        """
        from strategies.types import StrategyState
        for strat in self.strategies.values():
            if strat.state == StrategyState.FLAT:
                positions = self.position_manager.get_positions_by_strategy(strat.strategy_id)
                open_pos = [p for p in positions if p.is_open]
                if open_pos:
                    pos = open_pos[0]
                    strat.position_side = "LONG" if pos.side.value == "LONG" else "SHORT"
                    strat.stop_price = pos.stop_price
                    strat.state = (
                        StrategyState.LONG_POSITION if pos.side.value == "LONG"
                        else StrategyState.SHORT_POSITION
                    )
                    print(f"[Restore] RECONCILE: {strat.strategy_id} was FLAT but has open "
                          f"{pos.side.value} position @ {pos.average_entry} — "
                          f"restored to {strat.state.value}, stop={strat.stop_price}",
                          flush=True)

    def _backfill_ledger_for_open_positions(self) -> None:
        """Ensure every restored/open position has an analytics trade record.

        Called after position restore (and safe to call any time open
        positions exist).  For each open position lacking a trades_analytics
        row it creates the trade (status=OPEN), the entry leg, and the
        POSITION_OPENED event so analytics.db can never silently diverge from
        trading.db / in-memory state.  Failures are LOUD (logged), never
        silently swallowed.
        """
        if self.trade_ledger is None:
            print("[Engine] Ledger backfill skipped: trade_ledger is None", flush=True)
            return
        for pos in self.position_manager.open_positions:
            trade_id = pos.position_id
            fill_id = pos.entry_fill_ids[0] if pos.entry_fill_ids else None
            try:
                existing = self.trade_ledger.get_trade(trade_id)
            except Exception:
                existing = None
            if existing is not None:
                # Trade ALREADY present in analytics.  Guard against the
                # partial-state case (Defect 6): the create_trade row existed
                # but the entry leg write failed, leaving an OPEN trade with
                # zero fills.  Heal by adding the missing entry leg only.
                try:
                    legs = self.trade_ledger.get_legs_for_trade(trade_id)
                    entry_leg_present = any(
                        leg.fill_id == fill_id and getattr(leg, "is_entry", True)
                        for leg in legs
                    ) if legs else False
                except Exception:
                    entry_leg_present = False
                if not entry_leg_present and fill_id:
                    try:
                        order_id = ""
                        entry_price = pos.average_entry
                        if self._persistence:
                            try:
                                f = self._persistence.get_fill(fill_id)
                                if f:
                                    order_id = f.get("order_id") or ""
                                    entry_price = f.get("price") or pos.average_entry
                            except Exception:
                                pass
                        self.trade_ledger.record_fill(
                            trade_id=pos.position_id,
                            fill_id=fill_id,
                            order_id=order_id,
                            side="BUY" if pos.is_long else "SELL",
                            quantity=pos.quantity,
                            price=entry_price,
                            timestamp=pos.entry_timestamp,
                            is_entry=True,
                        )
                        print(f"[Engine] Backfilled missing entry leg for {pos.position_id}", flush=True)
                    except Exception as e:
                        print(f"[Engine] WARNING: entry-leg heal failed for {pos.position_id}: {e}", flush=True)
                continue
            side = "LONG" if pos.is_long else "SHORT"
            order_id = ""
            entry_price = pos.average_entry
            if fill_id and self._persistence:
                try:
                    f = self._persistence.get_fill(fill_id)
                    if f:
                        order_id = f.get("order_id") or ""
                        entry_price = f.get("price") or pos.average_entry
                except Exception:
                    pass
            try:
                self.trade_ledger.create_trade(
                    strategy_id=pos.strategy_id,
                    instrument=pos.instrument,
                    side=side,
                    entry_quantity=pos.quantity,
                    signal_time=pos.entry_timestamp,
                    trigger_price=pos.average_entry,
                    stop_price=pos.stop_price or 0.0,
                    multiplier=pos.multiplier,
                    trade_id=pos.position_id,
                    position_id=pos.position_id,
                    entry_dema=None,
                    entry_atr=None,
                    entry_dema_atr=None,
                    entry_htf_value=None,
                )
                if fill_id:
                    self.trade_ledger.record_fill(
                        trade_id=pos.position_id,
                        fill_id=fill_id,
                        order_id=order_id,
                        side="BUY" if pos.is_long else "SELL",
                        quantity=pos.quantity,
                        price=entry_price,
                        timestamp=pos.entry_timestamp,
                        is_entry=True,
                    )
                if self.event_store:
                    self.event_store.record(
                        trade_id=pos.position_id,
                        strategy_id=pos.strategy_id,
                        instrument=pos.instrument,
                        event_type="POSITION_OPENED",
                        payload={"side": side, "price": entry_price,
                                 "quantity": pos.quantity, "restored": True},
                    )
                print(f"[Engine] Backfilled ledger for restored open position {pos.position_id}", flush=True)
            except Exception as e:
                print(f"[Engine] WARNING: ledger backfill failed for {pos.position_id}: {e}", flush=True)
