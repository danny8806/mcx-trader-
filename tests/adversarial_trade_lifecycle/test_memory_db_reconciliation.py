"""
ADVERSARIAL TEST: Memory vs DB Reconciliation (Phase 17)
========================================================
Independently compare lifecycle in-memory state with DB records.
No application helper methods — raw SQLite queries only.
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


def _make_signal():
    return Signal(
        signal_type=SignalType.LONG, instrument="GOLDM",
        strategy_id="gold_01", timestamp=time.time(),
        trigger_price=100000.0, stop_price=99000.0, quantity=1,
    )


def _raw_db_query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class TestMemoryVsDbLifecycle:
    """
    Compare lifecycle in-memory state with DB records.
    Uses raw SQL — no application ORM methods.
    """

    def test_lifecycle_trade_not_in_db(self):
        """
        CRITICAL: Lifecycle trade exists in memory but NOT in DB
        because _persist_trade() fails silently (missing 'side' column).
        """
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )

        # Memory state
        mem_trade = lifecycle.get_trade(ctx.trade_id)
        assert mem_trade is not None, "Trade should exist in memory"
        assert mem_trade.status == TradeStatus.OPEN.value

        # DB state (raw SQL)
        db_trades = _raw_db_query(db_path, "SELECT * FROM trades")
        db_fills = _raw_db_query(db_path, "SELECT * FROM fills")

        print(f"\n  Memory: trade_id={mem_trade.trade_id}, status={mem_trade.status}")
        print(f"  DB trades: {len(db_trades)}")
        print(f"  DB fills: {len(db_fills)}")

        # CANONICAL MODEL: create_trade_from_signal() persists the trade
        # (signal row first), so memory and DB agree on identity. This is
        # what makes crash-window-B recovery possible (Section 37).
        assert len(db_trades) == 1, \
            f"create_trade_from_signal() must persist the trade, got {len(db_trades)}"
        assert db_trades[0]["trade_id"] == mem_trade.trade_id, \
            "DB trade_id must equal the in-memory lifecycle trade_id"
        signals = _raw_db_query(db_path, "SELECT * FROM signals")
        assert len(signals) == 1, "signal row must exist before the trade row"
        print("  CONFIRMED: Trade persisted under canonical trade_id (memory == DB)")

    def test_position_id_independent_from_lifecycle_trade_id(self):
        """
        Verify position.position_id and lifecycle.trade_id are different UUIDs.
        DB uses position.position_id, lifecycle uses its own trade_id.
        """
        lifecycle = TradeLifecycleManager()
        position_mgr = PositionManager()

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        from execution.paper_broker import Fill
        fill = Fill(
            fill_id="F-001", order_id="O-001", instrument="GOLDM",
            side="BUY", quantity=1, price=100000.0, timestamp=time.time(),
            strategy_id="gold_01", multiplier=1,
            entry_signal_id=sig.signal_id, trade_id=ctx.trade_id,
        )
        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
            trade_id=ctx.trade_id,
        )

        print(f"\n  lifecycle_trade.trade_id = {ctx.trade_id}")
        print(f"  position.position_id     = {position.position_id}")
        print(f"  Same? {ctx.trade_id == position.position_id}")

        # These MUST be different (proven by prior test)
        assert ctx.trade_id != position.position_id, \
            "trade_id and position_id should be different UUIDs"

        # DB would use position.position_id as trade_id
        # Lifecycle uses ctx.trade_id
        # These are different — memory and DB have different identity


class TestDbForensicIntegrity:
    """
    Phase 18: Database Forensic Audit
    Verify schema constraints, orphan detection, FK relationships.
    """

    def test_no_foreign_keys_enforced(self):
        """Verify SQLite has no FK constraints on the trades DB."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)

        conn = sqlite3.connect(db_path)
        # Check if foreign_keys pragma is enabled
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()

        print(f"\n  PRAGMA foreign_keys = {fk_status}")
        assert fk_status == 0, "Foreign keys should NOT be enforced (system doesn't use them)"

    def test_insert_or_replace_destroys_history(self):
        """Inserting the same trade_id twice must NOT destroy the audit trail:
        canonical save_trade uses ON CONFLICT DO UPDATE (row identity preserved),
        unlike the old INSERT OR REPLACE which deleted+recreated the row."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)

        # Seed the canonical signal so the entry-signal trigger passes.
        persistence.save_signal({
            "signal_id": "SIG-001", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "signal_type": "LONG",
            "timestamp": time.time(),
        })

        # Insert trade (open)
        persistence.save_trade({
            "trade_id": "TRD-001", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "LONG",
            "entry_timestamp": "2026-09-04T10:00:00",
            "entry_price": 100000.0, "exit_timestamp": None,
            "exit_price": None, "quantity": 1, "multiplier": 1.0,
            "gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0,
            "exit_reason": None, "status": "open",
            "entry_signal_id": "SIG-001", "exit_signal_id": None,
        })

        row1 = _raw_db_query(db_path, "SELECT * FROM trades WHERE trade_id='TRD-001'")[0]
        auto_id_1 = row1["id"]
        print(f"\n  First insert: id={auto_id_1}")

        # Update with same trade_id but different data (close it)
        persistence.save_trade({
            "trade_id": "TRD-001", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "LONG",
            "entry_timestamp": "2026-09-04T10:00:00",
            "entry_price": 100000.0, "exit_timestamp": "2026-09-04T11:00:00",
            "exit_price": 101000.0, "quantity": 1, "multiplier": 1.0,
            "gross_pnl": 1000.0, "charges": 10.0, "net_pnl": 990.0,
            "exit_reason": "signal_exit", "status": "closed",
            "entry_signal_id": "SIG-001", "exit_signal_id": "SIG-002",
        })

        row2 = _raw_db_query(db_path, "SELECT * FROM trades WHERE trade_id='TRD-001'")[0]
        auto_id_2 = row2["id"]
        print(f"  Second insert: id={auto_id_2}")

        # ON CONFLICT DO UPDATE preserves the row identity (audit trail intact);
        # the old INSERT OR REPLACE would have changed auto_id.
        assert auto_id_1 == auto_id_2, \
            f"canonical save_trade must preserve row identity (ON CONFLICT UPDATE): {auto_id_1} -> {auto_id_2}"
        assert row2["status"] == "closed"
        assert row2["net_pnl"] == 990.0
        print("  CONFIRMED: ON CONFLICT UPDATE preserves the canonical row (audit trail intact)")

    def test_orphan_fill_with_no_trade_is_prevented(self):
        """Canonical integrity triggers REJECT a fill that references no trade.
        A fill with trade_id=NULL/'' must never be writable to the DB."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        # Attempt to insert an orphan fill (trade_id NULL, order NULL) directly.
        # The canonical DB rejects it at the constraint layer.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                INSERT INTO fills (fill_id, order_id, strategy_id, instrument,
                    side, quantity, price, timestamp, entry_signal_id, trade_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("ORPHAN-F-001", "O-X", "gold_01", "GOLDM", "BUY", 1, 100000.0,
                  "2026-09-04T10:00:00", "", ""))
            conn.commit()
            rejected = False
        except sqlite3.IntegrityError:
            rejected = True
        finally:
            conn.close()

        # The orphan must NOT be creatable — integrity is enforced.
        assert rejected, "DB must reject a fill lacking trade/order lineage"
        orphans = _raw_db_query(
            db_path,
            "SELECT fill_id FROM fills WHERE trade_id IS NULL OR trade_id = ''"
        )
        assert len(orphans) == 0, "no orphan fills may exist in the canonical DB"
        print("  CONFIRMED: orphan fill with no trade is REJECTED by integrity trigger")

    def test_orphan_fill_with_nonexistent_trade_is_prevented(self):
        """Canonical integrity triggers REJECT a fill referencing a missing trade."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)

        # Attempt to insert a fill referencing a nonexistent trade and order.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                INSERT INTO fills (fill_id, order_id, strategy_id, instrument,
                    side, quantity, price, timestamp, entry_signal_id, trade_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("BAD-F-001", "O-X", "gold_01", "GOLDM", "BUY", 1, 100000.0,
                  "2026-09-04T10:00:00", "", "NONEXISTENT-TRADE-ID"))
            conn.commit()
            rejected = False
        except sqlite3.IntegrityError:
            rejected = True
        finally:
            conn.close()

        assert rejected, "DB must reject a fill referencing a missing trade/order"
        fills = _raw_db_query(db_path, "SELECT * FROM fills WHERE fill_id='BAD-F-001'")
        assert len(fills) == 0, "a rejected orphan fill must not be persisted"
        print("  CONFIRMED: orphan fill with nonexistent trade is REJECTED by integrity trigger")
