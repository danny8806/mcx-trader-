# 06 - Signal Forensic Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER
**Primary Strategy:** DEMA-ATR Crossover with HTF confirmation

---

## 1. Signal Generation Architecture

### Signal Flow

```
Candle Close (5m)
  → Indicator.update(OHLC)                        [indicators/dema_atr.py:56-98]
  → HTF Engine.on_htf_bar_closed()                 [htf/backtest_style_htf.py]
  → HTF Engine.map_to_fast_bar()                   [searchsorted mapping]
  → Strategy.on_bar(bar, htf_mapped, fast_value)   [strategies/base_dema_strategy.py:104-227]
  → Signal or None
```

### Crossover Conditions

**File:** `strategies/base_dema_strategy.py`

#### LONG Signal (`_check_long_cross`, line 229-248)
```python
cross = close > htf_val and prev_close <= prev_htf_val
# PLUS: 15m confirmation line must be strictly below 1H line
if mid_val is not None and htf_val is not None:
    if mid_val >= htf_val:
        return False
```
- **Condition:** `close` crosses ABOVE `1H DEMA-ATR` AND previous bar's close was at or below
- **Confirmation:** `15m DEMA-ATR` must be strictly below `1H DEMA-ATR`
- **Tolerance:** None — strict `>` and `<=` operators

#### SHORT Signal (`_check_short_cross`, line 250-268)
```python
cross = close < htf_val and prev_close >= prev_htf_val
# PLUS: 15m confirmation line must be above 1H line
if mid_val is not None and htf_val is not None:
    if mid_val <= htf_val:
        return False
```
- **Condition:** `close` crosses BELOW `1H DEMA-ATR` AND previous bar's close was at or above
- **Confirmation:** `15m DEMA-ATR` must be strictly above `1H DEMA-ATR`
- **Tolerance:** None — strict `<` and `>=` operators

### DEMA-ATR Line Calculation

**File:** `indicators/dema_atr.py:56-98`

```
DEMA = 2 * EMA1(close, period=3) - EMA2(close, period=3)
ATR = Wilder smoothed ATR(period=6)
Band = ATR * factor (factor=1.0)
Upper = DEMA + Band
Lower = DEMA - Band
Output = recursive_clamp(upper, Lower, prev_output)
```

**Recursive band clamp behavior:**
- If previous output < Lower → output = Lower (ratchet up)
- If previous output > Upper → output = Upper (ratchet down)
- Otherwise → output = previous output (no change)
- First bar: output = DEMA value

**Parameters:** DEMA period=3, ATR period=6, ATR factor=1.0 (default from `trading_engine.py:172-174`)

---

## 2. Signal Types

**File:** `strategies/types.py:13-18`

```python
class SignalType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"
    REVERSAL = "REVERSAL"
```

| SignalType | When Used | Execution Model |
|------------|-----------|-----------------|
| LONG | New long entry or long breakout | Pending entry (bar high trigger) |
| SHORT | New short entry or short breakout | Pending entry (bar low trigger) |
| FLAT | (unused) | N/A |
| REVERSAL | Opposite crossover while in position | Deferred exit + pending re-entry |

---

## 3. Entry Execution Model

**File:** `strategies/base_dema_strategy.py:334-393` (_create_pending_signal)

### Breakout Entry (Primary)
1. Signal bar's high/low becomes the trigger level
2. Entry is **NOT** immediate — creates a `PendingEntry`
3. Entry fills only when a later bar's price crosses the trigger level
4. Fill price = trigger level (not bar close)

```python
# LONG trigger:
trigger = signal_bar.high
# SHORT trigger:
trigger = signal_bar.low
```

### Entry Confirmation (`_check_pending_entry`, line 452-505)
```python
if pen.side == "LONG" and bar.high > pen.trigger_price:
    triggered = True
elif pen.side == "SHORT" and bar.low < pen.trigger_price:
    triggered = True
```

### Tick-Level Entry (`on_tick`, line 574-637)
```python
if pen.side == "LONG" and ltp > pen.trigger_price:
    triggered = True
elif pen.side == "SHORT" and ltp < pen.trigger_price:
    triggered = True
```

### Pending Entry Timeout
**File:** `strategies/base_dema_strategy.py:166-173`
```python
if self.pending_entry.bars_pending >= self.pending_timeout_bars:
    # Expire after 50 bars (default)
    self.pending_entry = None
    self.state = StrategyState.FLAT
```

---

## 4. Exit Execution Model

### Stop Loss (`_check_stop_loss`, line 507-521)
```python
# LONG stop: bar.low <= stop_price → exit at bar.close
# SHORT stop: bar.high >= stop_price → exit at bar.close
```
- **Fill price:** Bar close (not trigger level)
- **Execution:** Immediate signal generation

### Tick-Level Stop (`on_tick`, line 587-595)
```python
# LONG stop: ltp <= stop_price → exit at ltp
# SHORT stop: ltp >= stop_price → exit at ltp
```
- **Fill price:** Current LTP

### Same-Bar Stop (`_consume_same_bar_stop`, line 523-547)
- When entry AND stop both fire on the same bar
- Entry fills at trigger level
- Stop fills at bar close
- Booked as a round-trip

### Reversal Exit (`_create_reversal_signal`, line 395-450)
- Opposite crossover while in position
- **Deferred exit:** Scheduled at next bar's OPEN
- Fill price = next bar's open price
- Non-immediate: `pending_exit_at_open = True`
- Engine consumes at `trading_engine.py:652-666` (tick) or `trading_engine.py:717` (bar)

---

## 5. Stop Price Calculation

**File:** `strategies/base_dema_strategy.py:338-348`

```python
# LONG:
trigger = signal_bar.high
stop = min(signal_bar.low, prev_bar.low)

# SHORT:
trigger = signal_bar.low
stop = max(signal_bar.high, prev_bar.high)
```

- Stop is set at signal bar's extreme, considering previous bar
- Stop never changes once set (no trailing stop)

---

## 6. Strategy State Machine

**File:** `strategies/types.py:21-33`

```
FLAT → PENDING_LONG/PENDING_SHORT (on signal)
  → LONG_POSITION/SHORT_POSITION (on fill)
    → EXIT_ORDER_SUBMITTED (on exit signal)
      → FLAT (after fill processed)
```

Additional states:
- `SIGNAL_LONG/SIGNAL_SHORT` — intermediate detection states
- `ENTRY_TRIGGERED` — pending entry triggered
- `STOP_ACTIVE` — (unused)

---

## 7. Signal Deduplication

**File:** `trading_engine.py:899`

```python
key = f"{signal.strategy_id}:{signal.instrument}:{signal.timestamp}"
if key in self._pending_signals:
    return None  # Duplicate signal blocked
```

**Risk:** Two different signals with the same strategy+instrument+timestamp are treated as duplicates. This is correct because the strategy emits at most one signal per bar.

---

## 8. Signal Audit Trail

**File:** `strategies/base_dema_strategy.py:639-647`

```python
def _emit(self, event_type: str, **kwargs) -> None:
    self._events.append({...})
    if len(self._events) > 1000:
        self._events = self._events[-500:]  # Prune to last 500
```

**Events emitted:**
- `PENDING_ENTRY_CREATED` (line 392)
- `ENTRY_EXECUTED` (line 331, 502)
- `ENTRY_TRIGGERED` (line 634)
- `POSITION_CLOSED` (line 571)
- `REVERSAL_SIGNAL` (line 449)
- `PENDING_ENTRY_EXPIRED` (line 167)
