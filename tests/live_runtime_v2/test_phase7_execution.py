"""
PHASE 7 — SIGNAL -> EXECUTION VERIFICATION
===========================================
Verify that the execution layer does exactly what the live specification requires.
"""
from __future__ import annotations

import math
import os
import tempfile
import time
from typing import Optional

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence, REPORT_DIR


def _fill(fid, oid, strat, inst, side, qty, price, ts=None):
    from execution.paper_broker import Fill
    return Fill(fill_id=fid, order_id=oid, strategy_id=strat, instrument=inst,
                side=side, quantity=qty, price=price, timestamp=ts or time.time())


class TestSignalToExecution:
    """Phase 7: Signal -> execution verification."""

    def test_tick_triggers_pending_entry(self):
        """Tick above trigger price fills pending entry."""
        from strategies.gold import GoldStrategy01
        from strategies.types import PendingEntry, Signal, SignalType
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.pending_entry = PendingEntry(
            signal=Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                         trigger_price=100.0, stop_price=99.0, quantity=1),
            trigger_price=100.0, side="LONG")
        strat.state = "pending_long"
        result = strat.on_tick(ltp=101.0, timestamp=1001.0)
        assert result is not None, "Tick above trigger should fire signal"
        assert result.trigger_price == 100.0, f"Fill price should be trigger, got {result.trigger_price}"
        assert strat.position_side == "LONG"
        assert strat.pending_entry is None

    def test_tick_below_trigger_no_fill(self):
        """Tick below trigger price does NOT fill pending LONG entry."""
        from strategies.gold import GoldStrategy01
        from strategies.types import PendingEntry, Signal, SignalType
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.pending_entry = PendingEntry(
            signal=Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                         trigger_price=100.0, stop_price=99.0, quantity=1),
            trigger_price=100.0, side="LONG")
        result = strat.on_tick(ltp=99.0, timestamp=1001.0)
        assert result is None, "Tick below trigger should NOT fill"

    def test_tick_stop_loss_long(self):
        """Tick at/below stop price triggers SL exit for LONG."""
        from strategies.gold import GoldStrategy01
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.position_side = "LONG"
        strat.stop_price = 99.0
        strat.state = "long_position"
        result = strat.on_tick(ltp=98.5, timestamp=1001.0)
        assert result is not None, "Tick at SL should trigger exit"
        assert result.metadata.get("exit") is True
        assert result.metadata.get("exit_reason") == "stop_loss_hit"
        # _close_position sets EXIT_ORDER_SUBMITTED state but doesn't clear position_side
        # position_side is cleared by the engine after the fill is processed
        assert strat.state.value == "exit_order_submitted"

    def test_tick_stop_loss_short(self):
        """Tick at/above stop price triggers SL exit for SHORT."""
        from strategies.silver import SilverStrategy01
        strat = SilverStrategy01(strategy_id="silver_01", instrument="SILVERM",
                                fast_timeframe="15m", htf_timeframe="1h")
        strat.position_side = "SHORT"
        strat.stop_price = 101.0
        strat.state = "short_position"
        result = strat.on_tick(ltp=101.5, timestamp=1001.0)
        assert result is not None
        assert result.metadata.get("exit") is True

    def test_negative_ltp_ignored(self):
        """Negative LTP is completely ignored (sentinel guard)."""
        from strategies.gold import GoldStrategy01
        from strategies.types import PendingEntry, Signal, SignalType
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.pending_entry = PendingEntry(
            signal=Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                         trigger_price=100.0, stop_price=99.0, quantity=1),
            trigger_price=100.0, side="LONG")
        result = strat.on_tick(ltp=-1.0, timestamp=1001.0)
        assert result is None, "Negative LTP should be ignored"
        assert strat.pending_entry is not None, "Pending entry should remain"

    def test_nan_ltp_ignored(self):
        """NaN LTP is completely ignored."""
        from strategies.gold import GoldStrategy01
        from strategies.types import PendingEntry, Signal, SignalType
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.pending_entry = PendingEntry(
            signal=Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                         trigger_price=100.0, stop_price=99.0, quantity=1),
            trigger_price=100.0, side="LONG")
        result = strat.on_tick(ltp=float("nan"), timestamp=1001.0)
        assert result is None

    def test_zero_ltp_ignored(self):
        """Zero LTP is completely ignored."""
        from strategies.gold import GoldStrategy01
        from strategies.types import PendingEntry, Signal, SignalType
        strat = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                              fast_timeframe="5m", htf_timeframe="1h")
        strat.pending_entry = PendingEntry(
            signal=Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                         trigger_price=100.0, stop_price=99.0, quantity=1),
            trigger_price=100.0, side="LONG")
        result = strat.on_tick(ltp=0.0, timestamp=1001.0)
        assert result is None

    def test_paper_broker_executes_order(self):
        """PaperExecutionEngine creates fill on order."""
        from execution.paper_broker import PaperExecutionEngine, Fill
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        signal = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                       trigger_price=150000.0, stop_price=149000.0, quantity=1)
        signal.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(signal, multiplier=10.0, trade_id="TRD-E1")
        assert order is not None, "Order should be created"
        fills = mgr.drain_fills()
        assert len(fills) >= 1, f"Expected at least 1 fill, got {len(fills)}"
        assert fills[0].price == 150000.0

    def test_order_manager_dedup(self):
        """OrderManager produces unique order IDs."""
        from execution.paper_broker import PaperExecutionEngine
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        ids = set()
        for i in range(10):
            sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0 + i,
                        trigger_price=150000.0, stop_price=149000.0, quantity=1)
            sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
            order = mgr.submit_signal(sig, multiplier=10.0, trade_id=f"TRD-{i}")
            assert order is not None, f"Order {i} should be created"
            ids.add(order.order_id)
        assert len(ids) == 10, f"All order IDs should be unique, got {len(ids)} unique out of 10"
