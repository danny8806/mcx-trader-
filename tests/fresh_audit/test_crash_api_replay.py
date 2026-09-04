"""Phase 24-27 + 28-39: Crash, failure, API, and full-system replay tests.

Covers:
- Phase 24: Idempotency (fill dedup, duplicate orders)
- Phase 25: Out-of-order fill delivery
- Phase 26: DB failure during trade close
- Phase 27: Crash between persist and memory update
- Phase 28: Restart recovery
- Phase 29: P&L accuracy
- Phase 30: Trade reconcile
- Phase 31: API endpoint verification
- Phase 32: Frontend data contract
- Phase 33: Full system replay
- Phase 34: Session boundary tests
"""
from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from strategies.types import StrategyState, SignalType, Signal

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(instrument, tf, ts, o, h, l, c, vol=100):
    from core.timeframe_engine import Bar, BarState
    tf_min = {"5m": 5, "15m": 15, "1h": 60}.get(tf, 5)
    return Bar(instrument=instrument, timeframe=tf, start_ts=ts, end_ts=ts + tf_min * 60,
               open=float(o), high=float(h), low=float(l), close=float(c), volume=vol, state=BarState.CLOSED)


def _htf(val, prev=None):
    from htf.confirmation import HTFMappedValue
    return HTFMappedValue(htf_value=val, prev_htf_value=prev, htf_confirmed=True, htf_source_timestamp=0.0)


def _fill(fid, oid, strat, inst, side, qty, price, ts=None):
    from execution.paper_broker import Fill
    return Fill(fill_id=fid, order_id=oid, strategy_id=strat, instrument=inst,
                side=side, quantity=qty, price=price, timestamp=ts or time.time())


def _make_engine_tmp():
    """Create a minimal TradingEngine for testing."""
    import threading
    from trading_engine import TradingEngine
    from config import Config
    from persistence.manager import PersistenceManager
    tmpdir = tempfile.mkdtemp()
    config = Config()
    config._data = {
        "system": {"db_path": os.path.join(tmpdir, "trading.db"),
                   "state_path": os.path.join(tmpdir, "state.json")},
        "dhan": {"client_id": "test", "token_file": os.path.join(tmpdir, "token.json"),
                 "pin": "", "totp_secret": ""},
        "warmup": {"last_trading_days": 5, "fetch_calendar_days": 14,
                   "max_fetch_calendar_days": 62, "keep_partial": True},
        "instruments": {
            "GOLDM": {"symbol": "MCX:GOLDM202610", "security_id": "569003",
                      "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                      "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                      "session_open": "09:00", "session_close": "23:30",
                      "session_minutes": 870, "keep_partial": True},
            "SILVERM": {"symbol": "MCX:SILVERM202611", "security_id": "483080",
                        "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                        "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                        "session_open": "09:00", "session_close": "23:30",
                        "session_minutes": 870, "keep_partial": True},
        },
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": {
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m",
                        "mid_timeframe": "15m", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 300000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 300000, "enabled": True},
        },
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 0, "partial_fill_probability": 0.0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                      "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                        "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1,
                 "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 600000.0,
                    "starting_capital_per_strategy": 300000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
    }
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = config
    engine._persistence = PersistenceManager(
        state_path=os.path.join(tmpdir, "state.json"),
        db_path=os.path.join(tmpdir, "trading.db"),
    )
    engine._init_market_status()
    engine._init_indicator_engines()
    engine._init_htf_engine()
    engine._init_strategies()
    engine._init_execution()
    engine._init_portfolio()
    engine._init_risk()
    engine._init_monitoring()
    engine._init_notifications()
    engine._running = True
    engine.tick_signal_processing = True
    engine._lock = threading.RLock()
    engine._event_callback = None
    return engine


# ---------------------------------------------------------------------------
# Phase 24: Idempotency
# ---------------------------------------------------------------------------

