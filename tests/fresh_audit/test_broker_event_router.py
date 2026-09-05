"""MISSION §39–§40 — BrokerEventRouter: explicit broker event routing.

Acceptance:
  §39  Every broker event resolves via explicit broker_order_id ->
       strategy_id mapping — NEVER by symbol/side/latest order.
  §39  Unmappable or conflicting broker events are quarantined and never
       acted upon ("do not guess").
  §40  The broker_order_id -> order_id -> trade_id -> strategy_id mapping
       lives in canonical persistence and survives process restart.
"""
import time

import pytest

from execution.paper_broker import Fill, Order, OrderState
from execution.broker_router import BrokerEventRouter, BrokerOrderMapping


def _fill(order_id, strategy_id, trade_id="t1", fill_id=None, instrument="GOLDM"):
    return Fill(
        fill_id=fill_id or f"f-{order_id}",
        order_id=order_id, instrument=instrument, side="BUY", quantity=1,
        price=100.0, timestamp=time.time(), strategy_id=strategy_id,
        trade_id=trade_id,
    )


class _Collector:
    def __init__(self):
        self.calls = []

    def __call__(self, fill, entry_signal_id=None, is_exit=None):
        self.calls.append((fill, entry_signal_id, is_exit))


# ── registration + resolution ─────────────────────────────────────────────

def test_register_and_resolve_mapping():
    r = BrokerEventRouter()
    r.register_from_kwargs("o1", "o1", "t1", "gold_01", "GOLDM")
    m = r.resolve("o1")
    assert m is not None
    assert m.strategy_id == "gold_01"
    assert m.trade_id == "t1"
    assert m.order_id == "o1"
    assert r.resolve_strategy("o1") == "gold_01"
    assert r.resolve("nope") is None
    assert r.resolve_strategy("nope") is None


def test_register_from_order():
    order = Order(order_id="o9", strategy_id="gold_02", instrument="GOLDM",
                  side="BUY", quantity=1, trade_id="t9")
    r = BrokerEventRouter()
    r.register_from_order(order)
    m = r.resolve("o9")
    assert m.strategy_id == "gold_02"
    assert m.trade_id == "t9"
    assert m.broker_order_id == order.order_id


def test_register_mapping_direct():
    r = BrokerEventRouter()
    r.register_mapping(BrokerOrderMapping(
        broker_order_id="o5", order_id="o5", trade_id="t5", strategy_id="silver_02",
        instrument="SILVERM"))
    assert r.resolve("o5").strategy_id == "silver_02"


# ── routing ───────────────────────────────────────────────────────────────

def test_route_fill_routes_to_correct_strategy():
    r = BrokerEventRouter()
    r.register_from_kwargs("o1", "o1", "t1", "gold_01", "GOLDM")
    r.register_from_kwargs("o2", "o2", "t2", "gold_02", "GOLDM")
    collector = _Collector()
    assert r.route_fill(_fill("o1", "gold_01", "t1"), collector,
                        entry_signal_id="s1", is_exit=False) is True
    assert r.route_fill(_fill("o2", "gold_02", "t2"), collector,
                        entry_signal_id="s2", is_exit=False) is True
    assert [c[1] for c in collector.calls] == ["s1", "s2"]
    assert r.routed_count == 2


def test_unmappable_fill_quarantined_and_not_acted_upon():
    r = BrokerEventRouter()
    collector = _Collector()
    assert r.route_fill(_fill("unknown", "gold_01"), collector) is False
    assert len(collector.calls) == 0
    assert r.quarantine_count == 1
    assert r._quarantined_events[0]["reason"] == "unmappable_broker_order"


def test_fill_strategy_mismatch_quarantined():
    """Mapping says gold_02, fill claims gold_01 — quarantine, never guess."""
    r = BrokerEventRouter()
    r.register_from_kwargs("o1", "o1", "t2", "gold_02", "GOLDM")
    collector = _Collector()
    assert r.route_fill(_fill("o1", "gold_01", "t2"), collector) is False
    assert len(collector.calls) == 0
    ev = r._quarantined_events[0]
    assert ev["reason"] == "fill_strategy_mismatch"
    assert ev["details"]["mapped_strategy"] == "gold_02"
    assert ev["details"]["fill_strategy"] == "gold_01"


def test_fill_without_order_id_quarantined():
    r = BrokerEventRouter()
    collector = _Collector()
    f = _fill("o1", "gold_01")
    f.order_id = None
    assert r.route_fill(f, collector) is False
    assert len(collector.calls) == 0
    assert r._quarantined_events[0]["reason"] == "fill_without_order_id"


def test_route_order_event_by_mapping():
    r = BrokerEventRouter()
    r.register_from_kwargs("o1", "o1", "t1", "gold_01", "GOLDM")
    seen = []
    assert r.route_order_event("o1", lambda m: seen.append(m.strategy_id)) is True
    assert seen == ["gold_01"]
    assert r.route_order_event("o-unknown", lambda m: seen.append(m)) is False
    assert len(seen) == 1
    assert r.quarantine_count == 1


# ── durability / restart (§40) ────────────────────────────────────────────

@pytest.fixture()
def _persistence(tmp_path):
    from persistence.manager import PersistenceManager
    pm = PersistenceManager(
        state_path=str(tmp_path / "data" / "db" / "system_state.json"),
        db_path=str(tmp_path / "data" / "db" / "trading.db"),
    )
    yield pm
    try:
        pm.close()
    except Exception:
        pass


def test_mapping_survives_restart(_persistence):
    r1 = BrokerEventRouter(persistence=_persistence)
    r1.register_from_kwargs("o1", "o1", "t1", "gold_01", "GOLDM")
    r1.register_from_kwargs("o2", "o2", "t2", "gold_02", "GOLDM")
    assert _persistence.get_broker_order_mappings() and True

    # Simulate process restart: a brand-new router restores from the same db.
    r2 = BrokerEventRouter(persistence=_persistence)
    restored = r2.restore()
    assert restored == 2
    assert r2.resolve_strategy("o1") == "gold_01"
    assert r2.resolve("o2").trade_id == "t2"


def test_quarantine_persisted_to_db(_persistence):
    r = BrokerEventRouter(persistence=_persistence)
    collector = _Collector()
    r.route_fill(_fill("unknown-order", "gold_01"), collector)
    rows = _persistence.get_quarantine_records()
    assert any(row["reason"] == "unmappable_broker_order" for row in rows)


def test_restore_absent_persistence_is_noop():
    r = BrokerEventRouter(persistence=None)
    assert r.restore() == 0