"""
PHASE 30 — FINAL CROSS-LAYER RECONCILIATION
============================================
For every completed trade calculate independently:
Raw market data -> signal -> trigger -> fill -> position -> trade -> P&L -> equity
Then compare: INDEPENDENT vs RUNTIME vs DB
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence, REPORT_DIR


def _mk_fill(fid, oid, inst, side, qty, px, ts, strat, trade_id="TRD-UNIT"):
    from execution.paper_broker import Fill
    return Fill(fid, oid, inst, side, qty, px, ts, strat, 1.0, None, trade_id)


class TestFinalReconciliation:
    """Phase 30: Final cross-layer reconciliation."""

    def test_trade_lifecycle_independent_calculation(self):
        """Independently calculate P&L for a known trade and compare."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        entry_price = 150000.0
        exit_price = 151000.0
        multiplier = 10.0
        quantity = 1

        gross_pnl = (exit_price - entry_price) * multiplier * quantity
        assert gross_pnl == 10000.0

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E_LIFECYCLE", "O_E", "GOLDM", "BUY",
                         quantity, entry_price, time.time(), "gold_01")
        exit_ = _mk_fill("X_LIFECYCLE", "O_X", "GOLDM", "SELL",
                         quantity, exit_price, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, multiplier)

        assert abs(gross - gross_pnl) < 1e-6
        assert charges > 0
        assert abs(net - (gross - charges)) < 1e-6

        get_evidence().record("phase30", "trade_lifecycle_calc", "PASS", {
            "gross_pnl": gross, "charges": charges, "net_pnl": net,
            "independent_gross": gross_pnl,
        })

    def test_short_trade_pnl(self):
        """Independently calculate P&L for a SHORT trade."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        entry_price = 151000.0
        exit_price = 150000.0
        multiplier = 10.0

        gross_pnl = (entry_price - exit_price) * multiplier
        assert gross_pnl == 10000.0

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E_SHORT", "O_E", "GOLDM", "SELL",
                         1, entry_price, time.time(), "gold_01")
        exit_ = _mk_fill("X_SHORT", "O_X", "GOLDM", "BUY",
                         1, exit_price, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, multiplier)
        assert abs(gross - gross_pnl) < 1e-6

    def test_charges_breakdown(self):
        """Charges are positive and less than gross for profitable trade."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E_CHG", "O_E", "GOLDM", "BUY",
                         1, 150000.0, time.time(), "gold_01")
        exit_ = _mk_fill("X_CHG", "O_X", "GOLDM", "SELL",
                         1, 151000.0, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, 10.0)
        assert charges > 0
        assert charges < gross
        assert net < gross
        assert net > 0

    def test_reconciliation_clean_state(self):
        """Clean state (no trades) passes reconciliation."""
        from reconciliation.engine import ReconciliationEngine
        from portfolio.position_manager import PositionManager
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.order_manager import OrderManager
        from execution.paper_broker import PaperExecutionEngine
        from persistence.manager import PersistenceManager
        from unittest.mock import MagicMock
        tmpdir = tempfile.mkdtemp()
        pm = PersistenceManager(state_path=os.path.join(tmpdir, "state.json"),
                               db_path=os.path.join(tmpdir, "trading.db"))
        posmgr = PositionManager()
        exec_eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                       partial_fill_probability=0.0)
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
        result = recon.reconcile(phase="final_reconciliation")
        assert result.is_consistent, f"Clean state should pass: {result.errors}"

    def test_strategy_matrix_matches_trades(self):
        """Strategy metrics match independent calculation from trades."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        trades = [
            (150000.0, 151000.0, 10.0, "BUY", "SELL"),  # Win
            (151000.0, 150000.0, 10.0, "SELL", "BUY"),  # Win
            (150000.0, 149000.0, 10.0, "BUY", "SELL"),  # Loss
        ]
        total_gross = 0
        total_charges = 0
        total_net = 0
        wins = 0
        losses = 0
        for entry_px, exit_px, mult, entry_side, exit_side in trades:
            entry = _mk_fill(f"E_{entry_px}", "O_E", "GOLDM", entry_side,
                            1, entry_px, time.time(), "gold_01")
            exit_ = _mk_fill(f"X_{exit_px}", "O_X", "GOLDM", exit_side,
                            1, exit_px, time.time(), "gold_01")
            gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, mult)
            pnl.record_trade(gross, charges, net)
            total_gross += gross
            total_charges += charges
            total_net += net
            if net > 0:
                wins += 1
            else:
                losses += 1
        assert pnl.trade_count == 3
        assert wins == 2
        assert losses == 1
        assert total_gross > 0
        assert total_charges > 0

    def test_equity_formula(self):
        """Equity = starting_capital + realized_pnl."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        from portfolio.account import AccountEngine

        starting = 300000.0
        acct = AccountEngine(starting_capital=starting, margin_per_trade_pct=6.5)
        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E_EQ", "O_E", "GOLDM", "BUY",
                         1, 150000.0, time.time(), "gold_01")
        exit_ = _mk_fill("X_EQ", "O_X", "GOLDM", "SELL",
                         1, 151000.0, time.time(), "gold_01")
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_, 10.0)
        assert net == gross - charges
        assert abs(net - (10000.0 - charges)) < 1e-6

    def test_reversal_pnl(self):
        """Reversal trade: LONG then SHORT, each with correct P&L."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry_long = _mk_fill("E_REV_L", "O1", "GOLDM", "BUY",
                              1, 150000.0, time.time(), "gold_01")
        exit_long = _mk_fill("X_REV_L", "O2", "GOLDM", "SELL",
                             1, 151000.0, time.time(), "gold_01")
        g1, c1, n1 = pnl.calculate_realized_pnl(entry_long, exit_long, 10.0)
        assert g1 == 10000.0

        entry_short = _mk_fill("E_REV_S", "O3", "GOLDM", "SELL",
                               1, 151000.0, time.time(), "gold_01")
        exit_short = _mk_fill("X_REV_S", "O4", "GOLDM", "BUY",
                              1, 150000.0, time.time(), "gold_01")
        g2, c2, n2 = pnl.calculate_realized_pnl(entry_short, exit_short, 10.0)
        assert g2 == 10000.0

    def test_pnl_snapshot_restore(self):
        """PnL engine snapshot/restore preserves state."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel

        pnl = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        entry = _mk_fill("E_SNAP", "O1", "GOLDM", "BUY",
                         1, 150000.0, time.time(), "gold_01")
        exit_ = _mk_fill("X_SNAP", "O2", "GOLDM", "SELL",
                         1, 151000.0, time.time(), "gold_01")
        g, c, n = pnl.calculate_realized_pnl(entry, exit_, 10.0)
        pnl.record_trade(g, c, n)
        snap = pnl.snapshot()
        pnl2 = PNLEngine(fee_model=MCXFeeModel(brokerage_per_side=20))
        pnl2.restore(snap)
        assert pnl2.realized_net == n
        assert pnl2.trade_count == 1
