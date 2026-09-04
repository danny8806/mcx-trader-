"""PHASE 11 — TRADE LIFECYCLE (SIGNAL -> FILL -> P&L)
Verifies: End-to-end from signal to fill to P&L calculation.
"""
from __future__ import annotations

import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


def _mk_fill(fid, oid, inst, side, qty, px, ts, strat, mult=1.0):
    from execution.paper_broker import Fill
    return Fill(fid, oid, inst, side, qty, px, ts, strat, mult)


class TestTradeLifecycle:
    """Phase 11: Full trade lifecycle verification."""

    def test_long_trade_pnl(self):
        """LONG trade: buy low, sell high -> positive P&L."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01")
        exit_ = _mk_fill("X1", "O2", "GOLDM", "SELL", 1, 152000.0, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, 10.0)
        assert gross == 20000.0
        assert charges > 0
        assert net == gross - charges

    def test_short_trade_pnl(self):
        """SHORT trade: sell high, buy low -> positive P&L."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E1", "O1", "SILVERM", "SELL", 1, 95000.0, time.time(), "silver_01")
        exit_ = _mk_fill("X1", "O2", "SILVERM", "BUY", 1, 93000.0, time.time(), "silver_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, 5.0)
        assert gross == 10000.0
        assert net == gross - charges

    def test_trade_count_increments(self):
        """record_trade increments trade_count."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        assert pnl.trade_count == 0
        pnl.record_trade(1000.0, 50.0, 950.0)
        assert pnl.trade_count == 1
        pnl.record_trade(-500.0, 50.0, -550.0)
        assert pnl.trade_count == 2

    def test_charges_included(self):
        """P&L includes brokerage, STT, exchange fees, GST."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01")
        exit_ = _mk_fill("X1", "O2", "GOLDM", "SELL", 1, 151000.0, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, 10.0)
        # Charges should include brokerage (20*2=40), STT, exchange fees, GST
        assert charges > 40.0, f"Charges {charges} should exceed brokerage alone (40)"

    def test_fill_gross_value(self):
        """Fill.gross_value = quantity * price * multiplier."""
        from execution.paper_broker import Fill
        fill = Fill("F1", "O1", "GOLDM", "BUY", 2, 150000.0, time.time(), "gold_01", 10.0)
        assert fill.gross_value == 3000000.0, f"Expected 3000000, got {fill.gross_value}"

    def test_multi_trade_accumulation(self):
        """Multiple trades accumulate realized P&L correctly."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        trades = [
            ("BUY", 150000.0, "SELL", 152000.0, 10.0),
            ("BUY", 152000.0, "SELL", 151000.0, 10.0),
            ("BUY", 151000.0, "SELL", 153000.0, 10.0),
        ]
        for i, (entry_side, entry_px, exit_side, exit_px, mult) in enumerate(trades):
            entry = _mk_fill(f"E{i}", f"O{i}", "GOLDM", entry_side, 1, entry_px, time.time(), "gold_01")
            exit_ = _mk_fill(f"X{i}", f"OX{i}", "GOLDM", exit_side, 1, exit_px, time.time(), "gold_01")
            g, c, n = pnl.calculate_realized_pnl(entry, exit_, mult)
            pnl.record_trade(g, c, n)
        assert pnl.trade_count == 3
        assert pnl.realized_gross == 30000.0  # 20000 - 10000 + 20000

    def test_position_manager_close_pnl(self):
        """PositionManager.close_position calculates P&L correctly."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        entry = _mk_fill("F1", "O1", "GOLDM", "BUY", 1, 150000.0, time.time(), "gold_01", 10.0)
        pos = pm.open_position(entry, multiplier=10.0)
        exit_ = _mk_fill("F2", "O2", "GOLDM", "SELL", 1, 155000.0, time.time(), "gold_01")
        closed = pm.close_position(pos.position_id, exit_, "signal_exit")
        assert closed.realized_pnl == 50000.0
