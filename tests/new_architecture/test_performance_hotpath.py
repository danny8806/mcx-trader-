"""Performance / hot-path guarantees (Phase 54-55, §71).

The LTP tick hot path must NOT recompute indicators, must NOT write to the
canonical DB, and must NOT generate trades when there is no position — a flat
portfolio processing thousands of ticks must be a pure in-memory no-op.
"""
import time

from ._harness import open_long, positions


def _tick(engine, price, ts):
    engine._on_tick({"instrument": "GOLDM", "ltp": price, "event_timestamp": ts})


def test_flat_portfolio_ticks_are_noop(engine, persistence):
    db = persistence._db
    base_trades = db.scalar("SELECT COUNT(*) FROM trades")
    base_events = db.scalar("SELECT COUNT(*) FROM events")
    base_fills = db.scalar("SELECT COUNT(*) FROM fills")
    base_orders = db.scalar("SELECT COUNT(*) FROM orders")
    base_processed = db.scalar("SELECT COUNT(*) FROM processed_fills")
    base_stream = engine.strategies["gold_01"]._shared_streams["fast"].snapshot()

    t0 = time.time()
    for i in range(500):
        _tick(engine, 78000.0 + i, t0 + i)
    elapsed = time.time() - t0

    assert db.scalar("SELECT COUNT(*) FROM trades") == base_trades
    assert db.scalar("SELECT COUNT(*) FROM events") == base_events
    assert db.scalar("SELECT COUNT(*) FROM fills") == base_fills
    assert db.scalar("SELECT COUNT(*) FROM orders") == base_orders
    assert db.scalar("SELECT COUNT(*) FROM processed_fills") == base_processed
    # indicators must not have been recomputed by ticks
    after_stream = engine.strategies["gold_01"]._shared_streams["fast"].snapshot()
    assert after_stream["indicator_count"] == base_stream["indicator_count"]
    assert after_stream["bar_count"] == base_stream["bar_count"]
    # all four strategies stayed flat
    for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
        assert positions(engine, sid) == []
    # hot path stays fast (500 ticks well under the 2s generosity bound)
    assert elapsed < 2.0, f"500 LTP ticks took {elapsed:.3f}s"


def test_open_position_tick_updates_mark_only(engine, persistence):
    """With an open position, ticks update LTP/mark — never the DB."""
    open_long(engine, "gold_01", time.time())
    assert len(positions(engine, "gold_01")) == 1
    db = persistence._db
    base_fills = db.scalar("SELECT COUNT(*) FROM fills")
    base_events = db.scalar("SELECT COUNT(*) FROM events")
    for i in range(300):
        _tick(engine, 78000.0 + i * 10, time.time() + i)
    assert db.scalar("SELECT COUNT(*) FROM fills") == base_fills
    assert db.scalar("SELECT COUNT(*) FROM events") == base_events
    assert len(positions(engine, "gold_01")) == 1


def test_tick_loop_pipeline_latency(engine):
    t0 = time.time()
    for i in range(1000):
        _tick(engine, 78000.0 + i, t0 + i)
    per_tick_us = (time.time() - t0) * 1_000_000.0 / 1000.0
    assert per_tick_us < 2000.0, f"tick hot path too slow: {per_tick_us:.1f}us/tick"