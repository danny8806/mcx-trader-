"""Main trading engine - orchestrates all components."""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from config import Config
from data.dhan import DhanDataAdapter
from core.timeframe_engine import Bar
from core.risk_engine import RiskEngine
from core.market_status import MarketStatus, MarketState, EngineStatus, DataStatus
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
from monitoring.health import HealthMonitor
from notifications.telegram_router import TelegramRouter
from analytics.event_store import EventStore
from analytics.trade_ledger import TradeLedger


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

        # Analytics event store (writes to analytics.db)
        analytics_db = str(Path(self.config.get("system", {}).get("db_path", "trading.db")).parent / "analytics.db")
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
        db_path = str(self.config.get("system", {}).get("db_path", "trading.db"))
        self.fill_dedup = FillDeduplicator(db_path=db_path)

        # Safe mode manager
        self.safe_mode = SafeModeManager(self.market_status)

        # Atomic trade close manager (wired after portfolio init)
        self._trade_close_manager = None  # wired in start()

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._persistence = None

    def set_persistence(self, persistence) -> None:
        """Set persistence manager for trade logging."""
        self._persistence = persistence

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
            partial_fill_probability=paper_config.get("partial_fill_probability", 0.1),
        )
        self.order_manager = OrderManager(
            execution_engine=self.execution_engine,
            on_fill=self._on_fill,
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
        self.data_adapter.disconnect()
        # Stop candle fetcher
        self.candle_fetcher.stop()
        # Stop Telegram
        try:
            self.telegram.stop()
        except Exception:
            pass
        print("[Engine] Stopped", flush=True)

    def _on_tick(self, tick: dict[str, Any]) -> None:
        """Handle incoming tick from WebSocket.
        
        WebSocket is ONLY for LTP (live price). Candles come from REST API.
        """
        if not self._running:
            return
        instrument = tick.get("instrument")
        ltp = tick.get("ltp", 0.0)
        timestamp = tick.get("event_timestamp") or tick.get("timestamp", time.time())

        # Update market data status
        ws_connected = self.data_adapter.ws.connected if self.data_adapter.ws else False
        self.market_status.update_data_status(
            connected=ws_connected,
            last_tick_time=self.data_adapter.ws._last_tick_time if self.data_adapter.ws else 0.0,
        )

        # Stale connection check
        if self.data_adapter.ws.is_stale():
            print("[Engine] WARNING: WebSocket stale - no ticks received recently", flush=True)
            if self.market_status.is_trading_allowed:
                self.safe_mode.enter_safe_mode("market_data_uncertain", "WebSocket stale during trading hours")

        self.health.record_tick()

        # Check for EOD force-close
        if self.market_status.should_force_close:
            self._execute_eod_close()

        # Transition engine to TRADING if market is open and data is flowing
        if (self.market_status.state == MarketState.LIVE_TRADING
                and self.market_status.data_status == DataStatus.CONNECTED
                and self.market_status.engine_status == EngineStatus.READY):
            self.market_status.set_engine_status(EngineStatus.TRADING)

        with self._lock:
            # [1] Update execution engine price (for order fills)
            self.execution_engine.update_price(instrument, ltp)

            # [2] Tick-level P&L marking for open positions
            open_positions = self.position_manager.get_positions_by_instrument(instrument)
            for pos in open_positions:
                if pos.is_open:
                    pos.update_mark(ltp)
            # Update per-strategy accounts
            for strat_id in self.account_engines:
                strat_positions = self.position_manager.get_positions_by_strategy(strat_id)
                strat_unrealized = sum(p.unrealized_pnl for p in strat_positions if p.is_open and p.instrument == instrument)
                self.account_engines[strat_id].update_unrealized_pnl(strat_unrealized)
            # Update global account
            all_unrealized = sum(p.unrealized_pnl for p in self.position_manager.open_positions)
            self.account_engine.update_unrealized_pnl(all_unrealized)

            # [3] Update peak equity for drawdown tracking
            current_equity = self.account_engine.equity if hasattr(self.account_engine, 'equity') else None
            if current_equity is not None:
                self.risk_engine.update_peak_equity(current_equity)

            # [4] Process pending entry triggers (check if price hit trigger)
            for strat in self.strategies.values():
                if strat.instrument == instrument and strat.pending_entry:
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

                # Process bar with HTF value
                signal = strat.on_bar(bar, htf_mapped, fast_indicator.value, mid_mapped)
                if signal:
                    self._process_signal(signal)

    def _execute_eod_close(self) -> None:
        """Force-close all open positions at market close (EOD)."""
        if self.market_status._eod_close_done_today:
            return
        open_positions = list(self.position_manager.open_positions)
        if not open_positions:
            self.market_status.mark_eod_close_done()
            return
        print(f"[Engine] EOD CLOSE: {len(open_positions)} open positions", flush=True)
        for pos in open_positions:
            try:
                ltp = self.execution_engine._current_prices.get(pos.instrument, pos.average_entry)
                exit_fill = Fill(
                    fill_id=f"eod_{pos.position_id}_{int(time.time())}",
                    order_id="",
                    instrument=pos.instrument,
                    side="SELL" if pos.is_long else "BUY",
                    quantity=pos.quantity,
                    price=ltp,
                    timestamp=time.time(),
                    strategy_id=pos.strategy_id,
                    multiplier=pos.multiplier if hasattr(pos, 'multiplier') else 10.0,
                )
                if self._trade_close_manager:
                    self._trade_close_manager.close_position(
                        fill=exit_fill, position=pos,
                        strategy_id=pos.strategy_id, multiplier=exit_fill.multiplier,
                    )
                # Reset strategy state
                strat = self.strategies.get(pos.strategy_id)
                if strat:
                    strat.state = StrategyState.FLAT
                    strat.position_side = None
                    strat.stop_price = None
                    strat.pending_entry = None
                print(f"[Engine] EOD closed: {pos.instrument} {pos.side.value} @ {ltp}", flush=True)
            except Exception as e:
                print(f"[Engine] EOD close failed for {pos.position_id}: {e}", flush=True)
        self.market_status.mark_eod_close_done()
        try:
            acct_snap = self.account_engine.snapshot()
            risk_snap = self.risk_engine.snapshot()
            pnl_snap = self.account_engine.get_pnl_summary() if hasattr(self.account_engine, 'get_pnl_summary') else {}
            self.telegram.send_daily_summary(acct_snap, pnl_snap, risk_snap)
        except Exception:
            self.telegram.send_sync(f"[EOD] Force-closed {len(open_positions)} positions at market close")

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
        # Safe mode check — block new trades if unsafe
        if self.safe_mode.is_active:
            print(f"[Signal] BLOCKED by safe mode: {signal.strategy_id} {signal.signal_type}", flush=True)
            return
        # Market state check — block new trades when market is closed
        if not self.market_status.is_trading_allowed:
            print(f"[Signal] BLOCKED by market state ({self.market_status.state.value}): {signal.strategy_id} {signal.signal_type}", flush=True)
            return
        self.health.record_signal()
        # Risk check using per-strategy account
        instrument_config = self.config.instrument(signal.instrument)
        multiplier = instrument_config.get("multiplier", 1.0)
        strat_account = self.account_engines.get(signal.strategy_id)
        if strat_account is None:
            print(f"[Risk] No account engine for strategy {signal.strategy_id}", flush=True)
            return
        margin_required = self._calculate_margin(
            signal.instrument, signal.trigger_price, signal.quantity,
            side="BUY" if signal.signal_type in (SignalType.LONG, SignalType.REVERSAL) else "SELL",
        )

        allowed, reason = self.risk_engine.check_order(
            signal=signal,
            current_positions=len(self.position_manager.open_positions),
            strategy_positions=len(self.position_manager.get_positions_by_strategy(signal.strategy_id)),
            available_margin=strat_account.available_margin,
            margin_required=margin_required,
            current_equity=strat_account.equity,
        )

        if not allowed:
            # If this is an EXIT signal, allow it regardless of position limits
            is_exit = signal.metadata.get("exit", False) if hasattr(signal, 'metadata') and signal.metadata else False
            if is_exit:
                print(f"[Risk] Exit signal allowed despite: {reason} (strategy={signal.strategy_id})", flush=True)
                allowed = True
                reason = None
            else:
                print(f"[Risk] Order rejected: {reason}", flush=True)
                print(f"  Strategy: {signal.strategy_id}  Instrument: {signal.instrument}  Side: {signal.signal_type}  Price: {signal.trigger_price}", flush=True)
                print(f"  Positions for strategy: {len(self.position_manager.get_positions_by_strategy(signal.strategy_id))}  Total open: {len(self.position_manager.open_positions)}", flush=True)

                # Reset strategy state so it doesn't get stuck
                strat = self.strategies.get(signal.strategy_id)
                if strat:
                    print(f"  Strategy state was: {strat.state}  position_side={strat.position_side}", flush=True)
                    strat.state = StrategyState.FLAT
                    strat.position_side = None
                    strat.stop_price = None
                    strat.pending_entry = None
                    print(f"  Strategy state RESET to FLAT", flush=True)

                # Record to analytics EventStore
                if self.event_store:
                    try:
                        self.event_store.record(
                            trade_id="", strategy_id=signal.strategy_id,
                            instrument=signal.instrument, event_type="ORDER_REJECTED",
                            payload={
                                "reason": reason, "rejected": True,
                                "side": str(signal.signal_type), "trigger_price": signal.trigger_price,
                                "stop_price": signal.stop_price, "quantity": signal.quantity,
                                "strategy_id": signal.strategy_id, "instrument": signal.instrument,
                                "strategy_positions": len(self.position_manager.get_positions_by_strategy(signal.strategy_id)),
                                "total_positions": len(self.position_manager.open_positions),
                            },
                        )
                    except Exception:
                        pass

                # Publish to dashboard EventBus
                self.publish_event("order_rejected", {
                    "strategy_id": signal.strategy_id,
                    "instrument": signal.instrument,
                    "side": str(signal.signal_type),
                    "trigger_price": signal.trigger_price,
                    "stop_price": signal.stop_price,
                    "quantity": signal.quantity,
                    "reason": reason,
                    "strategy_positions": len(self.position_manager.get_positions_by_strategy(signal.strategy_id)),
                    "total_positions": len(self.position_manager.open_positions),
                    "available_margin": strat_account.available_margin,
                    "equity": strat_account.equity,
                })

                # Telegram alert with full details
                try:
                    self.telegram.on_risk_alert({
                        "type": "order_rejected",
                        "severity": "WARNING",
                        "message": (
                            f"Order REJECTED: {reason}\n"
                            f"Strategy: {signal.strategy_id}\n"
                            f"Instrument: {signal.instrument}\n"
                            f"Side: {signal.signal_type}\n"
                            f"Price: {signal.trigger_price}\n"
                            f"Stop: {signal.stop_price}\n"
                            f"Qty: {signal.quantity}\n"
                            f"Strategy positions: {len(self.position_manager.get_positions_by_strategy(signal.strategy_id))}\n"
                            f"Total open: {len(self.position_manager.open_positions)}\n"
                            f"Margin available: {strat_account.available_margin:,.0f}\n"
                            f"Equity: {strat_account.equity:,.0f}"
                        ),
                        "strategy_id": signal.strategy_id,
                        "instrument": signal.instrument,
                        "side": str(signal.signal_type),
                        "trigger_price": signal.trigger_price,
                        "stop_price": signal.stop_price,
                        "quantity": signal.quantity,
                        "value": str(len(self.position_manager.get_positions_by_strategy(signal.strategy_id))),
                        "limit": str(self.risk_engine.max_positions_per_strategy),
                    })
                except Exception:
                    pass
            return

        # Submit order
        order = self.order_manager.submit_signal(signal, multiplier=multiplier)
        if order:
            print(f"[Order] Submitted: {order.order_id} {order.side} {order.instrument}", flush=True)
            if self.event_store:
                try:
                    self.event_store.record(
                        trade_id=order.order_id, strategy_id=signal.strategy_id,
                        instrument=signal.instrument, event_type="ORDER_CREATED",
                        payload={"order_id": order.order_id, "side": order.side,
                                 "trigger_price": signal.trigger_price, "stop_price": signal.stop_price},
                    )
                except Exception:
                    pass
            self.publish_event("order_submitted", {
                "order_id": order.order_id,
                "strategy_id": signal.strategy_id,
                "instrument": signal.instrument,
                "side": str(order.side),
                "trigger_price": signal.trigger_price,
                "stop_price": signal.stop_price,
                "quantity": signal.quantity,
            })
            # Persist order immediately
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
                        "state": str(order.state),
                        "filled_quantity": order.quantity,
                        "average_fill_price": order.fill_price,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    pass
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

    def _on_fill(self, fill: Fill) -> None:
        """Handle order fill with dedup and atomic close."""
        # Fill deduplication
        if self.fill_dedup.is_duplicate(fill.fill_id):
            print(f"[Fill] DUPLICATE ignored: {fill.fill_id}", flush=True)
            return
        self.fill_dedup.mark_processed(fill.fill_id)

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
                self.account_engine.block_margin(margin)
                position = self.position_manager.open_position(
                    fill=fill, multiplier=multiplier, margin=margin,
                )
                print(f"[Position] Opened: {position.side.value} {fill.instrument} @ {fill.price} (strategy={fill.strategy_id})", flush=True)
                if self.event_store:
                    try:
                        self.event_store.record(
                            trade_id=position.position_id, strategy_id=fill.strategy_id,
                            instrument=fill.instrument, event_type="POSITION_OPENED",
                            payload={"side": position.side.value, "price": fill.price,
                                     "quantity": fill.quantity, "margin": margin},
                        )
                    except Exception:
                        pass
                self.publish_event("position_opened", {
                    "position_id": position.position_id, "strategy_id": fill.strategy_id,
                    "instrument": fill.instrument, "side": position.side.value,
                    "price": fill.price, "quantity": fill.quantity, "margin": margin,
                })
                # Create trade in ledger
                if self.trade_ledger:
                    try:
                        strat = self.strategies.get(fill.strategy_id)
                        stop_price = strat.stop_price if strat and hasattr(strat, 'stop_price') else None
                        self.trade_ledger.create_trade(
                            strategy_id=fill.strategy_id,
                            instrument=fill.instrument,
                            side=position.side.value,
                            entry_quantity=fill.quantity,
                            signal_time=fill.timestamp,
                            trigger_price=fill.price,
                            stop_price=stop_price or 0.0,
                            multiplier=multiplier,
                        )
                    except Exception:
                        pass
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
                # Persist fill immediately
                if self._persistence:
                    try:
                        self._persistence.save_fill({
                            "fill_id": fill.fill_id, "order_id": fill.order_id,
                            "strategy_id": fill.strategy_id, "instrument": fill.instrument,
                            "side": fill.side, "quantity": fill.quantity, "price": fill.price,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
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
            if self._trade_close_manager:
                success = self._trade_close_manager.close_position(
                    fill=fill, position=position,
                    strategy_id=strategy_id, multiplier=multiplier,
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
                else:
                    gross_pnl, charges, net_pnl = 0.0, 0.0, 0.0
                self.position_manager.close_position(
                    position_id=position.position_id, fill=fill,
                    reason=position.exit_reason or "signal_exit",
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
                        "exit_reason": position.exit_reason or "signal_exit",
                        "duration": duration_str,
                    })
                except Exception:
                    pass

            # If this was a reversal (old position closed, new pending entry exists),
            # now open the new position from the same fill
            strat = self.strategies.get(strategy_id)
            if strat and strat.pending_entry is not None and strat.state in (StrategyState.PENDING_LONG, StrategyState.PENDING_SHORT):
                new_side = strat.pending_entry.side
                fill_side = "BUY" if new_side == "LONG" else "SELL"
                margin = self._calculate_margin(fill.instrument, fill.price, fill.quantity, side=fill_side)
                if strat_account and strat_account.block_margin(margin):
                    self.account_engine.block_margin(margin)
                    new_position = self.position_manager.open_position(
                        fill=fill, multiplier=multiplier, margin=margin,
                    )
                    print(f"[Position] REVERSAL opened: {new_side} {fill.instrument} @ {fill.price} (strategy={strategy_id})", flush=True)

    def _on_status(self, status: str) -> None:
        """Handle data adapter status change."""
        print(f"[Data] Status: {status}", flush=True)

    def _warmup_from_rest(self) -> None:
        """Startup backfill: fetch 5m candles via REST, resample to 1H/15m, pre-populate HTF engine AND indicators.

        Every startup fetches previous day+ data to warm up indicators.
        Eliminates the cold-start penalty — indicators ready from first live tick.
        """
        import datetime as _dt
        print("[Engine] Starting backfill from REST API...", flush=True)

        now = _dt.datetime.now()
        # Fetch 7 days back to match backtest data range.
        # DEMA-ATR needs 6+ bars to initialize; 3 days was insufficient,
        # causing live 15m DEMA-ATR to drift above 1H DEMA-ATR and block signals.
        from_date = (now - _dt.timedelta(days=7)).date()
        to_date = now.date()

        instruments = self.config.get("instruments", {})
        for name, cfg in instruments.items():
            try:
                # Fetch 5m candles via REST
                candles = self.data_adapter.fetch_historical_candles(
                    name, "5", from_date, to_date,
                )
                if not candles:
                    print(f"[Backfill] {name}: no REST data, skipping", flush=True)
                    continue

                # Convert to DataFrame for resampling
                import pandas as pd
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                df = df.sort_values("datetime").reset_index(drop=True)
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

                # --- Resample to 1H and 15m ---
                from core.timeframe_engine import Bar, BarState
                for tf, tf_minutes in [("1h", 60), ("15m", 15)]:
                    d = df.copy()
                    dt = d["datetime"]
                    dates = dt.dt.date.astype(str)
                    session_start = pd.to_datetime(dates + f" {session_open}")
                    mins = ((dt - session_start).dt.total_seconds() // 60).astype(int)
                    d["_bucket"] = session_start + pd.to_timedelta((mins // tf_minutes) * tf_minutes, unit="m")
                    htf = d.groupby("_bucket", sort=True).agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum",
                    }).reset_index().rename(columns={"_bucket": "datetime"})

                    # Convert to Bar objects and feed to HTF engine
                    bars = []
                    for _, row in htf.iterrows():
                        bar_dt = row["datetime"]
                        if bar_dt.tzinfo is None:
                            from datetime import timezone, timedelta
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

            except Exception as e:
                print(f"[Backfill] {name}: error - {e}", flush=True)

        print("[Engine] Backfill complete", flush=True)

    def snapshot(self) -> dict:
        """Get complete system state for persistence.

        Separates LIVE STATE (restorable) from HISTORICAL STATE (in DB only).
        """
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
            "indicators": {
                key: ind.snapshot()
                for key, ind in self.indicators.items()
            },
            "htf": self.htf_engine.snapshot(),
            "health": self.health.snapshot(),
            # Historical state reference (authoritative source is trades DB)
            "historical_source": "trading.db",
        }

    def restore(self, state: dict) -> None:
        """Restore system state from persistence.

        Restore order: market status → strategies → positions → accounts → PnL → risk → execution → indicators → HTF
        NOTE: starting_capital is NOT restored from saved state - it always comes from config.
        """
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
        # Restore indicator state (DEMA-ATR values)
        for key, ind_state in state.get("indicators", {}).items():
            if key in self.indicators:
                self.indicators[key].restore(ind_state)
        # Restore HTF engine state
        if "htf" in state:
            self.htf_engine.restore(state["htf"])
        # Recalculate global account starting_capital from per-strategy accounts
        total = sum(a.starting_capital for a in self.account_engines.values())
        self.account_engine.starting_capital = total
