import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r"C:\Users\pc\Desktop\MCX-TRADER")
from full_simulator import build_bars, ist  # noqa

CSVS = {
    "GOLDM":   r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\gold\GOLDM_04Sep2026_5m.csv",
    "SILVERM": r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\silver\SILVERM_30Nov2026_5m.csv",
}
LAST5 = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]


def load_5m(path):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime").drop_duplicates(subset="datetime", keep="last")
    return df.reset_index(drop=True)


def resample_ohlcv(ref):
    df = ref.copy()
    dt = pd.to_datetime(df["datetime"])
    minutes = 15
    dates = dt.dt.date.astype(str)
    session_start = pd.to_datetime(dates + " 09:00")
    mins = ((dt - session_start).dt.total_seconds() // 60).astype(int)
    df["_bucket"] = session_start + pd.to_timedelta((mins // minutes) * minutes, unit="m")
    out = (df.groupby("_bucket", sort=True)
           .agg({"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"})
           .reset_index().rename(columns={"_bucket": "datetime"}))
    return out[["datetime", "open", "high", "low", "close", "volume"]]


IST_TZ = timezone(timedelta(hours=5, minutes=30))


def as_epoch_ist(naive_ist):
    return int(naive_ist.replace(tzinfo=IST_TZ).timestamp())


for name, path in CSVS.items():
    df5 = load_5m(path)
    df5 = df5[df5["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)].reset_index(drop=True)

    ref15 = resample_ohlcv(df5).reset_index(drop=True)

    rows = []
    for _, r in df5.iterrows():
        naive = r["datetime"].replace(tzinfo=None)
        rows.append((as_epoch_ist(naive), float(r["open"]), float(r["high"]),
                     float(r["low"]), float(r["close"]), int(r["volume"])))
    b5, b15, b1h = build_bars(name, rows, keep_partial=True)

    ref_dt = pd.to_datetime(ref15["datetime"])
    live_dt = pd.Series([ist(b.start_ts).replace(tzinfo=None) for b in b15])
    ldf = pd.DataFrame({
        "dt": live_dt,
        "l_open": [b.open for b in b15], "l_high": [b.high for b in b15],
        "l_low": [b.low for b in b15], "l_close": [b.close for b in b15],
        "l_vol": [b.volume for b in b15],
    })
    m = pd.DataFrame({"dt": ref_dt,
                      "open": ref15["open"], "high": ref15["high"], "low": ref15["low"],
                      "close": ref15["close"], "volume": ref15["volume"]}).merge(
        ldf, on="dt", how="outer", indicator=True)
    only_ref = m[m["_merge"] == "left_only"]
    only_live = m[m["_merge"] == "right_only"]
    both = m[m["_merge"] == "both"]

    print(f"=== {name}  STEP2 15m RESAMPLE ===")
    print(f"  ref 15m bars: {len(ref15)}    live(keep_partial) 15m bars: {len(b15)}")
    print(f"  buckets both: {len(both)}   only ref: {len(only_ref)}   only live: {len(only_live)}")
    for _, row in only_ref.iterrows():
        print(f"    only-REF bucket {row['dt']}")
    for _, row in only_live.iterrows():
        print(f"    only-LIVE bucket {row['dt']}")
    for col, lo in (("open", "l_open"), ("high", "l_high"), ("low", "l_low"),
                    ("close", "l_close"), ("volume", "l_vol")):
        a = both[lo].to_numpy(float)
        r = both[col].to_numpy(float)
        print(f"  {col:<7} shared match {int((a == r).sum())}/{len(both)}   max_abs_diff={float(abs(a - r).max()):.6g}")
    print(f"  first {both['dt'].iloc[0]}   last {both['dt'].iloc[-1]}   days {both['dt'].dt.date.nunique()}")
    print()

# also show closed-state of live fast bars
for name in ("GOLDM", "SILVERM"):
    df5 = load_5m(CSVS[name])
    df5 = df5[df5["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)].reset_index(drop=True)
    rows = [(as_epoch_ist(r["datetime"].replace(tzinfo=None)), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), int(r["volume"])) for _, r in df5.iterrows()]
    b5, b15, b1h = build_bars(name, rows, keep_partial=True)
    print(f"{name} 15m bars all BarState.CLOSED: {all(b.is_closed for b in b15)}; "
          f"1h bars all CLOSED: {all(b.is_closed for b in b1h)}")