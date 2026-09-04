"""PHASE 9 — POSITION LIFECYCLE
Verifies: open -> mark -> close, P&L calculation, snapshot/restore.
"""
from __future__ import annotations

import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


def _mk_fill(fid, oid, inst, side, qty, px, ts, strat, mult=1.0, trade_id="TRD-UNIT"):
    from execution.paper_broker import Fill
    return Fill(fid, oid, inst, side, qty, px, ts, strat, mult, None, trade_id)


class TestPositionLifecycle:
    """Phase 9: Position state machine correctness."""

    def test_open_position_long(self):
        """Opening a BUY fill creates a LONG position."""
        from portfolio.position_manager import PositionManager, PositionSide, PositionStatus
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(fill, multiplier=10.0, stop_price=149000.0)
        assert pos.side == PositionSide.LONG
        assert pos.quantity == 1
        assert pos.average_entry == 150000.0
        assert pos.stop_price == 149000.0
        assert pos.status == PositionStatus.OPEN
        assert pos.instrument == "GOLDM"
        assert pos.strategy_id == "gold_01"
        assert pos.multiplier == 10.0

    def test_open_position_short(self):
        """Opening a SELL fill creates a SHORT position."""
        from portfolio.position_manager import PositionManager, PositionSide
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "SILVERM", "SELL", 1, 95000.0, time.time(), "silver_01", 5.0)
        pos = pm.open_position(fill, multiplier=5.0)
        assert pos.side == PositionSide.SHORT

    def test_update_mark_long(self):
        """LONG position unrealized P&L = (mark - entry) * qty * mult."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(fill, multiplier=10.0)
        pos.update_mark(151000.0)
        assert pos.unrealized_pnl == 10000.0, f"Expected 10000, got {pos.unrealized_pnl}"

    def test_update_mark_short(self):
        """SHORT position unrealized P&L = (entry - mark) * qty * mult."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "SILVERM", "SELL", 1, 95000.0, time.time(), "silver_01", 5.0)
        pos = pm.open_position(fill, multiplier=5.0)
        pos.update_mark(94000.0)
        assert pos.unrealized_pnl == 5000.0, f"Expected 5000, got {pos.unrealized_pnl}"

    def test_close_position_long_profit(self):
        """Closing LONG above entry yields positive realized P&L."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        entry = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(entry, multiplier=10.0)
        exit_ = _mk_fill("F2", "O2", "GOLDM", "SELL", 1, 152000.0, time.time(), "gold_01")
        closed = pm.close_position(pos.position_id, exit_, "signal_exit")
        assert closed.realized_pnl == 20000.0, f"Expected 20000, got {closed.realized_pnl}"
        assert len(pm.open_positions) == 0
        assert len(pm.closed_positions) == 1

    def test_close_position_short_profit(self):
        """Closing SHORT below entry yields positive realized P&L."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        entry = _mk_fill("F1", "O1", "SILVERM", "SELL", 1, 95000.0, time.time(), "silver_01", 5.0)
        pos = pm.open_position(entry, multiplier=5.0)
        exit_ = _mk_fill("F2", "O2", "SILVERM", "BUY", 1, 93000.0, time.time(), "silver_01")
        closed = pm.close_position(pos.position_id, exit_, "signal_exit")
        assert closed.realized_pnl == 10000.0, f"Expected 10000, got {closed.realized_pnl}"

    def test_close_position_unknown_raises(self):
        """Closing non-existent position raises ValueError."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01")
        with pytest.raises(ValueError):
            pm.close_position("nonexistent", fill, "test")

    def test_positions_by_strategy(self):
        """Can filter positions by strategy_id."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        f1 = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        f2 = _mk_fill("F2", "O2", "SILVERM", "BUY", 1, 95000.0, time.time(), "silver_01", 5.0)
        pm.open_position(f1, multiplier=10.0)
        pm.open_position(f2, multiplier=5.0)
        gold_pos = pm.get_positions_by_strategy("gold_01")
        assert len(gold_pos) == 1
        assert gold_pos[0].instrument == "GOLDM"

    def test_position_snapshot_restore(self):
        """Position snapshot/restore preserves all fields."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        fill = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(fill, multiplier=10.0, stop_price=149000.0)
        snap = pm.snapshot()
        pm2 = PositionManager()
        pm2.restore(snap)
        assert len(pm2.open_positions) == 1
        pos2 = pm2.open_positions[0]
        assert pos2.position_id == pos.position_id
        assert pos2.strategy_id == "gold_01"
        assert pos2.average_entry == 150000.0
        assert pos2.stop_price == 149000.0
