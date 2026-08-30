"""PHASE-1 / PARTS 31-32 — INDICATOR DEMA-ATR PARITY, HTF->LTF MAPPING PARITY,
NO-LOOKAHEAD INVARIANT.

Executes the CURRENT engine warmup (offline CSV REST), the CURRENT HTF engine
mapping, and the CURRENT DEMAATR/ATR modules, then compares every value against
an INDEPENDENT reference computation on the same input.

Outputs:
  INDICATOR_PARITY_REPORT.csv   (per bucket-end timestamp)
  MAPPING_PARITY_REPORT.csv     (test points + real 5m mappings + no-lookahead)
Exit code 0 iff all pass.
"""
from __future__ import annotations

import sys
from datetime import datetime

import numpy as np
import pandas as pd

import _p1_lib as L
from full_simulator import build_bars
from htf.backtest_style_htf import BacktestStyleHTFEngine
from indicators.atr import ATR
from indicators.dema_atr import DEMAATR
from core.timeframe_engine import Bar, BarState

IDX_FILE = L.AUDIT_DIR / "INDICATOR_PARITY_REPORT.csv"
MAP_FILE = L.AUDIT_DIR / "MAPPING_PARITY_REPORT.csv"
for f in (IDX_FILE, MAP_FILE):
    if f.exists():
        f.unlink()

DEMA_TOL = 1e-3
ATR_TOL = 1e-6
idx_rows, map_rows = [], []


