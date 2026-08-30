"""EXACT live system replication - same data flow as trading_engine._warmup_from_rest + on_bar."""
import json, datetime, time, bisect, sys
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, ".")

from pathlib import Path
_ROOT = Path(__file__).resolve().parent
_token_file = _ROOT / "data" / "dhan_token.json"
try:
    from config import Config
    _cfg = Config()
    _cfg.load()
    _tf = _cfg.get("dhan.token_file", "").strip()
    if _tf:
        _cand = (_ROOT / _tf)
        if _cand.exists():
            _token_file = _cand
except Exception:
    pass
with open(_token_file) as f:
    TOKEN = json.load(f)["access_token"]

def api_call(payload):
    r = requests.post("https://api.dhan.co/v2/charts/intraday", json=payload,
        headers={"access-token": TOKEN, "Content-Type": "application/json"}, timeout=30)
    j = r.json()
    if "open" not in j:
        print("API ERROR:", j)
        sys.exit(1)
    return j

day = datetime.date(2026, 8, 28)
from_date = day - datetime.timedelta(days=7)
to_date = day

print("=" * 70)
print("EXACT LIVE SYSTEM REPLICATION: %s" % day)
print("=" * 70)

instruments = {"GOLDM": "563946", "SILVERM": "483080"}
session_open = "09:00"

