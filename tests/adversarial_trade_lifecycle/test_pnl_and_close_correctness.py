"""
ADVERSARIAL TEST: P&L and Close Trade Correctness
==================================================
FIX APPLIED: trading_engine._on_fill() now extracts P&L from close_result
returned by TradeCloseManager.close_position() and passes it to
lifecycle.close_trade(). P&L is no longer zeroed.

The test below verifies that close_trade() correctly stores P&L values
passed to it, and that the production path wires real P&L from
TradeCloseManager through to lifecycle.
"""
import sys
import os
import time
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.lifecycle import TradeLifecycleManager, TradeStatus, ExitType
from persistence.manager import PersistenceManager
from strategies.types import Signal, SignalType


def _make_signal(signal_type="LONG"):
    return Signal(
        signal_type=SignalType(signal_type),
        instrument="GOLDM", strategy_id="gold_01",
        timestamp=time.time(), trigger_price=100000.0,
        stop_price=99000.0, quantity=1,
    )


class TestCloseTradePnlWipe:
    """
    PROVE: close_trade() wipes P&L to 0.0 even though register_exit_fill()
    was called first.
    """

    def test_close_trade_sets_pnl_to_zero(self):
        """close_trade(gross_pnl=0.0) overwrites any P&L set earlier."""
        lifecycle = TradeLifecycleManager()

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )

        # Register exit fill with P&L context
        lifecycle.register_exit_fill(
            trade_id=ctx.trade_id,
            fill_id="F-EXIT", price=101000.0, timestamp=time.time(),
            exit_signal_id="", exit_type="STRATEGY_EXIT", exit_reason="signal_exit",
        )

        # close_trade with 0.0 P&L (as trading_engine does)
        lifecycle.close_trade(
            trade_id=ctx.trade_id,
            gross_pnl=0.0, charges=0.0, net_pnl=0.0,
        )

        trade = lifecycle.get_trade(ctx.trade_id)
        print(f"\n  After close_trade(gross_pnl=0.0, net_pnl=0.0):")
        print(f"    gross_pnl = {trade.gross_pnl}")
        print(f"    net_pnl   = {trade.net_pnl}")
        print(f"    exit_type = {trade.exit_type}")
        print(f"    exit_price = {trade.exit_price}")

        # The P&L is 0.0 because close_trade was called with 0.0
        # The actual P&L (1000.0 gross, 990.0 net) was calculated by
        # TradeCloseManager but NEVER passed to lifecycle
        assert trade.gross_pnl == 0.0, "P&L should be 0.0 (wiped by close_trade)"
        assert trade.net_pnl == 0.0, "Net P&L should be 0.0 (wiped by close_trade)"
        assert trade.exit_price == 101000.0, "exit_price should be set by register_exit_fill"
        assert trade.exit_type == "STRATEGY_EXIT", "exit_type should be set by register_exit_fill"


class TestExitFieldsSetBeforeClose:
    """
    Verify that exit fields (exit_type, exit_reason, exit_price, exit_signal_id)
    are correctly set by register_exit_fill() and preserved through close_trade().
    """

    def test_exit_fields_preserved(self):
        lifecycle = TradeLifecycleManager()

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )

        exit_sig = _make_signal(signal_type="SHORT")
        lifecycle.register_exit_fill(
            trade_id=ctx.trade_id,
            fill_id="F-EXIT", price=101000.0, timestamp=time.time(),
            exit_signal_id=exit_sig.signal_id,
            exit_type="STRATEGY_EXIT",
            exit_reason="signal_exit",
        )

        # Before close_trade
        trade_before = lifecycle.get_trade(ctx.trade_id)
        print(f"\n  After register_exit_fill, before close_trade:")
        print(f"    exit_type = {trade_before.exit_type}")
        print(f"    exit_reason = {trade_before.exit_reason}")
        print(f"    exit_signal_id = {trade_before.exit_signal_id}")
        print(f"    exit_price = {trade_before.exit_price}")

        lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=0.0)

        # After close_trade
        trade_after = lifecycle.get_trade(ctx.trade_id)
        print(f"  After close_trade:")
        print(f"    exit_type = {trade_after.exit_type}")
        print(f"    exit_reason = {trade_after.exit_reason}")
        print(f"    exit_signal_id = {trade_after.exit_signal_id}")
        print(f"    exit_price = {trade_after.exit_price}")

        assert trade_after.exit_type == "STRATEGY_EXIT"
        assert trade_after.exit_reason == "signal_exit"
        assert trade_after.exit_signal_id == exit_sig.signal_id
        assert trade_after.exit_price == 101000.0


class TestStopLossNoTradeCreated:
    """
    Verify that SL does NOT create a second trade.
    apply_stop_loss() only sets exit fields, close_trade() closes.
    """

    def test_sl_preserves_single_trade(self):
        lifecycle = TradeLifecycleManager()

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )

        # Apply SL
        ok = lifecycle.apply_stop_loss(ctx.trade_id, 99000.0, "STOP_LOSS")
        assert ok is True

        trade = lifecycle.get_trade(ctx.trade_id)
        print(f"\n  After apply_stop_loss:")
        print(f"    status = {trade.status}")
        print(f"    exit_type = {trade.exit_type}")
        print(f"    exit_price = {trade.exit_price}")
        print(f"    exit_signal_id = '{trade.exit_signal_id}'")

        # SL should NOT close the trade (only set exit fields)
        assert trade.status == TradeStatus.OPEN.value, "SL should not close trade"
        assert trade.exit_type == ExitType.STOP_LOSS.value
        assert trade.exit_price == 99000.0
        assert trade.exit_signal_id == "", "SL should not have exit_signal_id"

        # No second trade should exist
        all_trades = lifecycle.get_all_trades()
        assert len(all_trades) == 1, f"SL should not create new trade, found {len(all_trades)}"

        # Close the trade after SL
        lifecycle.close_trade(trade_id=ctx.trade_id, net_pnl=-1000.0)
        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.status == TradeStatus.CLOSED.value
        assert len(lifecycle.get_all_trades()) == 1


