# 09 - Execution Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Entry Trigger Execution

### Bar-Based Entry (Primary)

**File:** `strategies/base_dema_strategy.py:452-505`

**Trigger conditions:**
- LONG: `bar.high > pending_entry.trigger_price`
- SHORT: `bar.low < pending_entry.trigger_price`

**Fill price:** `pending_entry.trigger_price` (the trigger level, not bar close)

**Flow:**
```
Bar closes → _check_pending_entry(bar)
  → If triggered: Signal(fill_price=trigger_price, metadata={"source": "breakout"})
  → Engine._process_signal(signal)
  → OrderManager.submit_signal(signal)
  → PaperExecutionEngine creates Fill at LTP + slippage
```

### Tick-Based Entry (Supplementary)

**File:** `strategies/base_dema_strategy.py:574-637`

**Trigger conditions:**
- LONG: `ltp > pending_entry.trigger_price`
- SHORT: `ltp < pending_entry.trigger_price`

**Fill price:** `pending_entry.trigger_price` (same as bar-based)

**Guard:** Only active when `tick_signal_processing = True` and no `pending_exit_at_open`

### Direct Market Entry (Legacy Path)

**File:** `strategies/base_dema_strategy.py:297-332`

**NOT USED** in current flow. This method exists but is never called. The system uses breakout (pending) entries exclusively.

---

## 2. Stop Loss Execution

### Bar-Based Stop

**File:** `strategies/base_dema_strategy.py:507-521`

**Trigger conditions:**
- LONG: `bar.low <= stop_price`
- SHORT: `bar.high >= stop_price`

**Fill price:** `bar.close` (the bar that triggered the stop)

**Flow:**
```
Bar closes → _check_stop_loss(bar)
  → If triggered: Signal(fill_price=bar.close, metadata={"source": "stop_loss"})
  → Engine._process_signal(signal)
```

### Tick-Based Stop

**File:** `strategies/base_dema_strategy.py:587-595`

**Trigger conditions:**
- LONG: `ltp <= stop_price`
- SHORT: `ltp >= stop_price`

**Fill price:** `ltp` (current market price)

### Same-Bar Stop

**File:** `strategies/base_dema_strategy.py:523-547`

**Condition:** Entry AND stop both fire on the same bar

**Fill price:** `bar.close`

**Flow:**
```
Entry fills on bar → _consume_same_bar_stop(bar)
  → Signal(fill_price=bar.close, metadata={"source": "same_bar_stop"})
  → Engine._process_signal(signal)
```

---

## 3. Reversal Execution

### Deferred Exit Model

**File:** `strategies/base_dema_strategy.py:395-450`

**Trigger:** Opposite crossover while in position

**Execution model:**
1. Signal bar T: crossover detected
2. Schedule exit at bar T+1's OPEN (not immediate)
3. Arm opposite-side breakout entry

**Flow:**
```
Bar T closes → _create_reversal_signal(side, ...)
  → Arms pending_exit_at_open = True
  → Arms pending_entry (opposite side)
  → Returns None (no immediate order)

Bar T+1 → engine._process_deferred_exit(strat, bar)
  → Exit fill at bar.open
  → Creates exit Signal

Bar T+N → pending_entry triggers
  → New entry fill at trigger level
```

**Exit fill price:** `bar.open` of the next fast bar (or `ltp` if tick-based)

**File:** `trading_engine.py:731-774`
```python
exit_price = ltp if ltp is not None else bar.open
```

---

## 4. Margin Calculation

**File:** `trading_engine.py:776-797`

```python
def _calculate_margin(self, instrument, price, quantity, side="BUY") -> float:
    # Primary: linear model from config
    if margin_model:
        margin = quantity * (slope * price + intercept)
        return margin
    # Fallback: percentage-based
    multiplier = instrument_config.get("multiplier", 1.0)
    margin_pct = config.get("risk", {}).get("margin_per_trade_pct", 6.5)
    return price * quantity * multiplier * margin_pct / 100.0
```

**Dual margin check:**
1. Per-strategy AccountEngine.block_margin(margin)
2. Global AccountEngine.block_margin(margin)
3. If global fails → rollback per-strategy

---

## 5. Paper Execution Simulation

**File:** `execution/paper_broker.py:63-185`

| Parameter | Default | Configurable |
|-----------|---------|-------------|
| Slippage | 1 tick (₹1) | Yes (config) |
| Latency | 100ms | Yes (config) |
| Partial fills | 0% (disabled) | **Locked to 0** |

**Partial fill restriction:** `paper_broker.py:83-87`
```python
if partial_fill_probability != 0:
    raise ValueError(
        "Partial fills are not supported by the position ledger; "
        "set partial_fill_probability to 0 until partial-close accounting exists."
    )
```

**Slippage model:**
```python
slippage = self.slippage_ticks * 1.0  # tick_size = 1.0 for MCX
if order.side == "BUY":
    fill_price = current_price + slippage
else:
    fill_price = current_price - slippage
```

---

## 6. Order Submission Sequence

**File:** `trading_engine.py:890-957`

```
1. persistence.save_order(order)                    ← BEFORE fills
2. order_manager.drain_fills()                      ← Get fills
3. For each fill:
   a. _on_fill(fill)
      i.   fill_dedup check
      ii.  Entry: margin check → position open
      iii. Exit:  TradeCloseManager.close_position
```

**DB invariant:** Order row exists before any fill row referencing it.

---

## 7. Fee Model

**File:** `execution/fee_model.py:20-103`

| Fee Component | Calculation |
|---------------|-------------|
| Brokerage | ₹20 per side × 2 sides = ₹40 flat |
| STT | Sell turnover × 0.01% |
| Exchange | (Buy + Sell turnover) × 0.0026% |
| SEBI | (Buy + Sell turnover) × 0.0001% |
| GST | (Brokerage + Exchange + SEBI) × 18% |
| Stamp Duty | Buy turnover × 0.005% |

**Configurable via:** `config → charges → {instrument}`

---

## 8. Risk Checks Before Execution

**File:** `trading_engine.py:833-840`

```python
allowed, reason = self.risk_engine.check_order(
    signal=signal,
    current_positions=len(self.position_manager.open_positions),
    strategy_positions=len(self.position_manager.get_positions_by_strategy(signal.strategy_id)),
    available_margin=strat_account.available_margin,
    margin_required=margin_required,
    current_equity=strat_account.equity,
)
```

**Checks (in order):**
1. Kill switch active → reject
2. Per-strategy position limit (default: 1) → reject
3. Total position limit (default: 8) → reject
4. Insufficient margin → reject
5. Daily loss limit → reject + activate kill switch
6. Max drawdown → reject + activate kill switch
