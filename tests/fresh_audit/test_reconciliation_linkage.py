"""Reconciliation position-linkage tests.

Trades in the persistence DB are position-anchored 1:1 (trade_id ==
position_id). The reconciliation check must match on that linkage, never on the
weak strategy:instrument key, which collides across sequential positions on the
same instrument and produced false startup reconciliation failures (sending the
engine into safe mode).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from reconciliation.engine import ReconciliationEngine, ReconciliationResult
from portfolio.position_manager import PositionManager, Position, PositionSide, PositionStatus
from execution.paper_broker import Fill


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_recon.db")


def _open_position(position_id: str, strategy: str = "gold_01", instrument: str = "GOLDM") -> Position:
    return Position(
        position_id=position_id,
        strategy_id=strategy,
        instrument=instrument,
        side=PositionSide.LONG,
        quantity=1,
        average_entry=100.0,
        entry_timestamp=time.time(),
        entry_fill_ids=[f"{position_id}-entry"],
    )


def _closed_position(position_id: str, strategy: str = "gold_01", instrument: str = "GOLDM") -> Position:
    pos = _open_position(position_id, strategy, instrument)
    pos.exit_fills = [
        Fill(
            fill_id=f"{position_id}-exit",
            order_id="",
            instrument=instrument,
            side="SELL",
            quantity=1,
            price=110.0,
            timestamp=time.time(),
            strategy_id=strategy,
        )
    ]
    pos.status = PositionStatus.CLOSED
    return pos


def _engine(tmp_db) -> ReconciliationEngine:
    from persistence.manager import PersistenceManager
    if tmp_db is None:
        persistence = MagicMock()
    else:
        persistence = PersistenceManager(state_path=str(tmp_db) + ".json", db_path=tmp_db)
    pos_mgr = PositionManager()
    exec_engine = MagicMock()
    exec_engine._orders = {}
    exec_engine._fills = []
    order_mgr = MagicMock()
    order_mgr.execution_engine = exec_engine
    return ReconciliationEngine(
        persistence=persistence,
        position_manager=pos_mgr,
        pnl_engines={},
        account_engines={},
        strategies={},
        order_manager=order_mgr,
    )


def test_old_false_positive_is_gone():
    """A closed DB trade for an OLD position (same strategy:instrument) must
    NOT error while a NEW position with the same strategy:instrument is open."""
    eng = _engine(None)
    open_pos = _open_position("P2", "gold_01", "GOLDM")
    db_trades = [
        {"trade_id": "P1", "strategy_id": "gold_01", "instrument": "GOLDM", "status": "closed"},
        {"trade_id": "P1b", "strategy_id": "gold_01", "instrument": "GOLDM", "status": "closed"},
    ]
    res = ReconciliationResult()
    eng._check_positions_vs_trades([open_pos], [], db_trades, res)
    assert res.errors == []


def test_closed_in_db_but_open_in_memory_is_error():
    """A closed trade row whose trade_id is still an open position is the real
    inconsistency and must be flagged."""
    eng = _engine(None)
    open_pos = _open_position("P9")
    db_trades = [{"trade_id": "P9", "strategy_id": "gold_01", "instrument": "GOLDM", "status": "closed"}]
    res = ReconciliationResult()
    eng._check_positions_vs_trades([open_pos], [], db_trades, res)
    assert any("P9" in e and "still open in memory" in e for e in res.errors)


def test_closed_position_missing_db_row_is_error():
    """A closed in-memory position with no DB trade row means the close was
    never persisted - genuine integrity failure."""
    eng = _engine(None)
    closed_pos = _closed_position("P3")
    res = ReconciliationResult()
    eng._check_positions_vs_trades([], [closed_pos], [], res)
    assert any("P3" in e and "no trade row" in e for e in res.errors)


def test_consistent_state_passes():
    """Closed position has its DB row, no open positions, no false positives."""
    eng = _engine(None)
    closed_pos = _closed_position("P4")
    db_trades = [{"trade_id": "P4", "strategy_id": "gold_01", "instrument": "GOLDM", "status": "closed"}]
    res = ReconciliationResult()
    eng._check_positions_vs_trades([], [closed_pos], db_trades, res)
    assert res.errors == []


def test_state_after_a_round_trip_is_consistent(tmp_db):
    """Full reconcile() smoke test: a position closed via the real close path
    must reconcile cleanly even when a new position re-opens the same
    strategy:instrument."""
    from portfolio.pnl import PNLEngine
    from portfolio.account import AccountEngine
    from execution.fee_model import MCXFeeModel
    from persistence.manager import PersistenceManager

    pers = PersistenceManager(state_path=str(tmp_db) + ".json", db_path=tmp_db)
    pm = PositionManager()
    pnl_eng = PNLEngine(fee_model=MCXFeeModel())

    # Simulate the exact DB row trade_close writes (trade_id == position_id).
    p1 = pm.open_position(
        Fill(fill_id="f1", order_id="o1", instrument="GOLDM", side="BUY",
             quantity=1, price=100.0, timestamp=time.time(), strategy_id="gold_01"),
        multiplier=10.0,
    )
    pm.close_position(
        p1.position_id,
        Fill(fill_id="f2", order_id="o2", instrument="GOLDM", side="SELL",
             quantity=1, price=110.0, timestamp=time.time(), strategy_id="gold_01"),
        "signal_exit",
    )
    trade_record = {
        "trade_id": p1.position_id,
        "strategy_id": "gold_01",
        "instrument": "GOLDM",
        "side": "LONG",
        "exit_reason": "signal_exit",
        "status": "closed",
    }
    pers.save_trade(trade_record)

    # New position re-opens the same strategy:instrument (old false positive).
    p2 = pm.open_position(
        Fill(fill_id="f3", order_id="o3", instrument="GOLDM", side="BUY",
             quantity=1, price=112.0, timestamp=time.time(), strategy_id="gold_01"),
        multiplier=10.0,
    )

    exec_engine = MagicMock()
    exec_engine._orders = {}
    exec_engine._fills = []
    order_mgr = MagicMock()
    order_mgr.execution_engine = exec_engine

    eng = ReconciliationEngine(
        persistence=pers,
        position_manager=pm,
        pnl_engines={"gold_01": pnl_eng},
        account_engines={},
        strategies={},
        order_manager=order_mgr,
    )
    res = eng.reconcile(phase="startup")
    assert not any("still open in memory" in e for e in res.errors), res.errors
    assert not any("no trade row" in e for e in res.errors), res.errors