"""PHASE 10 — STOP-LOSS / EXIT LOGIC
Verifies: SL trigger conditions, exit reasons, reversal handling.
"""
from __future__ import annotations

import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestStopLossExit:
    """Phase 10: SL and exit verification."""

    def test_sl_long_trigger(self):
        """LONG SL triggers when LTP <= stop_price."""
        from strategies.gold import GoldStrategy01
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.position_side = "LONG"
        strat.stop_price = 149000.0
        strat.state = "long_position"
        # LTP exactly at stop
        result = strat.on_tick(ltp=149000.0, timestamp=1001.0)
        assert result is not None
        assert result.metadata.get("exit_reason") == "stop_loss_hit"
        assert result.metadata.get("exit") is True

    def test_sl_short_trigger(self):
        """SHORT SL triggers when LTP >= stop_price."""
        from strategies.silver import SilverStrategy01
        strat = SilverStrategy01(strategy_id="silver_01", instrument="SILVERM",
                                fast_timeframe="15m", htf_timeframe="1h")
        strat.position_side = "SHORT"
        strat.stop_price = 96000.0
        strat.state = "short_position"
        # LTP above stop
        result = strat.on_tick(ltp=96001.0, timestamp=1001.0)
        assert result is not None
        assert result.metadata.get("exit_reason") == "stop_loss_hit"

    def test_no_sl_above_trigger_long(self):
        """LONG position NOT exited when LTP > stop_price."""
        from strategies.gold import GoldStrategy01
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.position_side = "LONG"
        strat.stop_price = 149000.0
        strat.state = "long_position"
        result = strat.on_tick(ltp=149500.0, timestamp=1001.0)
        assert result is None

    def test_no_sl_below_trigger_short(self):
        """SHORT position NOT exited when LTP < stop_price."""
        from strategies.silver import SilverStrategy01
        strat = SilverStrategy01(strategy_id="silver_01", instrument="SILVERM",
                                fast_timeframe="15m", htf_timeframe="1h")
        strat.position_side = "SHORT"
        strat.stop_price = 96000.0
        strat.state = "short_position"
        result = strat.on_tick(ltp=95500.0, timestamp=1001.0)
        assert result is None

    def test_pending_entry_trigger(self):
        """Pending entry triggers when LTP > trigger_price (strict >)."""
        from strategies.gold import GoldStrategy01
        from strategies.types import Signal, SignalType, PendingEntry, StrategyState
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        inner_signal = Signal(
            SignalType.LONG, "GOLDM", "gold_01", 1000.0,
            trigger_price=150000.0, stop_price=149000.0, quantity=1,
        )
        strat.pending_entry = PendingEntry(
            signal=inner_signal, trigger_price=150000.0, side="LONG",
        )
        strat.state = "pending_long"
        # LTP above trigger (strict > required)
        result = strat.on_tick(ltp=150001.0, timestamp=1001.0)
        assert result is not None
        assert result.signal_type == SignalType.LONG
        assert strat.pending_entry is None
        assert strat.state == StrategyState.LONG_POSITION

    def test_pending_entry_no_trigger(self):
        """Pending entry does NOT trigger when LTP <= trigger_price."""
        from strategies.gold import GoldStrategy01
        from strategies.types import Signal, SignalType, PendingEntry
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        inner_signal = Signal(
            SignalType.LONG, "GOLDM", "gold_01", 1000.0,
            trigger_price=150000.0, stop_price=149000.0, quantity=1,
        )
        strat.pending_entry = PendingEntry(
            signal=inner_signal, trigger_price=150000.0, side="LONG",
        )
        strat.state = "pending_long"
        result = strat.on_tick(ltp=149500.0, timestamp=1001.0)
        assert result is None
        assert strat.pending_entry is not None

    def test_sl_after_state_change(self):
        """After SL trigger, strategy state changes from long_position to exit_order_submitted."""
        from strategies.gold import GoldStrategy01
        from strategies.types import StrategyState
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.position_side = "LONG"
        strat.stop_price = 149000.0
        strat.state = "long_position"
        strat.on_tick(ltp=148500.0, timestamp=1001.0)
        assert strat.state == StrategyState.EXIT_ORDER_SUBMITTED