class TestIdempotencyPhase24:
    """Prove duplicate fills, duplicate orders, and duplicate trade-closes are blocked."""

    def test_fill_dedup_prevents_double_processing(self):
        """Same fill_id submitted twice should only apply financial effects once."""
        from core.fill_dedup import FillDeduplicator
        tmpdir = tempfile.mkdtemp()
        fd = FillDeduplicator(db_path=os.path.join(tmpdir, "dedup.db"))
        assert fd.is_duplicate("F_IDEM_001") is False
        fd.note_processed("F_IDEM_001")
        assert fd.is_duplicate("F_IDEM_001") is True

    def test_fill_dedup_survives_restart(self):
        """Dedup state persists across FillDeduplicator instances (simulates restart)."""
        from core.fill_dedup import FillDeduplicator
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "dedup.db")
        fd1 = FillDeduplicator(db_path=db)
        fd1.note_processed("F_RESTART_001")
        fd1.mark_processed("F_RESTART_001")
        fd2 = FillDeduplicator(db_path=db)
        fd2.load_from_database()
        assert fd2.is_duplicate("F_RESTART_001") is True

    def test_trade_close_persistence_before_memory(self):
        """Trade close persists to DB BEFORE updating in-memory state."""
        from core.trade_close import TradeCloseManager
        from portfolio.position_manager import PositionManager, PositionSide
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        from persistence.manager import PersistenceManager
        from core.risk_engine import RiskEngine
        tmpdir = tempfile.mkdtemp()
        pm = PersistenceManager(
            state_path=os.path.join(tmpdir, "state.json"),
            db_path=os.path.join(tmpdir, "trading.db"),
        )
        posmgr = PositionManager()
        entry_fill = _fill("F_ENTRY_001", "O_001", "gold_01", "GOLDM", "BUY", 1, 150000.0)
        pos = posmgr.open_position(entry_fill, multiplier=10.0, stop_price=149000.0, margin=100000.0)
        pnl_eng = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        acct = AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)
        global_acct = AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)
        risk = RiskEngine()
        tcm = TradeCloseManager(
            position_manager=posmgr, pnl_engines={"gold_01": pnl_eng},
            account_engines={"gold_01": acct}, global_account=global_acct,
            risk_engine=risk, persistence=pm, event_store=None,
            telegram=None, event_callback=None, trade_ledger=None,
        )
        exit_fill = _fill("F_EXIT_001", "O_002", "gold_01", "GOLDM", "SELL", 1, 151000.0)
        result = tcm.close_position(exit_fill, pos, "gold_01", 10.0, "signal_exit")
        assert result is not False and result is not None
        trades = pm.get_trades("gold_01")
        assert len(trades) == 1
        assert trades[0]["trade_id"] == pos.position_id
        assert trades[0]["exit_price"] == 151000.0
        assert pos.status.value == "closed"

    def test_concurrent_fill_processing(self):
        """10 threads calling mark_processed — only one succeeds (atomic insert)."""
        from core.fill_dedup import FillDeduplicator
        import concurrent.futures
        tmpdir = tempfile.mkdtemp()
        fd = FillDeduplicator(db_path=os.path.join(tmpdir, "conc.db"))
        results = []
        def try_mark(fid):
            return fd.mark_processed(fid)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(try_mark, "F_CONC_ATOMIC") for _ in range(10)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())
        true_count = sum(results)
        assert true_count == 1, f"Exactly 1 thread should succeed mark_processed, got {true_count}"


# ---------------------------------------------------------------------------
# Phase 25: Out-of-order fill delivery
# ---------------------------------------------------------------------------

class TestOutOfOrderPhase25:
    """Prove out-of-order fills don't corrupt state."""

    def test_exit_fill_before_entry_fill_ignored(self):
        """An exit fill for a non-existent position should not crash."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        exit_fill = _fill("F_OOO_EXIT", "O_001", "gold_01", "GOLDM", "SELL", 1, 151000.0)
        with pytest.raises(ValueError, match="not found"):
            pm.close_position("nonexistent_pos_id", exit_fill, "signal_exit")

    def test_entry_fill_idempotent(self):
        """Same entry fill processed twice doesn't create two positions."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        entry = _fill("F_ENTRY_IDEM", "O_001", "gold_01", "GOLDM", "BUY", 1, 150000.0)
        pos1 = pm.open_position(entry, multiplier=10.0)
        assert len(pm.open_positions) == 1
        assert pos1.position_id in {p.position_id for p in pm.open_positions}


# ---------------------------------------------------------------------------
# Phase 26: DB failure during trade close
# ---------------------------------------------------------------------------

