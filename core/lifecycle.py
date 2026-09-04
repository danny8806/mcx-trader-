"""Central Trade Lifecycle — single source of truth for all trade identity.

Architecture:
    Signal → TradeContext → PendingOrder → Order → Fill → Position → Exit → DB → API → Frontend

One trade = one trade_id. Immutable. Every lifecycle object references trade_id.
No strategy, manager, or component may independently create or modify trade identity.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class TradeStatus(str, Enum):
    PENDING = "PENDING"          # Signal received, waiting for trigger
    OPEN = "OPEN"                # Position active
    EXIT_PENDING = "EXIT_PENDING"  # Exit order submitted
    CLOSED = "CLOSED"            # Position closed, P&L finalized
    REJECTED = "REJECTED"        # Signal rejected by risk/market gate
    CANCELLED = "CANCELLED"      # Pending order expired


class ExitType(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    REVERSAL = "REVERSAL"
    STRATEGY_EXIT = "STRATEGY_EXIT"
    MANUAL = "MANUAL"
    FORCED = "FORCED"
    TIMEOUT = "TIMEOUT"
    SAME_BAR_STOP = "SAME_BAR_STOP"


class OrderRole(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REVERSAL_EXIT = "REVERSAL_EXIT"
    REVERSAL_ENTRY = "REVERSAL_ENTRY"


class SignalEventType(str, Enum):
    ENTRY_LONG = "ENTRY_LONG"
    ENTRY_SHORT = "ENTRY_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"


# ─────────────────────────────────────────────
# TradeContext — the canonical trade identity
# ─────────────────────────────────────────────

@dataclass
class TradeContext:
    """One trade. One trade_id. Immutable identity. Central authority.

    Every lifecycle object (pending_order, order, fill, position, exit)
    references this trade_id. This is the single source of truth.
    """

    # ── Identity (immutable once created) ──
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = ""
    strategy_name: str = ""
    instrument: str = ""
    security_id: str = ""

    # ── Entry signal ──
    entry_signal_id: str = ""          # COMPULSORY — every trade has this
    entry_side: str = ""               # "LONG" or "SHORT"
    entry_action: str = ""             # "BUY" or "SELL"
    entry_event_type: str = ""         # "ENTRY_LONG" or "ENTRY_SHORT"

    # ── Entry trigger / fill ──
    entry_trigger_price: float = 0.0   # Signal candle HIGH/LOW
    entry_price: float = 0.0           # Actual fill price
    entry_timestamp: float = 0.0

    # ── Stop loss ──
    stop_loss_price: float = 0.0

    # ── Position ──
    position_id: str = ""              # separate child identity
    quantity: int = 0
    multiplier: float = 1.0
    margin: float = 0.0

    # ── Pending order (if waiting for trigger) ──
    pending_order_id: str = ""
    pending_status: str = ""           # "pending", "triggered", "expired"

    # ── Entry order/fill ──
    entry_order_id: str = ""
    entry_fill_id: str = ""

    # ── Exit (optional — NULL until closed) ──
    exit_signal_id: str = ""           # OPTIONAL — NULL for SL
    exit_order_id: str = ""
    exit_fill_id: str = ""
    exit_type: str = ""                # ExitType value
    exit_action: str = ""              # "BUY" or "SELL"
    exit_event_type: str = ""          # "EXIT_LONG" or "EXIT_SHORT"
    exit_reason: str = ""              # STOP_LOSS, REVERSAL, etc.
    exit_price: float = 0.0
    exit_timestamp: float = 0.0

    # ── P&L ──
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0

    # ── Status ──
    status: str = TradeStatus.PENDING.value

    # ── Signal metadata (for audit) ──
    signal_candle_open: float = 0.0
    signal_candle_high: float = 0.0
    signal_candle_low: float = 0.0
    signal_candle_close: float = 0.0
    signal_htf_value: float = 0.0
    signal_mid_value: float = 0.0
    signal_fast_dema: float = 0.0
    signal_fast_atr: float = 0.0
    signal_reason: str = ""

    # ── Timestamps ──
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: float = 0.0

    # ── Snapshot metadata ──
    bars_processed: int = 0
    strategy_version: str = "v1"

    def snapshot(self) -> dict:
        """Serialize for DB persistence and API responses."""
        return {
            "trade_id": self.trade_id,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "instrument": self.instrument,
            "security_id": self.security_id,
            "side": self.entry_side,  # DB requires 'side' NOT NULL
            "entry_signal_id": self.entry_signal_id,
            "entry_side": self.entry_side,
            "entry_action": self.entry_action,
            "entry_event_type": self.entry_event_type,
            "entry_trigger_price": self.entry_trigger_price,
            "entry_price": self.entry_price,
            "entry_timestamp": self.entry_timestamp,
            "stop_loss_price": self.stop_loss_price,
            "position_id": self.position_id,
            "quantity": self.quantity,
            "multiplier": self.multiplier,
            "margin": self.margin,
            "pending_order_id": self.pending_order_id,
            "pending_status": self.pending_status,
            "entry_order_id": self.entry_order_id,
            "entry_fill_id": self.entry_fill_id,
            "exit_signal_id": self.exit_signal_id,
            "exit_order_id": self.exit_order_id,
            "exit_fill_id": self.exit_fill_id,
            "exit_type": self.exit_type,
            "exit_action": self.exit_action,
            "exit_event_type": self.exit_event_type,
            "exit_reason": self.exit_reason,
            "exit_price": self.exit_price,
            "exit_timestamp": self.exit_timestamp,
            "gross_pnl": self.gross_pnl,
            "charges": self.charges,
            "net_pnl": self.net_pnl,
            "status": self.status,
            "signal_candle_open": self.signal_candle_open,
            "signal_candle_high": self.signal_candle_high,
            "signal_candle_low": self.signal_candle_low,
            "signal_candle_close": self.signal_candle_close,
            "signal_htf_value": self.signal_htf_value,
            "signal_mid_value": self.signal_mid_value,
            "signal_fast_dema": self.signal_fast_dema,
            "signal_fast_atr": self.signal_fast_atr,
            "signal_reason": self.signal_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "bars_processed": self.bars_processed,
            "strategy_version": self.strategy_version,
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "TradeContext":
        """Restore from DB/API snapshot."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────
