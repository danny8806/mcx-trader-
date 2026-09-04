"""
TradeLifecycleManager — comprehensive unit tests.

Tests the central trade lifecycle manager covering:
- Trade creation from signals
- Pending order registration and activation
- Order registration
- Entry fill + position registration
- Exit fill + trade close
- Stop loss application
- Reversal (atomic close + new open)
- Identity resolution (signal→trade, order→trade, etc.)
- Orphan detection
- Reconciliation
- Snapshot/restore roundtrip
- Edge cases
"""
import sys
import os
import time
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import (
    TradeLifecycleManager,
    TradeContext,
    TradeStatus,
    ExitType,
    OrderRole,
    SignalEventType,
    LifecycleEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_minutes=0):
    return datetime(2026, 9, 4, 9 + offset_minutes // 60, offset_minutes % 60,
                    tzinfo=timezone.utc)


def _make_signal(signal_type="LONG", strategy_id="gold_01", instrument="GOLDM",
                 trigger_price=100000.0, stop_price=99000.0, quantity=1):
    from strategies.types import Signal, SignalType
    return Signal(
        signal_type=SignalType(signal_type),
        instrument=instrument,
        strategy_id=strategy_id,
        timestamp=_ts(),
        trigger_price=trigger_price,
        stop_price=stop_price,
        quantity=quantity,
    )


# ===========================================================================
# 1. Trade creation from signal
# ===========================================================================

