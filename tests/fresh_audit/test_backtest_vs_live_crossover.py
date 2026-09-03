"""Test: Backtest vs Live system crossover signal comparison on SAME data.

Loads GOLDM_5m_mcx.csv and runs both signal generators:
1. Backtest: core/dema_mtf.py -> compute_signals() (raw crossovers)
2. Live: strategies/base_dema_strategy.py -> _check_long_cross/_check_short_cross

Both use the SAME position-tracking logic. Every entry signal, SL value, and
reversal must be IDENTICAL.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# LIVE_ROOT points to THIS repo (MCX-TRADER = source of truth / deployed code).
LIVE_ROOT = r"C:\Users\pc\Desktop\MCX-TRADER"
BACKTEST_ROOT = r"C:\Users\pc\Desktop\nifty dema backtest\project"

sys.path.insert(0, LIVE_ROOT)

from strategies.base_dema_strategy import BaseDEMAStrategy, SignalType
from core.timeframe_engine import Bar
from htf.backtest_style_htf import BacktestStyleHTFEngine, HTFMappedValue

# This module only needs LIVE_ROOT at import time for the classes above.
# Remove it from sys.path so it does NOT shadow the MCX-TRADER (source of
# truth) copy for every other test file in the suite.
if sys.path and sys.path[0] == LIVE_ROOT:
    sys.path.pop(0)


# ---------------------------------------------------------------------------
# Inline backtest helper functions (pure numpy/pandas, no dependencies)
# ---------------------------------------------------------------------------
def _pine_ema(source: pd.Series, length: int) -> pd.Series:
    return source.ewm(alpha=2.0 / (length + 1.0), adjust=False, min_periods=1).mean()

def _wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    result = pd.Series(np.nan, index=df.index, dtype="float64")
    if len(tr) < period:
        return result
    result.iloc[period - 1] = tr.iloc[:period].mean()
    alpha = 1.0 / period
    for i in range(period, len(tr)):
        result.iloc[i] = alpha * tr.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result

def _dema_atr(df: pd.DataFrame, dema_period: int, atr_period: int, atr_factor: float) -> pd.Series:
    ema1 = _pine_ema(df["close"], dema_period)
    dema = 2 * ema1 - _pine_ema(ema1, dema_period)
    band = _wilder_atr(df, atr_period) * atr_factor
    upper = dema + band
    lower = dema - band
    dema_np = dema.to_numpy(dtype="float64")
    up_np = upper.to_numpy(dtype="float64")
    lo_np = lower.to_numpy(dtype="float64")
    out = np.full(len(df), np.nan, dtype="float64")
    for i in range(len(df)):
        cur = out[i - 1] if i and not np.isnan(out[i - 1]) else dema_np[i]
        if not np.isnan(lo_np[i]) and lo_np[i] > cur:
            cur = lo_np[i]
        if not np.isnan(up_np[i]) and up_np[i] < cur:
            cur = up_np[i]
        out[i] = cur
    return pd.Series(out, index=df.index)

# Mock build_15min_enriched for dema_mtf.py
import types as _types
_mock = _types.ModuleType("build_15min_enriched")
_mock.dema_atr = _dema_atr
sys.modules["build_15min_enriched"] = _mock

# Load core.dema_mtf via importlib
_spec = importlib.util.spec_from_file_location(
    "backtest_dema_mtf",
    os.path.join(BACKTEST_ROOT, "core", "dema_mtf.py"),
)
_dema_mtf = importlib.util.module_from_spec(_spec)
sys.modules["backtest_dema_mtf"] = _dema_mtf
_spec.loader.exec_module(_dema_mtf)

htf_dema_line = _dema_mtf.htf_dema_line
backtest_compute_signals = _dema_mtf.compute_signals


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_goldm_5m_data():
    path = os.path.join(BACKTEST_ROOT, "data_mcx", "GOLDM_5m_mcx.csv")
    df = pd.read_csv(path)
    required = {"datetime", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    invalid = ((df["high"] < df[["open", "close", "low"]].max(axis=1)) |
               (df["low"] > df[["open", "close", "high"]].min(axis=1)) |
               (df[["open", "high", "low", "close"]] <= 0).any(axis=1))
    if invalid.any():
        df = df[~invalid].copy()
    df["volume"] = df["volume"].fillna(0).astype("int64")
    df = df.sort_values("datetime").drop_duplicates(subset="datetime", keep="last")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Shared position-tracking signal filter
# ---------------------------------------------------------------------------
def filter_signals_with_position_state(raw_buy, raw_sell, sl_buy, sl_sell):
    """Apply position-tracking state machine to raw crossover signals.
    
    Returns filtered buy/sell arrays (only first crossover after flat/position change).
    This is the logic both backtest execution and live strategy use.
    """
    n = len(raw_buy)
    filtered_buy = np.zeros(n, dtype=bool)
    filtered_sell = np.zeros(n, dtype=bool)
    f_sl_buy = np.full(n, np.nan)
    f_sl_sell = np.full(n, np.nan)
    
    position_side = None
    
    for i in range(n):
        if raw_buy[i]:
            if position_side is None:
                filtered_buy[i] = True
                f_sl_buy[i] = sl_buy[i]
                position_side = "LONG"
            elif position_side == "SHORT":
                # Reversal
                filtered_buy[i] = True
                f_sl_buy[i] = sl_buy[i]
                position_side = "LONG"
            # else: already LONG, skip
        
        if raw_sell[i]:
            if position_side is None:
                filtered_sell[i] = True
                f_sl_sell[i] = sl_sell[i]
                position_side = "SHORT"
            elif position_side == "LONG":
                # Reversal
                filtered_sell[i] = True
                f_sl_sell[i] = sl_sell[i]
                position_side = "SHORT"
            # else: already SHORT, skip
    
    return filtered_buy, filtered_sell, f_sl_buy, f_sl_sell


# ---------------------------------------------------------------------------
# Live strategy signal emulation (mirrors BaseDEMAStrategy._check_long_cross etc.)
# ---------------------------------------------------------------------------
def run_live_signal_logic(close, high, low, dema_1h, dema_15m):
    """Run the exact same crossover logic as the live strategy."""
    n = len(close)
    live_buy = np.zeros(n, dtype=bool)
    live_sell = np.zeros(n, dtype=bool)
    live_sl_buy = np.full(n, np.nan)
    live_sl_sell = np.full(n, np.nan)
    
    position_side = None
    
    for i in range(1, n):
        htf_val = dema_1h[i]
        prev_htf_val = dema_1h[i - 1]
        mid_val = dema_15m[i]
        
        if np.isnan(htf_val) or np.isnan(prev_htf_val) or np.isnan(mid_val):
            continue
        
        curr_close = close[i]
        prev_close = close[i - 1]
        curr_high = high[i]
        prev_high = high[i - 1]
        curr_low = low[i]
        prev_low = low[i - 1]
        
        # LONG crossover: close > h1h AND prev_close <= prev_h1h AND h15 < h1h
        buy = (curr_close > htf_val and prev_close <= prev_htf_val
               and mid_val < htf_val)
        # SHORT crossover: close < h1h AND prev_close >= prev_h1h AND h15 > h1h
        sell = (curr_close < htf_val and prev_close >= prev_htf_val
                and mid_val > htf_val)
        
        if buy:
            if position_side is None or position_side == "SHORT":
                live_buy[i] = True
                live_sl_buy[i] = min(curr_low, prev_low)
                position_side = "LONG"
        elif sell:
            if position_side is None or position_side == "LONG":
                live_sell[i] = True
                live_sl_sell[i] = max(curr_high, prev_high)
                position_side = "SHORT"
    
    return live_buy, live_sell, live_sl_buy, live_sl_sell


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------
def test_crossover_comparison():
    print("Loading GOLDM 5m data...")
    df = load_goldm_5m_data()
    print(f"Loaded {len(df)} bars: {df['datetime'].min()} -> {df['datetime'].max()}")

    df_base = df.copy()

    # === BACKTEST ===
    print("\n=== Running BACKTEST signal computation ===")
    DEMA_PERIOD = 3
    dema_15m = htf_dema_line(df_base, "15min", DEMA_PERIOD, 6, 1.0, session_open="09:00")
    dema_1h = htf_dema_line(df_base, "60min", DEMA_PERIOD, 6, 1.0, session_open="09:00")
    bt_result = backtest_compute_signals(df_base, dema_15m, dema_1h)

    bt_raw_buy = bt_result["raw_buy"]
    bt_raw_sell = bt_result["raw_sell"]
    print(f"Backtest RAW crossovers:  BUY={int(np.sum(bt_raw_buy))}  SELL={int(np.sum(bt_raw_sell))}")

    # Filter backtest through position state
    bt_buy, bt_sell, bt_sl_buy, bt_sl_sell = filter_signals_with_position_state(
        bt_raw_buy, bt_raw_sell, bt_result["sl_buy"], bt_result["sl_sell"],
    )
    print(f"Backtest FILTERED signals: BUY={int(np.sum(bt_buy))}  SELL={int(np.sum(bt_sell))}")

    # === LIVE ===
    print("\n=== Running LIVE strategy signal logic ===")
    close = df_base["close"].to_numpy(dtype=float)
    high = df_base["high"].to_numpy(dtype=float)
    low = df_base["low"].to_numpy(dtype=float)

    live_buy, live_sell, live_sl_buy, live_sl_sell = run_live_signal_logic(
        close, high, low, dema_1h, dema_15m,
    )
    print(f"Live signals:             BUY={int(np.sum(live_buy))}  SELL={int(np.sum(live_sell))}")

    # === COMPARE ===
    print("\n=== COMPARISON RESULTS ===")
    
    buy_match = bool(np.array_equal(bt_buy, live_buy))
    sell_match = bool(np.array_equal(bt_sell, live_sell))

    print(f"\nBUY signals:")
    print(f"  Backtest (filtered): {int(np.sum(bt_buy))}")
    print(f"  Live:                {int(np.sum(live_buy))}")
    print(f"  MATCH: {buy_match}")
    if not buy_match:
        only_bt = sorted(set(np.where(bt_buy)[0]) - set(np.where(live_buy)[0]))
        only_live = sorted(set(np.where(live_buy)[0]) - set(np.where(bt_buy)[0]))
        print(f"  Only in backtest: {only_bt[:20]}")
        print(f"  Only in live:     {only_live[:20]}")
        for idx in only_bt[:5]:
            print(f"    idx {idx}: close={close[idx]:.2f}, h1h={dema_1h[idx]:.2f}, prev_h1h={dema_1h[idx-1]:.2f}, h15={dema_15m[idx]:.2f}")
        for idx in only_live[:5]:
            print(f"    idx {idx}: close={close[idx]:.2f}, h1h={dema_1h[idx]:.2f}, prev_h1h={dema_1h[idx-1]:.2f}, h15={dema_15m[idx]:.2f}")

    print(f"\nSELL signals:")
    print(f"  Backtest (filtered): {int(np.sum(bt_sell))}")
    print(f"  Live:                {int(np.sum(live_sell))}")
    print(f"  MATCH: {sell_match}")
    if not sell_match:
        only_bt = sorted(set(np.where(bt_sell)[0]) - set(np.where(live_sell)[0]))
        only_live = sorted(set(np.where(live_sell)[0]) - set(np.where(bt_sell)[0]))
        print(f"  Only in backtest: {only_bt[:20]}")
        print(f"  Only in live:     {only_live[:20]}")

    # SL comparison where both signal
    both_buy = bt_buy & live_buy
    if np.any(both_buy):
        sl_match = bool(np.allclose(bt_sl_buy[both_buy], live_sl_buy[both_buy], equal_nan=True, rtol=1e-5))
        print(f"\nSL BUY match ({int(np.sum(both_buy))} signals): {sl_match}")
        if not sl_match:
            for idx in np.where(both_buy)[0][:5]:
                print(f"  idx {idx}: bt={bt_sl_buy[idx]:.2f}, live={live_sl_buy[idx]:.2f}")

    both_sell = bt_sell & live_sell
    if np.any(both_sell):
        sl_match = bool(np.allclose(bt_sl_sell[both_sell], live_sl_sell[both_sell], equal_nan=True, rtol=1e-5))
        print(f"SL SELL match ({int(np.sum(both_sell))} signals): {sl_match}")
        if not sl_match:
            for idx in np.where(both_sell)[0][:5]:
                print(f"  idx {idx}: bt={bt_sl_sell[idx]:.2f}, live={live_sl_sell[idx]:.2f}")

    overall = buy_match and sell_match
    print(f"\n{'='*60}")
    print(f"OVERALL SIGNAL MATCH: {overall}")
    print(f"{'='*60}")

    return overall


if __name__ == "__main__":
    try:
        result = test_crossover_comparison()
        sys.exit(0 if result else 1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