# Lifecycle Event Log
# ─────────────────────────────────────────────

@dataclass
class LifecycleEvent:
    """Immutable audit record of a lifecycle transition."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trade_id: str = ""
    event_type: str = ""          # SIGNAL_CREATED, TRADE_CREATED, PENDING_ORDER_CREATED, etc.
    signal_id: str = ""
    order_id: str = ""
    fill_id: str = ""
    position_id: str = ""
    timestamp: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)


# ─────────────────────────────────────────────
# TradeLifecycleManager — the central authority
# ─────────────────────────────────────────────

class TradeLifecycleManager:
    """Central authority for all trade identity and lifecycle.

    Responsibilities:
    - Create trade_id (one place, one time)
    - Map signal → trade
    - Map pending_order → trade
    - Map order → trade
    - Map fill → trade
    - Map position → trade
    - Handle entry, exit, SL, reversal
    - Persist canonical state
    - Provide authoritative queries

    No strategy, order manager, position manager, or other component
    may independently create or modify trade identity.
    """

    def __init__(self, persistence=None, event_store=None, trade_ledger=None):
        self._lock = threading.RLock()
        self._persistence = persistence
        self._event_store = event_store
        self._trade_ledger = trade_ledger

        # ── Canonical in-memory state ──
        self._trades: Dict[str, TradeContext] = {}          # trade_id → TradeContext
        self._signal_to_trade: Dict[str, str] = {}          # signal_id → trade_id
        self._order_to_trade: Dict[str, str] = {}           # order_id → trade_id
        self._fill_to_trade: Dict[str, str] = {}            # fill_id → trade_id
        self._position_to_trade: Dict[str, str] = {}        # position_id → trade_id
        self._pending_to_trade: Dict[str, str] = {}         # pending_order_id → trade_id

        # ── Event log ──
        self._events: List[LifecycleEvent] = []

    # ═══════════════════════════════════════════
    # ID RESOLUTION — one authoritative resolver
    # ═══════════════════════════════════════════

    def resolve_trade_from_signal(self, signal_id: str) -> Optional[TradeContext]:
        trade_id = self._signal_to_trade.get(signal_id)
        if trade_id:
            return self.get_trade(trade_id)
        return self._load_trade_by("entry_signal_id", signal_id)

    def resolve_trade_from_order(self, order_id: str) -> Optional[TradeContext]:
        trade_id = self._order_to_trade.get(order_id)
        if trade_id:
            return self.get_trade(trade_id)
        return self._load_trade_by("entry_order_id", order_id) or self._load_trade_by(
            "exit_order_id", order_id
        )

    def resolve_trade_from_fill(self, fill_id: str) -> Optional[TradeContext]:
        trade_id = self._fill_to_trade.get(fill_id)
        if trade_id:
            return self.get_trade(trade_id)
        return self._load_trade_by("entry_fill_id", fill_id) or self._load_trade_by(
            "exit_fill_id", fill_id
        )

    def resolve_trade_from_position(self, position_id: str) -> Optional[TradeContext]:
        trade_id = self._position_to_trade.get(position_id)
        if trade_id:
            return self.get_trade(trade_id)
        return self._load_trade_by("position_id", position_id)

    def resolve_trade_from_pending(self, pending_order_id: str) -> Optional[TradeContext]:
        trade_id = self._pending_to_trade.get(pending_order_id)
        return self._trades.get(trade_id) if trade_id else None

    def get_trade(self, trade_id: str) -> Optional[TradeContext]:
        trade = self._trades.get(trade_id)
        if trade is not None or not self._persistence:
            return trade
        rows = self._persistence.get_trades()
        for data in rows:
            if data.get("trade_id") == trade_id:
                return self._cache_restored_trade(data)
        return None

    def _load_trade_by(self, field: str, value: str) -> Optional[TradeContext]:
        """Load an explicitly linked trade after a runtime-cache miss."""
        if not self._persistence or field not in {
            "entry_signal_id", "entry_order_id", "exit_order_id",
            "entry_fill_id", "exit_fill_id", "position_id",
        }:
            return None
        for data in self._persistence.get_trades():
            if data.get(field) == value:
                return self._cache_restored_trade(data)
        return None

    def _cache_restored_trade(self, data: dict) -> TradeContext:
        trade = TradeContext.from_snapshot(data)
        for field in ("entry_timestamp", "exit_timestamp"):
            if isinstance(getattr(trade, field), str):
                try:
                    setattr(trade, field, datetime.fromisoformat(getattr(trade, field)).timestamp())
                except (TypeError, ValueError):
                    setattr(trade, field, 0.0)
        self._trades[trade.trade_id] = trade
        if trade.entry_signal_id:
            self._signal_to_trade[trade.entry_signal_id] = trade.trade_id
        if trade.exit_signal_id:
            self._signal_to_trade[trade.exit_signal_id] = trade.trade_id
        if trade.entry_order_id:
            self._order_to_trade[trade.entry_order_id] = trade.trade_id
        if trade.exit_order_id:
            self._order_to_trade[trade.exit_order_id] = trade.trade_id
        if trade.entry_fill_id:
            self._fill_to_trade[trade.entry_fill_id] = trade.trade_id
        if trade.exit_fill_id:
            self._fill_to_trade[trade.exit_fill_id] = trade.trade_id
        if trade.position_id:
            self._position_to_trade[trade.position_id] = trade.trade_id
        if trade.pending_order_id:
            self._pending_to_trade[trade.pending_order_id] = trade.trade_id
        return trade

    def get_open_trades(self) -> List[TradeContext]:
        return [t for t in self._trades.values() if t.status in (
            TradeStatus.PENDING.value, TradeStatus.OPEN.value, TradeStatus.EXIT_PENDING.value
        )]

    def get_closed_trades(self) -> List[TradeContext]:
        return [t for t in self._trades.values() if t.status == TradeStatus.CLOSED.value]

    def get_all_trades(self) -> List[TradeContext]:
        return list(self._trades.values())

    # ═══════════════════════════════════════════
    # TRADE CREATION — the ONLY place trade_id is born
    # ═══════════════════════════════════════════

    def create_trade_from_signal(self, signal, strategy_id: str, strategy_name: str = "",
                                  instrument: str = "", quantity: int = 0,
                                  multiplier: float = 1.0, **kwargs) -> TradeContext:
        """Create a new trade from a signal. This is the ONLY trade creation point.

        Args:
            signal: Signal dataclass (must have signal_id, signal_type, instrument, etc.)
            strategy_id: Strategy identifier
            strategy_name: Human-readable strategy name
            instrument: Trading instrument
            quantity: Order quantity
            multiplier: Contract multiplier
            **kwargs: Additional fields (stop_loss_price, signal candle data, etc.)

        Returns:
            TradeContext with new trade_id, linked to signal_id.
        """
        with self._lock:
            from strategies.types import SignalType

            # Determine side and action
            side = signal.side or ("LONG" if signal.signal_type in (SignalType.LONG,) else "SHORT")
            if signal.signal_type == SignalType.SHORT:
                side = "SHORT"
            elif signal.signal_type == SignalType.LONG:
                side = "LONG"

            # Resolve action and event type from semantic meaning
            metadata = signal.metadata or {}
            is_exit = bool(metadata.get("exit"))

            if is_exit:
                # Exit signal: side tells us what position we're closing
                action = "BUY" if side == "SHORT" else "SELL"  # opposite
                event_type = "EXIT_SHORT" if side == "SHORT" else "EXIT_LONG"
                side = side  # keep original side for exit context
            else:
                action = "BUY" if side == "LONG" else "SELL"
                event_type = "ENTRY_LONG" if side == "LONG" else "ENTRY_SHORT"

            # Create the trade
            trade = TradeContext(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                instrument=instrument or signal.instrument,
                entry_signal_id=signal.signal_id,
                entry_side=side,
                entry_action=action,
                entry_event_type=event_type,
                entry_trigger_price=signal.trigger_price,
                stop_loss_price=signal.stop_price,
                quantity=quantity or signal.quantity,
                multiplier=multiplier,
                status=TradeStatus.PENDING.value if not metadata.get("executed") else TradeStatus.OPEN.value,
                signal_candle_open=metadata.get("signal_candle_open", 0.0),
                signal_candle_high=metadata.get("signal_candle_high", 0.0),
                signal_candle_low=metadata.get("signal_candle_low", 0.0),
                signal_candle_close=metadata.get("signal_candle_close", 0.0),
                signal_htf_value=metadata.get("signal_htf_value", 0.0),
                signal_mid_value=metadata.get("signal_mid_value", 0.0),
                signal_fast_dema=metadata.get("signal_fast_dema", 0.0),
                signal_fast_atr=metadata.get("signal_fast_atr", 0.0),
                signal_reason=metadata.get("signal_reason", ""),
            )

            if metadata.get("executed"):
                trade.entry_price = metadata.get("entry_price", metadata.get("fill_price", 0.0))
                trade.entry_timestamp = signal.timestamp

            # Register in canonical maps
            self._trades[trade.trade_id] = trade
            self._signal_to_trade[signal.signal_id] = trade.trade_id

            # Persist before emitting an event because trade_events has a
            # foreign key to trades.trade_id.
            self._persist_trade(trade)

            # Log event
            self._record_event(trade.trade_id, "TRADE_CREATED", signal_id=signal.signal_id,
                               payload={"side": side, "action": action, "status": trade.status})

            print(f"[Lifecycle] TRADE CREATED: {trade.trade_id} | {side} {instrument} | "
                  f"signal={signal.signal_id[:8]}... | status={trade.status}", flush=True)

            return trade

    # ═══════════════════════════════════════════
    # PENDING ORDER
    # ═══════════════════════════════════════════

    def register_pending_order(self, trade_id: str, pending_order_id: str) -> bool:
        """Register a pending order for a trade."""
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                print(f"[Lifecycle] ERROR: trade {trade_id} not found for pending order", flush=True)
                return False
            trade.pending_order_id = pending_order_id
            trade.pending_status = "pending"
            trade.updated_at = time.time()
            self._pending_to_trade[pending_order_id] = trade_id
            self._record_event(trade_id, "PENDING_ORDER_CREATED",
                               payload={"pending_order_id": pending_order_id})
            self._persist_trade(trade)
            return True

    def activate_pending_order(self, pending_order_id: str, order_id: str) -> Optional[TradeContext]:
        """Convert pending order to active order. Same trade_id preserved."""
        with self._lock:
            trade_id = self._pending_to_trade.get(pending_order_id)
            if not trade_id:
                print(f"[Lifecycle] ERROR: pending order {pending_order_id} not linked to any trade", flush=True)
                return None
            trade = self._trades[trade_id]
            trade.pending_status = "triggered"
            trade.entry_order_id = order_id
            trade.status = TradeStatus.OPEN.value if trade.status == TradeStatus.PENDING.value else trade.status
            trade.updated_at = time.time()
            self._order_to_trade[order_id] = trade_id
            self._record_event(trade_id, "PENDING_ORDER_ACTIVATED",
                               order_id=order_id,
                               payload={"pending_order_id": pending_order_id})
            self._persist_trade(trade)
            return trade

    # ═══════════════════════════════════════════
    # ORDER
    # ═══════════════════════════════════════════

    def register_order(self, trade_id: str, order_id: str, role: str = "ENTRY") -> bool:
        """Register an order for a trade."""
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                print(f"[Lifecycle] ERROR: trade {trade_id} not found for order {order_id}", flush=True)
                return False
            if role == "ENTRY":
                trade.entry_order_id = order_id
            elif role in ("EXIT", "REVERSAL_EXIT"):
                trade.exit_order_id = order_id
            trade.updated_at = time.time()
            self._order_to_trade[order_id] = trade_id
            self._record_event(trade_id, "ORDER_CREATED",
                               order_id=order_id,
                               payload={"role": role})
            return True

    # ═══════════════════════════════════════════
    # FILL
    # ═══════════════════════════════════════════

    def register_entry_fill(self, trade_id: str, fill_id: str, price: float,
                             timestamp: float = 0.0) -> bool:
        """Register an entry fill for a trade."""
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                print(f"[Lifecycle] ERROR: trade {trade_id} not found for entry fill {fill_id}", flush=True)
                return False
            trade.entry_fill_id = fill_id
            trade.entry_price = price
            trade.entry_timestamp = timestamp or time.time()
            trade.status = TradeStatus.OPEN.value
            trade.updated_at = time.time()
            self._fill_to_trade[fill_id] = trade_id
            self._record_event(trade_id, "ENTRY_FILL_RECORDED",
                               fill_id=fill_id,
                               payload={"price": price})
            return True

    def register_exit_fill(self, trade_id: str, fill_id: str, price: float,
                            timestamp: float = 0.0, exit_signal_id: str = "",
                            exit_type: str = "", exit_reason: str = "") -> bool:
        """Register an exit fill for a trade."""
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                print(f"[Lifecycle] ERROR: trade {trade_id} not found for exit fill {fill_id}", flush=True)
                return False
            trade.exit_fill_id = fill_id
            trade.exit_price = price
            trade.exit_timestamp = timestamp or time.time()
            trade.exit_signal_id = exit_signal_id or trade.exit_signal_id
            trade.exit_type = exit_type or trade.exit_type
            trade.exit_reason = exit_reason or trade.exit_reason

            # Exit action is opposite of entry
            if trade.entry_side == "LONG":
                trade.exit_action = "SELL"
                trade.exit_event_type = "EXIT_LONG"
            else:
                trade.exit_action = "BUY"
                trade.exit_event_type = "EXIT_SHORT"

            trade.updated_at = time.time()
            self._fill_to_trade[fill_id] = trade_id
            self._record_event(trade_id, "EXIT_FILL_RECORDED",
                               fill_id=fill_id,
                               payload={"price": price, "exit_type": exit_type, "exit_reason": exit_reason})
            return True

    # ═══════════════════════════════════════════
    # POSITION
    # ═══════════════════════════════════════════

    def register_position(self, trade_id: str, position_id: str) -> bool:
        """Register a position for a trade.

        A position has its own immutable identity. It references the trade but
        never replaces the trade_id or re-keys lifecycle state.
        """
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                return False

            trade.position_id = position_id
            self._position_to_trade[position_id] = trade_id

            self._persist_trade(trade)
            return True

    # ═══════════════════════════════════════════
    # EXIT — close trade
    # ═══════════════════════════════════════════

    def close_trade(self, trade_id: str, gross_pnl: float = 0.0, charges: float = 0.0,
                    net_pnl: float = 0.0) -> bool:
        """Close a trade. This is the canonical close operation."""
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                print(f"[Lifecycle] ERROR: trade {trade_id} not found for close", flush=True)
                return False
            if trade.status == TradeStatus.CLOSED.value:
                print(f"[Lifecycle] WARNING: trade {trade_id} already closed", flush=True)
                return True
            trade.gross_pnl = gross_pnl
            trade.charges = charges
            trade.net_pnl = net_pnl
            trade.status = TradeStatus.CLOSED.value
            trade.closed_at = time.time()
            trade.updated_at = time.time()
            self._record_event(trade_id, "TRADE_CLOSED",
                               payload={"gross_pnl": gross_pnl, "charges": charges, "net_pnl": net_pnl})
            self._persist_trade(trade)
            print(f"[Lifecycle] TRADE CLOSED: {trade_id} | P&L={net_pnl:.2f}", flush=True)
            return True

    # ═══════════════════════════════════════════
    # REVERSAL — atomic old close + new open
    # ═══════════════════════════════════════════

    def reverse_trade(self, old_trade_id: str, new_signal, strategy_id: str,
                      strategy_name: str = "", instrument: str = "",
                      quantity: int = 0, multiplier: float = 1.0,
                      exit_price: float = 0.0, **kwargs) -> Optional[TradeContext]:
        """Reverse a trade: close old, create new. Atomic operation.

        The old trade gets exit_signal_id = new_signal.signal_id.
        The new trade gets entry_signal_id = new_signal.signal_id.
        This is valid and intentional.

        Returns:
            The new TradeContext, or None on failure.
        """
        with self._lock:
            old_trade = self._trades.get(old_trade_id)
            if not old_trade:
                print(f"[Lifecycle] ERROR: old trade {old_trade_id} not found for reversal", flush=True)
                return None

            # Close old trade
            old_trade.exit_signal_id = new_signal.signal_id
            old_trade.exit_type = ExitType.REVERSAL.value
            old_trade.exit_reason = "REVERSAL"
            old_trade.exit_price = exit_price
            old_trade.exit_timestamp = new_signal.timestamp
            old_trade.status = TradeStatus.CLOSED.value
            old_trade.closed_at = time.time()
            old_trade.updated_at = time.time()

            if old_trade.entry_side == "LONG":
                old_trade.exit_action = "SELL"
                old_trade.exit_event_type = "EXIT_LONG"
            else:
                old_trade.exit_action = "BUY"
                old_trade.exit_event_type = "EXIT_SHORT"

            self._record_event(old_trade_id, "TRADE_REVERSED",
                               payload={"exit_signal_id": new_signal.signal_id, "exit_price": exit_price})

            # Create new trade
            new_trade = self.create_trade_from_signal(
                new_signal, strategy_id, strategy_name, instrument,
                quantity, multiplier, **kwargs
            )

            # Set entry_price on new trade to old trade's exit_price
            new_trade.entry_price = exit_price
            new_trade.entry_timestamp = new_signal.timestamp

            self._persist_trade(old_trade)
            self._persist_trade(new_trade)

            print(f"[Lifecycle] REVERSAL: {old_trade_id} CLOSED -> {new_trade.trade_id} OPENED | "
                  f"signal={new_signal.signal_id[:8]}...", flush=True)

            return new_trade

    # ═══════════════════════════════════════════
    # STOP LOSS — closes existing trade, NO new trade
    # ═══════════════════════════════════════════

    def apply_stop_loss(self, trade_id: str, exit_price: float,
                        exit_reason: str = "STOP_LOSS") -> bool:
        """Mark a trade as stopped out. Does NOT create a new trade.

        exit_signal_id remains NULL (SL doesn't require a signal).
        """
        with self._lock:
            trade = self._trades.get(trade_id)
            if not trade:
                return False
            trade.exit_type = ExitType.STOP_LOSS.value
            trade.exit_reason = exit_reason
            trade.exit_price = exit_price
            trade.exit_timestamp = time.time()
            # exit_signal_id stays NULL — SL doesn't need a signal
            self._record_event(trade_id, "STOP_LOSS_APPLIED",
                               payload={"exit_price": exit_price, "exit_reason": exit_reason})
            self._persist_trade(trade)
            return True

    # ═══════════════════════════════════════════
    # EVENT LOG
    # ═══════════════════════════════════════════

    def _record_event(self, trade_id: str, event_type: str,
                      signal_id: str = "", order_id: str = "",
                      fill_id: str = "", position_id: str = "",
                      payload: dict = None):
        """Record an immutable lifecycle event."""
        event = LifecycleEvent(
            trade_id=trade_id,
            event_type=event_type,
            signal_id=signal_id,
            order_id=order_id,
            fill_id=fill_id,
            position_id=position_id,
            payload=payload or {},
        )
        self._events.append(event)

        # Also record to event store if available
        if self._event_store:
            try:
                self._event_store.record(
                    trade_id=trade_id,
                    strategy_id=self._trades[trade_id].strategy_id if trade_id in self._trades else "",
                    instrument=self._trades[trade_id].instrument if trade_id in self._trades else "",
                    event_type=event_type,
                    payload={
                        "signal_id": signal_id,
                        "order_id": order_id,
                        "fill_id": fill_id,
                        "position_id": position_id,
                        **(payload or {}),
                    },
                )
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════

    def _persist_signal(self, trade: TradeContext) -> None:
        """Persist the entry signal BEFORE the trade row (Section 3 invariant).

        trades.entry_signal_id is enforced at the database level (trigger +
        FK): a trade row cannot exist without its signal row.  save_signal is
        idempotent (INSERT OR IGNORE) so repeated persists are no-ops and the
        persisted signal candle can never be overwritten (signal immutability).
        """
        if not self._persistence or not trade.entry_signal_id:
            return
        try:
            self._persistence.save_signal({
                "signal_id": trade.entry_signal_id,
                "strategy_id": trade.strategy_id,
                "instrument": trade.instrument,
                "side": trade.entry_side,
                "signal_type": trade.entry_event_type,
                "timestamp": trade.entry_timestamp,
                "trigger_price": trade.entry_trigger_price,
                "stop_price": trade.stop_loss_price,
                "quantity": trade.quantity,
                "candle_data": {
                    "open": trade.signal_candle_open,
                    "high": trade.signal_candle_high,
                    "low": trade.signal_candle_low,
                    "close": trade.signal_candle_close,
                },
                "indicator_data": {
                    "htf_value": trade.signal_htf_value,
                    "mid_value": trade.signal_mid_value,
                    "fast_dema": trade.signal_fast_dema,
                    "fast_atr": trade.signal_fast_atr,
                    "reason": trade.signal_reason,
                },
            })
        except Exception as e:
            print(f"[Lifecycle] WARNING: failed to persist signal "
                  f"{trade.entry_signal_id}: {e}", flush=True)

    def _persist_trade(self, trade: TradeContext):
        """Persist canonical trade to DB. Signal row is guaranteed first."""
        if not self._persistence:
            return
        try:
            # The signal MUST exist before the trade row (DB trigger + FK
            # enforce trades.entry_signal_id -> signals.signal_id).
            self._persist_signal(trade)
            data = trade.snapshot()
            # Convert float timestamps to ISO strings for DB
            if trade.entry_timestamp:
                data["entry_timestamp"] = datetime.fromtimestamp(
                    trade.entry_timestamp, tz=timezone.utc
                ).isoformat()
            if trade.exit_timestamp:
                data["exit_timestamp"] = datetime.fromtimestamp(
                    trade.exit_timestamp, tz=timezone.utc
                ).isoformat()
            self._persistence.save_trade(data)
        except Exception as e:
            print(f"[Lifecycle] WARNING: failed to persist trade {trade.trade_id}: {e}", flush=True)

    # ═══════════════════════════════════════════
    # RECOVERY — restore from DB
    # ═══════════════════════════════════════════

    def restore_from_db(self):
        """Restore all trades from DB on startup. Rebuilds identity maps."""
        if not self._persistence:
            return
        with self._lock:
            try:
                trades = self._persistence.get_trades()
                for t_data in trades:
                    trade = TradeContext.from_snapshot(t_data)
                    # Convert ISO timestamps back to float
                    if isinstance(trade.entry_timestamp, str):
                        try:
                            trade.entry_timestamp = datetime.fromisoformat(
                                trade.entry_timestamp
                            ).timestamp()
                        except (ValueError, TypeError):
                            trade.entry_timestamp = 0.0
                    if isinstance(trade.exit_timestamp, str):
                        try:
                            trade.exit_timestamp = datetime.fromisoformat(
                                trade.exit_timestamp
                            ).timestamp()
                        except (ValueError, TypeError):
                            trade.exit_timestamp = 0.0

                    self._trades[trade.trade_id] = trade
                    # Rebuild identity maps
                    if trade.entry_signal_id:
                        self._signal_to_trade[trade.entry_signal_id] = trade.trade_id
                    if trade.exit_signal_id:
                        self._signal_to_trade[trade.exit_signal_id] = trade.trade_id
                    if trade.entry_order_id:
                        self._order_to_trade[trade.entry_order_id] = trade.trade_id
                    if trade.exit_order_id:
                        self._order_to_trade[trade.exit_order_id] = trade.trade_id
                    if trade.entry_fill_id:
                        self._fill_to_trade[trade.entry_fill_id] = trade.trade_id
                    if trade.exit_fill_id:
                        self._fill_to_trade[trade.exit_fill_id] = trade.trade_id
                    if trade.position_id:
                        self._position_to_trade[trade.position_id] = trade.trade_id
                    if trade.pending_order_id:
                        self._pending_to_trade[trade.pending_order_id] = trade.trade_id

                print(f"[Lifecycle] Restored {len(self._trades)} trades from DB", flush=True)
            except Exception as e:
                print(f"[Lifecycle] ERROR: failed to restore trades from DB: {e}", flush=True)

    # ═══════════════════════════════════════════
    # RECONCILIATION — find orphans and mismatches
    # ═══════════════════════════════════════════

    def reconcile(self, position_manager=None, order_manager=None) -> dict:
        """Scan for orphans, mismatches, and inconsistencies.

        Returns:
            dict with errors, warnings, and stats.
        """
        errors = []
        warnings = []
        stats = {"total_trades": len(self._trades), "open": 0, "closed": 0, "pending": 0}

        with self._lock:
            for trade_id, trade in self._trades.items():
                if trade.status == TradeStatus.OPEN.value:
                    stats["open"] += 1
                elif trade.status == TradeStatus.CLOSED.value:
                    stats["closed"] += 1
                elif trade.status == TradeStatus.PENDING.value:
                    stats["pending"] += 1

                # Check entry_signal_id is not empty
                if not trade.entry_signal_id:
                    errors.append({"type": "MISSING_ENTRY_SIGNAL", "trade_id": trade_id})

                # Check open trades have position_id
                if trade.status == TradeStatus.OPEN.value and not trade.position_id:
                    errors.append({"type": "OPEN_TRADE_NO_POSITION", "trade_id": trade_id})

                # Check closed trades have exit info
                if trade.status == TradeStatus.CLOSED.value:
                    if not trade.exit_price and not trade.exit_type:
                        warnings.append({"type": "CLOSED_NO_EXIT_INFO", "trade_id": trade_id})

                # Check entry_fill exists for open trades
                if trade.status == TradeStatus.OPEN.value and not trade.entry_fill_id:
                    warnings.append({"type": "OPEN_TRADE_NO_ENTRY_FILL", "trade_id": trade_id})

            # Check for fills not linked to any trade
            if self._persistence:
                try:
                    fills = self._persistence.get_fills() if hasattr(self._persistence, 'get_fills') else []
                    for fill in fills:
                        fill_id = fill.get("fill_id", "")
                        if fill_id and fill_id not in self._fill_to_trade:
                            warnings.append({"type": "ORPHAN_FILL", "fill_id": fill_id,
                                             "order_id": fill.get("order_id", "")})
                except Exception:
                    pass

        return {"errors": errors, "warnings": warnings, "stats": stats}

    def orphan_scan(self) -> dict:
        """Comprehensive orphan detection across all lifecycle objects.

        Checks every object in memory maps against DB and each other.
        Returns detailed report with counts and specific orphan instances.
        """
        report = {
            "orphan_fills": [],
            "orphan_orders": [],
            "orphan_positions": [],
            "orphan_pending_orders": [],
            "trades_without_signals": [],
            "trades_without_positions": [],
            "trades_with_wrong_exit_state": [],
            "mismatched_memory_db": [],
            "total_orphans": 0,
            "is_clean": False,
        }

        with self._lock:
            # 1. Check fills in DB not linked to any trade
            if self._persistence:
                try:
                    conn = self._persistence._get_conn()
                    conn.row_factory = __import__("sqlite3").Row
                    # Fills without trade_id
                    rows = conn.execute(
                        "SELECT fill_id, order_id, trade_id FROM fills WHERE trade_id IS NULL OR trade_id = ''"
                    ).fetchall()
                    for r in rows:
                        report["orphan_fills"].append({
                            "fill_id": r["fill_id"],
                            "order_id": r["order_id"],
                            "reason": "fill has no trade_id",
                        })
                    # Orders without trade_id
                    rows = conn.execute(
                        "SELECT order_id, trade_id FROM orders WHERE trade_id IS NULL OR trade_id = ''"
                    ).fetchall()
                    for r in rows:
                        report["orphan_orders"].append({
                            "order_id": r["order_id"],
                            "reason": "order has no trade_id",
                        })
                    # Fills linked to trade_id not in lifecycle
                    rows = conn.execute("SELECT fill_id, trade_id FROM fills WHERE trade_id IS NOT NULL AND trade_id != ''").fetchall()
                    for r in rows:
                        if r["trade_id"] not in self._trades:
                            report["orphan_fills"].append({
                                "fill_id": r["fill_id"],
                                "trade_id": r["trade_id"],
                                "reason": f"fill references non-existent trade {r['trade_id']}",
                            })
                    # Orders linked to trade_id not in lifecycle
                    rows = conn.execute("SELECT order_id, trade_id FROM orders WHERE trade_id IS NOT NULL AND trade_id != ''").fetchall()
                    for r in rows:
                        if r["trade_id"] not in self._trades:
                            report["orphan_orders"].append({
                                "order_id": r["order_id"],
                                "trade_id": r["trade_id"],
                                "reason": f"order references non-existent trade {r['trade_id']}",
                            })
                except Exception as e:
                    report["scan_error"] = str(e)

            # 2. Check in-memory lifecycle maps
            for fill_id, trade_id in self._fill_to_trade.items():
                if trade_id not in self._trades:
                    report["orphan_fills"].append({
                        "fill_id": fill_id,
                        "trade_id": trade_id,
                        "reason": "fill mapped to non-existent trade in memory",
                    })

            for order_id, trade_id in self._order_to_trade.items():
                if trade_id not in self._trades:
                    report["orphan_orders"].append({
                        "order_id": order_id,
                        "trade_id": trade_id,
                        "reason": "order mapped to non-existent trade in memory",
                    })

            for pos_id, trade_id in self._position_to_trade.items():
                if trade_id not in self._trades:
                    report["orphan_positions"].append({
                        "position_id": pos_id,
                        "trade_id": trade_id,
                        "reason": "position mapped to non-existent trade",
                    })

            for pend_id, trade_id in self._pending_to_trade.items():
                if trade_id not in self._trades:
                    report["orphan_pending_orders"].append({
                        "pending_order_id": pend_id,
                        "trade_id": trade_id,
                        "reason": "pending order mapped to non-existent trade",
                    })

            # 3. Check trades with missing fields
            for trade_id, trade in self._trades.items():
                if not trade.entry_signal_id:
                    report["trades_without_signals"].append(trade_id)
                if trade.status == TradeStatus.OPEN.value and not trade.position_id:
                    report["trades_without_positions"].append(trade_id)
                if trade.status == TradeStatus.CLOSED.value and not trade.exit_type and not trade.exit_price:
                    report["trades_with_wrong_exit_state"].append(trade_id)

        total = (len(report["orphan_fills"]) + len(report["orphan_orders"]) +
                 len(report["orphan_positions"]) + len(report["orphan_pending_orders"]) +
                 len(report["trades_without_signals"]) + len(report["trades_without_positions"]))
        report["total_orphans"] = total
        report["is_clean"] = total == 0
        return report

    def get_trades_for_api(self, strategy_id: str = None, instrument: str = None) -> list:
        """Return canonical trade data formatted for API responses.

        Each trade includes full lineage: signal, pending order, order, fill,
        position, exit details, P&L — all from the single authoritative source.
        """
        with self._lock:
            result = []
            for trade in self._trades.values():
                if strategy_id and trade.strategy_id != strategy_id:
                    continue
                if instrument and trade.instrument != instrument.upper():
                    continue
                result.append(trade.snapshot())
            # Sort by created_at descending (newest first)
            result.sort(key=lambda t: t.get("created_at", 0), reverse=True)
            return result

    # ═══════════════════════════════════════════
    # SNAPSHOT — full state for persistence
    # ═══════════════════════════════════════════

    def snapshot(self) -> dict:
        """Serialize all canonical trade state."""
        return {
            "trades": {tid: t.snapshot() for tid, t in self._trades.items()},
            "signal_to_trade": dict(self._signal_to_trade),
            "order_to_trade": dict(self._order_to_trade),
            "fill_to_trade": dict(self._fill_to_trade),
            "position_to_trade": dict(self._position_to_trade),
            "pending_to_trade": dict(self._pending_to_trade),
        }

    def restore(self, data: dict):
        """Restore canonical state from snapshot."""
        with self._lock:
            if not data:
                return
            for tid, t_data in data.get("trades", {}).items():
                self._trades[tid] = TradeContext.from_snapshot(t_data)
            self._signal_to_trade = data.get("signal_to_trade", {})
            self._order_to_trade = data.get("order_to_trade", {})
            self._fill_to_trade = data.get("fill_to_trade", {})
            self._position_to_trade = data.get("position_to_trade", {})
            self._pending_to_trade = data.get("pending_to_trade", {})
