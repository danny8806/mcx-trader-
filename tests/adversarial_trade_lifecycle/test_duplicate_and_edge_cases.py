"""
ADVERSARIAL TEST: Duplicate Events & Idempotency
=================================================
Verify that sending the same event twice doesn't create duplicates.
"""
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import TradeLifecycleManager, TradeStatus
from strategies.types import Signal, SignalType


def _make_signal():
    return Signal(
        signal_type=SignalType.LONG, instrument="GOLDM",
        strategy_id="gold_01", timestamp=time.time(),
        trigger_price=100000.0, stop_price=99000.0, quantity=1,
    )


class TestDuplicateEvents:
    """
    Verify idempotency of lifecycle operations.
    """

    def test_duplicate_signal_creates_separate_trades(self):
        """Each signal gets its own trade_id (signal_id is unique per Signal)."""
        lifecycle = TradeLifecycleManager()
        sig1 = _make_signal()
        sig2 = _make_signal()  # Different UUID

        ctx1 = lifecycle.create_trade_from_signal(sig1, "gold_01", "Gold 01", "GOLDM", 1, 1.0)
        ctx2 = lifecycle.create_trade_from_signal(sig2, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        assert ctx1.trade_id != ctx2.trade_id
        assert len(lifecycle.get_all_trades()) == 2

    def test_duplicate_close_is_idempotent(self):
        """Closing an already-closed trade should be a no-op."""
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )

        # First close
        ok1 = lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=500.0)
        assert ok1 is True

        # Second close (should be idempotent)
        ok2 = lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=1000.0)
        assert ok2 is True

        # P&L should still be 500.0 (from first close)
        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.net_pnl == 500.0, \
            f"Duplicate close should not overwrite P&L: expected 500.0, got {trade.net_pnl}"

    def test_duplicate_entry_fill_overwrites(self):
        """register_entry_fill with same fill_id overwrites (INSERT OR REPLACE)."""
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )
        # Register again with different price (simulating duplicate event with drift)
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100001.0, timestamp=time.time(),
        )

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.entry_fill_id == "F-001"
        assert trade.entry_price == 100001.0, \
            f"Duplicate fill should overwrite price: expected 100001.0, got {trade.entry_price}"


class TestEdgeCaseNoLifecycleTrade:
    """
    Verify behavior when lifecycle has no trade for a given signal.
    """

    def test_register_order_unknown_trade_returns_false(self):
        lifecycle = TradeLifecycleManager()
        ok = lifecycle.register_order("NONEXISTENT-TRADE", "ORD-001", "ENTRY")
        assert ok is False

    def test_register_entry_fill_unknown_trade_returns_false(self):
        lifecycle = TradeLifecycleManager()
        ok = lifecycle.register_entry_fill("NONEXISTENT-TRADE", "F-001", 100000.0)
        assert ok is False

    def test_close_unknown_trade_returns_false(self):
        lifecycle = TradeLifecycleManager()
        ok = lifecycle.close_trade("NONEXISTENT-TRADE", net_pnl=0.0)
        assert ok is False

    def test_apply_stop_loss_unknown_trade_returns_false(self):
        lifecycle = TradeLifecycleManager()
        ok = lifecycle.apply_stop_loss("NONEXISTENT-TRADE", 99000.0)
        assert ok is False

    def test_reverse_unknown_trade_returns_none(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        result = lifecycle.reverse_trade("NONEXISTENT-TRADE", sig, "gold_01")
        assert result is None