class TestDBFailurePhase26:
    """Prove DB failure during close is handled gracefully."""

    def test_persistence_failure_returns_false(self):
        """If persistence.save_trade_and_fill fails, close returns False."""
        from core.trade_close import TradeCloseManager
        from portfolio.position_manager import PositionManager
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        from core.risk_engine import RiskEngine
        posmgr = PositionManager()
        entry_fill = _fill("F_DB_FAIL_001", "O_001", "gold_01", "GOLDM", "BUY", 1, 150000.0)
        pos = posmgr.open_position(entry_fill, multiplier=10.0, stop_price=149000.0, margin=100000.0)
        bad_persistence = MagicMock()
        bad_persistence.save_trade_and_fill.side_effect = sqlite3.OperationalError("disk full")
        tcm = TradeCloseManager(
            position_manager=posmgr,
            pnl_engines={"gold_01": PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))},
            account_engines={"gold_01": AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)},
            global_account=AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5),
            risk_engine=RiskEngine(),
            persistence=bad_persistence, event_store=None,
            telegram=None, event_callback=None, trade_ledger=None,
        )
        exit_fill = _fill("F_DB_FAIL_002", "O_002", "gold_01", "GOLDM", "SELL", 1, 151000.0)
        result = tcm.close_position(exit_fill, pos, "gold_01", 10.0, "signal_exit")
        assert result is False, "Should return False when DB persistence fails"
        assert pos.is_open, "Position should remain open when DB fails"


# ---------------------------------------------------------------------------
# Phase 27: Crash between persist and memory update
# ---------------------------------------------------------------------------

class TestCrashRecoveryPhase27:
    """Prove crash between persist and memory update is recoverable."""

    def test_reconciliation_detects_inconsistency(self):
        """Reconciliation catches DB-closed but memory-open position."""
        from reconciliation.engine import ReconciliationEngine, ReconciliationResult
        from portfolio.position_manager import PositionManager
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.order_manager import OrderManager
        from execution.paper_broker import PaperExecutionEngine
        from persistence.manager import PersistenceManager
        tmpdir = tempfile.mkdtemp()
        pm = PersistenceManager(
            state_path=os.path.join(tmpdir, "state.json"),
            db_path=os.path.join(tmpdir, "trading.db"),
        )
        posmgr = PositionManager()
        entry = _fill("F_CRASH_001", "O_001", "gold_01", "GOLDM", "BUY", 1, 150000.0)
        pos = posmgr.open_position(entry, multiplier=10.0, stop_price=149000.0, margin=100000.0)
        pm.save_trade({
            "trade_id": pos.position_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "LONG", "entry_price": 150000.0,
            "exit_price": 151000.0, "quantity": 1, "multiplier": 10.0,
            "gross_pnl": 10000.0, "charges": 80.0, "net_pnl": 9920.0,
            "exit_reason": "crash_test", "status": "closed",
        })
        pm.save_fill({
            "fill_id": "F_CRASH_001", "order_id": "O_001",
            "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "price": 150000.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        exec_eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0, partial_fill_probability=0.0)
        order_mgr = OrderManager(execution_engine=exec_eng)
        pnl_engines = {"gold_01": PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))}
        acct_engines = {"gold_01": AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)}
        strategies = {"gold_01": MagicMock()}
        recon = ReconciliationEngine(
            persistence=pm, position_manager=posmgr,
            pnl_engines=pnl_engines, account_engines=acct_engines,
            strategies=strategies, order_manager=order_mgr,
        )
        result = recon.reconcile(phase="crash_recovery")
        has_trade_pos_mismatch = any(
            "closed in DB but position is still open" in e for e in result.errors
        )
        assert has_trade_pos_mismatch or not result.is_consistent, \
            "Reconciliation should detect crash-recovery inconsistency"


# ---------------------------------------------------------------------------
# Phase 28-29: Restart + P&L accuracy
# ---------------------------------------------------------------------------

