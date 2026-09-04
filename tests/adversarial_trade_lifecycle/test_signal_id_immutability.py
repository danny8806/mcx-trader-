"""
ADVERSARIAL TEST: Signal ID Immutability & Identity Resolution
==============================================================
Verify that entry_signal_id is NEVER changed after creation.
Verify that identity resolution works correctly after every lifecycle event.
"""
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import TradeLifecycleManager, TradeStatus
from strategies.types import Signal, SignalType


def _make_signal(signal_type="LONG", trigger_price=100000.0):
    return Signal(
        signal_type=SignalType(signal_type),
        instrument="GOLDM", strategy_id="gold_01",
        timestamp=time.time(), trigger_price=trigger_price,
        stop_price=99000.0, quantity=1,
    )


class TestSignalIdImmutability:
    """
    Verify entry_signal_id never changes after trade creation.
    """

    def test_signal_id_unchanged_after_entry_fill(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        original_signal_id = ctx.entry_signal_id
        assert original_signal_id == sig.signal_id

        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.entry_signal_id == original_signal_id, \
            f"entry_signal_id changed after entry fill: {original_signal_id} -> {trade.entry_signal_id}"

    def test_signal_id_unchanged_after_exit_fill(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        original_signal_id = ctx.entry_signal_id

        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )
        lifecycle.register_exit_fill(
            trade_id=ctx.trade_id,
            fill_id="F-EXIT", price=101000.0, timestamp=time.time(),
        )

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.entry_signal_id == original_signal_id

    def test_signal_id_unchanged_after_close(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        original_signal_id = ctx.entry_signal_id

        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )
        lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=500.0)

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.entry_signal_id == original_signal_id

    def test_signal_id_unchanged_after_stop_loss(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)

        original_signal_id = ctx.entry_signal_id

        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )
        lifecycle.apply_stop_loss(ctx.trade_id, 99000.0, "STOP_LOSS")
        lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=-1000.0)

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.entry_signal_id == original_signal_id


class TestIdentityResolutionAfterEvents:
    """
    Verify identity resolution works correctly after every lifecycle event.
    """

    def test_resolve_after_entry_fill(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)
        lifecycle.register_order(ctx.trade_id, "ORD-001", "ENTRY")
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-001", price=100000.0, timestamp=time.time(),
        )
        lifecycle.register_position(ctx.trade_id, "POS-001")

        # All 5 resolve methods should work
        assert lifecycle.resolve_trade_from_signal(sig.signal_id) is not None
        assert lifecycle.resolve_trade_from_order("ORD-001") is not None
        assert lifecycle.resolve_trade_from_fill("F-001") is not None
        assert lifecycle.resolve_trade_from_position("POS-001") is not None
        assert lifecycle.get_trade(ctx.trade_id) is not None

        # All should resolve to the same trade
        from_signal = lifecycle.resolve_trade_from_signal(sig.signal_id)
        from_order = lifecycle.resolve_trade_from_order("ORD-001")
        from_fill = lifecycle.resolve_trade_from_fill("F-001")
        from_position = lifecycle.resolve_trade_from_position("POS-001")

        assert from_signal.trade_id == from_order.trade_id == from_fill.trade_id == from_position.trade_id

    def test_resolve_after_close(self):
        lifecycle = TradeLifecycleManager()
        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0)
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )
        lifecycle.register_exit_fill(
            trade_id=ctx.trade_id,
            fill_id="F-EXIT", price=101000.0, timestamp=time.time(),
        )
        lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=1000.0)

        # Signal resolution should still work after close
        resolved = lifecycle.resolve_trade_from_signal(sig.signal_id)
        assert resolved is not None
        assert resolved.trade_id == ctx.trade_id
        assert resolved.status == TradeStatus.CLOSED.value

        # Fill resolution should work for both entry and exit fills
        entry_resolved = lifecycle.resolve_trade_from_fill("F-ENTRY")
        exit_resolved = lifecycle.resolve_trade_from_fill("F-EXIT")
        assert entry_resolved is not None
        assert exit_resolved is not None
        assert entry_resolved.trade_id == exit_resolved.trade_id == ctx.trade_id
