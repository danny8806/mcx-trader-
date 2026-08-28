"""Position manager for tracking open positions."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from execution.paper_broker import Fill


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class Position:
    """Represents a trading position."""
    position_id: str
    strategy_id: str
    instrument: str
    side: PositionSide
    quantity: int
    average_entry: float
    entry_timestamp: float
    entry_fill_ids: list[str] = field(default_factory=list)
    stop_price: Optional[float] = None
    current_mark: Optional[float] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    margin: float = 0.0
    exit_reason: Optional[str] = None
    exit_fills: list[Fill] = field(default_factory=list)
    status: PositionStatus = PositionStatus.OPEN
    multiplier: float = 1.0

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    def update_mark(self, price: float) -> None:
        """Update current mark price and recalculate unrealized P&L."""
        self.current_mark = price
        if self.is_long:
            self.unrealized_pnl = (price - self.average_entry) * self.quantity * self.multiplier
        else:
            self.unrealized_pnl = (self.average_entry - price) * self.quantity * self.multiplier

    def snapshot(self) -> dict:
        """Get position state for persistence."""
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "side": self.side.value,
            "quantity": self.quantity,
            "average_entry": self.average_entry,
            "entry_timestamp": self.entry_timestamp,
            "entry_fill_ids": self.entry_fill_ids,
            "stop_price": self.stop_price,
            "current_mark": self.current_mark,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "margin": self.margin,
            "exit_reason": self.exit_reason,
            "exit_fills": [
                {
                    "fill_id": f.fill_id,
                    "order_id": f.order_id,
                    "instrument": f.instrument,
                    "side": f.side,
                    "quantity": f.quantity,
                    "price": f.price,
                    "timestamp": f.timestamp,
                    "strategy_id": f.strategy_id,
                    "multiplier": f.multiplier,
                }
                for f in self.exit_fills
            ],
            "status": self.status.value,
            "is_open": self.is_open,
            "multiplier": self.multiplier,
        }


class PositionManager:
    """Manages open positions for all strategies.
    
    Supports multiple strategies independently.
    Each strategy has its own position.
    """

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._closed_positions: list[Position] = []
        self._lock = threading.Lock()

    def open_position(
        self,
        fill: Fill,
        multiplier: float = 1.0,
        stop_price: Optional[float] = None,
        margin: float = 0.0,
    ) -> Position:
        """Open a new position from an entry fill."""
        position = Position(
            position_id=str(uuid.uuid4()),
            strategy_id=fill.strategy_id,
            instrument=fill.instrument,
            side=PositionSide.LONG if fill.side == "BUY" else PositionSide.SHORT,
            quantity=fill.quantity,
            average_entry=fill.price,
            entry_timestamp=fill.timestamp,
            entry_fill_ids=[fill.fill_id],
            stop_price=stop_price,
            current_mark=fill.price,
            margin=margin,
            multiplier=multiplier,
        )
        with self._lock:
            self._positions[position.position_id] = position
        return position

    def close_position(
        self,
        position_id: str,
        fill: Fill,
        reason: str,
    ) -> Position:
        """Close an existing position with an exit fill."""
        with self._lock:
            position = self._positions.get(position_id)
            if not position:
                raise ValueError(f"Position {position_id} not found")

            position.exit_fills.append(fill)
            position.exit_reason = reason
            position.status = PositionStatus.CLOSED

            # Calculate realized P&L
            if position.is_long:
                position.realized_pnl = (
                    (fill.price - position.average_entry)
                    * position.quantity
                    * position.multiplier
                )
            else:
                position.realized_pnl = (
                    (position.average_entry - fill.price)
                    * position.quantity
                    * position.multiplier
                )

            self._closed_positions.append(position)
            if len(self._closed_positions) > 500:
                self._closed_positions = self._closed_positions[-250:]
            del self._positions[position_id]
            return position

    def update_marks(self, prices: dict[str, float]) -> None:
        """Update mark prices for all open positions."""
        with self._lock:
            for pos in self._positions.values():
                price = prices.get(pos.instrument)
                if price is not None:
                    pos.update_mark(price)

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        with self._lock:
            return self._positions.get(position_id)

    def get_positions_by_strategy(self, strategy_id: str) -> list[Position]:
        """Get all positions for a strategy."""
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.strategy_id == strategy_id
            ]

    def get_positions_by_instrument(self, instrument: str) -> list[Position]:
        """Get all positions for an instrument."""
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.instrument == instrument
            ]

    @property
    def open_positions(self) -> list[Position]:
        """All open positions."""
        with self._lock:
            return list(self._positions.values())

    @property
    def closed_positions(self) -> list[Position]:
        """All closed positions."""
        with self._lock:
            return list(self._closed_positions)

    def snapshot(self) -> dict:
        """Get position manager state for persistence."""
        with self._lock:
            return {
                "open_positions": {
                    pid: pos.snapshot()
                    for pid, pos in self._positions.items()
                },
                "closed_count": len(self._closed_positions),
            }

    def restore(self, data: dict) -> None:
        """Restore position manager state from persistence."""
        with self._lock:
            for pid, pos_data in data.get("open_positions", {}).items():
                exit_fills = []
                for f_data in pos_data.get("exit_fills", []):
                    exit_fills.append(Fill(
                        fill_id=f_data["fill_id"],
                        order_id=f_data.get("order_id", ""),
                        instrument=f_data["instrument"],
                        side=f_data["side"],
                        quantity=f_data["quantity"],
                        price=f_data["price"],
                        timestamp=f_data["timestamp"],
                        strategy_id=f_data.get("strategy_id", ""),
                        multiplier=f_data.get("multiplier", 1.0),
                    ))
                pos = Position(
                    position_id=pos_data["position_id"],
                    strategy_id=pos_data["strategy_id"],
                    instrument=pos_data["instrument"],
                    side=PositionSide(pos_data["side"]),
                    quantity=pos_data["quantity"],
                    average_entry=pos_data["average_entry"],
                    entry_timestamp=pos_data["entry_timestamp"],
                    entry_fill_ids=pos_data.get("entry_fill_ids", []),
                    stop_price=pos_data.get("stop_price"),
                    current_mark=pos_data.get("current_mark"),
                    realized_pnl=pos_data.get("realized_pnl", 0.0),
                    unrealized_pnl=pos_data.get("unrealized_pnl", 0.0),
                    margin=pos_data.get("margin", 0.0),
                    exit_reason=pos_data.get("exit_reason"),
                    exit_fills=exit_fills,
                    status=PositionStatus(pos_data.get("status", "open")),
                    multiplier=pos_data.get("multiplier", 1.0),
                )
                self._positions[pid] = pos
