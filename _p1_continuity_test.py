"""PHASE-1 / PART 34 — FIVE-DAY BACKTEST-ALIGNED SESSION CONTINUITY.

Executes the CURRENT warmup on the real data and proves the stream the engine
sees matches the backtest LAST5 seed session-for-session:

  C1  the warmup session set == the backtest LAST5 (5 trading dates, exact)
  C2  per-day bar counts: 5m=174, 15m=58, 1h=15 (incl. trailing 23:00 partial)
  C3  per-day anchoring: first 5m candle 09:00, closes 23:25/23:30; first 15m
      bucket ends 09:15; last 15m bucket ends 23:30
  C4  day-to-day continuity: first fast (5m) bar of each new day maps to the
      PREVIOUS day's final HTF bucket (15m -> 23:30, 1h -> next-day 00:00),
      i.e. the line folds over the boundary without reset or gap
  C5  no reseeding: DEMA-ATR processed 290 (15m) / 75 (1h) values, one pass

Output: FIVE_DAY_CONTINUITY_REPORT.csv
Exit code 0 iff all pass.
"""
from __future__ import annotations

import sys
from datetime import datetime

import _p1_lib as L
from core.timeframe_engine import Bar, BarState
from full_simulator import build_bars

REPORT = L.AUDIT_DIR / "FIVE_DAY_CONTINUITY_REPORT.csv"
if REPORT.exists():
    REPORT.unlink()

rows = []
cfg = L.write_config(L.fresh_run_root("continuity"), warmup={"keep_partial": True})
engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(engine, L.CSVFeedAdapter())
engine._warmup_from_rest()

b5 = build_bars("GOLDM", L.load_csv_rows("GOLDM", L.LAST5[0], L.LAST5[-1]))[0]
eng15 = engine.htf_engine._engines["GOLDM:15m"]
eng1h = engine.htf_engine._engines["GOLDM:1h"]
v15 = dict(zip(eng15.end_times, eng15.values))


def isod(ts):
    return L.ist_from_epoch(ts).replace(tzinfo=None)


def hm(ts):
    return isod(ts).strftime("%Y-%m-%d %H:%M:%S")


all_ok = True
# C1 session set
sessions = sorted({isod(b.start_ts).strftime("%Y-%m-%d") for b in b5})
ok1 = sessions == sorted(L.LAST5)
rows.append({"check": "C1_session_set_is_LAST5", "value": str(sessions),
             "expect": str(sorted(L.LAST5)), "pass": "PASS" if ok1 else "FAIL"})
all_ok &= ok1

for day in L.LAST5:
    day_bars = [b for b in b5 if isod(b.start_ts).strftime("%Y-%m-%d") == day]
    ok2 = len(day_bars) == 174
    ok3 = isod(day_bars[0].start_ts).strftime("%H:%M") == "09:00" \
        and isod(day_bars[-1].start_ts).strftime("%H:%M") == "23:25"
    d15_end = eng15.end_times
    day15 = [e for e in d15_end if isod(e).strftime("%Y-%m-%d") == day]
    # 1h buckets are keyed by bucket END (23:00->00:00(D+1) partial included);
    # count against the backtest START alignment as end - tf.
    day1h = [e for e in eng1h.end_times
             if isod(e - 3600).strftime("%Y-%m-%d") == day]
    ok4 = (len(day15) == 58 and len(day1h) == 15
           and hm(day15[0]) == f"{day} 09:15:00" and hm(day15[-1]) == f"{day} 23:30:00")
    ok = ok2 and ok3 and ok4
    all_ok &= ok
    rows.append({"check": f"C2C3_day_counts_{day}", "value":
                 f"5m={len(day_bars)} 15m={len(day15)} 1h={len(day1h)} "
                 f"first5m={isod(day_bars[0].start_ts).strftime('%H:%M')} "
                 f"last5m={isod(day_bars[-1].start_ts).strftime('%H:%M')}",
                 "expect": "5m=174 15m=58 1h=15 09:00/23:25 09:15/23:30",
                 "pass": "PASS" if ok else "FAIL"})

# C4 continuity across each boundary (D -> D+1)
for i in range(len(L.LAST5) - 1):
    d, nd = L.LAST5[i], L.LAST5[i + 1]
    first_fast = [b for b in b5 if isod(b.start_ts).strftime("%Y-%m-%d") == nd
                  and isod(b.start_ts).strftime("%H:%M") == "09:00"][0]
    bar = Bar("GOLDM", "5m", first_fast.start_ts, first_fast.end_ts,
              first_fast.open, first_fast.high, first_fast.low, first_fast.close,
              first_fast.volume, BarState.CLOSED)
    m15 = engine.htf_engine._map_htf_to_fast(bar, "15m")
    m1h = engine.htf_engine._map_htf_to_fast(bar, "1h")
    exp15 = hm(max(e for e in eng15.end_times
                   if isod(e).strftime("%Y-%m-%d") == d))  # day D's last 15m end == 23:30
    exp1h = hm(max(e for e in eng1h.end_times
                   if isod(e).strftime("%Y-%m-%d") == d or
                   (isod(e).strftime("%Y-%m-%d") == nd and isod(e).strftime("%H") == "00")))
    ok15 = m15.htf_confirmed and hm(m15.htf_source_timestamp) == exp15
    ok1h = m1h.htf_confirmed and hm(m1h.htf_source_timestamp) == exp1h
    # value continuity: mapped value equals the value carried at the previous
    # day's final bucket (the same clamped line continues, no reseed)
    val15 = m15.htf_value
    prev_end = max(e for e in eng15.end_times if isod(e).strftime("%Y-%m-%d") == d)
    ok_val = val15 is not None and abs(val15 - v15[prev_end]) < 1e-9
    ok = ok15 and ok1h and ok_val
    all_ok &= ok
    rows.append({"check": f"C4_transition_{d}_to_{nd}", "value":
                 f"15m->{hm(m15.htf_source_timestamp) if m15.htf_confirmed else 'none'} "
                 f"1h->{hm(m1h.htf_source_timestamp) if m1h.htf_confirmed else 'none'} "
                 f"val_cont={ok_val}",
                 "expect":
                 f"15m->{exp15} 1h->{exp1h} val_cont=True (same continuous line)",
                 "pass": "PASS" if ok else "FAIL"})

# C5 counts (one pass, no reseeding)
ind15 = engine.indicators["GOLDM:15m"].snapshot()
ind1h = engine.indicators["GOLDM:1h"].snapshot()
ok5 = len(eng15.end_times) == 290 and len(eng1h.end_times) == 75 \
    and ind15.get("count") == 290 and ind1h.get("count") == 75
rows.append({"check": "C5_no_reseed_counts", "value":
             f"15m_count={ind15.get('count')} 1h_count={ind1h.get('count')}",
             "expect": "290 / 75", "pass": "PASS" if ok5 else "FAIL"})
all_ok &= ok5

L.append_rows(REPORT, rows)
print(f"\n=== FIVE-DAY CONTINUITY ({len(rows)} checks) ===")
for r in rows:
    print(f"  {r['pass']}  {r['check']:<24s} {r['value'][:80]}")
print(f"REPORT -> {REPORT}")
print(f"RESULT: {'ALL PASSED' if all_ok else 'FAILURES PRESENT'}")
sys.exit(0 if all_ok else 1)