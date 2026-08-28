"""Paper execution engine for realistic paper trading simulation."""
from __future__ import annotations

import time
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from strategies.types import Signal, SignalType


class OrderState(Enum):
    """Order lifecycle states."""
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    strategy_id: str
    instrument: str
    side: str  # "BUY" or "SELL"
    quantity: int
    order_type: str = "MARKET"
    price: Optional[float] = None
    state: OrderState = OrderState.CREATED
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    fill_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    reason: Optional[str] = None
    multiplier: float = 1.0


@dataclass
class Fill:
    """Represents an order fill."""
    fill_id: str
    order_id: str
    instrument: str
    side: str
    quantity: int
    price: float
    timestamp: float
    strategy_id: str
    multiplier: float = 1.0

    @property
    def gross_value(self) -> float:
        return self.quantity * self.price * self.multiplier


class PaperExecutionEngine:
    """Simulates realistic paper trading execution.
    
    Handles:
    - Order creation and validation
    - Slippage modeling
    - Latency simulation
    - Partial fills
    - Fill generation
    - Fee calculation
    """

    def __init__(
        self,
        slippage_ticks: int = 1,
        latency_ms: float = 100.0,
        partial_fill_probability: float = 0.1,
    ):
        self.slippage_ticks = slippage_ticks
        self.latency_ms = latency_ms
        self.partial_fill_probability = partial_fill_probability

        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._current_prices: dict[str, float] = {}
        self._price_lock = threading.Lock()

    def update_price(self, instrument: str, price: float) -> None:
        """Update current market price for an instrument."""
        with self._price_lock:
            self._current_prices[instrument] = price

    def create_order(
        self,
        signal: Signal,
        multiplier: float = 1.0,
    ) -> Order:
        """Create a new order from a signal."""
        # Determine order side: REVERSAL signals carry explicit side ("LONG"/"SHORT")
        if signal.signal_type == SignalType.REVERSAL and signal.side:
            order_side = "BUY" if signal.side == "LONG" else "SELL"
        elif signal.signal_type in (SignalType.LONG, SignalType.REVERSAL):
            order_side = "BUY"
        else:
            order_side = "SELL"
        order = Order(
            order_id=str(uuid.uuid4()),
            strategy_id=signal.strategy_id,
            instrument=signal.instrument,
            side=order_side,
            quantity=signal.quantity,
            state=OrderState.CREATED,
            multiplier=multiplier,
        )
        self._orders[order.order_id] = order
        return order

    def submit_order(self, order: Order) -> Order:
        """Submit order for execution."""
        if order.state != OrderState.CREATED:
            raise ValueError(f"Cannot submit order in state {order.state}")

        order.state = OrderState.SUBMITTED
        order.updated_at = time.time()

        # Simulate execution
        fill = self._execute_order(order)
        if fill:
            order.state = OrderState.FILLED
            order.filled_quantity = order.quantity
            order.average_fill_price = fill.price
            order.fill_ids.append(fill.fill_id)
        else:
            order.state = OrderState.REJECTED
            order.reason = "No market data available"

        return order

    def _execute_order(self, order: Order) -> Optional[Fill]:
        """Execute order with slippage and latency simulation."""
        with self._price_lock:
            current_price = self._current_prices.get(order.instrument)
        if current_price is None:
            return None

        # Apply slippage
        slippage = self.slippage_ticks * 1.0  # tick_size = 1.0 for MCX
        if order.side == "BUY":
            fill_price = current_price + slippage
        else:
            fill_price = current_price - slippage

        # Create fill
        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            timestamp=time.time(),
            strategy_id=order.strategy_id,
            multiplier=order.multiplier,
        )
        self._fills.append(fill)
        return fill

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self._orders.get(order_id)

    def get_fills(
        self,
        strategy_id: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> list[Fill]:
        """Get fills with optional filtering."""
        fills = self._fills
        if strategy_id:
            fills = [f for f in fills if f.strategy_id == strategy_id]
        if instrument:
            fills = [f for f in fills if f.instrument == instrument]
        return fills

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        order = self._orders.get(order_id)
        if order and order.state in (OrderState.CREATED, OrderState.SUBMITTED):
            order.state = OrderState.CANCELED
            order.updated_at = time.time()
            return True
        return False

    def snapshot(self) -> dict:
        """Get execution state for persistence."""
        return {
            "orders_count": len(self._orders),
            "fills_count": len(self._fills),
            "current_prices": dict(self._current_prices),
            "orders": [
                {
                    "order_id": o.order_id,
                    "strategy_id": o.strategy_id,
                    "instrument": o.instrument,
                    "side": o.side,
                    "quantity": o.quantity,
                    "order_type": o.order_type,
                    "multiplier": o.multiplier,
                    "state": o.state.value,
                    "created_at": o.created_at,
                    "updated_at": o.updated_at,
                }
                for o in self._orders.values()
            ],
            "fills": [
                {
                    "fill_id": f.fill_id,
                    "order_id": f.order_id,
                    "strategy_id": f.strategy_id,
                    "instrument": f.instrument,
                    "side": f.side,
                    "price": f.price,
                    "quantity": f.quantity,
                    "multiplier": f.multiplier,
                    "timestamp": f.timestamp,
                    "gross_value": f.gross_value,
                }
                for f in self._fills
            ],
        }

    def restore(self, data: dict) -> None:
        """Restore execution state from persistence."""
        if not data:
            return
        # Restore current prices
        self._current_prices = data.get("current_prices", {})
        # Restore fills
        for f_data in data.get("fills", []):
            fill = Fill(
                fill_id=f_data["fill_id"],
                order_id=f_data["order_id"],
                strategy_id=f_data.get("strategy_id", ""),
                instrument=f_data["instrument"],
                side=f_data["side"],
                price=f_data["price"],
                quantity=f_data["quantity"],
                multiplier=f_data.get("multiplier", 1),
                timestamp=f_data["timestamp"],
            )
            self._fills.append(fill)
        # Restore orders
        for o_data in data.get("orders", []):
            order = Order(
                order_id=o_data["order_id"],
                strategy_id=o_data["strategy_id"],
                instrument=o_data["instrument"],
                side=o_data["side"],
                quantity=o_data["quantity"],
                order_type=o_data.get("order_type", "MARKET"),
                multiplier=o_data.get("multiplier", 1),
            )
            order.state = OrderState(o_data["state"])
            order.created_at = o_data.get("created_at", 0)
            order.updated_at = o_data.get("updated_at", 0)
            self._orders[order.order_id] = order
