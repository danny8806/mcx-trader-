import sys, importlib.util
import numpy as np
import pandas as pd
from datetime import timezone, timedelta

sys.path.insert(0, r"C:\Users\pc\Desktop\MCX-TRADER")
sys.path.append(r"C:\Users\pc\Desktop\nifty dema backtest\project")  # LAST: only build_15min_enriched/utils

from htf.backtest_style_htf import BacktestStyleHTFEngine
from full_simulator import build_bars, ist

BASE = importlib.util.spec_from_file_location(
    "dema_mtf_base", r"C:\Users\pc\AppData\Local\Temp\opencode\dema_mtf_base.py")
base = importlib.util.module_from_spec(BASE); BASE.loader.exec_module(base)

CSVS = {
    "GOLDM":   r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\gold\GOLDM_04Sep2026_5m.csv",
    "SILVERM": r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\silver\SILVERM_30Nov2026_5m.csv",
}
LAST5 = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]
_TF_RANK = {"1h": 0, "15m": 1}
IST_TZ = timezone(timedelta(hours=5, minutes=30))


def load_5m(path):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime").drop_duplicates(subset="datetime", keep="last")
    return df.reset_index(drop=True)


def epoch(naive):
    return int(naive.replace(tzinfo=IST_TZ).timestamp())


for name, path in CSVS.items():
    df5 = load_5m(path)
    df5 = df5[df5["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)].reset_index(drop=True)
    rows = [(epoch(r["datetime"].replace(tzinfo=None)), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), int(r["volume"])) for _, r in df5.iterrows()]
    b5, b15, b1h = build_bars(name, rows, keep_partial=True)

    # REFERENCE lines on the 15m base (equal rule 15m; htf rule 60m), KEEP-ALL
    b15df = pd.DataFrame({"datetime": [ist(x.start_ts).replace(tzinfo=None) for x in b15],
                          "open": [x.open for x in b15], "high": [x.high for x in b15],
                          "low": [x.low for x in b15], "close": [x.close for x in b15],
                          "volume": [x.volume for x in b15]})
    ref_h15 = base.htf_dema_line(b15df, "15min", 3, 6, 1.0, session_open="09:00").to_numpy(float)
    ref_h1 = base.htf_dema_line(b15df, "60min", 3, 6, 1.0, session_open="09:00").to_numpy(float)

    # LIVE engine fed exactly in replay order (end_ts asc, 1h before 15m on ties)
    htf = BacktestStyleHTFEngine()
    htf.register(name, "1h", 3, 6, 1.0, "09:00")
    htf.register(name, "15m", 3, 6, 1.0, "09:00")
    seq = sorted(b15 + b1h, key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))

    live_h15 = np.full(len(b15), np.nan)
    live_h1 = np.full(len(b15), np.nan)
    bar_index = {b.start_ts: i for i, b in enumerate(b15)}
    for bar in seq:
        if bar.timeframe in ("15m", "1h"):
            htf.on_htf_bar_closed(bar)
        if bar.timeframe == "15m":
            i = bar_index[bar.start_ts]
            live_h15[i] = htf.map_mid_to_fast_bar(bar, "15m").htf_value or np.nan
            m = htf.map_to_fast_bar(bar, "1h")
            live_h1[i] = m.htf_value or np.nan

    ok15 = np.isfinite(ref_h15) | np.isfinite(live_h15)
    ok1 = np.isfinite(ref_h1) | np.isfinite(live_h1)
    both15 = np.isfinite(ref_h15) & np.isfinite(live_h15)
    both1 = np.isfinite(ref_h1) & np.isfinite(live_h1)
    print(f"=== {name}  STEP5 HTF MAPPING  (15m bars={len(b15)}) ===")
    print(f"  h15 line  finite ref={int(np.isfinite(ref_h15).sum())} live={int(np.isfinite(live_h15).sum())} "
          f"both={int(both15.sum())} equal={int((ref_h15[both15]==live_h15[both15]).sum())} "
          f"max_abs_diff={float(abs(ref_h15[both15]-live_h15[both15]).max()):.9g}")
    print(f"  h1  line  finite ref={int(np.isfinite(ref_h1).sum())} live={int(np.isfinite(live_h1).sum())} "
          f"both={int(both1.sum())} equal={int((ref_h1[both1]==live_h1[both1]).sum())} "
          f"max_abs_diff={float(abs(ref_h1[both1]-live_h1[both1]).max()):.9g}")
    # mismatches where one finite, other not, or both finite but different
    mism = []
    for i in range(len(b15)):
        if ref_h1[i] != live_h1[i] and not (np.isnan(ref_h1[i]) and np.isnan(live_h1[i])):
            mism.append((b15[i].start_ts, ref_h1[i], live_h1[i]))
    print(f"  h1 mismatched rows (incl. one-sided NaN): {len(mism)}")
    for t, r, l in mism[:6]:
        print(f"    bar {ist(t)}  ref={r}  live={l}")
    print()