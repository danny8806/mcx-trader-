# 08 - Order/Fill/Position Lineage Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Order Lifecycle

### Order States

**File:** `execution/paper_broker.py:14-22`

```
CREATED → SUBMITTED → FILLED
                   → REJECTED
         → CANCELED
```

### Order Creation

**File:** `execution/paper_broker.py:101-124`

```python
def create_order(self, signal, multiplier=1.0) -> Order:
    order = Order(
        order_id=str(uuid.uuid4()),
        strategy_id=signal.strategy_id,
        instrument=signal.instrument,
        side="BUY" or "SELL",  # derived from signal type
        quantity=signal.quantity,
        state=OrderState.CREATED,
        multiplier=multiplier,
    )
    self._orders[order.order_id] = order
    return order
```

### Order Submission

**File:** `execution/paper_broker.py:126-145`

```python
def submit_order(self, order) -> Order:
    order.state = OrderState.SUBMITTED
    fill = self._execute_order(order)
    if fill:
        order.state = OrderState.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill.price
        order.fill_ids.append(fill.fill_id)
    else:
        order.state = OrderState.REJECTED
    return order
```

### Order-to-Fill Linkage

```
Order.order_id ──────→ Fill.order_id (1:N, but always 1 fill per order in paper mode)
Order.fill_ids[] ────→ Fill.fill_id (explicit tracking)
```

---

## 2. Fill Lifecycle

### Fill Creation

**File:** `execution/paper_broker.py:147-185`

```python
def _execute_order(self, order) -> Optional[Fill]:
    current_price = self._current_prices.get(order.instrument)
    # Slippage simulation
    if order.side == "BUY":
        fill_price = current_price + slippage
    else:
        fill_price = current_price - slippage
    
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
```

### Fill Properties

**File:** `execution/paper_broker.py:58-60`

```python
@property
def gross_value(self) -> float:
    return self.quantity * self.price * self.multiplier
```

### Fill Deduplication

**File:** `core/fill_dedup.py`

1. `is_duplicate(fill_id)` — checks in-memory set, then DB
2. `mark_processed(fill_id)` — atomic INSERT into `processed_fills` table
3. On startup: `load_from_database()` — populates in-memory set from DB

**Dedup check in engine:** `trading_engine.py:981-985`
```python
if self.fill_dedup.is_duplicate(fill.fill_id):
    return  # Skip duplicate
if not self.fill_dedup.mark_processed(fill.fill_id):
    return  # Race condition: another thread marked it first
```

### Fill-to-DB Persistence

**Entry fill:** `trading_engine.py:1018-1027`
```python
if self._persistence:
    self._persistence.save_fill({
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "strategy_id": fill.strategy_id,
        "instrument": fill.instrument,
        "side": fill.side,
        "quantity": fill.quantity,
        "price": fill.price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
```

**Exit fill:** `core/trade_close.py:117-127`
```python
fill_record = {
    "fill_id": fill.fill_id,
    "order_id": fill.order_id,
    "strategy_id": fill.strategy_id,
    "instrument": fill.instrument,
    "side": fill.side,
    "quantity": fill.quantity,
    "price": fill.price,
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
self._persistence.save_trade_and_fill(trade_record, fill_record)
```

---

## 3. Position Lifecycle

### Position Creation

**File:** `portfolio/position_manager.py:114-138`

```python
def open_position(self, fill, multiplier=1.0, stop_price=None, margin=0.0) -> Position:
    position = Position(
        position_id=str(uuid.uuid4()),  # ← THIS IS THE ANCHOR ID
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
    self._positions[position.position_id] = position
    return position
```

### Position States

**File:** `portfolio/position_manager.py:19-20`

```python
class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
```

### Position-to-Fill Linkage

```
Position.position_id ───────→ Trade trades.trade_id (1:1)
Position.entry_fill_ids[] ──→ Fill.fill_id (entry)
Position.exit_fills[] ──────→ Fill.fill_id (exit)
Position.strategy_id ───────→ Strategy.strategy_id
```

### Position Mark-to-Market

**File:** `portfolio/position_manager.py:57-63`

```python
def update_mark(self, price: float) -> None:
    self.current_mark = price
    if self.is_long:
        self.unrealized_pnl = (price - self.average_entry) * self.quantity * self.multiplier
    else:
        self.unrealized_pnl = (self.average_entry - price) * self.quantity * self.multiplier
```

### Position Close

**File:** `portfolio/position_manager.py:140-175`

```python
def close_position(self, position_id, fill, reason) -> Position:
    position = self._positions.get(position_id)
    position.exit_fills.append(fill)
    position.exit_reason = reason
    position.status = PositionStatus.CLOSED
    # Calculate realized P&L
    if position.is_long:
        position.realized_pnl = (fill.price - position.average_entry) * qty * multiplier
    else:
        position.realized_pnl = (position.average_entry - fill.price) * qty * multiplier
    self._closed_positions.append(position)
    del self._positions[position_id]
    return position
```

**Closed position cap:** `position_manager.py:172-173`
```python
if len(self._closed_positions) > 500:
    self._closed_positions = self._closed_positions[-250:]
```

---

## 4. State Transition Summary

### Entry Path
```
Signal → Order(CREATED) → Order(SUBMITTED) → Fill created
  → Order(SUBILLED) → Position(OPEN) → DB: trades row
  → Strategy: LONG_POSITION/SHORT_POSITION
```

### Exit Path
```
Exit Signal → TradeCloseManager.close_position()
  → Calculate P&L (pure)
  → DB: trades row (INSERT OR REPLACE)
  → DB: fills row (INSERT OR REPLACE)
  → Position(CLOSED) → Strategy: FLAT
  → AccountEngine: realized_pnl += net_pnl
  → RiskEngine: daily_pnl += net_pnl
```

### Reversal Path
```
Opposite crossover while in position
  → Strategy._create_reversal_signal()
    → Arms pending_exit_at_open (deferred)
    → Arms pending_entry (opposite side)
  → Next bar: engine._process_deferred_exit()
    → Exit fill at bar.open
    → Strategy state: FLAT (momentarily)
  → Later bar: pending_entry triggers
    → New entry fill
    → Strategy state: NEW_POSITION_SIDE
```

---

## 5. Lineage Verification Matrix

| Relationship | From | To | Constraint | Actual |
|-------------|------|----|-----------|--------|
| Order → Fill | Order.fill_ids | Fill.fill_id | 1:N | Always 1 in paper mode |
| Fill → Order | Fill.order_id | Order.order_id | N:1 | **No FK constraint** |
| Position → Fill (entry) | Position.entry_fill_ids | Fill.fill_id | N:1 | **No FK constraint** |
| Position → Fill (exit) | Position.exit_fills | Fill.fill_id | N:N | **No FK constraint** |
| Trade → Position | trades.trade_id | position_id | 1:1 | Same value, different DBs |
| Trade → Strategy | trades.strategy_id | strategy_id | N:1 | **No FK constraint** |
| TradeLedger → Position | trades_analytics.position_id | position_id | 1:1 | Same value |