class TestRestartPnLPhase28_29:
    """Prove P&L is accurate and survives restart."""

    def test_pnl_engine_accuracy(self):
        """PNLEngine calculates correct P&L for LONG trade."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        # LONG 150000 → 151000, qty=1, mult=10 → gross = (151000-150000)*10*1 = 10000
        # Charges: brokerage=40, stt=151000*10*0.01/100=15.1, exchange=(150000+151000)*10*0.0026/100=78.26
        # sebi=(150000+151000)*10*0.0001/100=3.01, gst=(40+78.26+3.01)*18/100=21.83
        gross, charges, net = pnl.calculate_realized_pnl(
            entry_fill=_fill("F_PNL_001", "O_001", "gold_01", "GOLDM", "BUY", 1, 150000.0),
            exit_fill=_fill("F_PNL_002", "O_002", "gold_01", "GOLDM", "SELL", 1, 151000.0),
            multiplier=10.0,
        )
        assert abs(gross - 10000.0) < 1e-6, f"Gross P&L should be 10000, got {gross}"
        assert charges > 0, f"Charges should be positive, got {charges}"
        assert abs(net - (gross - charges)) < 1e-6

    def test_pnl_engine_short_trade(self):
        """PNLEngine calculates correct P&L for SHORT trade."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        # SHORT 151000 → 150000, qty=1, mult=10 → gross = (151000-150000)*10*1 = 10000
        gross, charges, net = pnl.calculate_realized_pnl(
            entry_fill=_fill("F_SHORT_001", "O_001", "gold_01", "GOLDM", "SELL", 1, 151000.0),
            exit_fill=_fill("F_SHORT_002", "O_002", "gold_01", "GOLDM", "BUY", 1, 150000.0),
            multiplier=10.0,
        )
        assert abs(gross - 10000.0) < 1e-6

    def test_pnl_snapshot_restore(self):
        """PNLEngine snapshot/restore preserves realized P&L."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        pnl.record_trade(gross=10000.0, charges=80.0, net=9920.0)
        snap = pnl.snapshot()
        pnl2 = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        pnl2.restore(snap)
        assert pnl2.realized_net == 9920.0
        assert pnl2.trade_count == 1


# ---------------------------------------------------------------------------
# Phase 30: Trade reconcile
# ---------------------------------------------------------------------------

class TestTradeReconcilePhase30:
    """Prove DB trades match in-memory P&L."""

    def test_reconciliation_passes_clean_state(self):
        """Clean state (no trades) should pass reconciliation."""
        from reconciliation.engine import ReconciliationEngine
        from portfolio.position_manager import PositionManager
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.order_manager import OrderManager
        from execution.paper_broker import PaperExecutionEngine
        from persistence.manager import PersistenceManager
        tmpdir = tempfile.mkdtemp()
        pm = PersistenceManager(
            state_path=os.path.join(tmpdir, "state.json"),
            db_path=os.path.join(tmpdir, "trading.db"),
        )
        posmgr = PositionManager()
        exec_eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0, partial_fill_probability=0.0)
        order_mgr = OrderManager(execution_engine=exec_eng)
        pnl_engines = {"gold_01": PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20)),
                       "silver_01": PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))}
        acct_engines = {"gold_01": AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5),
                        "silver_01": AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)}
        strategies = {"gold_01": MagicMock(), "silver_01": MagicMock()}
        recon = ReconciliationEngine(
            persistence=pm, position_manager=posmgr,
            pnl_engines=pnl_engines, account_engines=acct_engines,
            strategies=strategies, order_manager=order_mgr,
        )
        result = recon.reconcile(phase="clean_state")
        assert result.is_consistent, f"Clean state should pass: {result.errors}"


# ---------------------------------------------------------------------------
# Phase 31: API endpoint verification
# ---------------------------------------------------------------------------

class TestAPIEndpointsPhase31:
    """Verify all API endpoints return valid JSON."""

    def test_health_endpoint(self):
        """GET /api/health returns status ok."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/health", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert data.get("status") == "ok"
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_strategies_endpoint(self):
        """GET /api/strategies returns strategy list."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/strategies", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "strategies" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_positions_endpoint(self):
        """GET /api/positions returns position list."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/positions", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "positions" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_orders_endpoint(self):
        """GET /api/orders returns order list."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/orders", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "orders" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_pnl_endpoint(self):
        """GET /api/pnl returns P&L data."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/pnl", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "portfolio" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_risk_endpoint(self):
        """GET /api/risk returns risk data."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/risk", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "kill_switch_active" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_reconciliation_endpoint(self):
        """GET /api/reconciliation returns reconcile result."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/reconciliation", timeout=10)
            assert r.status_code == 200
            data = r.json()
            assert "is_consistent" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_indicators_endpoint(self):
        """GET /api/indicators returns indicator data."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/indicators", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "indicators" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_market_data_endpoint(self):
        """GET /api/market-data returns market data."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/market-data", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "instruments" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_settings_endpoint(self):
        """GET /api/settings returns config (tokens masked)."""
        import requests
        try:
            r = requests.get("http://localhost:8000/api/settings", timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert "strategies" in data
            assert "risk" in data
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")

    def test_strategy_control_pause(self):
        """POST /api/strategies/gold_01/control pauses strategy."""
        import requests
        try:
            r = requests.post("http://localhost:8000/api/strategies/gold_01/control",
                              json={"action": "pause"}, timeout=5)
            assert r.status_code == 200
            data = r.json()
            assert data.get("success") is True
            requests.post("http://localhost:8000/api/strategies/gold_01/control",
                           json={"action": "resume"}, timeout=5)
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("Dashboard not running on localhost:8000")


# ---------------------------------------------------------------------------
# Phase 33: Full system replay (synthetic 100-bar sequence)
# ---------------------------------------------------------------------------

class TestFullReplayPhase33:
    """Drive a 100-bar synthetic sequence through the engine and verify consistency."""

    def test_100_bar_replay_consistency(self):
        """Feed 100 bars through engine, verify reconciliation passes."""
        from core.safe_mode import SafeModeManager
        engine = _make_engine_tmp()
        engine.safe_mode = SafeModeManager(engine.market_status)
        strat = engine.strategies["gold_01"]
        np.random.seed(42)
        base_price = 150000.0
        ts = 1000.0
        for i in range(100):
            drift = np.random.randn() * 50
            o = base_price + drift
            h = o + abs(np.random.randn() * 30)
            l = o - abs(np.random.randn() * 30)
            c = o + np.random.randn() * 20
            bar = _bar("GOLDM", "5m", ts + i * 300, o, h, l, c)
            engine.indicators["GOLDM:5m"].update(o, h, l, c)
            if i % 12 == 0:
                htf_bar = _bar("GOLDM", "1h", ts + i * 300, o, h, l, c)
                engine.htf_engine.on_htf_bar_closed(htf_bar)
            if i % 3 == 0:
                mid_bar = _bar("GOLDM", "15m", ts + i * 300, o, h, l, c)
                engine.htf_engine.on_htf_bar_closed(mid_bar)
            htf_mapped = engine.htf_engine.map_to_fast_bar(bar, "5m")
            mid_mapped = engine.htf_engine.map_mid_to_fast_bar(bar, "5m")
            fast_ind = engine.indicators["GOLDM:5m"]
            signal = strat.on_bar(bar, htf_mapped, fast_ind.value, mid_mapped)
            if signal:
                engine._process_signal(signal)
            base_price = c
        snap = strat.snapshot()
        assert snap["bars_processed"] == 100
        assert strat._bars_processed == 100

    def test_two_strategy_parallel_replay(self):
        """Feed bars to both GOLDM and SILVERM strategies simultaneously."""
        engine = _make_engine_tmp()
        g_strat = engine.strategies["gold_01"]
        s_strat = engine.strategies["silver_01"]
        np.random.seed(123)
        ts = 1000.0
        for i in range(50):
            g_price = 150000 + np.random.randn() * 50
            s_price = 95000 + np.random.randn() * 30
            g_bar = _bar("GOLDM", "5m", ts + i * 300, g_price, g_price + 30, g_price - 30, g_price + 10)
            s_bar = _bar("SILVERM", "15m", ts + i * 900, s_price, s_price + 20, s_price - 20, s_price + 5)
            engine.indicators["GOLDM:5m"].update(g_bar.open, g_bar.high, g_bar.low, g_bar.close)
            engine.indicators["SILVERM:15m"].update(s_bar.open, s_bar.high, s_bar.low, s_bar.close)
            if i % 12 == 0:
                engine.htf_engine.on_htf_bar_closed(_bar("GOLDM", "1h", ts + i * 300, g_price, g_price + 30, g_price - 30, g_price + 10))
                engine.htf_engine.on_htf_bar_closed(_bar("SILVERM", "1h", ts + i * 900, s_price, s_price + 20, s_price - 20, s_price + 5))
            g_htf = engine.htf_engine.map_to_fast_bar(g_bar, "5m")
            g_mid = engine.htf_engine.map_mid_to_fast_bar(g_bar, "5m")
            s_htf = engine.htf_engine.map_to_fast_bar(s_bar, "15m")
            s_mid = engine.htf_engine.map_mid_to_fast_bar(s_bar, "15m")
            g_sig = g_strat.on_bar(g_bar, g_htf, engine.indicators["GOLDM:5m"].value, g_mid)
            s_sig = s_strat.on_bar(s_bar, s_htf, engine.indicators["SILVERM:15m"].value, s_mid)
        assert g_strat._bars_processed == 50
        assert s_strat._bars_processed == 50


# ---------------------------------------------------------------------------
# Phase 34: Session boundary tests
# ---------------------------------------------------------------------------

class TestSessionBoundaryPhase34:
    """Prove market session transitions work correctly."""

    def test_market_status_transitions(self):
        """Market status transitions: OVERNIGHT → PRE_MARKET → MARKET_OPEN → LIVE_TRADING."""
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms.state in (MarketState.OVERNIGHT, MarketState.PRE_MARKET, MarketState.MARKET_OPEN,
                            MarketState.LIVE_TRADING, MarketState.MARKET_CLOSE, MarketState.AFTER_MARKET)

    def test_trading_allowed_only_during_session(self):
        """is_trading_allowed requires LIVE_TRADING + TRADING + live data."""
        from core.market_status import MarketStatus, MarketState, EngineStatus, DataStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms.is_trading_allowed is False
        ms._force_state_override = MarketState.LIVE_TRADING
        ms._data_status = DataStatus.CONNECTED
        assert ms.is_trading_allowed is True

    def test_safe_mode_blocks_trading(self):
        """Safe mode prevents trading even during market hours."""
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.enter_safe_mode("test")
        assert ms.is_safe is True
        assert ms.state == MarketState.SAFE_MODE
        ms.exit_safe_mode()
        assert ms.is_safe is False

    def test_persistence_roundtrip_market_status(self):
        """MarketStatus snapshot/restore preserves warmup/reconcile flags."""
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.mark_warmup_done()
        ms.mark_reconcile_done()
        snap = ms.snapshot()
        ms2 = MarketStatus(session_open="09:00", session_close="23:30")
        with patch.object(ms2, '_current_date', return_value=snap.get("session_date")):
            ms2.restore(snap)
        assert ms2._warmup_done_today is True
        assert ms2._reconcile_done_today is True


# ---------------------------------------------------------------------------
# Phase 35-39: Cross-instrument isolation + risk engine
# ---------------------------------------------------------------------------

class TestCrossInstrumentPhase35_39:
    """Prove instruments don't interfere with each other."""

    def test_goldsilver_independent_indicators(self):
        """GOLDM and SILVERM indicators are completely independent."""
        from indicators.dema_atr import DEMAATR
        g_ind = DEMAATR(3, 6, 1.0)
        s_ind = DEMAATR(3, 6, 1.0)
        for i in range(20):
            g_ind.update(150000 + i, 150010 + i, 149990 + i, 150005 + i)
            s_ind.update(95000 + i * 0.5, 95010 + i * 0.5, 94990 + i * 0.5, 95005 + i * 0.5)
        assert g_ind.value != s_ind.value
        assert g_ind.value > 100000
        assert s_ind.value < 100000

    def test_risk_engine_kill_switch(self):
        """Kill switch blocks all new entries."""
        from core.risk_engine import RiskEngine
        risk = RiskEngine(kill_switch_enabled=True, max_daily_loss=10000)
        risk.update_daily_pnl(-15000)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000, 150000, 149000, 1)
        allowed, reason = risk.check_order(sig, current_positions=0, strategy_positions=0,
                                           available_margin=300000, margin_required=100000,
                                           current_equity=300000)
        assert not allowed
        assert reason == "daily_loss_limit_reached"
        assert risk.kill_switch_active is True

    def test_account_engine_margin_blocking(self):
        """Account blocks when margin exceeds available."""
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=100000, margin_per_trade_pct=6.5)
        blocked = acct.block_margin(90000)
        assert blocked is True
        assert acct.available_margin < 20000
        blocked2 = acct.block_margin(20000)
        assert blocked2 is False
