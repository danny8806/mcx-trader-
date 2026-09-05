"""API routes vs canonical DB lineage (Phase 42/43).

Every dashboard read endpoint must return the same strategy/trade lineage that
is persisted in the canonical trading.db. The sync accessors power the async
routes, so validating them validates the API surface.
"""
import time

from dashboard.event_bus import EventBus
from dashboard.routes import trades as trades_routes
from dashboard.routes import orders as orders_routes
from dashboard.routes import positions as positions_routes

from ._harness import SIDS, open_long, open_short


def test_api_trades_match_db(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "silver_01", ts + 0.1)

    trades_routes.init(engine, EventBus(), persistence)
    resp = trades_routes._list_trades_sync()
    assert resp.get("source") in ("lifecycle", "persistence")
    api_trades = resp["trades"]
    assert len(api_trades) == 2

    for t in api_trades:
        db = persistence._db.query(
            "SELECT strategy_id, entry_signal_id, status FROM trades WHERE trade_id=?",
            (t["trade_id"],))
        assert len(db) == 1
        assert db[0]["strategy_id"] == t["strategy_id"]
        assert t["strategy_id"] in SIDS


def test_api_trade_detail_matches_db(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    trade_id = engine.position_manager.get_positions_by_strategy("gold_01")[0].trade_id

    trades_routes.init(engine, EventBus(), persistence)
    detail = trades_routes._get_trade_sync(trade_id)
    assert "error" not in detail
    assert detail["trade_id"] == trade_id
    assert detail["strategy_id"] == "gold_01"
    db = persistence._db.query("SELECT status FROM trades WHERE trade_id=?", (trade_id,))
    assert db[0]["status"] == detail.get("status", "OPEN")


def test_api_positions_lineage(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_long(engine, "gold_02", ts + 0.1)

    positions_routes.init(engine, EventBus())
    resp = positions_routes._list_positions_sync(status="open")
    assert resp["count"] == 2
    for p in resp["positions"]:
        assert p["strategy_id"] in ("gold_01", "gold_02")
        assert p["position_id"] != p.get("trade_id"), "position_id must differ from trade_id"
        db = persistence._db.query(
            "SELECT strategy_id FROM positions WHERE position_id=?", (p["position_id"],))
        assert len(db) == 1
        assert db[0]["strategy_id"] == p["strategy_id"]


def test_api_orders_lineage_via_db(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "silver_01", ts + 0.1)

    orders_routes.init(engine, EventBus())
    resp = orders_routes._list_orders_sync()
    assert resp["count"] >= 2
    for o in resp["orders"]:
        assert o["strategy_id"] in SIDS
        db = persistence._db.query(
            "SELECT trade_id FROM orders WHERE order_id=?",
            (o["order_id"],))
        if db:
            trade = persistence._db.query(
                "SELECT strategy_id FROM trades WHERE trade_id=?", (db[0]["trade_id"],))
            assert trade and trade[0]["strategy_id"] == o["strategy_id"]


def test_api_fills_lineage(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "silver_01", ts + 0.1)

    orders_routes.init(engine, EventBus())
    resp = orders_routes._list_fills_sync()
    assert resp["count"] == 2
    for f in resp["fills"]:
        assert f["strategy_id"] in SIDS
        db = persistence._db.query(
            "SELECT trade_id, strategy_id FROM fills WHERE fill_id=?", (f["fill_id"],))
        assert len(db) == 1
        assert db[0]["strategy_id"] == f["strategy_id"]


def test_api_filters_by_strategy(engine, persistence):
    ts = time.time()
    open_long(engine, "gold_01", ts)
    open_short(engine, "silver_01", ts + 0.1)

    trades_routes.init(engine, EventBus(), persistence)
    resp = trades_routes._list_trades_sync(strategy="gold_01")
    assert resp["count"] == 1
    assert resp["trades"][0]["strategy_id"] == "gold_01"