class TestCreateTradeFromSignal:
    def test_basic_creation(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        assert ctx.trade_id is not None
        assert ctx.strategy_id == "gold_01"
        assert ctx.instrument == "GOLDM"
        assert ctx.status == TradeStatus.PENDING.value
        assert ctx.entry_signal_id == sig.signal_id
        assert ctx.entry_trigger_price == 100000.0
        assert ctx.stop_loss_price == 99000.0
        assert ctx.quantity == 1

    def test_trade_id_unique(self):
        mgr = TradeLifecycleManager()
        sig1 = _make_signal(trigger_price=100000)
        sig2 = _make_signal(trigger_price=200000)
        ctx1 = mgr.create_trade_from_signal(sig1, "gold_01", instrument="GOLDM")
        ctx2 = mgr.create_trade_from_signal(sig2, "gold_01", instrument="GOLDM")
        assert ctx1.trade_id != ctx2.trade_id

    def test_signal_to_trade_resolution(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        resolved = mgr.resolve_trade_from_signal(sig.signal_id)
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id

    def test_duplicate_signal_creates_new_trade(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx1 = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        ctx2 = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        assert ctx1.trade_id != ctx2.trade_id

    def test_events_recorded(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        assert len(mgr._events) == 1
        assert mgr._events[0].event_type == "TRADE_CREATED"

    def test_entry_side_derived_from_signal_type(self):
        mgr = TradeLifecycleManager()
        sig_long = _make_signal(signal_type="LONG")
        ctx_long = mgr.create_trade_from_signal(sig_long, "gold_01", instrument="GOLDM")
        assert ctx_long.entry_side == "LONG"
        assert ctx_long.entry_action == "BUY"

        sig_short = _make_signal(signal_type="SHORT")
        ctx_short = mgr.create_trade_from_signal(sig_short, "gold_01", instrument="GOLDM")
        assert ctx_short.entry_side == "SHORT"
        assert ctx_short.entry_action == "SELL"


# ===========================================================================
# 2. Pending order flow
# ===========================================================================

class TestPendingOrder:
    def test_register_and_activate(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        ok = mgr.register_pending_order(ctx.trade_id, "PEND-001")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.pending_order_id == "PEND-001"

        new_trade = mgr.activate_pending_order("PEND-001", "ORD-100")
        assert new_trade is not None
        t = mgr.get_trade(ctx.trade_id)
        assert t.pending_status == "triggered"
        assert t.entry_order_id == "ORD-100"

    def test_pending_to_trade_resolution(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_pending_order(ctx.trade_id, "PEND-001")

        resolved = mgr.resolve_trade_from_pending("PEND-001")
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id

    def test_activate_unknown_pending_returns_none(self):
        mgr = TradeLifecycleManager()
        result = mgr.activate_pending_order("NONEXISTENT", "ORD-999")
        assert result is None

    def test_register_pending_unknown_trade_returns_false(self):
        mgr = TradeLifecycleManager()
        ok = mgr.register_pending_order("NONEXISTENT", "PEND-X")
        assert ok is False


# ===========================================================================
# 3. Order registration
# ===========================================================================

class TestOrderRegistration:
    def test_register_entry_order(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        ok = mgr.register_order(ctx.trade_id, "ORD-001", "ENTRY")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.entry_order_id == "ORD-001"

    def test_register_exit_order(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        ok = mgr.register_order(ctx.trade_id, "ORD-EXIT", "EXIT")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.exit_order_id == "ORD-EXIT"

    def test_order_to_trade_resolution(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_order(ctx.trade_id, "ORD-001", "ENTRY")

        resolved = mgr.resolve_trade_from_order("ORD-001")
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id

    def test_register_order_unknown_trade_returns_false(self):
        mgr = TradeLifecycleManager()
        ok = mgr.register_order("NONEXISTENT", "ORD-X", "ENTRY")
        assert ok is False


# ===========================================================================
# 4. Entry fill + position
# ===========================================================================

class TestEntryFill:
    def test_register_entry_fill(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_order(ctx.trade_id, "ORD-001", "ENTRY")

        ok = mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0, time.time())
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.entry_fill_id == "FILL-001"
        assert t.entry_price == 100000.0
        assert t.status == TradeStatus.OPEN.value

    def test_fill_to_trade_resolution(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        resolved = mgr.resolve_trade_from_fill("FILL-001")
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id

    def test_register_position(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        ok = mgr.register_position(ctx.trade_id, "POS-001")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.position_id == "POS-001"

    def test_position_id_auto_set_on_entry_fill(self):
        # position_id must be SEPARATE identity per architecture Section 1.
        # register_entry_fill does not auto-assign position_id; a position
        # must be created explicitly with a distinct identity via
        # register_position().
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        # Entry fill does not auto-assign position_id
        t = mgr.get_trade(ctx.trade_id)
        assert t.position_id == ""  # no position until explicitly registered
        assert t.entry_fill_id == "FILL-001"

        # Position must be registered with a SEPARATE identity
        mgr.register_position(ctx.trade_id, "POS-001")
        assert mgr.get_trade(ctx.trade_id).position_id == "POS-001"
        assert "POS-001" != ctx.trade_id  # position_id != trade_id

    def test_position_to_trade_resolution(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_position(ctx.trade_id, "POS-001")

        resolved = mgr.resolve_trade_from_position("POS-001")
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id

    def test_register_entry_fill_unknown_trade_returns_false(self):
        mgr = TradeLifecycleManager()
        ok = mgr.register_entry_fill("NONEXISTENT", "FILL-X", 100000.0)
        assert ok is False


# ===========================================================================
# 5. Exit fill + trade close
# ===========================================================================

class TestExitClose:
    def test_register_exit_fill(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        ok = mgr.register_exit_fill(ctx.trade_id, "FILL-EXIT", 101000.0,
                                     time.time(), "", "STRATEGY_EXIT", "manual exit")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.exit_fill_id == "FILL-EXIT"
        assert t.exit_price == 101000.0
        assert t.exit_type == "STRATEGY_EXIT"
        assert t.exit_action == "SELL"

    def test_close_trade(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.register_exit_fill(ctx.trade_id, "FILL-EXIT", 101000.0,
                               time.time(), "", "STRATEGY_EXIT")

        ok = mgr.close_trade(ctx.trade_id, gross_pnl=1000.0, charges=10.0, net_pnl=990.0)
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.status == TradeStatus.CLOSED.value
        assert t.gross_pnl == 1000.0
        assert t.net_pnl == 990.0
        assert t.closed_at > 0

    def test_close_with_exit_signal(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        exit_sig = _make_signal(signal_type="SHORT")
        mgr.register_exit_fill(ctx.trade_id, "FILL-EXIT", 101000.0,
                               time.time(), exit_sig.signal_id, "STRATEGY_EXIT")

        mgr.close_trade(ctx.trade_id, gross_pnl=1000.0, net_pnl=990.0)
        t = mgr.get_trade(ctx.trade_id)
        assert t.exit_signal_id == exit_sig.signal_id

    def test_close_already_closed_returns_true(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.close_trade(ctx.trade_id, net_pnl=0.0)

        # Second close should return True (already closed, no-op)
        ok = mgr.close_trade(ctx.trade_id, net_pnl=0.0)
        assert ok is True

    def test_close_unknown_trade_returns_false(self):
        mgr = TradeLifecycleManager()
        ok = mgr.close_trade("NONEXISTENT", net_pnl=0.0)
        assert ok is False


# ===========================================================================
# 6. Stop loss
# ===========================================================================

class TestStopLoss:
    def test_apply_stop_loss(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal(trigger_price=100000.0, stop_price=99000.0)
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        ok = mgr.apply_stop_loss(ctx.trade_id, 99000.0, "STOP_LOSS")
        assert ok is True
        t = mgr.get_trade(ctx.trade_id)
        assert t.exit_type == ExitType.STOP_LOSS.value
        assert t.exit_price == 99000.0
        assert t.exit_reason == "STOP_LOSS"
        assert t.exit_signal_id == ""  # SL has no signal

    def test_stop_loss_does_not_close_trade(self):
        """apply_stop_loss only sets exit details; close_trade is separate."""
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        mgr.apply_stop_loss(ctx.trade_id, 99000.0)
        t = mgr.get_trade(ctx.trade_id)
        # Status should still be OPEN until close_trade is called
        assert t.status == TradeStatus.OPEN.value

    def test_stop_loss_unknown_trade_returns_false(self):
        mgr = TradeLifecycleManager()
        ok = mgr.apply_stop_loss("NONEXISTENT", 99000.0)
        assert ok is False


# ===========================================================================
# 7. Reversal (atomic close + new open)
# ===========================================================================

class TestReversal:
    def test_reverse_trade(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal(signal_type="LONG")
        old_ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(old_ctx.trade_id, "FILL-OLD", 100000.0)

        new_sig = _make_signal(signal_type="SHORT", trigger_price=101000.0,
                               stop_price=102000.0)
        new_ctx = mgr.reverse_trade(
            old_trade_id=old_ctx.trade_id,
            new_signal=new_sig,
            strategy_id="gold_01",
            strategy_name="Gold 01",
            instrument="GOLDM",
            quantity=1,
            multiplier=1.0,
            exit_price=101000.0,
        )

        # Old trade closed
        old = mgr.get_trade(old_ctx.trade_id)
        assert old.status == TradeStatus.CLOSED.value
        assert old.exit_type == ExitType.REVERSAL.value

        # New trade open
        assert new_ctx is not None
        assert new_ctx.trade_id != old_ctx.trade_id
        assert new_ctx.entry_signal_id == new_sig.signal_id
        assert new_ctx.entry_side == "SHORT"
        assert new_ctx.entry_action == "SELL"

    def test_reversal_same_signal_both_uses(self):
        """Same signal is both old exit_signal and new entry_signal."""
        mgr = TradeLifecycleManager()
        sig1 = _make_signal(signal_type="LONG")
        old_ctx = mgr.create_trade_from_signal(sig1, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(old_ctx.trade_id, "FILL-1", 100000.0)

        reversal_sig = _make_signal(signal_type="SHORT", trigger_price=101000.0,
                                    stop_price=102000.0)
        new_ctx = mgr.reverse_trade(
            old_trade_id=old_ctx.trade_id,
            new_signal=reversal_sig,
            strategy_id="gold_01",
            instrument="GOLDM",
            exit_price=101000.0,
        )

        old = mgr.get_trade(old_ctx.trade_id)
        assert old.exit_signal_id == reversal_sig.signal_id
        assert new_ctx.entry_signal_id == reversal_sig.signal_id

    def test_reversal_nonexistent_trade_returns_none(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        result = mgr.reverse_trade("NONEXISTENT", sig, "gold_01",
                                   instrument="GOLDM", exit_price=101000.0)
        assert result is None


# ===========================================================================
# 8. Identity resolution — negative cases
# ===========================================================================

class TestIdentityResolution:
    def test_resolve_unknown_signal_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.resolve_trade_from_signal("nonexistent") is None

    def test_resolve_unknown_order_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.resolve_trade_from_order("nonexistent") is None

    def test_resolve_unknown_fill_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.resolve_trade_from_fill("nonexistent") is None

    def test_resolve_unknown_position_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.resolve_trade_from_position("nonexistent") is None

    def test_resolve_unknown_pending_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.resolve_trade_from_pending("nonexistent") is None

    def test_get_trade_returns_none(self):
        mgr = TradeLifecycleManager()
        assert mgr.get_trade("nonexistent") is None

    def test_get_all_trades_empty(self):
        mgr = TradeLifecycleManager()
        assert mgr.get_all_trades() == []

    def test_get_trades_for_api_empty(self):
        mgr = TradeLifecycleManager()
        assert mgr.get_trades_for_api() == []


# ===========================================================================
# 9. Orphan scan
# ===========================================================================

class TestOrphanScan:
    def test_no_orphans_when_clean(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.register_position(ctx.trade_id, "POS-001")
        mgr.register_order(ctx.trade_id, "ORD-001", "ENTRY")

        report = mgr.orphan_scan()
        assert report["orphan_fills"] == []
        assert report["orphan_orders"] == []
        assert report["orphan_positions"] == []

    def test_orphan_fill_detected(self):
        mgr = TradeLifecycleManager()
        # Inject an orphan
        mgr._fill_to_trade["FILL-ORPHAN"] = "NONEXISTENT_TRADE"
        sig = _make_signal()
        mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")

        report = mgr.orphan_scan()
        orphan_ids = [o["fill_id"] for o in report["orphan_fills"]]
        assert "FILL-ORPHAN" in orphan_ids

    def test_orphan_order_detected(self):
        mgr = TradeLifecycleManager()
        mgr._order_to_trade["ORD-ORPHAN"] = "NONEXISTENT_TRADE"
        report = mgr.orphan_scan()
        orphan_ids = [o["order_id"] for o in report["orphan_orders"]]
        assert "ORD-ORPHAN" in orphan_ids

    def test_orphan_position_detected(self):
        mgr = TradeLifecycleManager()
        mgr._position_to_trade["POS-ORPHAN"] = "NONEXISTENT_TRADE"
        report = mgr.orphan_scan()
        orphan_ids = [o["position_id"] for o in report["orphan_positions"]]
        assert "POS-ORPHAN" in orphan_ids


# ===========================================================================
# 10. Reconciliation
# ===========================================================================

class TestReconciliation:
    def test_reconcile_clean_state(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        report = mgr.reconcile()
        assert report["errors"] == []
        assert report["stats"]["total_trades"] == 1
        assert report["stats"]["pending"] == 1

    def test_reconcile_with_closed_trade(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.close_trade(ctx.trade_id, net_pnl=500.0)

        report = mgr.reconcile()
        assert report["errors"] == []
        assert report["stats"]["closed"] == 1


# ===========================================================================
# 11. Snapshot / restore roundtrip
# ===========================================================================

class TestSnapshotRestore:
    def test_roundtrip(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        snap = mgr.snapshot()
        assert "trades" in snap
        assert len(snap["trades"]) == 1

        mgr2 = TradeLifecycleManager()
        mgr2.restore(snap)

        restored = mgr2.get_trade(ctx.trade_id)
        assert restored is not None
        assert restored.entry_fill_id == "FILL-001"
        assert restored.status == TradeStatus.OPEN.value

    def test_roundtrip_preserves_identity_maps(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_order(ctx.trade_id, "ORD-001", "ENTRY")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        snap = mgr.snapshot()
        mgr2 = TradeLifecycleManager()
        mgr2.restore(snap)

        assert mgr2.resolve_trade_from_signal(sig.signal_id) is not None
        assert mgr2.resolve_trade_from_order("ORD-001") is not None
        assert mgr2.resolve_trade_from_fill("FILL-001") is not None

    def test_roundtrip_closed_trade(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.close_trade(ctx.trade_id, gross_pnl=500.0, net_pnl=490.0)

        snap = mgr.snapshot()
        mgr2 = TradeLifecycleManager()
        mgr2.restore(snap)

        restored = mgr2.get_trade(ctx.trade_id)
        assert restored.status == TradeStatus.CLOSED.value
        assert restored.net_pnl == 490.0


# ===========================================================================
# 12. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_multiple_trades_independent(self):
        mgr = TradeLifecycleManager()
        sig1 = _make_signal(strategy_id="gold_01", trigger_price=100000)
        sig2 = _make_signal(strategy_id="silver_01", trigger_price=200000)
        ctx1 = mgr.create_trade_from_signal(sig1, "gold_01", instrument="GOLDM")
        ctx2 = mgr.create_trade_from_signal(sig2, "silver_01", instrument="SILVERM")

        assert ctx1.trade_id != ctx2.trade_id
        mgr.register_entry_fill(ctx1.trade_id, "F-1", 100000.0)

        assert mgr.get_trade(ctx1.trade_id).entry_fill_id == "F-1"
        assert mgr.get_trade(ctx2.trade_id).entry_fill_id == ""

    def test_trade_events_accumulate(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)
        mgr.register_position(ctx.trade_id, "POS-001")

        event_types = [e.event_type for e in mgr._events]
        assert "TRADE_CREATED" in event_types
        assert "ENTRY_FILL_RECORDED" in event_types

    def test_get_trades_for_api(self):
        mgr = TradeLifecycleManager()
        sig = _make_signal()
        ctx = mgr.create_trade_from_signal(sig, "gold_01", instrument="GOLDM")
        mgr.register_entry_fill(ctx.trade_id, "FILL-001", 100000.0)

        trades = mgr.get_trades_for_api()
        assert len(trades) == 1
        assert trades[0]["trade_id"] == ctx.trade_id
        assert trades[0]["status"] == "OPEN"

    def test_full_lifecycle_e2e(self):
        """Complete lifecycle: signal → create → order → entry fill → position → exit fill → close."""
        mgr = TradeLifecycleManager()

        # 1. Signal received
        sig = _make_signal(signal_type="LONG", trigger_price=100000.0, stop_price=99000.0)

        # 2. Trade created
        ctx = mgr.create_trade_from_signal(sig, "gold_01", "Gold Strategy", "GOLDM", 1, 1.0)
        assert ctx.status == TradeStatus.PENDING.value

        # 3. Order registered
        mgr.register_order(ctx.trade_id, "ORD-ENTRY-001", "ENTRY")

        # 4. Entry fill
        mgr.register_entry_fill(ctx.trade_id, "FILL-ENTRY-001", 100000.0, time.time())
        t = mgr.get_trade(ctx.trade_id)
        assert t.status == TradeStatus.OPEN.value
        assert t.entry_price == 100000.0

        # 5. Position registered
        mgr.register_position(ctx.trade_id, "POS-001")

        # 6. Exit fill
        mgr.register_exit_fill(ctx.trade_id, "FILL-EXIT-001", 101500.0, time.time(),
                               "", "STRATEGY_EXIT", "profit target")

        # 7. Close
        mgr.close_trade(ctx.trade_id, gross_pnl=1500.0, charges=15.0, net_pnl=1485.0)

        t = mgr.get_trade(ctx.trade_id)
        assert t.status == TradeStatus.CLOSED.value
        assert t.entry_price == 100000.0
        assert t.exit_price == 101500.0
        assert t.net_pnl == 1485.0
        assert t.exit_type == "STRATEGY_EXIT"

        # Verify all identity resolutions
        assert mgr.resolve_trade_from_signal(sig.signal_id) is not None
        assert mgr.resolve_trade_from_order("ORD-ENTRY-001") is not None
        assert mgr.resolve_trade_from_fill("FILL-ENTRY-001") is not None
        assert mgr.resolve_trade_from_fill("FILL-EXIT-001") is not None
        assert mgr.resolve_trade_from_position("POS-001") is not None
