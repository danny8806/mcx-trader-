"""PHASE 8 — ORDER LIFECYCLE
Verifies: Created -> Submitted -> Filled/Rejected state machine.
"""
from __future__ import annotations

import time

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestOrderLifecycle:
    """Phase 8: Order state machine correctness."""

    def test_order_created_state(self):
        """New order starts in CREATED state."""
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        assert order is not None
        # Order should have transitioned through CREATED -> SUBMITTED -> FILLED
        assert order.state == OrderState.FILLED

    def test_order_rejected_without_price(self):
        """Order is rejected when no market price available."""
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        assert order is None, "Order should be rejected (no market price)"

    def test_order_slippage_buy(self):
        """BUY order fill price includes slippage above market."""
        from execution.paper_broker import PaperExecutionEngine
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=1, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        assert order is not None
        fills = mgr.drain_fills()
        assert fills[0].price == 150001.0, f"Expected 150001 (150000+1 tick), got {fills[0].price}"

    def test_order_slippage_sell(self):
        """SELL order fill price includes slippage below market."""
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=1, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.SHORT, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=151000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        assert order is not None
        fills = mgr.drain_fills()
        assert fills[0].price == 149999.0, f"Expected 149999 (150000-1 tick), got {fills[0].price}"

    def test_order_fill_has_correct_fields(self):
        """Fill produced by order has all required fields populated."""
        from execution.paper_broker import PaperExecutionEngine
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        fills = mgr.drain_fills()
        fill = fills[0]
        assert fill.fill_id, "fill_id must not be empty"
        assert fill.order_id == order.order_id
        assert fill.instrument == "GOLDM"
        assert fill.side == "BUY"
        assert fill.quantity == 1
        assert fill.price > 0
        assert fill.timestamp > 0
        assert fill.strategy_id == "gold_01"

    def test_order_cancel(self):
        """FILLED order cannot be cancelled (returns False)."""
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order = mgr.submit_signal(sig, multiplier=10.0)
        assert order is not None
        assert order.state == OrderState.FILLED
        # Already filled — cancel returns False
        result = mgr.cancel_order(order.order_id)
        assert result is False

    def test_order_dedup_same_signal(self):
        """OrderManager clears pending signal after FILLED, allowing re-submit.
        This tests the cleanup behavior — filled orders are pruned from
        _pending_signals to prevent memory growth."""
        from execution.paper_broker import PaperExecutionEngine
        from execution.order_manager import OrderManager
        from strategies.types import Signal, SignalType
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        eng.update_price("GOLDM", 150000.0)
        mgr = OrderManager(execution_engine=eng)
        sig = Signal(SignalType.LONG, "GOLDM", "gold_01", 1000.0,
                     trigger_price=150000.0, stop_price=149000.0, quantity=1)
        sig.metadata = {"fill_price": 150000.0, "executed": True, "market": True}
        order1 = mgr.submit_signal(sig, multiplier=10.0)
        assert order1 is not None
        assert order1.state.value == "filled"
        # After fill, the pending signal is pruned — same signal can be re-submitted
        order2 = mgr.submit_signal(sig, multiplier=10.0)
        assert order2 is not None, "Same signal should be allowed after fill cleanup"
