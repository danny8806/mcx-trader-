"""Shared type definitions for strategies and execution to avoid circular imports.

This module MUST NOT import anything from core, execution, htf, or strategies modules.
It only uses Python standard library types.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SignalType(Enum):
    """Signal types."""
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"
    REVERSAL = "REVERSAL"


class StrategyState(Enum):
    """Strategy state machine states."""
    FLAT = "flat"
    SIGNAL_LONG = "signal_long"
    SIGNAL_SHORT = "signal_short"
    PENDING_LONG = "pending_long"
    PENDING_SHORT = "pending_short"
    ENTRY_TRIGGERED = "entry_triggered"
    LONG_POSITION = "long_position"
    SHORT_POSITION = "short_position"
    STOP_ACTIVE = "stop_active"
    EXIT_PENDING = "exit_pending"
    EXIT_ORDER_SUBMITTED = "exit_order_submitted"


class OrderState(Enum):
    """Order lifecycle states."""
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"


@dataclass
class Signal:
    """Strategy signal output.

    Every signal gets a unique signal_id (UUID) that travels through the
    entire lifecycle: Signal -> Order -> Fill -> Position -> Trade.
    This provides complete signal lineage for audit and reconciliation.
    """
    signal_type: 'SignalType'
    instrument: str
    strategy_id: str
    timestamp: float
    trigger_price: float
    stop_price: float
    quantity: int
    side: Optional[str] = None  # "LONG" or "SHORT" — used for REVERSAL to determine order direction
    metadata: Optional[dict] = None
    signal_id: str = field(default_factory=lambda: str(_uuid.uuid4()))


@dataclass
class PendingEntry:
    """Pending entry order."""
    signal: 'Signal'
    trigger_price: float
    side: str  # "LONG" or "SHORT"
    status: str = "pending"
    created_at: float = 0.0  # timestamp when pending entry was created
    bars_pending: int = 0    # number of bars since creation
    immediate: bool = False  # direct-market re-entry: engine flips right after a reversal exit


@dataclass
class StrategyInput:
    """Input data for strategy decision."""
    instrument: str
    timestamp: float
    fast_bar: 'Bar'  # Forward reference
    fast_close: float
    fast_high: float
    fast_low: float
    previous_fast_close: float
    fast_dema_atr: float
    htf_dema_atr: Optional[float]
    previous_htf_dema_atr: Optional[float]
    htf_confirmed: bool
    htf_source_timestamp: Optional[float]


# Forward reference for type hints - Bar is imported at runtime in base_dema_strategy
Bar = Any  # Placeholder, actual import happens in base_dema_strategy