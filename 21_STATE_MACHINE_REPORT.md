# 21 - State Machine Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Strategy State Machine

**File:** `strategies/types.py:21-33` + `strategies/base_dema_strategy.py`

### States

```python
class StrategyState(Enum):
    FLAT = "flat"
    SIGNAL_LONG = "signal_long"          # Intermediate (unused in practice)
    SIGNAL_SHORT = "signal_short"        # Intermediate (unused in practice)
    PENDING_LONG = "pending_long"        # Armed long breakout entry
    PENDING_SHORT = "pending_short"      # Armed short breakout entry
    ENTRY_TRIGGERED = "entry_triggered"  # Intermediate
    LONG_POSITION = "long_position"      # Holding long position
    SHORT_POSITION = "short_position"    # Holding short position
    STOP_ACTIVE = "stop_active"          # (unused)
    EXIT_PENDING = "exit_pending"        # (unused)
    EXIT_ORDER_SUBMITTED = "exit_order_submitted"  # Exit signal sent
```

### Transitions

```
FLAT
  ├── signal detected → PENDING_LONG / PENDING_SHORT
  │     ├── breakout triggered → LONG_POSITION / SHORT_POSITION
  │     └── timeout (50 bars) → FLAT
  
LONG_POSITION / SHORT_POSITION
  ├── stop loss hit → EXIT_ORDER_SUBMITTED → FLAT
  ├── tick stop hit → EXIT_ORDER_SUBMITTED → FLAT
  ├── same-bar stop → EXIT_ORDER_SUBMITTED → FLAT
  └── reversal signal → EXIT_ORDER_SUBMITTED → FLAT
        └── next bar open fill → FLAT (momentarily)
              └── opposite pending_entry armed → PENDING_LONG / PENDING_SHORT

EXIT_ORDER_SUBMITTED
  └── fill processed → FLAT (or new PENDING if reversal)
```

### State Change Points

| Transition | Trigger | File:Line |
|-----------|---------|-----------|
| FLAT → PENDING_* | `_create_pending_signal()` | `base_dema_strategy.py:376` |
| PENDING_* → *_POSITION | `_check_pending_entry()` | `base_dema_strategy.py:496-499` |
| *_POSITION → EXIT_ORDER_SUBMITTED | `_close_position()` | `base_dema_strategy.py:572` |
| EXIT_ORDER_SUBMITTED → FLAT | `trading_engine._on_fill()` | `trading_engine.py:1209` |
| *_POSITION → PENDING_* (reversal) | `_create_reversal_signal()` | `base_dema_strategy.py:433-443` |
| Any → FLAT (reset) | `_reset_strategy_state()` | `trading_engine.py:970-976` |

---

## 2. Market Session State Machine

**File:** `core/market_status.py:30-39`

### States

```python
class MarketState(Enum):
    OVERNIGHT = "overnight"          # Idle, no market activity
    PRE_MARKET = "pre_market"        # Warmup (5min before open)
    MARKET_OPEN = "market_open"      # First minute of session
    LIVE_TRADING = "live_trading"    # Active trading hours
    MARKET_CLOSE = "market_close"    # Closing window (5min before close)
    AFTER_MARKET = "after_market"    # Post-close wind-down (30min)
    SAFE_MODE = "safe_mode"          # Critical error
    HALTED = "halted"                # Manual emergency stop
```

### Transitions (IST-based)

```
OVERNIGHT
  └── 08:55 IST → PRE_MARKET
        └── 09:00 IST → MARKET_OPEN
              └── 09:01 IST → LIVE_TRADING
                    └── 23:25 IST → MARKET_CLOSE
                          └── 23:30 IST → AFTER_MARKET
                                └── 00:00 IST → OVERNIGHT

Any → SAFE_MODE (on critical error)
Any → HALTED (manual emergency stop)
```

### Override States

**File:** `core/market_status.py:178-194`

