"""
ADVERSARIAL TEST: Lifecycle Persistence Failure
================================================
CRITICAL FINDING: lifecycle._persist_trade() fails silently because
TradeContext.snapshot() does not produce a 'side' column required by
the DB schema's trades table.

Every call to lifecycle.create_trade_from_signal(), register_entry_fill(),
register_exit_fill(), close_trade(), etc. tries to persist via _persist_trade().
ALL of these persist calls are SILENTLY FAILING.

This means:
1. The lifecycle in-memory state is authoritative
2. The DB state comes ONLY from trade_close_manager.close_position()
3. These use DIFFERENT trade_ids (lifecycle.trade_id vs position.position_id)
4. On restart, restore_from_db() reads DB trades which have position.position_id
   as trade_id, NOT lifecycle.trade_id
5. The lifecycle's identity maps are rebuilt from DB, but with WRONG trade_ids
"""
import sys
import os
import time
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import TradeLifecycleManager, TradeStatus
from portfolio.position_manager import PositionManager
from persistence.manager import PersistenceManager
from strategies.types import Signal, SignalType


def _make_signal(signal_type="LONG", strategy_id="gold_01", instrument="GOLDM"):
    return Signal(
        signal_type=SignalType(signal_type),
        instrument=instrument,
        strategy_id=strategy_id,
        timestamp=time.time(),
        trigger_price=100000.0,
        stop_price=99000.0,
        quantity=1,
    )


