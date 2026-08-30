import sys
import numpy as np
import pandas as pd
from datetime import timezone, timedelta

sys.path.insert(0, r"C:\Users\pc\Desktop\MCX-TRADER")
sys.path.insert(0, r"C:\Users\pc\Desktop\nifty dema backtest\project")  # ONLY for build_15min_enriched import; no core import below
from indicators.dema_atr import DEMAATR
from build_15min_enriched import dema_atr as ref_dema_atr
sys.path.pop(0)

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


def resample_ohlcv(ref, minutes):
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
    return out[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def both_lines(ohlc_df, label):
    rev = ohlc_df[["datetime", "open", "high", "low", "close"]].copy()
    ref_line = ref_dema_atr(rev, 3, 6, 1.0).to_numpy(dtype=float)
    live = DEMAATR(3, 6, 1.0)
    inc = np.full(len(rev), np.nan, dtype=float)
    for i, (_, r) in enumerate(rev.iterrows()):
        v = live.update(r["open"], r["high"], r["low"], r["close"])
        inc[i] = v if v is not None else np.nan
    both = ~(np.isnan(ref_line) & np.isnan(inc))
    same = np.isclose(ref_line[both], inc[both], rtol=0, atol=1e-9)
    print(f"  [{label}] bars={len(rev)} finite ref={int(np.isfinite(ref_line).sum())} "
          f"live={int(np.isfinite(inc).sum())} | shared={int(both.sum())} "
          f"match={int(same.sum())} max_abs_diff={float(abs(ref_line[both]-inc[both]).max()):.9g}")


for name, path in CSVS.items():
    df5 = load_5m(path)
    df5 = df5[df5["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)].reset_index(drop=True)
    print(f"=== {name}  STEP4 DEMA-ATR(3,6,1.0) math (reference batch vs live incremental) ===")
    both_lines(resample_ohlcv(df5, 15), "15m (fast/mid line, also h1 mapping source base)")
    both_lines(resample_ohlcv(df5, 60), "1h (htf line)")
    print()