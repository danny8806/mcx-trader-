"""PARITY REFERENCE CAPTURE — run the ACTUAL backtest reference (show_15_15_60.py
logic, keep-all base_mod resample override) and dump per-bar lines/signals + the
trade book to CSV so the live replay harness can compare against them.

Runs in its own process so the backtest project's os.chdir / module imports do
not pollute the live engine harness.
"""
import importlib.util
import os
import sys
from pathlib import Path

BT_PROJ = Path(r"C:\Users\pc\Desktop\nifty dema backtest\project")
BASE_PATH = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\dema_mtf_base.py")

os.chdir(BT_PROJ)
sys.path.insert(0, str(BT_PROJ))

import numpy as np
import pandas as pd

import core.dema_mtf as DM
spec = importlib.util.spec_from_file_location("dema_mtf_base", BASE_PATH)
base_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base_mod)
# The reference (show_15_15_60.py) uses the KEEP-ALL-BUCKETS resample override.
DM.resample_ohlcv = base_mod.resample_ohlcv

import goldm_dema_mtf_futures as G
import show_15_15_60 as SHOW  # module-level override already applied above; idempotent

OUT = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\parity_ref")
OUT.mkdir(parents=True, exist_ok=True)

LAST5 = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]

INSTRUMENTS = {
    "GOLDM":   ("data_mcx/gold/GOLDM_04Sep2026_5m.csv", 10.0),
    "SILVERM": ("data_mcx/silver/SILVERM_30Nov2026_5m.csv", 5.0),
}

all_trades = []
for name, (csv, mult) in INSTRUMENTS.items():
    G.MULTIPLIER = mult
    df5 = G.load_5m(csv)
    df5 = df5[df5["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)].reset_index(drop=True)
    df_base = DM.resample_ohlcv(df5, "15min", session_open=G.SESSION_OPEN).reset_index(drop=True)

    dema_mid = DM.htf_dema_line(df_base, "15min", G.DEMA_PERIOD, G.ATR_PERIOD,
                                G.ATR_FACTOR, session_open=G.SESSION_OPEN)
    dema_htf = DM.htf_dema_line(df_base, "60min", G.DEMA_PERIOD, G.ATR_PERIOD,
                                G.ATR_FACTOR, session_open=G.SESSION_OPEN)
    sig = DM.compute_signals(df_base, dema_mid, dema_htf)

    lines = pd.DataFrame({
        "bucket_start": df_base["datetime"].dt.strftime("%Y-%m-%d %H:%M"),
        "open": df_base["open"].to_numpy(float),
        "high": df_base["high"].to_numpy(float),
        "low": df_base["low"].to_numpy(float),
        "close": df_base["close"].to_numpy(float),
        "h15": np.asarray(sig["dema_15m"], dtype=float),
        "h1": np.asarray(sig["dema_1h"], dtype=float),
        "buy": sig["raw_buy"].astype(int),
        "sell": sig["raw_sell"].astype(int),
        "sl_buy": np.asarray(sig["sl_buy"], dtype=float),
        "sl_sell": np.asarray(sig["sl_sell"], dtype=float),
    })
    lines.to_csv(OUT / f"REFERENCE_LINES_{name}.csv", index=False)

    trades = SHOW.run_combo(df_base, SHOW.BASE, SHOW.MID, SHOW.HTF)
    print(f"{name}: base bars={len(df_base)} h15={int(np.isfinite(lines['h15']).sum())} "
          f"h1={int(np.isfinite(lines['h1']).sum())} buy={int(lines['buy'].sum())} "
          f"sell={int(lines['sell'].sum())} trades={len(trades)}")

    h15a = np.asarray(sig["dema_15m"], dtype=float)
    h1a = np.asarray(sig["dema_1h"], dtype=float)
    for t in trades:
        si = t.get("signal_idx")
        side = "LONG" if "BUY" in str(t.get("side", "")) else "SHORT"
        trig = float(df_base["high"][si]) if si is not None and side == "LONG" else (
               float(df_base["low"][si]) if si is not None else np.nan)
        all_trades.append({
            "instrument": name,
            "side": side,
            "signal_idx": si,
            "signal_time": str(df_base["datetime"][si])[:16] if si is not None else "",
            "trigger": round(trig, 2),
            "sl_time": t.get("sl_price"),
            "entry_idx": t.get("entry_idx"),
            "entry_time": str(t.get("entry_datetime", ""))[:16],
            "entry_price": t.get("entry_price"),
            "exit_idx": t.get("exit_idx"),
            "exit_time": str(t.get("exit_datetime", ""))[:16],
            "exit_price": t.get("exit_price"),
            "exit_reason": t.get("exit_reason"),
            "holding_minutes": t.get("holding_minutes"),
            "gross_pnl": t.get("gross_pnl"),
            "charges": t.get("charges"),
            "pnl": t.get("pnl"),
        })

pd.DataFrame(all_trades).to_csv(OUT / "REFERENCE_TRADES.csv", index=False)
print(f"REFERENCE TRADES -> {OUT / 'REFERENCE_TRADES.csv'} ({len(all_trades)} closed)")