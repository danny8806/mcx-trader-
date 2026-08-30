import sys
import importlib.util
import pandas as pd

G = importlib.util.spec_from_file_location(
    "g", r"C:\Users\pc\Desktop\nifty dema backtest\project\goldm_dema_mtf_futures.py"
)
g = importlib.util.module_from_spec(G)
G.loader.exec_module(g)

CSVS = {
    "GOLDM":   r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\gold\GOLDM_04Sep2026_5m.csv",
    "SILVERM": r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\silver\SILVERM_30Nov2026_5m.csv",
}

def load_live(csv_path):
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    s = df["datetime"].dt.tz_localize("Asia/Kolkata")
    secs = ((s - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()).astype("int64")
    out = []
    for e, o, h, l, c, v in zip(secs, df["open"], df["high"], df["low"], df["close"], df["volume"]):
        out.append((int(e), float(o), float(h), float(l), float(c), int(v)))
    return out, df

for name, path in CSVS.items():
    ref = g.load_5m(path)
    live, raw = load_live(path)

    win = (ref["datetime"] >= "2026-08-24") & (ref["datetime"] <= "2026-08-28 23:59")
    refw = ref[win]

    livew = raw[(raw["datetime"] >= "2026-08-24") & (raw["datetime"] <= "2026-08-28 23:59")]

    print(f"=== {name} ===")
    print(f"  file rows          : {len(ref)}   (ref loader)   vs  {len(raw)}   (live loader)")
    print(f"  dup datetimes      : {int(ref['datetime'].duplicated().sum())}")
    invalid = ((ref["high"] < ref[["open", "close", "low"]].max(axis=1)) |
               (ref["low"] > ref[["open", "close", "high"]].min(axis=1)) |
               (ref[["open", "high", "low", "close"]] <= 0).any(axis=1))
    print(f"  invalid OHLC rows  : {int(invalid.sum())}")
    print(f"  window bars        : {len(refw)}   (ref)  vs  {len(livew)}   (live)")
    print(f"  first window bar   : {refw['datetime'].iloc[0]}  |  last: {refw['datetime'].iloc[-1]}")

    refw = refw.reset_index(drop=True)
    livew = livew.reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        r, l = refw[col].to_numpy(dtype="float64"), livew[col].to_numpy(dtype="float64")
        same = (r == l) | (pd.isna(r) & pd.isna(l))
        print(f"  {col:<7} window match: {int(same.sum())}/{len(refw)}   max_abs_diff={float(abs(r-l).max()):.9g}")
    dt_neq = int((refw["datetime"].to_numpy() != livew["datetime"].to_numpy()).sum())
    print(f"  window datetime match: {dt_neq == 0}  (neq={dt_neq})")
    print()