```python
def enter_safe_mode(self, reason):
    self._force_state_override = MarketState.SAFE_MODE
    self._engine_status = EngineStatus.SAFE_MODE

def exit_safe_mode(self):
    self._force_state_override = None
    self._engine_status = EngineStatus.READY

def halt(self):
    self._force_state_override = MarketState.HALTED
    self._engine_status = EngineStatus.HALTED
```

---

## 3. Engine Status State Machine

**File:** `core/market_status.py:50-60`

```python
class EngineStatus(Enum):
    INITIALIZING = "initializing"
    RESTORING = "restoring"
    WARMING_UP = "warming_up"
    RECONCILING = "reconciling"
    READY = "ready"
    TRADING = "trading"
    SAFE_MODE = "safe_mode"
    HALTED = "halted"
    STOPPED = "stopped"
```

### Transitions (during startup)

```
INITIALIZING
  → RECONCILING (after persistence wired)
    → WARMING_UP (after reconciliation)
      → READY (after warmup + connection)
        → TRADING (when market open + data flowing)
```

### Transitions (during runtime)

```
TRADING
  → SAFE_MODE (on critical error)
  → HALTED (manual stop)

READY
  → TRADING (when conditions met)
  → SAFE_MODE (on error)
```

---

## 4. Data Status State Machine

**File:** `core/market_status.py:42-47`

```python
class DataStatus(Enum):
    CONNECTED = "connected"      # Receiving live ticks
    STALE = "stale"              # Connected but no recent ticks
    DISCONNECTED = "disconnected" # WebSocket not connected
    NO_DATA = "no_data"          # Never received any tick
```

### Transitions

```
NO_DATA
  └── first tick received → CONNECTED

CONNECTED
  ├── no ticks for 60s → STALE
  └── WebSocket disconnect → DISCONNECTED

STALE
  ├── tick received → CONNECTED
  └── WebSocket disconnect → DISCONNECTED

DISCONNECTED
  └── WebSocket reconnect → NO_DATA → CONNECTED
```

---

## 5. Order State Machine

**File:** `execution/paper_broker.py:14-22`

```python
class OrderState(Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"    # (unused)
    PARTIALLY_FILLED = "partially_filled"  # (unused, blocked)
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"
```

### Transitions

```
CREATED
  → SUBMITTED (submit_order)
    → FILLED (execution succeeds)
    → REJECTED (no market data)
  → CANCELED (cancel_order)
```

---

## 6. Position Status State Machine

**File:** `portfolio/position_manager.py:19-20`

```python
class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
```

### Transitions

```
OPEN (open_position)
  → CLOSED (close_position)
```

---

## 7. Safe Mode State Machine

**File:** `core/safe_mode.py`

### States

```
Normal (no active reasons)
  └── reason added → Safe Mode (one or more active reasons)
        ├── reason cleared → check if all clear
        │     ├── all clear → Normal
        │     └── reasons remain → Safe Mode
        └── exit_safe_mode() → check + cooldown
```

### Entry Reasons

```python
REASONS = {
    "position_mismatch": "Position state inconsistent",
    "fill_ambiguity": "Duplicate or ambiguous fill",
    "database_failure": "Critical persistence failure",
    "state_restore_failure": "Invalid restored state",
    "market_data_uncertain": "Market data uncertain",
    "persistence_failure": "Trade persistence failed",
    "order_state_uncertain": "Order state inconsistent",
    "reconciliation_failed": "Reconciliation errors",
}
```

### Trading Permission

```python
def should_allow_trading(self):
    if self._active_reasons: return False
    if self.market_status.is_safe: return False
    if not self.market_status.is_trading_allowed: return False
    return True
```

---

## 8. Trade Close State Machine

**File:** `core/trade_close.py`

```
close_position() called
  → Step 1: Calculate P&L (pure)
  → Step 2-3: Persist (try/except)
    → Success: continue to Steps 4-8
    → Failure: return False (NO memory update)
  → Step 4: Close position in memory
  → Step 5: Update account P&L
  → Step 6: Update risk engine
  → Step 6b: Close trade in ledger
  → Step 7: Record event
  → Step 7b: Publish to EventBus
  → Step 8: Send Telegram
  → return True
```
