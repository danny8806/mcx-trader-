# CANDLE SOURCE FORENSIC REPORT (CORRECTED)

## VERDICT: ALREADY MATCHED — Both Backtest and Live Use NATIVE Dhan Candles

---

## CORRECTION

The initial forensic analysis inspected the WRONG file (`goldm_dema_mtf_futures.py`)
and concluded the backtest resamples from 5m. This was incorrect.

The ACTUAL main backtest runner is `base_live_mcx_strategy_backtest.py`, which
uses NATIVE 15m and 1H candles from Dhan — identical to the live system.

---

## 1. ACTUAL BACKTEST CANDLE SOURCE (Authoritative)

### Main Runner: `base_live_mcx_strategy_backtest.py`

Line 112 — fetches ALL THREE native timeframes:
```python
df5, df15, df60 = live_data.fetch_metal(metal)
```

Lines 129-131 — uses NATIVE HTF mapping:
```python
# Line mapping always uses NATIVE 15m / 60m bars -> 15m line + 1H line
dm = DM.native_map_htf(df, df15, 15, DEMA_P, ATR_P, ATR_F)
dh = DM.native_map_htf(df, df60, 60, DEMA_P, ATR_P, ATR_F)
```

### Data Source: `live_data.py`

Lines 154-161 — fetches native candles from Dhan REST API:
```python
raws_5 = _fetch_range(sid, "5", START, now)    # NATIVE 5m from Dhan
raws_15 = _fetch_range(sid, "15", START, now)   # NATIVE 15m from Dhan
raws_60 = _fetch_range(sid, "60", START, now)   # NATIVE 60m from Dhan
```

API calls via `dhan_broker.py`:
```python
fetch_intraday(security_id, "5", from_dt, to_dt, exchange_segment="MCX_COMM", instrument="FUTCOM")
fetch_intraday(security_id, "15", from_dt, to_dt, exchange_segment="MCX_COMM", instrument="FUTCOM")
fetch_intraday(security_id, "60", from_dt, to_dt, exchange_segment="MCX_COMM", instrument="FUTCOM")
```

### Signal Engine: `core/dema_mtf.py`

`native_map_htf()` (lines 106-135) — computes DEMA-ATR directly on native HTF candles:
```python
def native_map_htf(base_df, htf_df, htf_minutes, ...):
    """DEMA-ATR of NATIVE higher-TF bars mapped onto the base timeline.
    Unlike htf_dema_line (which resamples), this computes dema_atr directly
    on the broker-provided native HTF candles."""
    htf = htf_df.sort_values("datetime").reset_index(drop=True)
    vals = dema_atr(htf, dema_period, atr_period, atr_factor).to_numpy(dtype=float)
    src_avail = pd.to_datetime(htf["datetime"]).to_numpy() + np.timedelta64(htf_minutes, "m")
    ...
    idx = np.searchsorted(src_avail, target_close, side="right") - 1
```

### Wrapper Scripts

`run_goldm_base.py` and `run_silverm_base.py` both call:
```python
from base_live_mcx_strategy_backtest import run, BASES
```

---

## 2. ACTUAL LIVE SYSTEM CANDLE SOURCE

### `core/candle_fetcher.py`

5m candles (line 206):
```python
candles = self.data_adapter.fetch_historical_candles(name, "5", from_date, to_date)
```

15m candles — PRIMARY PATH (line 164, `_check_native_timeframe`):
```python
native_interval = {"15m": "15", "1h": "60"}[timeframe]
candles = self.data_adapter.fetch_historical_candles(name, native_interval, ...)
```

1H candles — PRIMARY PATH (same method):
```python
candles = self.data_adapter.fetch_historical_candles(name, "60", ...)
```

### `data/dhan/rest_client.py`

All three intervals fetched as native candles:
```python
fetch_intraday(security_id, "5", from_dt, to_dt)   # interval="5"
fetch_intraday(security_id, "15", from_dt, to_dt)  # interval="15"
fetch_intraday(security_id, "60", from_dt, to_dt)  # interval="60"
```

---

## 3. COMPARISON

| Timeframe | Backtest Source | Live Source | Match? |
|-----------|----------------|-------------|--------|
| **5m** | Native Dhan via `live_data.py` | Native Dhan via `rest_client.py` | **YES** |
| **15m** | Native Dhan via `live_data.py` | Native Dhan via `candle_fetcher.py` | **YES** |
| **1H** | Native Dhan via `live_data.py` | Native Dhan via `candle_fetcher.py` | **YES** |

Both systems:
- Fetch native 5m, 15m, 1H candles from Dhan REST API (`/charts/intraday`)
- Use the same security IDs (GOLDM=569003, SILVERM=483080)
- Use the same exchange segment (MCX_COMM) and instrument (FUTCOM)
- Convert UTC epoch timestamps to IST (+5:30)
- Filter to only fully-closed candles
- Use `native_map_htf()` / equivalent for HTF mapping

---

## 4. LEGACY CODE (NOT ON PRODUCTION PATH)

The following functions exist in `core/dema_mtf.py` but are NOT used by the
actual backtest runner:

| Function | Lines | Status |
|----------|-------|--------|
| `resample_ohlcv()` | 47-70 | **LEGACY** — not called by `base_live_mcx_strategy_backtest.py` |
| `htf_dema_line()` | 73-103 | **LEGACY** — not called by `base_live_mcx_strategy_backtest.py` |

These were used by the OLD runner (`goldm_dema_mtf_futures.py`) which loaded
only 5m CSVs and resampled. The NEW runner (`base_live_mcx_strategy_backtest.py`)
fetches all three native timeframes directly from Dhan.

---

## 5. RESAMPLING CODE AUDIT (CORRECTED)

### On Production Path (live system):

| Location | When Active | Status |
|----------|-------------|--------|
| `candle_fetcher.py:301` (`_aggregate_candles`) | Live fallback only | OK — fallback when native returns empty |
| `trading_engine.py:1855-1879` (warmup) | Startup only | OK — fallback when native HTF unavailable |

### NOT on Production Path (legacy/backtest):

| Location | Status |
|----------|--------|
| `core/dema_mtf.py:resample_ohlcv()` | **LEGACY** — old backtest only |
| `core/dema_mtf.py:htf_dema_line()` | **LEGACY** — old backtest only |

---

## 6. SUMMARY

| Item | Status |
|------|--------|
| Backtest 5m source | Native Dhan ✓ |
| Backtest 15m source | Native Dhan ✓ |
| Backtest 1H source | Native Dhan ✓ |
| Live 5m source | Native Dhan ✓ |
| Live 15m source | Native Dhan ✓ |
| Live 1H source | Native Dhan ✓ |
| HTF mapping | `native_map_htf()` (backtest) / equivalent (live) ✓ |
| Candle timestamps | UTC epoch → IST (+5:30) ✓ |
| Partial candle filtering | Fully-closed only ✓ |
| **PARITY** | **VERIFIED — Both systems use identical native Dhan candles** |

**NO FIX NEEDED. The backtest and live system already use the same native
Dhan candle source for all timeframes.**
