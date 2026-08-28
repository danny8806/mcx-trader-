"""Order manager for centralized order handling."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from .paper_broker import PaperExecutionEngine, Order, OrderState, Fill


class OrderManager:
    """Centralized order manager.
    
    Responsibilities:
    - Receive order intents from strategies
    - Validate orders
    - Assign client order IDs
    - Track order state
    - Process fills
    - Prevent duplicate orders
    """

    def __init__(
        self,
        execution_engine: PaperExecutionEngine,
        on_fill: Optional[Callable[[Fill], None]] = None,
    ):
        self.execution_engine = execution_engine
        self.on_fill = on_fill

        self._pending_signals: dict[str, Any] = {}
        self._active_orders: dict[str, Order] = {}
        self._lock = threading.Lock()

    def submit_signal(self, signal: Any, multiplier: float = 1.0) -> Optional[Order]:
        """Submit a trading signal for execution.
        
        Args:
            signal: Strategy signal (must have strategy_id, instrument, timestamp attributes)
            multiplier: Contract multiplier for the instrument
            
        Returns:
            Order if created, None if rejected
        """
        with self._lock:
            # Check for duplicate signals
            key = f"{signal.strategy_id}:{signal.instrument}:{signal.timestamp}"
            if key in self._pending_signals:
                return None
            self._pending_signals[key] = signal

            # Cleanup old pending signals (> 1 hour old) to prevent memory leak
            import time
            now = time.time()
            stale_keys = [k for k, v in self._pending_signals.items()
                          if hasattr(v, 'timestamp') and now - v.timestamp > 3600]
            for k in stale_keys:
                del self._pending_signals[k]

            # Create order
            order = self.execution_engine.create_order(signal, multiplier=multiplier)
            self._active_orders[order.order_id] = order

            # Execute
            order = self.execution_engine.submit_order(order)

            # Process fill if successful
            if order.state == OrderState.FILLED and order.fill_ids:
                for fill_id in order.fill_ids:
                    fills = self.execution_engine.get_fills(strategy_id=signal.strategy_id)
                    for fill in fills:
                        if fill.fill_id == fill_id:
                            if self.on_fill:
                                self.on_fill(fill)
                            break
            elif order.state == OrderState.REJECTED:
                # Order rejected — clean up memory
                self._pending_signals.pop(key, None)
                self._active_orders.pop(order.order_id, None)
                return None

            return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        with self._lock:
            return self.execution_engine.cancel_order(order_id)

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.execution_engine.get_order(order_id)

    def get_active_orders(
        self,
        strategy_id: Optional[str] = None,
    ) -> list[Order]:
        """Get active orders."""
        orders = [
            o for o in self.execution_engine._orders.values()
            if o.state in (OrderState.CREATED, OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED)
        ]
        if strategy_id:
            orders = [o for o in orders if o.strategy_id == strategy_id]
        return orders

    def snapshot(self) -> dict:
        """Get order manager state."""
        return {
            "pending_signals": len(self._pending_signals),
            "active_orders": len(self._active_orders),
        }
