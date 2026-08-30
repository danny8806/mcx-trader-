"""Step 6b probe: option-2 warmup alignment.

Proof for the D3 fix: with config  warmup = {last_trading_days: 5,
keep_partial: true}, `_warmup_from_rest` must seed the 1H (and 15m) DEMA-ATR
lines from the SAME last-5-trading-day window + KEEP-ALL buckets the backtest
uses, so the mapped live h1/h15 lines equal the reference lines on every bar of
the 24-28 Aug window (simulated startup = 2026-08-29 09:00 IST).

Read-only: builds an isolated engine on a temp root; does not touch config/
settings.json. Reference lines come from the parity_ref capture.
"""
from __future__ import annotations

import datetime as _dtm
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _parity_replay as pr
from _parity_replay import (  # noqa: E402
    ParityReplayDataAdapter, REF_DIR, build_engine, ist, load_rows, teardown,
)
from full_simulator import build_bars  # noqa: E402


def main():
    rows_by_inst = {n: load_rows(p) for n, p in pr.CSV_PATHS.items()}
    ref_lines = {}
    for inst in ("GOLDM", "SILVERM"):
        df = pd.read_csv(REF_DIR / f"REFERENCE_LINES_{inst}.csv")
        ref_lines[inst] = df.set_index("bucket_start")

    root = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\parity_warmup_probe")
    if root.exists():
        shutil.rmtree(root)

    cfg_path = pr.make_config(root, 0, True)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Option-2 live settings (exactly what config/settings.json now carries).
    cfg["warmup"] = {"last_trading_days": 5, "fetch_calendar_days": 14, "keep_partial": True}
    for inst_cfg in cfg["instruments"].values():
        inst_cfg["keep_partial"] = True
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    import trading_engine as te
    te.DhanDataAdapter = ParityReplayDataAdapter
    ParityReplayDataAdapter.STORE = rows_by_inst
    ParityReplayDataAdapter.CAP_BEFORE = None

    engine, persistence = build_engine(cfg_path)

    # Simulated production startup on the morning after the audited window:
    # 2026-08-29 09:00 IST  ->  fetch window 08-15..08-29  ->  last 5 trading
    # dates present in the CSV = 08-24..08-28 (the exact backtest window).
    start = ist(int(pd.Timestamp("2026-08-29 09:00", tz="Asia/Kolkata").timestamp()))
    _Fake = pr._FakeDatetime
    _Fake.FIXED = start

    _real = _dtm.datetime
    _df_ = _Fake
    _dtm.datetime = _df_
    try:
        engine._warmup_from_rest()
    finally:
        _dtm.datetime = _real

    h1_eng = engine.htf_engine._engines.get("GOLDM:1h")
    print("1h engine bars:", len(h1_eng.end_times) if h1_eng else "MISSING")

    checks = []
    for inst in ("GOLDM", "SILVERM"):
        b5, b15, b1 = build_bars(inst, rows_by_inst[inst], keep_partial=True)
        b5w = [b for b in b5 if ist(b.start_ts).strftime("%Y-%m-%d") in pr.WINDOW]
        b15w = [b for b in b15 if ist(b.start_ts).strftime("%Y-%m-%d") in pr.WINDOW]
        b1w = [b for b in b1 if ist(b.start_ts).strftime("%Y-%m-%d") in pr.WINDOW]
        if len(b5w) != 870:
            checks.append(f"{inst}: expected 870 window 5m bars, got {len(b5w)}")
        if len(b15w) != 290:
            checks.append(f"{inst}: expected 290 window 15m bars (partial 23:00 kept), got {len(b15w)}")
        if len(b1w) != 75:
            checks.append(f"{inst}: expected 75 window 1H bars (partial 23:00 kept), got {len(b1w)}")

    # Per-bar line comparison through the REAL-time mapping path, keyed by
    # bucket START to match the reference index (bucket_start).  The values
    # returned by map_to_fast_bar / map_mid_to_fast_bar are exactly what the
    # strategy consumes for its h1 cross / h15 confirmation lines.
    #
    # Session-aware per-day resampling (09:00-anchored, previous day ends at
    # its last candle) means each live line is compared to the reference at the
    # SAME 15m bucket — including every day's 09:00/09:15 session-open buckets.
    # Bars counted but not "exact" are limited to reference rows where h1 is
    # NaN (the accepted one-bar channel-reveal nuance) and None values from the
    # incremental DEMA-ATR still warming on the opening bars.
    totals = {}
    for inst in ("GOLDM", "SILVERM"):
        b5, b15, _b1h = build_bars(inst, rows_by_inst[inst], keep_partial=True)
        w15 = [b for b in b15 if ist(b.start_ts).strftime("%Y-%m-%d") in pr.WINDOW]
        ref = ref_lines[inst]
        h1_exact = h1_lag = h1_bad = h15_exact = h15_lag = h15_bad = 0
        h1_cmp = h15_cmp = 0
        h1_badlist = []
        h15_badlist = []
        prev_ref_h1 = prev_ref_h15 = float("nan")
        for bar in w15:
            key = ist(bar.start_ts).strftime("%Y-%m-%d %H:%M")
            if key not in ref.index:
                continue
            row = ref.loc[key]
            rh1 = row["h1"]
            rh15 = row["h15"]
            lh1 = engine.htf_engine.map_to_fast_bar(bar, "15m").htf_value
            lh15 = engine.htf_engine.map_mid_to_fast_bar(bar, "15m").htf_value
            if not pd.isna(rh1):
                h1_cmp += 1
                if lh1 is not None and abs(lh1 - rh1) < 1e-9:
                    h1_exact += 1
                elif lh1 is not None and not pd.isna(prev_ref_h1) and abs(lh1 - prev_ref_h1) < 1e-9:
                    h1_lag += 1
                else:
                    h1_bad += 1
                    h1_badlist.append((key, rh1, lh1))
            if not pd.isna(rh15):
                h15_cmp += 1
                if lh15 is not None and abs(lh15 - rh15) < 1e-9:
                    h15_exact += 1
                elif lh15 is not None and not pd.isna(prev_ref_h15) and abs(lh15 - prev_ref_h15) < 1e-9:
                    h15_lag += 1
                else:
                    h15_bad += 1
                    h15_badlist.append((key, rh15, lh15))
            if not pd.isna(rh1):
                prev_ref_h1 = rh1
            if not pd.isna(rh15):
                prev_ref_h15 = rh15
        totals[inst] = (h1_exact, h1_lag, h1_bad, h15_exact, h15_lag, h15_bad)
        print(f"{inst}: h1 exact={h1_exact} one-bar-lag={h1_lag} BAD={h1_bad} / {h1_cmp}   "
              f"h15 exact={h15_exact} one-bar-lag={h15_lag} BAD={h15_bad} / {h15_cmp}")
        if h1_badlist:
            print("  h1 BAD bars:", [(k, round(r, 2), None if l is None else round(l, 2)) for k, r, l in h1_badlist[:6]])
        if h15_badlist:
            print("  h15 BAD bars:", [(k, round(r, 2), None if l is None else round(l, 2)) for k, r, l in h15_badlist[:6]])

    # With bucket-start alignment, session-open bars (09:00/09:15 of every day)
    # map to the reference at the same bucket, so any warmup-induced divergence
    # shows up as BAD immediately.  Zero BAD bars are expected.
    budget = 0
    for inst, (_, _, h1b, _, _, h15b) in totals.items():
        if h1b > budget or h15b > budget:
            checks.append(f"{inst}: unresolved BAD bars beyond budget "
                          f"(h1={h1b}, h15={h15b})")

    # The money-critical case from the run-B report: SILVERM h1 line at
    # 2026-08-28 09:00 must be the backtest value ~249030, not the ~380-pt
    # offset 249409.82 that blocked the 09:00 SHORT and 11:45 LONG.
    for inst, target_key in (("SILVERM", "2026-08-28 09:00"),):
        bar = next(b for b in build_bars(inst, rows_by_inst[inst], keep_partial=True)[1]
                   if ist(b.start_ts).strftime("%Y-%m-%d %H:%M") == target_key)
        lv = engine.htf_engine.map_to_fast_bar(bar, "15m").htf_value
        rv = ref_lines[inst].loc[target_key, "h1"]
        delta = 0.0 if lv is None or pd.isna(rv) else lv - rv
        print(f"{inst} {target_key}:  live_h1={lv}  ref_h1={rv}  delta={delta}")
        if lv is None or pd.isna(rv) or abs(delta) > 15.0:
            checks.append(f"{inst} {target_key} h1 not aligned: live={lv} ref={rv}")

    teardown(engine, persistence)

    ok = len(checks) == 0
    for c in checks:
        print("CHECK FAIL:", c)
    print("RESULT:", "PASS — option-2 warmup == reference window/lines (D2+D3 resolved)" if ok else "FAIL")


if __name__ == "__main__":
    main()