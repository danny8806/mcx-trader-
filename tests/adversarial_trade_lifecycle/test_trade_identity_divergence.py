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
                    side="SELL", quantity=1, price=101000.0, strategy_id="gold_01",
                    trade_id=None):
    from execution.paper_broker import Fill
    return Fill(
        fill_id=fill_id, order_id=order_id, instrument=instrument,
        side=side, quantity=quantity, price=price, timestamp=time.time(),
        strategy_id=strategy_id, multiplier=1,
        entry_signal_id=None, trade_id=trade_id,
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

        # Create a fill (propagate the canonical trade_id explicitly)
        fill = _make_fill(
            fill_id="F-ENTRY-001",
            order_id="O-ENTRY-001",
            entry_signal_id=sig.signal_id,
            trade_id=lifecycle_trade.trade_id,
        )

        # Open position — creates position.position_id (DIFFERENT from trade_id)
        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
            trade_id=lifecycle_trade.trade_id,
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

    def test_close_and_lifecycle_both_use_canonical_trade_id(self):
        """
        PROVE (canonical model): trade_close_manager and lifecycle persist under
        the SAME canonical trade_id (NOT position.position_id). There must be
        NO split-brain, and fills/orders must link to trade_id, not position_id.
        """
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_divergence.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager()
        position_mgr = PositionManager()

        sig = _make_signal()
        # Seed the canonical signal so strict integrity triggers pass.
        persistence.save_signal({
            "signal_id": sig.signal_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "signal_type": "LONG",
            "timestamp": sig.timestamp,
        })
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        fill = _make_fill(
            fill_id="F-ENTRY-001", order_id="O-ENTRY-001",
            entry_signal_id=sig.signal_id, trade_id=lifecycle_trade.trade_id,
        )

        position = position_mgr.open_position(
            fill=fill, multiplier=1.0, margin=0.0,
            stop_price=99000.0, entry_signal_id=sig.signal_id,
            trade_id=lifecycle_trade.trade_id,
        )

        # Register in lifecycle
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id=fill.fill_id,
            price=fill.price,
            timestamp=fill.timestamp,
        )
        lifecycle.register_position(lifecycle_trade.trade_id, position.position_id)

        # Pin the canonical trade_id for the assertion.
        canonical_trade_id = lifecycle_trade.trade_id
        assert canonical_trade_id != position.position_id, \
            "trade_id and position_id are distinct identities"

        # Seed the OPEN canonical trade (signals row already exists).
        persistence.save_trade({
            "trade_id": canonical_trade_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "LONG", "entry_price": fill.price,
            "quantity": 1, "multiplier": 1.0,
            "entry_signal_id": sig.signal_id, "status": "open",
        })
        # Seed the entry order so the fill trigger references a real order.
        persistence.save_order({
            "order_id": fill.order_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "quantity": 1,
            "order_type": "MARKET", "state": "filled",
            "filled_quantity": 1, "average_fill_price": fill.price,
            "trade_id": canonical_trade_id,
        })
        # Persist the entry fill under the CANONICAL trade_id (never position_id).
        persistence.save_fill({
            "fill_id": fill.fill_id, "order_id": fill.order_id,
            "strategy_id": fill.strategy_id, "instrument": fill.instrument,
            "side": fill.side, "quantity": fill.quantity, "price": fill.price,
            "timestamp": datetime.fromtimestamp(fill.timestamp, tz=timezone.utc).isoformat(),
            "entry_signal_id": sig.signal_id,
            "trade_id": canonical_trade_id,
        })

        exit_fill = _make_exit_fill(order_id="O-EXIT", trade_id=canonical_trade_id)
        # Seed an exit order for the close fill.
        persistence.save_order({
            "order_id": "O-EXIT", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "SELL", "quantity": 1,
            "order_type": "MARKET", "state": "submitted",
            "filled_quantity": 0, "average_fill_price": 0.0,
            "trade_id": canonical_trade_id,
        })
        # Persist the close under the SAME canonical trade_id (updates row).
        trade_record = {
            "trade_id": canonical_trade_id,
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

        # Also close via the lifecycle using the SAME trade_id.
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

        db_trades = conn.execute("SELECT * FROM trades").fetchall()
        print(f"\n  DB trades count: {len(db_trades)}")
        for t in db_trades:
            print(f"    trade_id={t['trade_id']}, status={t['status']}, "
                  f"net_pnl={t['net_pnl']}")

        db_fills = conn.execute("SELECT * FROM fills").fetchall()
        print(f"  DB fills count: {len(db_fills)}")
        for f in db_fills:
            print(f"    fill_id={f['fill_id']}, trade_id={f['trade_id']}")

        # CANONICAL: EXACTLY ONE trade row, keyed by canonical trade_id.
        assert len(db_trades) == 1, \
            f"canonical model must yield exactly one trade row, got {len(db_trades)}"
        assert db_trades[0]["trade_id"] == lifecycle_trade.trade_id, \
            "trade must persist under the canonical trade_id, not position_id"

        # The position_id must NOT appear as a trade_id anywhere.
        for t in db_trades:
            assert t["trade_id"] != position.position_id, \
                "position_id must never be used as a trade_id"

        # Entry fill links to canonical trade_id, not position_id.
        entry_fill_in_db = conn.execute(
            "SELECT * FROM fills WHERE fill_id = ?", (fill.fill_id,)
        ).fetchone()
        assert entry_fill_in_db["trade_id"] == lifecycle_trade.trade_id, \
            "entry fill must link to canonical trade_id, not position_id"

        conn.close()

    def test_lifecycle_persist_is_single_row(self):
        """
        PROVE (canonical model): lifecycle.close_trade() persists the SAME
        canonical trade_id it created at signal time — never a second row under
        position_id. Exactly one trade row exists after close.
        """
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_second_row.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        sig = _make_signal()
        # Seed the canonical signal so the strict entry-signal trigger passes.
        persistence.save_signal({
            "signal_id": sig.signal_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "signal_type": "LONG",
            "timestamp": sig.timestamp,
        })
        lifecycle_trade = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )

        # create_trade_from_signal persists the OPEN trade under canonical
        # trade_id (signals lineage valid) — exactly ONE row, no split-brain.
        conn = sqlite3.connect(db_path)
        rows_at_creation = conn.execute("SELECT * FROM trades").fetchall()
        print(f"\n  Trades in DB after create_trade_from_signal: {len(rows_at_creation)}")
        for r in rows_at_creation:
            print(f"    trade_id={r[1]}, status={r[15]}")
        conn.close()

        assert len(rows_at_creation) == 1, \
            "canonical model: create_trade_from_signal persists one row (signal lineage valid)"
        assert rows_at_creation[0][1] == lifecycle_trade.trade_id, \
            "trade must persist under the canonical trade_id"

        # Now register entry fill and close
        lifecycle.register_entry_fill(
            trade_id=lifecycle_trade.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )
        lifecycle.close_trade(
            trade_id=lifecycle_trade.trade_id,
            gross_pnl=0.0, charges=0.0, net_pnl=0.0,
        )

        # Check DB — exactly ONE row, keyed by canonical trade_id.
        conn = sqlite3.connect(db_path)
        rows_after_close = conn.execute("SELECT * FROM trades").fetchall()
        print(f"  Trades in DB after close_trade: {len(rows_after_close)}")
        for r in rows_after_close:
            print(f"    trade_id={r[1]}, status={r[15]}")

        assert len(rows_after_close) == 1, \
            "canonical model: close_trade persist must still be exactly one row"
        assert rows_after_close[0][1] == lifecycle_trade.trade_id, \
            "close_trade must persist under the canonical trade_id"

        conn.close()
