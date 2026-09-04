"""Compare LIVE-system signals vs BACKTEST signals for a target day, per strategy.

For GOLDM + SILVERM it fetches the same 5m candles the production engine
warms up from (Dhan REST, matches `_warmup_from_rest`), then runs:

  LIVE     : production-line semantics — the live HTF lines (DEMA-ATR computed
             incrementally by `BacktestStyleHTFEngine`, i.e. exactly what
             trading_engine feeds the strategy) plus the strategy's exact
             crossover rules (close>h1 & prev<=prev_h1 & h15<h1 / mirrored).
  BACKTEST : the original backtester lines (`dema_mtf.htf_dema_line`, batch
             pandas-ewm + Wilder ATR) plus `dema_mtf.compute_signals`.

Both feeds are filtered through ONE identical position-state machine (first
crossover after flat/reversal — the decision layer).  Any difference on the
target day therefore isolates an indicator/line divergence — not a data or
state-machine difference.

Usage:  python compare_yesterday_signals.py [YYYY-MM-DD]
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types as _types
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.timeframe_engine import Bar, BarState  # noqa: E402
from htf.backtest_style_htf import BacktestStyleHTFEngine  # noqa: E402

BACKTEST_ROOT = r"C:\Users\pc\Desktop\nifty dema backtest\project"

STRATEGIES = {
    "gold_01":   {"instrument": "GOLDM",   "fast_tf": "5m",  "fast_min": 5},
    "gold_02":   {"instrument": "GOLDM",   "fast_tf": "15m", "fast_min": 15},
    "silver_01": {"instrument": "SILVERM", "fast_tf": "15m", "fast_min": 15},
    "silver_02": {"instrument": "SILVERM", "fast_tf": "5m",  "fast_min": 5},
}
INS = {"GOLDM": "563946", "SILVERM": "483080"}
SESSION = "09:00"
IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Dhan REST fetch (token file + call identical to full_simulator.py)
# ---------------------------------------------------------------------------
def _token():
    with open(os.path.join(ROOT, "data", "dhan_token.json")) as f:
        return json.load(f)["access_token"]


def fetch_5m(sec_id, from_date, to_date):
    r = requests.post(
        "https://api.dhan.co/v2/charts/intraday",
        json={
            "securityId": sec_id,
            "exchangeSegment": "MCX_COMM",
            "instrument": "FUTCOM",
            "interval": "5",
            "fromDate": f"{from_date} 09:00:00",
            "toDate": f"{to_date} 23:55:00",
        },
        headers={"access-token": _token(), "Content-Type": "application/json"},
        timeout=30,
    )
    j = r.json()
    if "open" not in j:
        raise RuntimeError(f"Dhan API error: {j}")
    df = pd.DataFrame({
        "timestamp": j["timestamp"],
        "open": j["open"], "high": j["high"],
        "low": j["low"], "close": j["close"],
        "volume": j.get("volume", [0] * len(j["open"])),
    })
    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    )
    return df.sort_values("datetime").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Backtest lines (dema_mtf.py loaded from the backtest project)
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


def _dema_atr(df, dema_period, atr_period, atr_factor):
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


_mock = _types.ModuleType("build_15min_enriched")
_mock.dema_atr = _dema_atr
sys.modules["build_15min_enriched"] = _mock

_spec = importlib.util.spec_from_file_location(
    "backtest_dema_mtf", os.path.join(BACKTEST_ROOT, "core", "dema_mtf.py"))
_dema_mtf = importlib.util.module_from_spec(_spec)
sys.modules["backtest_dema_mtf"] = _dema_mtf
_spec.loader.exec_module(_dema_mtf)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def resample(df, minutes):
    dt = df["datetime"]
    dates = dt.dt.date.astype(str)
    sess_start = pd.to_datetime(dates + " " + SESSION)
    mins = ((dt - sess_start).dt.total_seconds() // 60).astype(int)
    d = df.copy()
    d["_bucket"] = sess_start + pd.to_timedelta((mins // minutes) * minutes, unit="m")
    # only complete windows — matches CandleFetcher._fetch_candle expected_count
    d = d[d.groupby("_bucket")["datetime"].transform("size") == minutes // 5]
    return (d.groupby("_bucket", sort=True).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).reset_index().rename(columns={"_bucket": "datetime"}))


def build_bars(df, instrument, tf, tf_min):
    bars = []
    for _, row in df.iterrows():
        start_ts = row["datetime"].replace(tzinfo=IST).timestamp()
        bars.append(Bar(
            instrument=instrument, timeframe=tf,
            start_ts=start_ts, end_ts=start_ts + tf_min * 60,
            open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], volume=int(row["volume"]),
            state=BarState.CLOSED,
        ))
    return bars


def _hours_mins(ts_float):
    return datetime.fromtimestamp(ts_float).strftime("%H:%M")


# ---------------------------------------------------------------------------
# LIVE feed: production HTF lines (BacktestStyleHTFEngine) + strategy rules
# ---------------------------------------------------------------------------
def run_live_strategy(df_5m, instrument, fast_tf, fast_min):
    htf = BacktestStyleHTFEngine()
    htf.register(instrument, "1h", 3, 6, 1.0, SESSION)
    htf.register(instrument, "15m", 3, 6, 1.0, SESSION)
    for tf, tmin in [("1h", 60), ("15m", 15)]:
        htf.load_batch_htf(instrument, tf, build_bars(resample(df_5m, tmin), instrument, tf, tmin))

    fast = resample(df_5m, fast_min)
    fast_bars = build_bars(fast, instrument, fast_tf, fast_min)

    rows = []
    for i, bar in enumerate(fast_bars):
        hm = htf.map_to_fast_bar(bar, fast_tf)
        mm = htf.map_mid_to_fast_bar(bar, fast_tf)
        h1 = hm.htf_value
        h15 = mm.htf_value if mm else None
        rows.append({
            "ts": bar.start_ts,
            "close": bar.close, "high": bar.high, "low": bar.low,
            "h1": h1, "h15": h15,
        })

    # Position-state filter — IDENTICAL decision layer as backtest.
    signals = []
    pos_side = None
    for i in range(1, len(rows)):
        r, p = rows[i], rows[i - 1]
        if r["h1"] is None or p["h1"] is None or r["h15"] is None:
            continue
        long_cross = r["close"] > r["h1"] and p["close"] <= p["h1"] and r["h15"] < r["h1"]
        short_cross = r["close"] < r["h1"] and p["close"] >= p["h1"] and r["h15"] > r["h1"]
        if long_cross and (pos_side is None or pos_side == "SHORT"):
            pos_side = "LONG"
            signals.append((r["ts"], "LONG", r["close"], r["h1"], r["h15"]))
        elif short_cross and (pos_side is None or pos_side == "LONG"):
            pos_side = "SHORT"
            signals.append((r["ts"], "SHORT", r["close"], r["h1"], r["h15"]))
    return signals, rows


# ---------------------------------------------------------------------------
# BACKTEST feed: dema_mtf lines + compute_signals + same position-state filter
# ---------------------------------------------------------------------------
def run_backtest_signals(df_5m, instrument, fast_min):
    base = resample(df_5m, fast_min)
    h15 = _dema_mtf.htf_dema_line(base, "15min", 3, 6, 1.0, session_open=SESSION)
    h1 = _dema_mtf.htf_dema_line(base, "60min", 3, 6, 1.0, session_open=SESSION)
    res = _dema_mtf.compute_signals(base, h15, h1)

    signals = []
    pos_side = None
    for i in range(1, len(base)):
        ts = base["datetime"].iloc[i].replace(tzinfo=IST).timestamp()
        buy = bool(res["raw_buy"][i])
        sell = bool(res["raw_sell"][i])
        if buy and (pos_side is None or pos_side == "SHORT"):
            pos_side = "LONG"
            signals.append((ts, "LONG", float(base["close"].iloc[i]),
                            float(h1.iloc[i]), float(h15.iloc[i])))
        elif sell and (pos_side is None or pos_side == "LONG"):
            pos_side = "SHORT"
            signals.append((ts, "SHORT", float(base["close"].iloc[i]),
                            float(h1.iloc[i]), float(h15.iloc[i])))
    return signals


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "2026-08-28"
    day = datetime.strptime(target, "%Y-%m-%d").date()
    from_date = day - timedelta(days=7)
    print(f"\n=== LIVE-vs-BACKTEST YESTERDAY SIGNAL CHECK: {target} ===")
    print(f"(data window {from_date} -> {day}; both feeds share the same bars)\n")

    summary = []
    for inst_name, sec_id in INS.items():
        df = fetch_5m(sec_id, from_date, day)
        print(f"Fetched {inst_name}: {len(df)} 5m bars "
              f"({df['datetime'].min()} -> {df['datetime'].max()})")
        for sname, scfg in STRATEGIES.items():
            if scfg["instrument"] != inst_name:
                continue
            live, _ = run_live_strategy(df, inst_name, scfg["fast_tf"], scfg["fast_min"])
            bt = run_backtest_signals(df, inst_name, scfg["fast_min"])

            lv = [x for x in live if datetime.fromtimestamp(x[0]).date() == day]
            bv = [x for x in bt if datetime.fromtimestamp(x[0]).date() == day]

            live_map = {(_hours_mins(t), s): (c, h1, h15) for (t, s, c, h1, h15) in lv}
            bt_map = {(_hours_mins(t), s): (c, h1, h15) for (t, s, c, h1, h15) in bv}
            only_live = sorted(set(live_map) - set(bt_map))
            only_bt = sorted(set(bt_map) - set(live_map))
            match = (not only_live) and (not only_bt)

            print(f"\n [{sname}] {inst_name} fast={scfg['fast_tf']}  "
                  f"{'MATCH on {}'.format(target) if match else 'DIVERGENCE'}")
            print(f"   LIVE signals:    {[(_hours_mins(t), s) for (t, s, *_r) in lv]}")
            print(f"   BACKTEST signals:{[(_hours_mins(t), s) for (t, s, *_r) in bv]}")
            if only_live:
                print(f"   ONLY LIVE ({len(only_live)}):")
                for k in only_live[:12]:
                    c, h1, h15 = live_map[k]
                    print(f"     {k[0]} {k[1]}  close={c:.0f}  1h={h1:.1f}  15m={h15:.1f}")
                if len(only_live) > 12:
                    print(f"     ... +{len(only_live) - 12} more")
            if only_bt:
                print(f"   ONLY BACKTEST ({len(only_bt)}):")
                for k in only_bt[:12]:
                    c, h1, h15 = bt_map[k]
                    print(f"     {k[0]} {k[1]}  close={c:.0f}  1h={h1:.1f}  15m={h15:.1f}")
                if len(only_bt) > 12:
                    print(f"     ... +{len(only_bt) - 12} more")
            summary.append((sname, match, len(lv), len(bv)))

    print("\n=== SUMMARY ===")
    for sname, match, n_lv, n_bt in summary:
        print(f"  {sname:12s}  {'PASS' if match else 'FAIL'}   "
              f"live={n_lv:3d}  backtest={n_bt:3d}")


if __name__ == "__main__":
    main()