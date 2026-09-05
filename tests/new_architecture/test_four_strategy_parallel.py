"""Four-strategy parallel execution acceptance (§51-§53, §59, §65).

Proves that a multi-strategy engine handles simultaneous cross-instrument
trades, same-instrument same-side trades, and same-instrument opposite-side
trades with fully independent per-strategy state and correct DB lineage.
"""
import time

from portfolio.position_manager import PositionSide, PositionStatus
from strategies.types import SignalType

from ._harness import SIDS, open_long, open_short, positions


def _close(engine, sid, reason, price, ts, signal_id):
    engine.strategies[sid].last_exit_reason = reason
    from ._harness import exit_signal
    sig = exit_signal(sid, SignalType.SHORT, price, reason, ts, signal_id)
    engine.execution_engine.update_price("GOLDM" if sid.startswith("gold") else "SILVERM", price)
    engine._process_signal(sig)


def test_four_simultaneous_cross_instrument_trades(engine, persistence):
    ts = time.time()
    for sid in SIDS:
        open_long(engine, sid, ts)

    trade_ids = set()
    for sid in SIDS:
        pos = positions(engine, sid)
        assert len(pos) == 1, f"{sid}: expected 1 open position"
        trade_ids.add(pos[0].trade_id)
        assert pos[0].side == PositionSide.LONG
        assert pos[0].status == PositionStatus.OPEN
        rows = persistence._db.query(
            "SELECT trade_id, strategy_id, side, status FROM trades "
            "WHERE trade_id=? AND status='OPEN'", (pos[0].trade_id,))
        assert len(rows) == 1, f"{sid}: trade not persisted as OPEN"
        assert rows[0]["strategy_id"] == sid
    assert len(trade_ids) == 4, "trades must have distinct identity"


def test_same_instrument_same_side_independent(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_long(engine, "gold_02", ts)

    g1 = positions(engine, "gold_01")
    g2 = positions(engine, "gold_02")
    assert len(g1) == 1 and len(g2) == 1
    assert g1[0].trade_id != g2[0].trade_id
    assert g1[0].position_id != g2[0].position_id
    for pos in (g1[0], g2[0]):
        assert pos.instrument == "GOLDM"
        assert pos.side == PositionSide.LONG


def test_same_instrument_opposite_side_both_live(engine):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "gold_02", ts)

    g1 = positions(engine, "gold_01")
    g2 = positions(engine, "gold_02")
    assert len(g1) == 1
    assert len(g2) == 1
    assert g1[0].side == PositionSide.LONG
    assert g2[0].side == PositionSide.SHORT
    assert g1[0].trade_id != g2[0].trade_id


def test_silver_and_gold_simultaneous(engine):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "silver_01", ts)
    open_long(engine, "silver_02", ts)

    for sid in SIDS:
        pos = positions(engine, sid)
        if sid == "gold_02":
            assert pos == []
        else:
            assert len(pos) == 1


def test_reversal_closes_own_trade_only(engine, persistence):
    ts = time.time()
    for sid in SIDS:
        open_long(engine, sid, ts)
    ts2 = ts + 1.0
    for sid in SIDS:
        _close(engine, sid, "long_reversal", 78100.0 if sid.startswith("gold") else 239200.0,
               ts2, f"sig-rev-{sid}")

    for sid in SIDS:
        rows = persistence._db.query(
            "SELECT trade_id, side, status, exit_reason, exit_signal_id FROM trades "
            "WHERE strategy_id=? ORDER BY id", (sid,))
        assert len(rows) == 1, sid
        assert rows[0]["status"] == "CLOSED", sid
        assert rows[0]["exit_reason"] == "long_reversal", sid
        assert rows[0]["exit_signal_id"] == f"sig-rev-{sid}", sid
        assert positions(engine, sid) == []


def test_new_entry_after_reversal_creates_new_trade(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    _close(engine, "gold_01", "long_reversal", 78100.0, ts + 1.0, "sig-rev-again")
    open_long(engine, "gold_01", ts + 2.0)
    rows = persistence._db.query(
        "SELECT status FROM trades WHERE strategy_id='gold_01' ORDER BY id")
    assert [r["status"] for r in rows] == ["CLOSED", "OPEN"]


def test_runs_distinct_objects_per_strategy(engine):
    rts = {sid: engine.runtimes[sid] for sid in SIDS}
    assert len({id(rt.lifecycle) for rt in rts.values()}) == 4
    assert len({id(rt.order_manager) for rt in rts.values()}) == 4
    assert len({id(rt.position_manager) for rt in rts.values()}) == 4
    # shared infra is a single object
    g15 = engine.strategies["gold_01"]._shared_streams["mid"]
    assert engine.strategies["gold_02"]._shared_streams["fast"] is g15