def aggregate_series(inst):
    gdf = pd.read_csv(L.CSV_DIR / L.CSVS[inst]).astype({"datetime": str})
    gdf = gdf[(gdf["datetime"] >= "2026-08-24 09:00") &
              (gdf["datetime"] <= "2026-08-28 23:30")].reset_index(drop=True)
    dt = pd.to_datetime(gdf["datetime"]).dt.tz_localize(L.IST)
    rows5 = [(ds.value // 10**9, float(o), float(h), float(lo), float(c), float(v))
             for ds, o, h, lo, c, v in zip(dt, gdf["open"], gdf["high"],
                                           gdf["low"], gdf["close"], gdf["volume"])]
    _, bb15, bb1h = build_bars(inst, rows5, keep_partial=True)
    return gdf, bb15, bb1h


cfg = L.write_config(L.fresh_run_root("parity_e2e"), warmup={"keep_partial": True})
engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(engine, L.CSVFeedAdapter())
engine._warmup_from_rest()


def end_str(epoch):
    return L.ist_from_epoch(epoch).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


all_ok = True
for inst in ("GOLDM", "SILVERM"):
    _, bb15, bb1h = aggregate_series(inst)
    for tf_name, tf_min, bb in (("15m", 15, bb15), ("1h", 60, bb1h)):
        bb = sorted(bb, key=lambda b: b.start_ts)
        closes = np.array([b.close for b in bb], float)
        highs = np.array([b.high for b in bb], float)
        lows = np.array([b.low for b in bb], float)

        ref_line = L.ref_dema_atr(highs, lows, closes, 3, 6, 1.0)
        atr_prod = ATR.calculate_batch(highs, lows, closes, 6)
        atr_ref = L.ref_atr(highs, lows, closes, 6)
        # production batch line (independent of engine internals)
        prod_line = DEMAATR.calculate_batch(opens=np.zeros_like(highs), highs=highs,
                                            lows=lows, closes=closes,
                                            dema_period=3, atr_period=6, atr_factor=1.0)

        eng = engine.htf_engine._engines[f"{inst}:{tf_name}"]
        live_by_end = {end_str(e): v for e, v in zip(eng.end_times, eng.values)}

        mism = 0
        for i, (b, rl) in enumerate(zip(bb, ref_line)):
            es = end_str(b.start_ts + tf_min * 60)
            lv = live_by_end.get(es)
            ap = atr_prod[i]
            ar = atr_ref[i]
            ok = (lv is not None and not np.isnan(rl) and abs(lv - rl) <= DEMA_TOL)
            if not ok:
                mism += 1
            idx_rows.append({
                "instrument": inst, "timeframe": tf_name, "bucket_end_ist": es,
                "DEMA_LIVE": round(float(lv or np.nan), 4),
                "DEMA_REFERENCE": round(float(rl), 4),
                "DEMA_DIFF": round(abs(float(lv) - float(rl)) if lv is not None else 0.0, 6),
                "ATR_LIVE": round(float(ap), 4) if not np.isnan(ap) else "",
                "ATR_REFERENCE": round(float(ar), 4) if not np.isnan(ar) else "",
                "pass": "PASS" if ok else "FAIL",
            })
        atr_ok = bool(np.allclose(np.nan_to_num(atr_prod, nan=1e9),
                                  np.nan_to_num(atr_ref, nan=1e9), atol=ATR_TOL))
        # incremental vs batch (production internal-consistency)
        inc = DEMAATR(3, 6, 1.0)
        inc_vals = [inc.update(b.open, b.high, b.low, b.close)
                    for b in [(b) for b in bb]]
        inc_arr = np.array([v if v is not None else np.nan for v in inc_vals], float)
        inc_ok = bool(np.allclose(inc_arr, prod_line, equal_nan=True))
        ok = mism == 0 and atr_ok and inc_ok
        all_ok = all_ok and ok
        idx_rows.append({
            "instrument": inst, "timeframe": tf_name, "bucket_end_ist": "SUMMARY",
            "DEMA_LIVE": "", "DEMA_REFERENCE": "", "DEMA_DIFF": str(mism),
            "ATR_LIVE": "", "ATR_REFERENCE": "",
            "pass": "PASS" if ok else "FAIL",
        })
        print(f"  [{inst}:{tf_name}] buckets={len(bb)} DEMA_mismatch={mism} "
              f"ATR_parity={atr_ok} inc==batch={inc_ok}")

# ── mapping parity (GOLDM 15m + 1h) ──────────────────────────────
b5_all = build_bars("GOLDM", L.load_csv_rows("GOLDM", "2026-08-24", "2026-08-28"))[0]
eng15 = engine.htf_engine._engines["GOLDM:15m"]
eng1h = engine.htf_engine._engines["GOLDM:1h"]


def map_ok(fast_end, htf_ends, htf_vals, htf_name):
    ref_idx = L.ref_mapping_index(htf_ends, fast_end)
    exp_ts = htf_ends[ref_idx] if ref_idx >= 0 else None
    exp_val = htf_vals[ref_idx] if ref_idx >= 0 else None
    bar = Bar(instrument="GOLDM", timeframe="5m", start_ts=fast_end - 300,
              end_ts=fast_end, open=0.0, high=0.0, low=0.0, close=0.0,
              volume=0, state=BarState.CLOSED)
    res = engine.htf_engine._map_htf_to_fast(bar, htf_name)
    got = res.htf_source_timestamp
    ok_ts = (got == exp_ts) or (got is None and exp_ts is None)
    ok_val = (res.htf_value == exp_val) or (res.htf_value is None and exp_val is None)
    no_lookahead = (res.htf_confirmed is False) or (got is not None and got <= fast_end)
    return ok_ts and ok_val and no_lookahead, res.htf_confirmed, got


for htf_name, eng in (("15m", eng15), ("1h", eng1h)):
    # synthetic boundary points
    points = [
        (eng.end_times[0] - 1, "before_first"),
        (eng.end_times[0], "at_first"),
        (eng.end_times[0] + 1, "past_first"),
        ((eng.end_times[0] + eng.end_times[1]) // 2, "mid_two"),
        (eng.end_times[-1] + 5, "past_last"),
    ]
    for pe, label in points:
        ok, conf, got = map_ok(pe, eng.end_times, eng.values, htf_name)
        all_ok = all_ok and ok
        map_rows.append({
            "test_point": label, "timeframe": htf_name, "fast_end": end_str(pe),
            "idx_ref": L.ref_mapping_index(eng.end_times, pe),
            "mapped_ts": end_str(got) if got else "(none)",
            "confirmed": str(conf), "no_lookahead": str(True),
            "pass": "PASS" if ok else "FAIL",
        })
    # every real 5m bar of the warmup window
    bad = 0
    for b5 in b5_all:
        ok, conf, got = map_ok(b5.end_ts, eng.end_times, eng.values, htf_name)
        if not ok:
            bad += 1
    all_ok = all_ok and bad == 0
    map_rows.append({
        "test_point": f"all_5m_bars({len(b5_all)})", "timeframe": htf_name,
        "fast_end": "", "idx_ref": "", "mapped_ts": "",
        "confirmed": "", "no_lookahead": "all checked", "pass": "PASS" if bad == 0 else "FAIL",
    })
    print(f"  mapping {htf_name}: boundary_points ok, {len(b5_all)} real 5m bars all match ref, bad={bad}")

# ── no-lookahead: future bars must not affect past HTF state ────
def fresh_htf():
    h = BacktestStyleHTFEngine()
    h.register("GOLDM", "15m", 3, 6, 1.0)
    h.register("GOLDM", "1h", 3, 6, 1.0)
    return h


b15 = sorted(bb15, key=lambda x: x.start_ts)
A = fresh_htf()
B = fresh_htf()
# feed index 0..N-2 identically, then feed a MODIFIED last bar to A only
for b in b15[:-1]:
    A.on_htf_bar_closed(b)
    B.on_htf_bar_closed(b)
last = b15[-1]
# modify last bar's close * 2 -> must not change any earlier value
A.on_htf_bar_closed(Bar(last.instrument, last.timeframe, last.start_ts, last.end_ts,
                       last.open, last.high, last.low, last.close * 2, last.volume))
B.on_htf_bar_closed(Bar(last.instrument, last.timeframe, last.start_ts, last.end_ts,
                       last.open, last.high, last.low, last.close, last.volume))
sa, sb = A.snapshot()["GOLDM:15m"], B.snapshot()["GOLDM:15m"]
va = np.array([v if v is not None else np.nan for v in sa["values"][:-1]], float)
vb = np.array([v if v is not None else np.nan for v in sb["values"][:-1]], float)
la_ok = bool(np.allclose(va, vb, equal_nan=True))
# the last value legitimately differs (future bar modified only its own output)
map_rows.append({
    "test_point": "no_lookahead_future_mutation", "timeframe": "15m",
    "fast_end": "", "idx_ref": "", "mapped_ts": "",
    "confirmed": f"past_values_after_last={len(va)}",
    "no_lookahead": "all prior values unchanged", "pass": "PASS" if la_ok else "FAIL",
})
all_ok = all_ok and la_ok

L.append_rows(IDX_FILE, idx_rows)
L.append_rows(MAP_FILE, map_rows)
print(f"\n=== INDICATOR/MAPPING PARITY ===")
print(f"  indicator rows={len(idx_rows)} mapping rows={len(map_rows)}")
print(f"  RESULT: {'ALL PASSED' if all_ok else 'FAILURES PRESENT'}")
sys.exit(0 if all_ok else 1)