def _get_db_trades(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_db_fills(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM fills").fetchall()
    conn.close()
    return [dict(r) for r in rows]


class TestLifecyclePersistenceBroken:
    """
    PROVE: Every lifecycle persist call fails silently because
    the snapshot() output doesn't match the DB schema.
    """

    def test_create_trade_persist_fails(self):
        """create_trade_from_signal calls _persist_trade which should fail."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        # Check DB
        db_trades = _get_db_trades(db_path)
        print(f"\n  DB trades after create: {len(db_trades)}")
        for t in db_trades:
            print(f"    trade_id={t['trade_id']}, status={t['status']}")

        # If persist works: 1 row with lifecycle_trade.trade_id
        # If persist fails: 0 rows
        if len(db_trades) == 0:
            print("  CONFIRMED: lifecycle create_trade_from_signal persist FAILED silently")
            print("  The trade exists ONLY in memory, NOT in DB")
        else:
            print("  lifecycle persist succeeded")

    def test_close_trade_persist_fails(self):
        """close_trade calls _persist_trade which should fail."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )
        lifecycle.close_trade(
            trade_id=lifecycle_trade.trade_id,
            gross_pnl=500.0, charges=5.0, net_pnl=495.0,
        )

        # Check DB
        db_trades = _get_db_trades(db_path)
        print(f"\n  DB trades after close: {len(db_trades)}")

        if len(db_trades) == 0:
            print("  CONFIRMED: lifecycle close_trade persist FAILED silently")
        elif len(db_trades) == 1:
            t = db_trades[0]
            print(f"  One trade in DB: trade_id={t['trade_id']}, status={t['status']}, net_pnl={t['net_pnl']}")
            if t["trade_id"] == lifecycle_trade.trade_id:
                print("  trade_id matches lifecycle (correct)")
            else:
                print(f"  MISMATCH: lifecycle has {lifecycle_trade.trade_id}, DB has {t['trade_id']}")
        else:
            print(f"  UNEXPECTED: {len(db_trades)} trades in DB")


class TestDualPersistenceConflict:
    """
    PROVE: When both trade_close_manager AND lifecycle persist, they write
    to DIFFERENT trade_id rows, creating split-brain in DB.
    """

    def test_two_persistence_paths_create_two_rows(self):
        """Simulate the real flow: trade_close_manager + lifecycle both persist."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)
        position_mgr = PositionManager()

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        # Simulate position creation
        from execution.paper_broker import Fill
        fill = Fill(
            fill_id="F-ENTRY", order_id="O-ENTRY", instrument="GOLDM",
            side="BUY", quantity=1, price=100000.0, timestamp=time.time(),
            strategy_id="gold_01", multiplier=1,
            entry_signal_id=sig.signal_id, trade_id=None,
        )
        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
        )

        # Register in lifecycle
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=fill.fill_id, price=fill.price, timestamp=fill.timestamp,
        )
        lifecycle.register_position(lifecycle_trade.trade_id, position.position_id)

        # PATH 1: trade_close_manager persists using position.position_id
        # (simulating what trade_close.py does)
        persistence.save_fill({
            "fill_id": fill.fill_id, "order_id": fill.order_id,
            "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "price": 100000.0,
            "timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
            "entry_signal_id": sig.signal_id,
            "trade_id": position.position_id,
        })

        from execution.paper_broker import Fill as FillCls
        exit_fill = FillCls(
            fill_id="F-EXIT", order_id="O-EXIT", instrument="GOLDM",
            side="SELL", quantity=1, price=101000.0, timestamp=time.time(),
            strategy_id="gold_01", multiplier=1,
            entry_signal_id=None, trade_id=None,
        )

        # trade_close_manager persists trade with position.position_id
        persistence.save_trade({
            "trade_id": position.position_id,
            "strategy_id": "gold_01", "instrument": "GOLDM", "side": "LONG",
            "entry_timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
            "entry_price": 100000.0,
            "exit_timestamp": datetime.fromtimestamp(exit_fill.timestamp, tz=timezone.utc).isoformat(),
            "exit_price": 101000.0,
            "quantity": 1, "multiplier": 1.0,
            "gross_pnl": 1000.0, "charges": 10.0, "net_pnl": 990.0,
            "exit_reason": "signal_exit", "status": "closed",
            "entry_signal_id": sig.signal_id, "exit_signal_id": "",
        })

        # PATH 2: lifecycle persists (this will fail silently due to missing 'side')
        lifecycle.register_exit_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=exit_fill.fill_id, price=exit_fill.price,
            timestamp=exit_fill.timestamp,
        )
        lifecycle.close_trade(
            trade_id=lifecycle_trade.trade_id,
            gross_pnl=1000.0, charges=10.0, net_pnl=990.0,
        )

        # INDEPENDENT DB AUDIT
        db_trades = _get_db_trades(db_path)
        db_fills = _get_db_fills(db_path)

        print(f"\n  DB trades: {len(db_trades)}")
        for t in db_trades:
            print(f"    trade_id={t['trade_id']}, status={t['status']}, net_pnl={t['net_pnl']}")
        print(f"  DB fills: {len(db_fills)}")
        for f in db_fills:
            print(f"    fill_id={f['fill_id']}, trade_id={f['trade_id']}")

        # Expected if lifecycle persist works: 2 trade rows
        # Expected if lifecycle persist fails: 1 trade row
        lifecycle_key_exists = any(t["trade_id"] == lifecycle_trade.trade_id for t in db_trades)
        position_key_exists = any(t["trade_id"] == position.position_id for t in db_trades)

        print(f"\n  lifecycle_trade.trade_id in DB: {lifecycle_key_exists}")
        print(f"  position.position_id in DB: {position_key_exists}")
        print(f"  lifecycle_trade.trade_id = {lifecycle_trade.trade_id}")
        print(f"  position.position_id     = {position.position_id}")
        print(f"  Are they the same? {lifecycle_trade.trade_id == position.position_id}")

        if lifecycle_trade.trade_id != position.position_id:
            if lifecycle_key_exists and position_key_exists:
                print("\n  CONFIRMED SPLIT-BRAIN: Two different trades in DB")
            elif position_key_exists and not lifecycle_key_exists:
                print("\n  LIFECYCLE PERSIST BROKEN: Only position trade in DB")
                print("  Lifecycle in-memory state cannot be recovered from DB on restart")


class TestRestoreFromDbIdentityMismatch:
    """
    PROVE: After restart, restore_from_db() reads trades with position.position_id
    as trade_id. But the lifecycle's identity maps were built from lifecycle.trade_id.
    This means restore creates TradeContext objects with trade_id = position.position_id,
    NOT lifecycle.trade_id.
    """

    def test_restore_uses_position_id_as_trade_id(self):
        """DB stores position.position_id as trade_id. Restore reads that."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)

        # Manually persist a trade using position.position_id (as trade_close_manager does)
        test_trade_id = "POSITION-ID-TEST-001"
        test_signal_id = "SIGNAL-TEST-001"
        persistence.save_trade({
            "trade_id": test_trade_id,
            "strategy_id": "gold_01", "instrument": "GOLDM", "side": "LONG",
            "entry_timestamp": "2026-09-04T10:00:00+00:00",
            "entry_price": 100000.0,
            "exit_timestamp": "2026-09-04T11:00:00+00:00",
            "exit_price": 101000.0,
            "quantity": 1, "multiplier": 1.0,
            "gross_pnl": 1000.0, "charges": 10.0, "net_pnl": 990.0,
            "exit_reason": "signal_exit", "status": "closed",
            "entry_signal_id": test_signal_id, "exit_signal_id": "",
        })

        # Now create a fresh lifecycle and restore from DB
        lifecycle = TradeLifecycleManager(persistence=persistence)
        lifecycle.restore_from_db()

        # Check what trade_id the restored trade has
        all_trades = lifecycle.get_all_trades()
        print(f"\n  Trades after restore: {len(all_trades)}")
        for t in all_trades:
            print(f"    trade_id={t.trade_id}, entry_signal_id={t.entry_signal_id}, status={t.status}")

        assert len(all_trades) == 1
        restored = all_trades[0]
        assert restored.trade_id == test_trade_id, \
            f"Restored trade_id should be {test_trade_id}, got {restored.trade_id}"
        assert restored.entry_signal_id == test_signal_id, \
            f"Restored entry_signal_id should be {test_signal_id}, got {restored.entry_signal_id}"

        # Check signal resolution
        resolved = lifecycle.resolve_trade_from_signal(test_signal_id)
        assert resolved is not None, "Signal should resolve to trade after restore"
        assert resolved.trade_id == test_trade_id

        print(f"  Restore correctly used DB trade_id: {restored.trade_id}")
