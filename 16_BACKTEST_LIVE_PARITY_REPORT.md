# 16 - Backtest vs Live Parity Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Live Candle Fetching

### REST-Based Candle Acquisition

**File:** `core/candle_fetcher.py`

**Architecture:**
```
WebSocket: ONLY for LTP (live price)
REST API: Fetch actual OHLCV candles when they close
```

**Polling interval:** 30 seconds (`candle_fetcher.py:90`)

**Fetch trigger:**
1. CandleFetcher checks if any candle should have closed
2. For 5m: checks at :05, :10, :15, etc.
3. For 15m: aggregates from 5m candles
4. For 1h: aggregates from 5m candles

### Candle Aggregation

**File:** `core/candle_fetcher.py:179-200`

```python
# For 15m/1H: aggregate from multiple 5m candles
window_candles.sort(key=lambda candle: candle[0])
expected_count = tf_minutes // 5
if len(window_candles) == expected_count:
    bar = self._aggregate_candles(...)
```

**Critical rule:** Only fully completed aggregation windows are emitted. Partial windows are discarded.

### Warmup from REST

**File:** `trading_engine.py:1281-1391`

```python
def _warmup_from_rest(self):
    # Fetch 7 days of 5m candles
    from_date = (now - timedelta(days=7)).date()
    candles = self.data_adapter.fetch_historical_candles(name, "5", from_date, to_date)
    
    # Resample to 1H and 15m
    for tf, tf_minutes in [("1h", 60), ("15m", 15)]:
        # Only complete aggregation windows
        d = d[d.groupby("_bucket")["datetime"].transform("size") == tf_minutes // 5]
        htf = d.groupby("_bucket").agg({...})
```

---

## 2. Indicator Methodology

### DEMA-ATR Calculation

**File:** `indicators/dema_atr.py`

**Live (incremental):**
```python
def update(self, open, high, low, close):
    dema_val = self._dema.update(close)
    atr_val = self._atr.update(high, low, close)
    # Recursive band clamp
    ...
    return cur
```

**Batch (backtest):**
```python
@staticmethod
def calculate_batch(opens, highs, lows, closes, dema_period, atr_period, atr_factor):
    dema_values = DEMA.calculate_batch(closes, dema_period)
    atr_values = ATR.calculate_batch(highs, lows, closes, atr_period)
    # Same recursive band clamp
    ...
    return result
```

**Parity:** The incremental `update()` and batch `calculate_batch()` use identical logic. The only difference is incremental state vs. batch state initialization.

### DEMA Parity

**Live:** `DEMA.update(close)` — incremental EMA
**Batch:** `DEMA.calculate_batch(closes, period)` — numpy vectorized

**Formula:** `DEMA = 2 * EMA1 - EMA2` where EMA is standard Wilder smoothing.

### ATR Parity

**Live:** `ATR.update(high, low, close)` — incremental
**Batch:** `ATR.calculate_batch(highs, lows, closes, period)` — numpy vectorized

**Formula:** Standard Wilder ATR with period=6.

---

## 3. HTF Engine Parity

### Backtest-Style Mapping

**File:** `htf/backtest_style_htf.py`

**Live mapping:** `htf_engine.map_to_fast_bar(bar, timeframe)` — uses searchsorted to find the HTF value that was active at the time of the fast bar.

**Backtest mapping:** Same searchsorted logic applied to historical data.

**Parity:** The HTF engine is explicitly designed for backtest-live parity. The `BacktestStyleHTFEngine` class name reflects this.

---

## 4. Execution Model Parity

### Backtest Model

| Event | Backtest Price |
|-------|---------------|
| Entry | Signal bar's high/low (trigger level) |
| Stop loss | Bar close of the bar that triggered the stop |
| Reversal exit | Next bar's open |
| Same-bar stop | Entry bar's close |

### Live Model

| Event | Live Price |
|-------|-----------|
| Entry | Trigger level (breakout) + slippage |
| Stop loss (bar) | Bar close + slippage |
| Stop loss (tick) | Current LTP + slippage |
| Reversal exit | Next bar's open + slippage |
| Same-bar stop | Entry bar's close + slippage |

**Difference:** Live adds slippage (1 tick = ₹1 for MCX). Backtest has zero slippage.

### Slippage Model

**File:** `execution/paper_broker.py:156-161`

```python
slippage = self.slippage_ticks * 1.0  # tick_size = 1.0 for MCX
if order.side == "BUY":
    fill_price = current_price + slippage
else:
    fill_price = current_price - slippage
```

**Configurable:** `paper_execution.slippage_ticks` (default: 1)

---

## 5. Signal Logic Parity

### Crossover Detection

**File:** `strategies/base_dema_strategy.py:229-268`

```python
# LONG: close > htf_dema_atr AND prev_close <= prev_htf_dema_atr
# SHORT: close < htf_dema_atr AND prev_close >= prev_htf_dema_atr
```

**Confirmation (15m line):**
```python
# LONG: 15m line must be strictly below 1H line
# SHORT: 15m line must be strictly above 1H line
```

**Parity:** Same conditions in both backtest and live. The 15m confirmation filter was added to match the backtest exactly.

### Pending Entry Model

**File:** `strategies/base_dema_strategy.py:334-393`

**Backtest:** Signal bar's high/low becomes trigger; entry fills when later bar crosses it.

**Live:** Same model — `PendingEntry` created with trigger price; entry fills on breakout.

---

## 6. Known Parity Gaps

| Gap | Backtest | Live | Impact |
|-----|----------|------|--------|
| Slippage | None | 1 tick (₹1) | Minor P&L difference |
| Latency | None | 100ms simulation | Minor timing difference |
| Partial fills | None | Disabled | No difference |
| Data source | Historical CSV | REST API | Possible price differences |
| Session handling | Exact session hours | IST-based | Possible edge differences |
| Holiday handling | Not handled | Not handled | Same |
| Weekend gaps | Handled by data | Not applicable (no trading) | Same |

---

## 7. Data Flow Parity

```
Backtest:                          Live:
CSV data → DataFrame               REST API → 5m candles
  → Resample to 1H/15m              → CandleFetcher aggregates to 1H/15m
  → Feed to indicators              → Feed to indicators
  → Feed to HTF engine              → Feed to HTF engine
  → Strategy.on_bar()               → Strategy.on_bar()
  → Signal → Fill at bar price      → Signal → Fill at LTP + slippage
```
