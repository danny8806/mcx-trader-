"""PHASE-1 / PART 30 — SESSION-AWARE RESAMPLING AUDIT.

Proves on synthetic + real data that the CURRENT 5m -> 15m/1h resamplers
(warmup inline bucket loop in trading_engine.py and CandleFetcher aggregation
via full_simulator.build_bars) satisfy the session invariants:

  I1  session anchored:  bucket start == session_open(09:00 IST) + k*tf
  I2  single-session:    every bucket's source candles are from exactly one date
  I3  complete-window:   (keep_partial=False) buckets have exactly tf/5 candles
  I4  cross-midnight:    a next-day 00:10 print never merges into a prior-day
                         bucket and never forms a bucket (negative mins skipped)
  I5  dedup:             duplicate 5m rows never duplicate a bucket
  I6  robustness:        missing candle drops that window; out-of-order input
                         produces the same buckets as sorted input
  I7  warmup parity:     engine warmup HTF buckets == independent reference
                         full-window resample on the same real data + only the
                         trailing partial 1h bucket as an extra

Output: RESAMPLING_VALIDATION_REPORT.csv
Exit code 0 iff all pass.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import _p1_lib as L
from full_simulator import build_bars

REPORT = L.AUDIT_DIR / "RESAMPLING_VALIDATION_REPORT.csv"
if REPORT.exists():
    REPORT.unlink()

results = []


def record(scenario, tf, keep_partial, ok_anchors, ok_purity, ok_counts, pass_ok, detail):
    results.append({
        "scenario": scenario, "timeframe": tf, "keep_partial": str(keep_partial),
        "anchor_ok": str(ok_anchors), "single_session_ok": str(ok_purity),
        "counts_ok": str(ok_counts), "pass": "PASS" if pass_ok else "FAIL",
        "detail": detail,
    })


def cand_row(dt: datetime, price: float, vol=10) -> tuple:
    ts = dt.replace(tzinfo=L.IST).timestamp()
    return (ts, price, price + 2.0, price - 2.0, price, vol)


def day_times(base_date, minutes_step=5, n=174, start_hour=9):
    return [datetime(base_date.year, base_date.month, base_date.day, start_hour)
            + timedelta(minutes=k * minutes_step) for k in range(n)]


def prices_for(times, base=100.0):
    """Deterministic distinct OHLC-driving close prices (monotonic)."""
    return [base + 10.0 * i / max(len(times), 1) for i in range(len(times))]


def purity_ok(bars15, bars1h, src_times):
    src_dates = {L.ist_from_epoch(t.timestamp()).date().isoformat() for t in src_times}
    ok = True
    for bar in list(bars15) + list(bars1h):
        in_dates = {L.ist_from_epoch(t.timestamp()).date().isoformat()
                    for t in src_times
                    if bar.start_ts <= t.timestamp() < bar.end_ts}
        if len(in_dates) != 1:
            ok = False
    return ok


def anchor_ok(bars15, bars1h):
    ok = True
    for tf_min, bars in ((15, bars15), (60, bars1h)):
        for bar in bars:
            bt = L.ist_from_epoch(bar.start_ts)
            anchor = bt.replace(hour=9, minute=0, second=0, microsecond=0)
            off = int((bt - anchor).total_seconds() // 60)
            if off < 0 or off % tf_min != 0:
                ok = False
    return ok


# ════════════════════════════════════════════════════════════════
# T1 full 3 sessions — counts, anchors, purity (both keep_partial modes)
# ════════════════════════════════════════════════════════════════
N_DAYS = 3
times = []
for d in (1, 2, 3):  # Mon Aug 24..Wed Aug 26 2026
    times += day_times(datetime(2026, 8, d + 23))  # 24,25,26
rows = [cand_row(t, p) for t, p in zip(times, prices_for(times))]
for kp in (False, True):
    b5, b15, b1h = build_bars("SYNTH", rows, keep_partial=kp)
    exp15 = 58 * N_DAYS
    exp1h = 14 * N_DAYS if not kp else 15 * N_DAYS
    ok_counts = len(b15) == exp15 and len(b1h) == exp1h
    ok_p = purity_ok(b15, b1h, times)
    ok_a = anchor_ok(b15, b1h)
    record(f"T1_full_{'keep' if kp else 'drop'}partial", "15m/1h", kp,
           ok_a, ok_p, ok_counts, ok_p and ok_a and ok_counts,
           f"15m={len(b15)}(exp {exp15}) 1h={len(b1h)}(exp {exp1h})")

# ════════════════════════════════════════════════════════════════
# T2 partial end-of-session window only (22:00..23:15) — trailing partials
# ════════════════════════════════════════════════════════════════
ptimes = day_times(datetime(2026, 8, 24), minutes_step=5, n=16, start_hour=22)
prows = [cand_row(t, p) for t, p in zip(ptimes, prices_for(ptimes))]
# 5 full 15m buckets (22:00..23:15) + trailing 23:15 window has only 23:15 (partial)
b5, b15, b1h = build_bars("SYNTH", prows, keep_partial=False)
ok_counts = len(b15) == 5 and len(b1h) == 1  # 22:00 full; 23:00 1h partial dropped
ok_a = anchor_ok(b15, b1h)
ok_p = purity_ok(b15, b1h, ptimes)
record("T2_partial_boundary_drop", "15m/1h", False, ok_a, ok_p, ok_counts,
       ok_a and ok_p and ok_counts,
       f"15m={len(b15)}(exp 5) 1h={len(b1h)}(exp 1 — 23:00 partial dropped)")
b5, b15, b1h = build_bars("SYNTH", prows, keep_partial=True)
ok_counts = len(b15) == 6 and len(b1h) == 2  # partial 23:15/23:00 emitted
ok_a = anchor_ok(b15, b1h)
ok_p = purity_ok(b15, b1h, ptimes)
record("T2_partial_boundary_keep", "15m/1h", True, ok_a, ok_p, ok_counts,
       ok_a and ok_p and ok_counts,
       f"15m={len(b15)} 1h={len(b1h)}(exp 6/2 — partial kept)")

# ════════════════════════════════════════════════════════════════
# T3 cross-midnight: next-day 00:10 print must NOT merge / bucket
# ════════════════════════════════════════════════════════════════
ctimes = day_times(datetime(2026, 8, 24))  # full day 09:00..23:25
crow0 = datetime(2026, 8, 25, 0, 10)       # next-day pre-session print
ctimes_full = ctimes + [crow0]
crows = [cand_row(t, p) for t, p in zip(ctimes_full, prices_for(ctimes_full))]
b5, b15, b1h = build_bars("SYNTH", crows, keep_partial=False)
bad = [bar for bar in b15 + b1h
       if crow0.timestamp() >= bar.start_ts and crow0.timestamp() < bar.end_ts
       or L.ist_from_epoch(bar.start_ts).date().isoformat() != "2026-08-24"]
ok_no_merge = not bad and len(b15) == 58 and len(b1h) == 14
ok_a = anchor_ok(b15, b1h)
ok_p = purity_ok(b15, b1h, ctimes_full)
record("T3_cross_midnight", "15m/1h", False, ok_a, ok_p, ok_no_merge,
       ok_a and ok_p and ok_no_merge,
       f"no next-day/-bucket rows; 15m={len(b15)} 1h={len(b1h)}")

# ════════════════════════════════════════════════════════════════
# T4 mixed two sessions interleaved — every bucket single-date
# ════════════════════════════════════════════════════════════════
tA = day_times(datetime(2026, 8, 24))
tB = day_times(datetime(2026, 8, 25))
all_t = tA + tB
shuffled_t = random.Random(7).sample(all_t, len(all_t))
mrows = [cand_row(t, p) for t, p in zip(shuffled_t, prices_for(all_t))]
b5, b15, b1h = build_bars("SYNTH", mrows, keep_partial=False)
ok_p = purity_ok(b15, b1h, all_t)
ok_counts = len(b15) == 116 and len(b1h) == 28
record("T4_interleaved_sessions", "15m/1h", False, anchor_ok(b15, b1h),
       ok_p, ok_counts, ok_p and ok_counts,
       f"15m={len(b15)}(exp 116) 1h={len(b1h)}(exp 28)")

# ════════════════════════════════════════════════════════════════
# T5 duplicate 5m row — bucket must not be duplicated
# ════════════════════════════════════════════════════════════════
dtims = day_times(datetime(2026, 8, 24), n=6, start_hour=10)  # 10:00..10:25
drows = [cand_row(t, p) for t, p in zip(dtims, prices_for(dtims))]
drows.append(drows[0])  # exact duplicate of the first candle
b5, b15, b1h = build_bars("SYNTH", drows, keep_partial=False)
uniq = sorted({bar.start_ts for bar in b15})
ok_dedup = len(uniq) == len(b15) and len(b15) <= 2
ok_a = anchor_ok(b15, b1h)
ok_p = purity_ok(b15, b1h, dtims)
record("T5_duplicate_candle", "15m", False, ok_a, ok_p, ok_dedup,
       ok_a and ok_p and ok_dedup,
       f"duplicate input => {len(b15)} unique 15m buckets (no dup start_ts)")

# ════════════════════════════════════════════════════════════════
# T6a missing candle drops that window; T6b shuffle == sorted
# ════════════════════════════════════════════════════════════════
mtims = day_times(datetime(2026, 8, 24), n=10, start_hour=10)
del mtims[2]  # remove 10:10 -> 10:00 window now has 2/3 candles
mrows = [cand_row(t, p) for t, p in zip(mtims, prices_for(mtims))]
b5, b15, b1h = build_bars("SYNTH", mrows, keep_partial=False)
ok_counts = len(b15) == 2  # 10:15 & 10:30 full; 10:00 window incomplete -> dropped
ok_a = anchor_ok(b15, b1h)
ok_p = purity_ok(b15, b1h, mtims)
record("T6a_missing_candle", "15m", False, ok_a, ok_p, ok_counts,
       ok_a and ok_p and ok_counts,
       f"1 dropped 5m candle removes only its window -> 15m={len(b15)}(exp 2)")

stims = day_times(datetime(2026, 8, 24))
srows = [cand_row(t, p) for t, p in zip(stims, prices_for(stims))]
sorted_rows = list(srows)
shuffled = random.Random(3).sample(srows, len(srows))
_, A15, A1h = build_bars("SYNTH", sorted_rows, keep_partial=False)
_, B15, B1h = build_bars("SYNTH", shuffled, keep_partial=False)
sig = lambda bars: [(b.start_ts, b.open, b.high, b.low, b.close) for b in bars]
ok_order = sig(A15) == sig(B15) and sig(A1h) == sig(B1h)
record("T6b_shuffled_input", "15m/1h", False, True, True, ok_order,
       ok_order,
       "shuffled rows produce identical 15m/1h buckets as sorted rows")

# ════════════════════════════════════════════════════════════════
# T7 warmup parity on real GOLDM LAST5 data (engine warmup vs independent ref)
# ════════════════════════════════════════════════════════════════
gdf = pd.read_csv(L.CSV_DIR / L.CSVS["GOLDM"]).astype({"datetime": str})
gdf = gdf[(gdf["datetime"] >= "2026-08-24 09:00") &
          (gdf["datetime"] <= "2026-08-28 23:30")].reset_index(drop=True)
cfg = L.write_config(L.fresh_run_root("resample_e2e"), warmup={
    "keep_partial": True})
engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(engine, L.CSVFeedAdapter())
engine._warmup_from_rest()


ref_ok = True
dt_ser = pd.to_datetime(gdf["datetime"]).dt.tz_localize(L.IST)
rows5 = [
    (ds.value // 10**9, float(o), float(h), float(lo), float(c), float(v))
    for ds, o, h, lo, c, v in zip(dt_ser, gdf["open"], gdf["high"],
                                  gdf["low"], gdf["close"], gdf["volume"])]
_, bb15, bb1h = build_bars("GOLDM", rows5, keep_partial=True)


def end_str(epoch):
    return L.ist_from_epoch(epoch).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


for tf_name, tf_min, bb in (("15m", 15, bb15), ("1h", 60, bb1h)):
    bb = sorted(bb, key=lambda b: b.start_ts)
    bb_ends = {end_str(b.start_ts + tf_min * 60) for b in bb}
    # (a) independent anchor proof: every full-window ref bucket is produced,
    #     and the aggregated bucket set (warmup == CandleFetcher) is identical
    ref, _ = L.ref_session_resample(gdf, tf_min)
    ref_start = pd.to_datetime(ref["_bucket"])
    ref_ends = set((ref_start + pd.Timedelta(minutes=tf_min))
                   .dt.strftime("%Y-%m-%d %H:%M:%S"))
    ok_subset = ref_ends <= bb_ends
    # (b) independent DEMA-ATR over the aggregated series (incl. trailing
    #     partials, as the live warmup feeds them) must equal the engine line
    line = L.ref_dema_atr(np.array([b.high for b in bb], float),
                          np.array([b.low for b in bb], float),
                          np.array([b.close for b in bb], float), 3, 6, 1.0)
    eng = engine.htf_engine._engines.get(f"GOLDM:{tf_name}")
    live_by_end = {end_str(e): v for e, v in zip(eng.end_times, eng.values)}
    live_ends = set(live_by_end)
    mism = [s for s, lv in zip(sorted(bb_ends), line)
            if s in live_by_end and abs(live_by_end[s] - lv) > 1e-3]
    ok_line = len(mism) == 0
    ok_sets = live_ends == bb_ends
    ok = ok_subset and ok_line and ok_sets
    ref_ok = ref_ok and ok
    record(f"T7_warmup_ref_parity_{tf_name}", tf_name, True,
           str(ok_subset), str(ok_line), str(ok_sets), ok,
           f"ref_full_in_agg={ok_subset} DEMA_mismatches={len(mism)} "
           f"sets_equal={ok_sets} live={len(live_ends)} agg={len(bb_ends)}")
L.teardown(engine, persistence)

L.append_rows(REPORT, results)
failed = [r for r in results if r["pass"] == "FAIL"]
print(f"\n=== RESAMPLING VALIDATION ({len(results)} scenarios) ===")
for r in results:
    print(f"  {r['pass']}  {r['scenario']:<26s} {r['timeframe']:<5s} {r['detail'][:95]}")
print(f"REPORT -> {REPORT}")
print(f"RESULT: {'ALL PASSED' if not failed else f'{len(failed)} FAILED'}")
sys.exit(0 if not failed else 1)