"""Strategy runtime isolation (Phase 3, 22, 38)."""
import time

from strategies.types import SignalType

from ._harness import SIDS, positions


def test_runtime_count_is_four(engine):
    assert len(engine.runtimes.all()) == 4
    assert engine.runtimes.strategy_ids == SIDS


def test_runtimes_object_identity_all_pairs(engine):
    rt = {sid: engine.runtimes[sid] for sid in SIDS}
    for a in SIDS:
        for b in SIDS:
            if a == b:
                continue
            assert rt[a].lifecycle is not rt[b].lifecycle, f"{a} shares lifecycle with {b}"
            assert rt[a].order_manager is not rt[b].order_manager, f"{a} shares order_manager with {b}"
            assert rt[a].position_manager is not rt[b].position_manager, f"{a} shares position_manager with {b}"
            assert rt[a].strategy is not rt[b].strategy, f"{a} shares strategy with {b}"


def test_mutable_state_isolated_per_strategy(engine):
    g1 = engine.strategies["gold_01"]
    g2 = engine.strategies["gold_02"]
    g1._bars_processed = 10
    g2._bars_processed = 42
    assert g1._bars_processed == 10
    assert g2._bars_processed == 42
    assert g1 is not g2


def test_same_instrument_never_shares_position(engine):
    ts = time.time()
    from ._harness import open_long
    open_long(engine, "gold_01", ts)
    open_long(engine, "gold_02", ts + 0.1)
    g1 = positions(engine, "gold_01")
    g2 = positions(engine, "gold_02")
    assert g1[0].position_id != g2[0].position_id
    assert g1[0].trade_id != g2[0].trade_id


def test_execution_managers_isolated(engine):
    om = {sid: engine.runtimes[sid].order_manager for sid in SIDS}
    assert len({id(o) for o in om.values()}) == 4
    # ...but all order managers share one execution transport
    assert len({id(o.execution_engine) for o in om.values()}) == 1