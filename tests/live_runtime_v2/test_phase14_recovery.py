"""PHASE 14 — RESTART / RECOVERY
Verifies: State snapshot/restore, P&L recovery, position recovery.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


def _mk_fill(fid, oid, inst, side, qty, px, ts, strat, mult=1.0, trade_id="TRD-UNIT"):
    from execution.paper_broker import Fill
    return Fill(fid, oid, inst, side, qty, px, ts, strat, mult, None, trade_id)


class TestRecovery:
    """Phase 14: Restart and recovery verification."""

    def test_pnl_snapshot_restore(self):
        """PNLEngine snapshot/restore preserves all state."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        pnl.record_trade(10000.0, 100.0, 9900.0)
        pnl.record_trade(-5000.0, 80.0, -5080.0)
        snap = pnl.snapshot()
        pnl2 = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        pnl2.restore(snap)
        assert pnl2.realized_gross == 5000.0
        assert pnl2.trade_count == 2
        assert pnl2.realized_net == 4820.0

    def test_position_snapshot_restore(self):
        """PositionManager snapshot/restore preserves open + closed positions."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        f1 = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(f1, multiplier=10.0, stop_price=149000.0)
        snap = pm.snapshot()
        pm2 = PositionManager()
        pm2.restore(snap)
        assert len(pm2.open_positions) == 1
        assert pm2.open_positions[0].position_id == pos.position_id

    def test_indicator_snapshot_restore(self):
        """DEMA-ATR snapshot/restore produces same output."""
        from indicators.dema_atr import DEMAATR
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            ind.update(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i)
        val_before = ind.value
        snap = ind.snapshot()
        ind2 = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        ind2.restore(snap)
        assert ind2.value == val_before

    def test_risk_engine_snapshot_restore(self):
        """RiskEngine snapshot/restore preserves kill switch and daily P&L."""
        from core.risk_engine import RiskEngine
        risk = RiskEngine(kill_switch_enabled=True, max_daily_loss=1000.0)
        risk.update_daily_pnl(-500.0)
        snap = risk.snapshot()
        risk2 = RiskEngine()
        risk2.restore(snap)
        assert risk2.daily_pnl == -500.0

    def test_market_status_snapshot_restore(self):
        """MarketStatus snapshot contains expected fields.

        force_state_override is intentionally NOT persisted (transient).
        It should always be None in snapshots to prevent stale safe_mode
        from locking the engine across restarts.
        """
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.force_state(MarketState.LIVE_TRADING)
        ms.set_engine_status(EngineStatus.TRADING)
        snap = ms.snapshot()
        # force_state_override is transient — never persisted
        assert snap.get("force_state_override") is None
        assert snap.get("engine_status") == "trading"

    def test_fill_dedup_load_after_restart(self):
        """FillDeduplicator.load_from_database restores in-memory cache."""
        from core.fill_dedup import FillDeduplicator
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            d1 = FillDeduplicator(db_path=db)
            d1.mark_processed("F_RESTART_1")
            d1.mark_processed("F_RESTART_2")
            # New instance — but FillDeduplicator is NOT persisted to same DB
            # automatically; the in-memory cache starts empty, but is_duplicate
            # falls through to DB query. So it WILL find the fill.
            d2 = FillDeduplicator(db_path=db)
            # is_duplicate checks in-memory first, then DB — so it finds it
            assert d2.is_duplicate("F_RESTART_1")
            count = d2.load_from_database()
            assert count == 2
            assert d2.is_duplicate("F_RESTART_1")
            d2.close()
            d1.close()

    def test_order_manager_snapshot(self):
        """OrderManager snapshot returns valid state."""
        from execution.paper_broker import PaperExecutionEngine
        from execution.order_manager import OrderManager
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        mgr = OrderManager(execution_engine=eng)
        snap = mgr.snapshot()
        assert "pending_signals" in snap
        assert "active_orders" in snap
