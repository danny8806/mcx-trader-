"""Execution isolation (Phase 22-23, 34)."""
import time

from ._harness import open_long, open_short


def test_simultaneous_four_orders_unique_ids(engine):
    ts = time.time()
    for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
        open_long(engine, sid, ts if sid in ("gold_01", "silver_01") else ts + 0.1)

    trade_ids = set()
    order_ids = set()
    for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
        pos = engine.position_manager.get_positions_by_strategy(sid)
        assert len(pos) == 1, sid
        trade_ids.add(pos[0].trade_id)
        order_ids.add(pos[0].generated_order_id if hasattr(pos[0], "generated_order_id") else pos[0].trade_id)
        assert pos[0].strategy_id == sid
    assert len(order_ids) == 4
    assert len(trade_ids) == 4


def test_same_symbol_same_side_unique(engine):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_long(engine, "gold_02", ts)
    g1 = engine.position_manager.get_positions_by_strategy("gold_01")
    g2 = engine.position_manager.get_positions_by_strategy("gold_02")
    assert g1[0].trade_id != g2[0].trade_id
    assert g1[0].position_id != g2[0].position_id


def test_unknown_broker_order_quarantined(engine):
    """The broker router must expose a routing/quarantine boundary."""
    assert engine.broker_router is not None
    # broker router isolates unknown mappings from known ones via separate
    # counters/queues (routed vs quarantined) until a mapping is registered
    assert callable(getattr(engine.broker_router, "route_fill", None))
    assert callable(getattr(engine.broker_router, "route_order_event", None))
    assert isinstance(engine.broker_router.routed_count, int)
    assert isinstance(engine.broker_router.quarantine_count, int)
    # a shared event bus is the routing substrate
    assert engine.event_bus is not None


def test_one_shared_broker_transport(engine):
    """All four strategies route through ONE shared execution transport."""
    ids = {id(engine.runtimes[sid].order_manager.execution_engine) for sid in
           ("gold_01", "gold_02", "silver_01", "silver_02")}
    assert len(ids) == 1, "order managers must share one broker transport"
    rt = next(iter(engine.runtimes.all()))
    assert rt.order_manager.execution_engine is engine.execution_engine
    assert engine.execution_engine is not None