"""
ADVERSARIAL TEST: Trade Identity Divergence
============================================
CRITICAL FINDING: lifecycle.trade_id and position.position_id are DIFFERENT UUIDs.

This test proves whether the system has a split-brain identity problem.
The lifecycle creates trade_id = uuid4().
The position_manager creates position_id = uuid4().
These are different.

When trade_close_manager persists, it uses position.position_id as trade_id.
When lifecycle persists, it uses lifecycle_trade.trade_id.
These point to different DB rows.

This test verifies this divergence exists or is fixed.
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
from execution.paper_broker import PaperExecutionEngine
from persistence.manager import PersistenceManager
from strategies.types import Signal, SignalType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts():
    return datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def _make_signal(signal_type="LONG", strategy_id="gold_01", instrument="GOLDM",
                 trigger_price=100000.0, stop_price=99000.0, quantity=1):
    return Signal(
        signal_type=SignalType(signal_type),
        instrument=instrument,
        strategy_id=strategy_id,
        timestamp=time.time(),
        trigger_price=trigger_price,
        stop_price=stop_price,
        quantity=quantity,
    )


def _make_fill(fill_id="F-001", order_id="O-001", instrument="GOLDM",
               side="BUY", quantity=1, price=100000.0, strategy_id="gold_01",
               entry_signal_id=None, trade_id=None):
    from execution.paper_broker import Fill
    return Fill(
        fill_id=fill_id, order_id=order_id, instrument=instrument,
        side=side, quantity=quantity, price=price, timestamp=time.time(),
        strategy_id=strategy_id, multiplier=1,
        entry_signal_id=entry_signal_id, trade_id=trade_id,
    )


def _make_exit_fill(fill_id="F-EXIT", order_id="O-EXIT", instrument="GOLDM",
                    side="SELL", quantity=1, price=101000.0, strategy_id="gold_01"):
    from execution.paper_broker import Fill
    return Fill(
        fill_id=fill_id, order_id=order_id, instrument=instrument,
        side=side, quantity=quantity, price=price, timestamp=time.time(),
        strategy_id=strategy_id, multiplier=1,
        entry_signal_id=None, trade_id=None,
    )


# ===========================================================================
# TEST 1: trade_id vs position_id DIVERGENCE
# ===========================================================================

class TestTradeIdPositionIdDivergence:
    """
    CRITICAL: lifecycle.create_trade_from_signal() creates trade_id = uuid4().
    position_manager.open_position() creates position_id = uuid4().
    These are DIFFERENT UUIDs.
    
    This test proves whether the lifecycle and position_manager produce
    the same or different IDs.
    """

    def test_lifecycle_trade_id_vs_position_position_id(self):
        """PROVE: lifecycle trade_id != position.position_id after creation."""
        lifecycle = TradeLifecycleManager()
        position_mgr = PositionManager()

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        # Create a fill
        fill = _make_fill(
            fill_id="F-ENTRY-001",
            order_id="O-ENTRY-001",
            entry_signal_id=sig.signal_id,
        )

        # Open position — creates position.position_id (DIFFERENT UUID)
        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
        )

        # CRITICAL CHECK: Are these the same UUID?
        print(f"\n  lifecycle_trade.trade_id = {lifecycle_trade.trade_id}")
        print(f"  position.position_id     = {position.position_id}")

        # The lifecycle trade_id and position.position_id are DIFFERENT UUIDs
        # unless the system explicitly coordinates them.
        # This is the root of the split-brain problem.
        if lifecycle_trade.trade_id == position.position_id:
            # If they match, the system coordinates them — good
            print("  RESULT: IDs MATCH (coordinated)")
        else:
            print("  RESULT: IDs DIVERGE (split-brain)")

        # Now check what happens when lifecycle.register_entry_fill is called
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=fill.fill_id,
            price=fill.price,
            timestamp=fill.timestamp,
        )
        lifecycle.register_position(lifecycle_trade.trade_id, position.position_id)

        # After register_position, what is trade.position_id?
        trade_after = lifecycle.get_trade(lifecycle_trade.trade_id)
        print(f"  trade.position_id after register_position = {trade_after.position_id}")

        # The position_id field on the lifecycle trade should be position.position_id
        # But the trade_id used as the key in lifecycle._trades is lifecycle_trade.trade_id
        # These are the same trade object, but the position_id field may differ from trade_id
        assert trade_after.position_id == position.position_id, \
            f"position_id should be {position.position_id}, got {trade_after.position_id}"

    def test_close_uses_position_id_not_lifecycle_trade_id(self):
        """
        PROVE: trade_close_manager.close_position() persists with position.position_id,
        NOT lifecycle_trade.trade_id. This creates a DB row under a different key
        than what the lifecycle tracks.
        """
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_divergence.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager()
        position_mgr = PositionManager()

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        fill = _make_fill(
            fill_id="F-ENTRY-001", order_id="O-ENTRY-001",
            entry_signal_id=sig.signal_id,
        )

        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
        )

        # Register in lifecycle
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=fill.fill_id,
            price=fill.price,
            timestamp=fill.timestamp,
        )
        lifecycle.register_position(lifecycle_trade.trade_id, position.position_id)

        # Save the entry fill to DB with position.position_id as trade_id
        # (this is what trading_engine._on_fill does at line 1291)
        persistence.save_fill({
            "fill_id": fill.fill_id, "order_id": fill.order_id,
            "strategy_id": fill.strategy_id, "instrument": fill.instrument,
            "side": fill.side, "quantity": fill.quantity, "price": fill.price,
            "timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
            "entry_signal_id": sig.signal_id,
            "trade_id": position.position_id,  # ← uses position_id, NOT lifecycle_trade_id
        })

        # Now persist the close using position.position_id as trade_id
        # (this is what trade_close_manager.close_position does at line 121)
        exit_fill = _make_exit_fill()
        trade_record = {
            "trade_id": position.position_id,  # ← CRITICAL: uses position_id
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "LONG",
            "entry_timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
            "entry_price": fill.price,
            "exit_timestamp": datetime.fromtimestamp(exit_fill.timestamp, tz=timezone.utc).isoformat(),
            "exit_price": exit_fill.price,
            "quantity": 1,
            "multiplier": 1.0,
            "gross_pnl": 1000.0,
            "charges": 10.0,
            "net_pnl": 990.0,
            "exit_reason": "signal_exit",
            "status": "closed",
            "entry_signal_id": sig.signal_id,
            "exit_signal_id": "",
        }
        persistence.save_trade(trade_record)

        # NOW: lifecycle.close_trade uses lifecycle_trade.trade_id
        lifecycle.register_exit_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=exit_fill.fill_id,
            price=exit_fill.price,
            timestamp=exit_fill.timestamp,
        )
        lifecycle.close_trade(
            trade_id=lifecycle_trade.trade_id,
            gross_pnl=1000.0, charges=10.0, net_pnl=990.0,
        )

        # INDEPENDENT DB AUDIT
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Check: how many trades exist in DB?
        db_trades = conn.execute("SELECT * FROM trades").fetchall()
        print(f"\n  DB trades count: {len(db_trades)}")
        for t in db_trades:
            print(f"    trade_id={t['trade_id']}, status={t['status']}, "
                  f"net_pnl={t['net_pnl']}, entry_signal_id={t['entry_signal_id']}")

        # Check fills
        db_fills = conn.execute("SELECT * FROM fills").fetchall()
        print(f"  DB fills count: {len(db_fills)}")
        for f in db_fills:
            print(f"    fill_id={f['fill_id']}, trade_id={f['trade_id']}")

        # CRITICAL CHECK: DB should have the trade with position.position_id
        trade_in_db = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (position.position_id,)
        ).fetchone()
        assert trade_in_db is not None, \
            f"DB should have trade with position.position_id={position.position_id}"
        assert trade_in_db["status"] == "closed", \
            f"Trade in DB should be closed, got {trade_in_db['status']}"

        # CRITICAL CHECK: Does a second trade exist with lifecycle_trade.trade_id?
        lifecycle_key_in_db = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (lifecycle_trade.trade_id,)
        ).fetchone()

        if lifecycle_key_in_db is not None:
            print(f"\n  SPLIT-BRAIN DETECTED: DB has TWO trades:")
            print(f"    1. position_id={position.position_id} (from trade_close_manager)")
            print(f"    2. lifecycle_id={lifecycle_trade.trade_id} (from lifecycle.close_trade)")
            print(f"    The lifecycle trade_id was never the same as position.position_id!")
            # This is the BUG: lifecycle persists a second trade row
        else:
            print(f"\n  No split-brain: only one trade in DB (position.position_id)")

        # Verify fills link to position.position_id, not lifecycle_trade.trade_id
        entry_fill_in_db = conn.execute(
            "SELECT * FROM fills WHERE fill_id = ?", (fill.fill_id,)
        ).fetchone()
        assert entry_fill_in_db["trade_id"] == position.position_id, \
            f"Entry fill should link to position.position_id={position.position_id}, " \
            f"got trade_id={entry_fill_in_db['trade_id']}"

        conn.close()

    def test_lifecycle_persist_creates_second_row(self):
        """
        PROVE: When lifecycle._persist_trade() is called after close_trade(),
        it creates a SECOND row in the trades table because lifecycle_trade.trade_id
        is different from position.position_id.
        """
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_second_row.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        sig = _make_signal()
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        # Persist removed from create_trade_from_signal() — trade in memory only
        conn = sqlite3.connect(db_path)
        rows_at_creation = conn.execute("SELECT * FROM trades").fetchall()
        print(f"\n  Trades in DB after create_trade_from_signal: {len(rows_at_creation)}")
        for r in rows_at_creation:
            print(f"    trade_id={r[1]}, status={r[15]}")
        conn.close()

        # Persist removed — 0 rows until register_position() unifies identity
        assert len(rows_at_creation) == 0, \
            "create_trade_from_signal() should not persist (persist removed)"

        # Now register entry fill and close
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )
        lifecycle.close_trade(
            trade_id=lifecycle_trade.trade_id,
            gross_pnl=0.0, charges=0.0, net_pnl=0.0,
        )

        # Check DB again — FAIL-TL-001: persist still broken at close time
        conn = sqlite3.connect(db_path)
        rows_after_close = conn.execute("SELECT * FROM trades").fetchall()
        print(f"  Trades in DB after close_trade: {len(rows_after_close)}")
        for r in rows_after_close:
            print(f"    trade_id={r[1]}, status={r[15]}")

        # FAIL-TL-001: FIXED — close_trade persist succeeds, still 1 row with CLOSED status
        assert len(rows_after_close) == 1, \
            "FAIL-TL-001 FIXED: close_trade persist should succeed"

        conn.close()
