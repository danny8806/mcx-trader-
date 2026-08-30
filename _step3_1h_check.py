import sys
import numpy as np
import pandas as pd
from datetime import timezone, timedelta

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


def resample_ohlcv(ref, minutes=60):
    df = ref.copy()
    dt = pd.to_datetime(df["datetime"])
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

    ref1h = resample_ohlcv(df5, 60).reset_index(drop=True)          # ref KEEP-ALL
    rows = [(as_epoch_ist(r["datetime"].replace(tzinfo=None)), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), int(r["volume"])) for _, r in df5.iterrows()]

    def alive(keep_partial):
        _, _, b1h = build_bars(name, rows, keep_partial=keep_partial)
        return b1h, pd.to_datetime([ist(b.start_ts).replace(tzinfo=None) for b in b1h])

    b_kp, dt_kp = alive(True)
    b_cm, dt_cm = alive(False)

    ref_dt = pd.to_datetime(ref1h["datetime"])
    def compare(ref_dt, refdf, dt, bars, label):
        ldf = pd.DataFrame({"dt": dt,
                            "o": [b.open for b in bars], "h": [b.high for b in bars],
                            "l": [b.low for b in bars], "c": [b.close for b in bars],
                            "v": [b.volume for b in bars]})
        m = pd.DataFrame({"dt": ref_dt, "open": refdf["open"], "high": refdf["high"],
                          "low": refdf["low"], "close": refdf["close"],
                          "volume": refdf["volume"]}).merge(ldf, on="dt", how="outer", indicator=True)
        both = m[m["_merge"] == "both"]
        only_ref = m[m["_merge"] == "left_only"]
        only_live = m[m["_merge"] == "right_only"]
        print(f"  [{label}] bars live={len(bars)} ref={len(refdf)} | both={len(both)} "
              f"only_ref={len(only_ref)} only_live={len(only_live)}")
        for _, row in only_ref.iterrows():
            print(f"    ONLY-REF  {row['dt']}  close={row['close']}")
        for _, row in only_live.iterrows():
            print(f"    ONLY-LIVE {row['dt']}")
        for col, lc in (("open","o"), ("high","h"), ("low","l"), ("close","c"), ("volume","v")):
            a = both[lc].to_numpy(float); r = both[col].to_numpy(float)
            print(f"      {col:<7} match {int((a==r).sum())}/{len(both)}  max_abs_diff={float(abs(a-r).max()):.6g}")

    print(f"=== {name}  STEP3 1h RESAMPLE (ref=KEEP-ALL session-anchored 09:00) ===")
    compare(ref_dt, ref1h, dt_kp, b_kp, "keep_partial=True  (run A == ref)")
    compare(ref_dt, ref1h, dt_cm, b_cm, "keep_partial=False (run B / production)")
    print()