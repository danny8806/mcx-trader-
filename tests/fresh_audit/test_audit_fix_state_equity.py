"""Regression tests for two live-panel data-correctness fixes.

BUG-A (state desync): the Strategy Matrix showed silver_01/silver_02 as
FLAT/None while the Positions panel held open LONG positions for exactly those
strategies (confirmed live on two rapid captures + persisted system_state.json).
Root cause: the strategy object's ``position_side``/``state`` is not the
authoritative source for open positions -- the position manager is.  On restore
the strategy state was persisted FLAT while the position manager correctly kept
the open position, so every consumer of the strategy snapshot (HTTP /api/strategies
and the WS engine_state) reported FLAT.

Fix: _reconcile_open_position() derives the reported strategy state from the
open position held by the position manager, applied in the HTTP + WS serializers.

BUG-B (equity baseline): analytics equity/drawdown curves hardcoded a fictional
starting_equity of 1,000,000 while the frontend subtracts account starting_capital
(1,200,000) to plot net P&L.  For gold_01 that produced -200,803.97 instead of
the true -803.97 (off by -200,000).  Fix: the analytics baseline is configurable
and the dashboard seeds it from account.starting_capital so the equity curve and
the subtracted baseline agree.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analytics import routes as analytics_routes
from analytics.performance import PerformanceEngine
from analytics.schema import init_analytics_db
from portfolio.position_manager import Position, PositionSide


# ---------------------------------------------------------------------------
# BUG-A -- _reconcile_open_position
# ---------------------------------------------------------------------------

class _FakeEngine:
    def __init__(self, positions):
        self.position_manager = _FakePM(positions)


class _FakePM:
    def __init__(self, positions):
        self._positions = positions

    def get_positions_by_strategy(self, strategy_id):
        return [p for p in self._positions if p.strategy_id == strategy_id]


def _mk_pos(strategy_id, side, stop=235485.0):
    _pos = Position(
        position_id=str(uuid.uuid4()),
        strategy_id=strategy_id,
        instrument="SILVERM",
        side=side,
        quantity=1,
        average_entry=236489.0,
        entry_timestamp=1000.0,
        entry_fill_ids=[str(uuid.uuid4())],
        stop_price=stop,
        multiplier=5.0,
    )
    return _pos


def _seed_analytics_db(tmp_path):
    db = str(tmp_path / "analytics.db")
    init_analytics_db(db)
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trades_analytics
           (trade_id, strategy_id, instrument, side, status, entry_quantity,
            filled_quantity, remaining_quantity, average_entry_price,
            average_exit_price, exit_quantity, net_pnl, gross_pnl, fees,
            closed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "t-gold", "gold_01", "GOLDM", "LONG", "CLOSED", 1, 1, 0,
            150768.0, 150717.0, 1, -803.97, -510.0, 293.97, 1788354009.0,
        ),
    )
    conn.commit()
    conn.close()
    return db


def test_reconcile_sets_long_state_when_open_position(monkeypatch):
    """A strategy holding an open LONG position must NOT serialize as FLAT."""
    pos = _mk_pos("silver_01", PositionSide.LONG)
    from dashboard.routes import strategies as strat_routes
    monkeypatch.setattr(strat_routes, "_engine", _FakeEngine([pos]))

    snap = {"strategy_id": "silver_01", "state": "flat", "position_side": None}
    out = strat_routes._reconcile_open_position("silver_01", snap)
    assert out["position_side"] == "LONG"
    assert out["state"] == "long_position"
    assert out["stop_price"] == 235485.0


def test_reconcile_sets_short_state_when_open_position(monkeypatch):
    from dashboard.routes import strategies as strat_routes
    pos = _mk_pos("silver_02", PositionSide.SHORT)
    monkeypatch.setattr(strat_routes, "_engine", _FakeEngine([pos]))
    snap = {"strategy_id": "silver_02", "state": "flat", "position_side": None}
    out = strat_routes._reconcile_open_position("silver_02", snap)
    assert out["position_side"] == "SHORT"
    assert out["state"] == "short_position"


def test_reconcile_keeps_flat_when_no_open_position(monkeypatch):
    from dashboard.routes import strategies as strat_routes
    monkeypatch.setattr(strat_routes, "_engine", _FakeEngine([]))
    snap = {"strategy_id": "gold_01", "state": "flat", "position_side": None}
    out = strat_routes._reconcile_open_position("gold_01", snap)
    assert out["position_side"] is None
    assert out["state"] == "flat"


def test_reconcile_no_engine_is_noop():
    from dashboard.routes import strategies as strat_routes
    # No engine set
    assert strat_routes._engine is None or True
    original = strat_routes._engine
    strat_routes._engine = None
    try:
        snap = {"strategy_id": "gold_01", "state": "flat", "position_side": None}
        out = strat_routes._reconcile_open_position("gold_01", snap)
        assert out["state"] == "flat"
        assert out["position_side"] is None
    finally:
        strat_routes._engine = original


# ---------------------------------------------------------------------------
# BUG-B -- analytics equity baseline
# ---------------------------------------------------------------------------

def test_equity_curve_uses_configured_starting_capital(tmp_path, monkeypatch):
    """gold_01 equity must terminate at baseline - net_pnl (true P&L), and the
    point-0 baseline must equal account starting_capital (1,200,000)."""
    db = _seed_analytics_db(tmp_path)
    pe = PerformanceEngine(db)
    monkeypatch.setattr(analytics_routes, "_performance_engine", pe)
    monkeypatch.setattr(analytics_routes, "_default_starting_equity", 1_200_000)

    import asyncio
    resp = asyncio.run(analytics_routes.get_strategy_equity("gold_01"))
    curve = resp["equity_curve"]
    assert curve[0]["equity"] == pytest.approx(1_200_000.0)
    assert curve[-1]["equity"] == pytest.approx(1_200_000.0 - 803.97)


def test_equity_net_pnl_consistent_with_frontend_subtraction(tmp_path, monkeypatch):
    """Net P&L -- as the frontend computes it (final_equity - starting_capital) --
    must equal the true realized net P&L with the corrected baseline."""
    db = _seed_analytics_db(tmp_path)
    pe = PerformanceEngine(db)
    monkeypatch.setattr(analytics_routes, "_performance_engine", pe)
    monkeypatch.setattr(analytics_routes, "_default_starting_equity", 1_200_000)

    import asyncio
    resp = asyncio.run(analytics_routes.get_strategy_equity("gold_01"))
    curve = resp["equity_curve"]
    final_equity = curve[-1]["equity"]
    starting_capital = 1_200_000.0
    chart_net_pnl = final_equity - starting_capital
    assert chart_net_pnl == pytest.approx(-803.97, abs=0.01)