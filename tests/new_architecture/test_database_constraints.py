"""Canonical DB integrity constraints (Phase 29-31, 35-37).

Latent constraint enforcement via per-connection foreign_keys=ON plus
application triggers: trades need a real entry signal, orders need a real
trade, fills need real trade+order, positions must have identity distinct
from trades. All enforced at the engine's canonical trading.db.
"""
import sqlite3
import time

import pytest

from ._harness import SIDS, open_long


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def test_foreign_keys_and_wal_enabled(engine, persistence):
    db = persistence._db
    assert db.foreign_keys_enabled() is True, "PRAGMA foreign_keys must be ON per connection"
    assert db.scalar("PRAGMA journal_mode").lower() == "wal"
    assert db.integrity_check() == ["ok"]


def test_trade_requires_entry_signal(engine, persistence):
    db = persistence._db
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO trades (trade_id, strategy_id, instrument, side, status, "
            "entry_timestamp, entry_price, exit_reason, exit_signal_id) "
            "VALUES ('t-no-sig', 'gold_01', 'GOLDM', 'LONG', 'OPEN', ?, 78000.0, "
            "'STOP_LOSS', NULL)",
            (_now(),))


def test_trade_references_existing_signal(engine, persistence):
    db = persistence._db
    db.execute("INSERT INTO signals (signal_id, signal_type, strategy_id, instrument) "
               "VALUES ('sig-x-1', 'entry', 'gold_01', 'GOLDM')")
    db.execute(
        "INSERT INTO trades (trade_id, strategy_id, instrument, side, status, "
        "entry_timestamp, entry_price, entry_signal_id) "
        "VALUES ('t-ok-sig', 'gold_01', 'GOLDM', 'LONG', 'OPEN', ?, 78000.0, 'sig-x-1')",
        (_now(),))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO trades (trade_id, strategy_id, instrument, side, status, "
                   "entry_signal_id) VALUES ('t-missing-sig', 'gold_01', 'GOLDM', 'LONG', "
                   "'OPEN', 'sig-does-not-exist')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO fills (fill_id, trade_id, order_id, strategy_id, instrument, "
                   "side, quantity, price, timestamp) "
                   "VALUES ('f-1', 't-ok-sig', 'o-1', 'gold_01', 'GOLDM', 'BUY', 1, 78000.0, ?)",
                   (_now(),))


def test_order_requires_trade(engine, persistence):
    db = persistence._db
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO orders (order_id, strategy_id, instrument, side, quantity) "
                   "VALUES ('o-no-trade', 'gold_01', 'GOLDM', 'BUY', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO orders (order_id, trade_id, strategy_id, instrument, side, quantity) "
                   "VALUES ('o-bad-trade', 't-does-not-exist', 'gold_01', 'GOLDM', 'BUY', 1)")


def test_fill_requires_lineage(engine, persistence):
    db = persistence._db
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO fills (fill_id, trade_id, order_id, strategy_id, instrument, "
                   "side, quantity, price, timestamp) "
                   "VALUES ('f-bad', 't-x', 'o-x', 'gold_01', 'GOLDM', 'BUY', 1, 78000.0, ?)",
                   (_now(),))


def test_position_identity_separate_from_trade(engine, persistence):
    db = persistence._db
    db.execute("INSERT INTO signals (signal_id, signal_type, strategy_id, instrument) "
               "VALUES ('sig-x-2', 'entry', 'gold_01', 'GOLDM')")
    db.execute("INSERT INTO trades (trade_id, strategy_id, instrument, side, status, "
               "entry_signal_id) VALUES ('t-ident', 'gold_01', 'GOLDM', 'LONG', 'OPEN', 'sig-x-2')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO positions (position_id, trade_id) VALUES ('t-ident', 't-ident')")


def test_no_orphan_lineage_after_engine_cycle(engine, persistence):
    """Engine open+close must leave zero orphan fills/orders/positions."""
    open_long(engine, "gold_01", time.time())
    db = persistence._db
    fill_orphan = db.scalar(
        "SELECT COUNT(*) FROM fills f LEFT JOIN trades t ON f.trade_id = t.trade_id "
        "WHERE t.trade_id IS NULL")
    order_orphan = db.scalar(
        "SELECT COUNT(*) FROM orders o LEFT JOIN trades t ON o.trade_id = t.trade_id "
        "WHERE t.trade_id IS NULL")
    pos_orphan = db.scalar(
        "SELECT COUNT(*) FROM positions p LEFT JOIN trades t ON p.trade_id = t.trade_id "
        "WHERE t.trade_id IS NULL")
    assert fill_orphan == 0
    assert order_orphan == 0
    assert pos_orphan == 0
    assert len(dict(db.foreign_key_check())) == 0