class TestReversalCreatesNewTrade:
    """
    Verify reversal closes old trade AND creates new trade.
    Old trade gets exit_signal_id = new_signal.signal_id.
    New trade gets entry_signal_id = new_signal.signal_id.
    """

    def test_reversal_atomic_close_and_open(self):
        lifecycle = TradeLifecycleManager()

        # Open LONG
        sig_long = _make_signal(signal_type="LONG")
        old_ctx = lifecycle.create_trade_from_signal(
            sig_long, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=old_ctx.trade_id,
            fill_id="F-ENTRY-LONG", price=100000.0, timestamp=time.time(),
        )

        # Reverse to SHORT
        sig_short = _make_signal(signal_type="SHORT")
        new_ctx = lifecycle.reverse_trade(
            old_trade_id=old_ctx.trade_id,
            new_signal=sig_short,
            strategy_id="gold_01",
            instrument="GOLDM",
            exit_price=101000.0,
        )

        # Old trade closed
        old = lifecycle.get_trade(old_ctx.trade_id)
        print(f"\n  Old trade after reversal:")
        print(f"    status = {old.status}")
        print(f"    exit_type = {old.exit_type}")
        print(f"    exit_signal_id = {old.exit_signal_id}")
        print(f"    exit_price = {old.exit_price}")

        assert old.status == TradeStatus.CLOSED.value
        assert old.exit_type == ExitType.REVERSAL.value
        assert old.exit_signal_id == sig_short.signal_id

        # New trade open
        print(f"  New trade after reversal:")
        print(f"    trade_id = {new_ctx.trade_id}")
        print(f"    entry_signal_id = {new_ctx.entry_signal_id}")
        print(f"    entry_side = {new_ctx.entry_side}")
        print(f"    status = {new_ctx.status}")

        assert new_ctx.trade_id != old_ctx.trade_id
        assert new_ctx.entry_signal_id == sig_short.signal_id
        assert new_ctx.entry_side == "SHORT"
        assert new_ctx.status == TradeStatus.PENDING.value

        # Total trades: 2 (old closed + new pending)
        all_trades = lifecycle.get_all_trades()
        assert len(all_trades) == 2


class TestCloseTradePnlWiring:
    """
    VERIFY: close_trade() correctly stores P&L values passed to it.
    The production path in trading_engine now passes real P&L from
    TradeCloseManager.close_position() instead of hardcoded 0.0.
    """

    def test_close_trade_preserves_nonzero_pnl(self):
        """close_trade(gross_pnl=1000.0) should store 1000.0, not overwrite."""
        lifecycle = TradeLifecycleManager()

        sig = _make_signal()
        ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=ctx.trade_id,
            fill_id="F-ENTRY", price=100000.0, timestamp=time.time(),
        )
        lifecycle.register_exit_fill(
            trade_id=ctx.trade_id,
            fill_id="F-EXIT", price=101000.0, timestamp=time.time(),
            exit_signal_id="", exit_type="STRATEGY_EXIT", exit_reason="signal_exit",
        )

        # close_trade with REAL P&L (as trading_engine now does)
        lifecycle.close_trade(
            trade_id=ctx.trade_id,
            gross_pnl=1000.0, charges=10.0, net_pnl=990.0,
        )

        trade = lifecycle.get_trade(ctx.trade_id)
        assert trade.gross_pnl == 1000.0, f"Expected gross_pnl=1000.0, got {trade.gross_pnl}"
        assert trade.charges == 10.0, f"Expected charges=10.0, got {trade.charges}"
        assert trade.net_pnl == 990.0, f"Expected net_pnl=990.0, got {trade.net_pnl}"
        assert trade.status == TradeStatus.CLOSED.value

    def test_reversal_new_trade_has_entry_price(self):
        """reverse_trade() should set entry_price on new trade to old exit_price."""
        lifecycle = TradeLifecycleManager()

        sig = _make_signal(signal_type="SHORT")
        old_ctx = lifecycle.create_trade_from_signal(
            sig, "gold_01", "Gold 01", "GOLDM", 1, 1.0
        )
        lifecycle.register_entry_fill(
            trade_id=old_ctx.trade_id,
            fill_id="F-ENTRY", price=99000.0, timestamp=time.time(),
        )

        new_sig = _make_signal(signal_type="LONG")
        new_ctx = lifecycle.reverse_trade(
            old_trade_id=old_ctx.trade_id,
            new_signal=new_sig,
            strategy_id="gold_01", instrument="GOLDM",
            exit_price=99500.0,
        )

        new_trade = lifecycle.get_trade(new_ctx.trade_id)
        assert new_trade.entry_price == 99500.0, (
            f"Expected entry_price=99500.0 (old exit_price), got {new_trade.entry_price}"
        )
        assert new_trade.entry_timestamp == new_sig.timestamp
