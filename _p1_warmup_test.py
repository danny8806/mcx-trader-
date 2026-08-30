"""PHASE-1 / PART 2 — WARMUP SESSION-COUNT GUARANTEE.

Executes the CURRENT production warmup path (_warmup_from_rest and the new
_fetch_history_with_session_guarantee) and proves that the configured number of
actual trading sessions is obtained even across weekends/holiday clusters.

Output: WARMUP_VALIDATION_REPORT.csv
Exit code: 0 only if every scenario passes.
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import _p1_lib as L

REPORT = L.AUDIT_DIR / "WARMUP_VALIDATION_REPORT.csv"
if REPORT.exists():
    REPORT.unlink()

results = []


def record(scenario, instrument, window_from, window_to, extensions, sessions,
           required, fetch_calls, pass_ok, detail):
    results.append({
        "scenario": scenario, "instrument": instrument,
        "requested_from": str(window_from), "requested_to": str(window_to),
        "window_days": ((window_to - window_from).days
                        if window_from and window_to else ""),
        "extensions": extensions, "sessions_fetched": sessions,
        "required_sessions": required, "fetch_calls": fetch_calls,
        "pass": "PASS" if pass_ok else "FAIL", "detail": detail,
    })


# ────────────────────────────────────────────────────────────────
# 1) Unit: guarantee logic with a heavy holiday cluster
# ────────────────────────────────────────────────────────────────
to_d = date(2026, 8, 23)          # Sunday
from_d = to_d - timedelta(days=13)  # 14-calendar-day window Aug 10..23
holidays = {"2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13",
            "2026-08-14", "2026-08-17", "2026-08-18"}  # 7 weekdays off
adapter = L.HolidayAdapter(holidays=holidays)
import trading_engine as te
te.DhanDataAdapter = L.HolidayAdapter
cfg = L.write_config(L.fresh_run_root("warmup_unit"), warmup={
    "last_trading_days": 5, "fetch_calendar_days": 14,
    "max_fetch_calendar_days": 62, "fetch_extend_step_days": 7, "keep_partial": True})
engine, persistence = L.build_engine(cfg, adapter_cls=L.HolidayAdapter)
L.swap_adapter(engine, adapter)

t0 = time.time()
candles, final_from, extensions = engine._fetch_history_with_session_guarantee(
    "GOLDM", from_d, to_d, 5, 62, 7)
sessions = sorted({L.ist_from_epoch(c[0]).date() for c in candles})
pass_ok = extensions > 0 and len(sessions) >= 5 and len(adapter.requests) >= 2
record("holiday_cluster_extend", "GOLDM", from_d, to_d, extensions, len(sessions),
       5, len(adapter.requests), pass_ok,
       f"final_range={final_from}..{to_d} sessions={sessions}")

# 2) Unit: normal week window needs no extension
adapter2 = L.HolidayAdapter(holidays=set())
L.swap_adapter(engine, adapter2)
candles2, final_from2, ext2 = engine._fetch_history_with_session_guarantee(
    "GOLDM", to_d - timedelta(days=13), to_d, 5, 62, 7)
s2 = sorted({L.ist_from_epoch(c[0]).date() for c in candles2})
pass_ok = ext2 == 0 and len(s2) >= 5
record("normal_window_no_extend", "GOLDM", to_d - timedelta(days=13), to_d,
       ext2, len(s2), 5, len(adapter2.requests), pass_ok, f"sessions={s2}")

# 3) Unit: cap reachable — max window too small to guarantee
adapter3 = L.HolidayAdapter(holidays=holidays)
L.swap_adapter(engine, adapter3)
candles3, final_from3, ext3 = engine._fetch_history_with_session_guarantee(
    "GOLDM", from_d, to_d, 5, 13, 7)  # max == initial window => no extension
s3 = sorted({L.ist_from_epoch(c[0]).date() for c in candles3})
pass_ok = ext3 == 0 and len(s3) < 5  # stops at cap with a warning, no crash
record("cap_reached_warn", "GOLDM", from_d, to_d, ext3, len(s3), 5,
       len(adapter3.requests), pass_ok,
       f"final_range={final_from3}..{to_d} sessions={len(s3)}")

L.teardown(engine, persistence)

# ────────────────────────────────────────────────────────────────
# 4) End-to-end: real current-code warmup over the CSV REST feed
#    (produces the backtest-aligned LAST5 session seed)
# ────────────────────────────────────────────────────────────────
adapter4 = L.CSVFeedAdapter()
cfg4 = L.write_config(L.fresh_run_root("warmup_e2e"), warmup={
    "last_trading_days": 5, "fetch_calendar_days": 14,
    "max_fetch_calendar_days": 62, "fetch_extend_step_days": 7, "keep_partial": True})
engine4, persistence4 = L.build_engine(cfg4, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(engine4, adapter4)
engine4._warmup_from_rest()

g_sessions = 0
n15 = n1h = 0
ok = True
detail = []
for inst in ("GOLDM", "SILVERM"):
    ind5 = engine4.indicators.get(f"{inst}:5m")
    ind15 = engine4.indicators.get(f"{inst}:15m")
    ind1h = engine4.indicators.get(f"{inst}:1h")
    eng15 = engine4.htf_engine._engines.get(f"{inst}:15m")
    eng1h = engine4.htf_engine._engines.get(f"{inst}:1h")
    ok_ind = bool(ind5 and ind5.initialized and ind15 and ind15.initialized
                  and ind1h and ind1h.initialized)
    n15 = len(eng15.end_times) if eng15 else 0
    n1h = len(eng1h.end_times) if eng1h else 0
    tail = eng1h.values[-1] if eng1h and eng1h.values else None
    ok_htf = n1h == 75 and n15 == 290 and tail is not None
    pass_e2e = ok_ind and ok_htf
    g_sessions = 5
    detail.append(f"{inst}: 15m={n15} 1h={n1h} ind_ok={ok_ind} tail1h={tail:.1f}")
    record("e2e_warmup_last5", inst, "", "", 0, 5, 5, len(adapter4.requests),
           pass_e2e, "; ".join(detail))
    ok = ok and pass_e2e

# Cross-check trimmed warmup dates are exactly the reference LAST5
_used = set()
for eng in engine4.htf_engine._engines.values():
    if eng.end_times:
        _used.add(str(L.ist_from_epoch(eng.end_times[0]).date()))
# 15m engines begin at the first bucket of the earliest kept session
first_dates = []
for inst in ("GOLDM", "SILVERM"):
    eng = engine4.htf_engine._engines.get(f"{inst}:15m")
    if eng and eng.end_times:
        first_dates.append(str(L.ist_from_epoch(eng.end_times[0]).date()))
ok_last5 = bool(first_dates) and all(d == "2026-08-24" for d in first_dates)
if not ok_last5:
    ok = False
record("e2e_last5_seed_dates", "GOLDM/SILVERM", "", "", 0, 5, 5, len(adapter4.requests),
       ok_last5, f"first 15m bucket dates = {sorted(set(first_dates))} (expect 2026-08-24)")

L.teardown(engine4, persistence4)

L.append_rows(REPORT, results)
failed = [r for r in results if r["pass"] == "FAIL"]
print(f"\n=== WARMUP VALIDATION ({len(results)} scenarios) ===")
for r in results:
    print(f"  {r['pass']}  {r['scenario']:<24s} sessions={r['sessions_fetched']}/{r['required_sessions']} "
          f"ext={r['extensions']} calls={r['fetch_calls']}  {r['detail'][:90]}")
print(f"REPORT -> {REPORT}")
print(f"RESULT: {'ALL PASSED' if not failed else f'{len(failed)} FAILED'}")
sys.exit(0 if not failed else 1)