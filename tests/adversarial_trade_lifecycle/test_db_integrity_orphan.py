"""
ADVERSARIAL TEST: Database Integrity & Orphan Detection
========================================================
Verify that the lifecycle's orphan scan and reconciliation actually
detect real problems when they exist.
"""
import sys
import os
import time
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import TradeLifecycleManager, TradeStatus
from persistence.manager import PersistenceManager
from strategies.types import Signal, SignalType


def _make_signal():
    return Signal(
        signal_type=SignalType.LONG, instrument="GOLDM",
        strategy_id="gold_01", timestamp=time.time(),
        trigger_price=100000.0, stop_price=99000.0, quantity=1,
    )


class TestOrphanDetectionReal:
    """
    Verify orphan scan actually detects orphaned objects.
    """

    def test_orphan_fill_detected(self):
        lifecycle = TradeLifecycleManager()
        # Create a trade
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        # Inject orphan fill (fill_id → nonexistent trade)
        lifecycle._fill_to_trade["ORPHAN-FILL-001"] = "NONEXISTENT-TRADE-ID"

        report = lifecycle.orphan_scan()
        print(f"\n  Orphan scan report:")
        print(f"    is_clean = {report.get('is_clean', 'N/A')}")
        print(f"    total_orphans = {report.get('total_orphans', 'N/A')}")
        print(f"    orphan_fills = {report.get('orphan_fills', [])}")

        orphan_fill_ids = [o.get("fill_id") for o in report.get("orphan_fills", [])]
        assert "ORPHAN-FILL-001" in orphan_fill_ids, \
            f"Orphan scan should detect ORPHAN-FILL-001, got {orphan_fill_ids}"

    def test_orphan_order_detected(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        lifecycle._order_to_trade["ORPHAN-ORD-001"] = "NONEXISTENT-TRADE-ID"

        report = lifecycle.orphan_scan()
        orphan_order_ids = [o.get("order_id") for o in report.get("orphan_orders", [])]
        assert "ORPHAN-ORD-001" in orphan_order_ids

    def test_orphan_position_detected(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        lifecycle._position_to_trade["ORPHAN-POS-001"] = "NONEXISTENT-TRADE-ID"

        report = lifecycle.orphan_scan()
        orphan_pos_ids = [o.get("position_id") for o in report.get("orphan_positions", [])]
        assert "ORPHAN-POS-001" in orphan_pos_ids


class TestReconciliationDetection:
    """
    Verify reconciliation detects real problems.
    """

    def test_reconcile_catches_missing_entry_signal(self):
        """A trade without entry_signal_id should be flagged."""
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        # Tamper: clear entry_signal_id
        ctx.entry_signal_id = ""

        report = lifecycle.reconcile()
        error_types = [e.get("type") for e in report.get("errors", [])]
        print(f"\n  Reconcile errors: {report.get('errors', [])}")

        assert "MISSING_ENTRY_SIGNAL" in error_types, \
            f"Should detect MISSING_ENTRY_SIGNAL, got {error_types}"

    def test_reconcile_catches_open_trade_no_position(self):
        """An OPEN trade without position_id should be flagged."""
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        # Force status to OPEN without position
        ctx.status = TradeStatus.OPEN.value
        ctx.position_id = ""

        report = lifecycle.reconcile()
        error_types = [e.get("type") for e in report.get("errors", [])]
        print(f"  Reconcile errors: {report.get('errors', [])}")

        assert "OPEN_TRADE_NO_POSITION" in error_types, \
            f"Should detect OPEN_TRADE_NO_POSITION, got {error_types}"


class TestDbOrphanScan:
    """
    Test the DB-level orphan scan with a real database.
    """

    def test_db_orphan_fill_detected(self):
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test.db")
        state_path = os.path.join(db_dir, "state.json")
        persistence = PersistenceManager(state_path=state_path, db_path=db_path)
        lifecycle = TradeLifecycleManager(persistence=persistence)

        # Create a fill in DB with no trade_id (orphan)
        persistence.save_fill({
            "fill_id": "ORPHAN-FILL-DB", "order_id": "O-X",
            "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "price": 100000.0,
            "timestamp": "2026-09-04T10:00:00+00:00",
            "entry_signal_id": "", "trade_id": "",
        })

        # Create a valid trade to avoid empty-state edge case
        sig = _make_signal()
        lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        report = lifecycle.orphan_scan()
        orphan_fill_ids = [o.get("fill_id") for o in report.get("orphan_fills", [])]
        print(f"\n  DB orphan fills: {orphan_fill_ids}")
        assert "ORPHAN-FILL-DB" in orphan_fill_ids, \
            f"Should detect DB orphan fill ORPHAN-FILL-DB"