for inst_name, sec_id in instruments.items():
    print("\n--- %s ---" % inst_name)

    # Step 1: Fetch 5m candles (EXACT same as live _warmup_from_rest)
    print("Fetching 5m candles...")
    j5 = api_call({
        "securityId": sec_id, "exchangeSegment": "MCX_COMM",
        "instrument": "FUTCOM", "interval": "5",
        "fromDate": "%s 09:00:00" % from_date,
        "toDate": "%s 23:55:00" % to_date,
    })
    n5 = len(j5["open"])
    print("  5m: %d candles" % n5)

    # Convert to DataFrame (EXACT same as live)
    df = pd.DataFrame({
        "timestamp": j5["timestamp"],
        "open": j5["open"], "high": j5["high"],
        "low": j5["low"], "close": j5["close"],
        "volume": j5.get("volume", [0]*n5),
    })
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.sort_values("datetime").reset_index(drop=True)

    # Step 2: Resample to 1H and 15m (EXACT same as live _warmup_from_rest)
    from core.timeframe_engine import Bar, BarState
    from indicators.dema_atr import DEMAATR
    from htf.backtest_style_htf import BacktestStyleHTFEngine

    htf_engine = BacktestStyleHTFEngine()
    htf_engine.register(inst_name, "1h", 3, 6, 1.0, session_open)
    htf_engine.register(inst_name, "15m", 3, 6, 1.0, session_open)

    for tf, tf_minutes in [("1h", 60), ("15m", 15)]:
        d = df.copy()
        dt = d["datetime"]
        dates = dt.dt.date.astype(str)
        sess_start = pd.to_datetime(dates + " " + session_open)
        mins = ((dt - sess_start).dt.total_seconds() // 60).astype(int)
        d["_bucket"] = sess_start + pd.to_timedelta((mins // tf_minutes) * tf_minutes, unit="m")
        # only complete windows — matches CandleFetcher._fetch_candle expected_count
        d = d[d.groupby("_bucket")["datetime"].transform("size") == tf_minutes // 5]
        htf = d.groupby("_bucket", sort=True).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).reset_index().rename(columns={"_bucket": "datetime"})

        bars = []
        for _, row in htf.iterrows():
            bar_dt = row["datetime"]
            if bar_dt.tzinfo is None:
                from datetime import timezone, timedelta
                bar_dt = bar_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            start_ts = bar_dt.timestamp()
            bar = Bar(
                instrument=inst_name, timeframe=tf,
                start_ts=start_ts, end_ts=start_ts + tf_minutes * 60,
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=int(row["volume"]),
                state=BarState.CLOSED,
            )
            bars.append(bar)

        htf_engine.load_batch_htf(inst_name, tf, bars)
        print("  %s: %d bars loaded" % (tf, len(bars)))

    # Step 3: Simulate live on_bar for each fast bar
    strats = []
    if inst_name == "GOLDM":
        strats = [("gold_01", "5m", 5), ("gold_02", "15m", 15)]
    else:
        strats = [("silver_01", "15m", 15), ("silver_02", "5m", 5)]

    for sname, fast_tf, fast_min in strats:
        print("\n  %s (fast=%s):" % (sname, fast_tf))

        # Get fast bars
        if fast_tf == "5m":
            fast_df = df
        else:
            # Resample to 15m
            d = df.copy()
            dt = d["datetime"]
            dates = dt.dt.date.astype(str)
            sess_start = pd.to_datetime(dates + " " + session_open)
            mins = ((dt - sess_start).dt.total_seconds() // 60).astype(int)
            d["_bucket"] = sess_start + pd.to_timedelta((mins // 15) * 15, unit="m")
            d = d[d.groupby("_bucket")["datetime"].transform("size") == 15 // 5]
            fast_df = d.groupby("_bucket", sort=True).agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).reset_index().rename(columns={"_bucket": "datetime"})

        prev_close = None
        prev_htf_val = None
        prev_mid_val = None
        prev_high = None
        prev_low = None
        signals = []

        day_start = int(datetime.datetime.combine(day, datetime.time(9, 0)).timestamp())
        day_end = int(datetime.datetime.combine(day, datetime.time(23, 59)).timestamp())

        for i in range(len(fast_df)):
            row = fast_df.iloc[i]
            bar_dt = row["datetime"]
            if bar_dt.tzinfo is None:
                from datetime import timezone, timedelta
                bar_dt = bar_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            ts = bar_dt.timestamp()

            # Create bar object
            bar = Bar(
                instrument=inst_name, timeframe=fast_tf,
                start_ts=ts, end_ts=ts + fast_min * 60,
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=int(row["volume"]),
                state=BarState.CLOSED,
            )

            # Map HTF values (EXACT same as live)
            htf_mapped = htf_engine.map_to_fast_bar(bar, fast_tf)
            mid_mapped = htf_engine.map_mid_to_fast_bar(bar, fast_tf)

            htf_val = htf_mapped.htf_value
            mid_val = mid_mapped.htf_value if mid_mapped else None

            # Crossover logic (EXACT same as live strategy)
            if prev_close is not None and prev_htf_val is not None and htf_val is not None:
                # LONG: close > 1H AND prev_close <= prev_1H AND 15m < 1H
                if (bar.close > htf_val and prev_close <= prev_htf_val
                        and mid_val is not None and mid_val < htf_val):
                    if ts >= day_start and ts <= day_end:
                        dt_obj = datetime.datetime.fromtimestamp(ts)
                        signals.append(("LONG", dt_obj.strftime("%H:%M"), bar.close, htf_val, mid_val))

                # SHORT: close < 1H AND prev_close >= prev_1H AND 15m > 1H
                if (bar.close < htf_val and prev_close >= prev_htf_val
                        and mid_val is not None and mid_val > htf_val):
                    if ts >= day_start and ts <= day_end:
                        dt_obj = datetime.datetime.fromtimestamp(ts)
                        signals.append(("SHORT", dt_obj.strftime("%H:%M"), bar.close, htf_val, mid_val))

            prev_close = bar.close
            prev_htf_val = htf_val
            prev_mid_val = mid_val
            prev_high = bar.high
            prev_low = bar.low

        if signals:
            for d, t, cl, hv, mv in signals:
                print("    %s %s  close=%.0f  1H=%.0f  15m=%.0f  ratio=%.4f" % (d, t, cl, hv, mv, mv/hv if hv else 0))
        else:
            print("    No signals")

print("\n" + "=" * 70)
print("DONE")
