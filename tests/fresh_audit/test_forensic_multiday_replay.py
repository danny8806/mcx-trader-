"""Phase 20 - multi-day realistic replay across the full lifecycle.

Simulates ~5 trading sessions for two strategies (gold_01 on GOLDM,
silver_01 on SILVERM) with LONG and SHORT trades opening and closing each
day. Verifies with the REAL collaborators (PersistenceManager + TradeLedger +
TradeCloseManager):

- every closed trade is written to trading.db with P&L that recomputes
  independently to the same values;
- every trade is recorded exactly once in analytics.db (open then closed);
- each trade has exactly one entry leg and one exit leg (no dupes across
  the replay, even if a fill is replayed);
- final gross/net reconcile to the per-trade sums.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analytics.trade_ledger import TradeLedger
from analytics.schema import init_analytics_db
from portfolio.position_manager import PositionManager, Position, PositionSide
from portfolio.pnl import PNLEngine
from execution.paper_broker import Fill
from execution.fee_model import MCXFeeModel
from persistence.manager import PersistenceManager
from core.trade_close import TradeCloseManager


def _fees(entry, exitp, qty, mult, side):
    return MCXFeeModel().calculate(entry, exitp, qty, mult, side=side).total


def _expected_net(side, entry, exitp, qty, mult):
    gross = (exitp - entry) * qty * mult if side == "LONG" else (entry - exitp) * qty * mult
    return gross - _fees(entry, exitp, qty, mult, side)


def _scenario():
    # 5 days x (strategy, instrument, side, entry, exit, mult, qty, trade_id)
    days = []
    gmult, smult = 10.0, 5.0
    n = 0
    for d in range(5):
        day = []
        for (strat, inst, side, c0, c1, mult) in [
            ("gold_01", "GOLDM", "LONG", 150700.0 + 40*d, 150800.0 + 40*d, gmult),
            ("gold_01", "GOLDM", "SHORT", 150900.0 + 50*d, 150820.0 + 50*d, gmult),
            ("silver_01", "SILVERM", "LONG", 236000.0 + 100*d, 236150.0 + 100*d, smult),
            ("silver_01", "SILVERM", "SHORT", 236400.0 + 120*d, 236300.0 + 120*d, smult),
        ]:
            n += 1
            entry = c0 + 0.5
            exitp = c1 + 0.5
            day.append((f"day{d}t{n}", strat, inst, side, entry, exitp, mult, 1))
        days.append(day)
    return days


@pytest.fixture()
def env(tmp_path):
    tl_db = str(tmp_path / "analytics.db")
    init_analytics_db(tl_db)
    tl = TradeLedger(db_path=tl_db)
    pm = PersistenceManager(str(tmp_path / "state.json"), str(tmp_path / "trading.db"))
    pnl = PNLEngine(fee_model=MCXFeeModel())
    posmgr = PositionManager()

    def make_tcm():
        return TradeCloseManager(position_manager=posmgr, pnl_engines={"gold_01": pnl, "silver_01": pnl},
                                 account_engines={}, global_account=None,
                                 risk_engine=None, persistence=pm, event_store=None,
                                 telegram=None, trade_ledger=tl)

    return tl, pm, pnl, posmgr, make_tcm, tmp_path


def test_five_day_replay_all_closed_reconcile(env):
    tl, pm, pnl, posmgr, make_tcm, tmp_path = env
    days = _scenario()
    total_net = 0.0
    trade_ids = set()
    ts = 2600000.0
    for day in days:
        for (tid, strat, inst, side, entry, exitp, mult, qty) in day:
            assert tid not in trade_ids
            trade_ids.add(tid)
            # ---- OPEN ----
            tl.create_trade(strategy_id=strat, instrument=inst, side=side,
                            entry_quantity=qty, signal_time=ts, trigger_price=entry,
                            stop_price=entry - 100, multiplier=mult,
                            trade_id=tid, position_id=tid)
            tl.record_fill(tid, f"{tid}-e", f"{tid}-o", "BUY" if side == "LONG" else "SELL",
                           qty, entry, ts + 1, is_entry=True)
            pos = Position(position_id=tid, strategy_id=strat, instrument=inst,
                           side=PositionSide.LONG if side == "LONG" else PositionSide.SHORT,
                           quantity=qty, average_entry=entry, entry_timestamp=ts + 1,
                           entry_fill_ids=[f"{tid}-e"], multiplier=mult)
            posmgr._positions[tid] = pos
            # ---- CLOSE via the real manager this time ----
            tcm = make_tcm()
            ext = Fill(fill_id=f"{tid}-x", order_id=f"{tid}-xo", instrument=inst,
                       side="SELL" if side == "LONG" else "BUY", quantity=qty,
                       price=exitp, timestamp=ts + 300, strategy_id=strat, multiplier=mult)
            tcm._pnl_engines[strat] = pnl
            ok = tcm.close_position(fill=ext, position=pos, strategy_id=strat,
                                    multiplier=mult, exit_reason="replay_close")
            assert ok is True
            total_net += _expected_net(side, entry, exitp, qty, mult)
            ts += 500

    # trading.db closed rows
    conn = sqlite3.connect(str(tmp_path / "trading.db"))
    rows = conn.execute("SELECT trade_id,gross_pnl,net_pnl,charges,status FROM trades").fetchall()
    conn.close()
    assert len(rows) == len(days) * 4 == 20
    closed = {r[0]: (r[1], r[2], r[3]) for r in rows if r[4] == "closed"}
    assert len(closed) == 20
    for (tid, strat, inst, side, entry, exitp, mult, qty) in [x for d in days for x in d]:
        g, net, ch = closed[tid]
        exp_net = _expected_net(side, entry, exitp, qty, mult)
        exp_gross = (exitp - entry) * qty * mult if side == "LONG" else (entry - exitp) * qty * mult
        assert net == pytest.approx(exp_net)
        assert g == pytest.approx(exp_gross)
        assert ch == pytest.approx(exp_gross - exp_net)  # charges = gross - net

    # analytics: all closed, one entry + one exit leg each
    for tid in trade_ids:
        t = tl.get_trade(tid)
        assert t.status == "CLOSED"
        legs = tl.get_legs_for_trade(tid)
        assert len(legs) == 2
        assert len([l for l in legs if l.is_entry]) == 1
        assert len([l for l in legs if not l.is_entry]) == 1


def test_five_day_replay_fill_replay_does_not_duplicate_leg(env):
    """Phase 15/20 - replay a fill mid-session; it must not create a 2nd leg."""
    tl, pm, pnl, posmgr, make_tcm, tmp_path = env
    tid = "replaydup"
    side = "LONG"
    mult = 10.0
    tl.create_trade(strategy_id="gold_01", instrument="GOLDM", side=side,
                    entry_quantity=1, signal_time=100.0, trigger_price=1000.0,
                    stop_price=990.0, multiplier=mult, trade_id=tid, position_id=tid)
    l1 = tl.record_fill(tid, f"{tid}-e", f"{tid}-o", "BUY", 1, 1000.0, 101.0, is_entry=True)
    l2 = tl.record_fill(tid, f"{tid}-e", f"{tid}-o", "BUY", 1, 1000.0, 101.0, is_entry=True)
    assert l1.leg_id == l2.leg_id
    legs = tl.get_legs_for_trade(tid)
    assert len(legs) == 1
    assert tl.get_trade(tid).entry_quantity == 1