"""PHASE-1 / PART 36 — CRASH & REGRESSION IDEMPOTENCY.

Current-code regression gates against the failure classes the remediation
scoped (dedup / crash-replay / paper-ceiling / safe-mode gating):

  G1  double delivery: same fill_id delivered twice inside one process ->
      position opened exactly once, P&L applied once
  G2  DB replay after crash: new engine (same DB) re-delivers the same fills
      -> DB-replay skip, no phantom positions, no double margin
  G3  close-then-replay idempotency: after position closed, replaying the
      entry fill still opens NOTHING (entry fill is in DB -> skip)
  G4  paper ceiling: execution_mode != paper is rejected at construction
  G5  safe mode gates new entries but allows exits (`_process_signal`)
  G6  session transition anchoring: first 5m bar of D+1 maps to D 23:30 (15m)
      and to D+1 00:00 (1h) — crash-consistent day folding

Output: REGRESSION_REPORT.csv
Exit code 0 iff all pass.
"""
from __future__ import annotations

import sys
from datetime import datetime

import _p1_lib as L
from execution.paper_broker import Fill
from strategies.types import Signal, SignalType

REPORT = L.AUDIT_DIR / "REGRESSION_REPORT.csv"
if REPORT.exists():
    REPORT.unlink()

rows = []
all_ok = True


def check(name, ok, value, expect):
    global all_ok
    all_ok &= bool(ok)
    rows.append({"check": name, "value": str(value)[:120],
                 "expect": str(expect)[:120],
                 "pass": "PASS" if ok else "FAIL"})


def mk(engine, fid, oid, side, strat="gold_01", qty=1, price=150000.0):
    return Fill(fill_id=fid, order_id=oid, instrument="GOLDM", side=side,
                quantity=qty, price=price, timestamp=datetime.now().timestamp(),
                strategy_id=strat, multiplier=10.0)


# ---- G4 paper ceiling (checked first; construction raises) ----
cfg_bad = L.write_config(L.fresh_run_root("regr_live"))
cfg_bad.write_text(cfg_bad.read_text(encoding="utf-8").replace(
    '"execution_mode": "paper"', '"execution_mode": "live"'), encoding="utf-8")
try:
    L.build_engine(cfg_bad, adapter_cls=L.CSVFeedAdapter)
    check("G4_paper_ceiling_live_rejected", False, "no raise", "RuntimeError at construction")
except RuntimeError as e:
    check("G4_paper_ceiling_live_rejected", "paper" in str(e).lower(), str(e)[:80],
          "EXECUTION_MODE must be 'paper'")

# ---- G1/G2/G3 fill dedup + crash replay ----
cfg = L.write_config(L.fresh_run_root("regr"), warmup={"last_trading_days": 5})
eng, per = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eng, L.CSVFeedAdapter())
L.wire_trade_close(eng)

f_open = mk(eng, "F_OPEN_1", "O_OPEN_1", "LONG")
eng._on_fill(f_open)
n1 = len([p for p in eng.position_manager.open_positions
          if p.instrument == "GOLDM" and p.is_open])
check("G1_entry_opened_once", n1 == 1, n1, "1 open position")

eng._on_fill(f_open)  # duplicate delivery
n2 = len([p for p in eng.position_manager.open_positions
          if p.instrument == "GOLDM" and p.is_open])
check("G1_dup_ignored_inprocess", n2 == 1, n2, "still 1 open position")

f_close = mk(eng, "F_CLOSE_1", "O_CLOSE_1", "SELL")
eng._on_fill(f_close)
n3 = len([p for p in eng.position_manager.open_positions if p.is_open])
check("G1_exit_closed_position", n3 == 0, n3, "0 open positions")

margin_used = eng.account_engines["gold_01"].used_margin
check("G1_margin_released", margin_used == 0.0, margin_used, "0.0 margin used")

# ---- CRASH: fresh engine, same DB, replay the same fills ----
eng2, per2 = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eng2, L.CSVFeedAdapter())
L.wire_trade_close(eng2)
eng2.restore(per2.load_state() or {}) if hasattr(per2, "load_state") else None

eng2._on_fill(f_open)   # entry fill already persisted -> DB replay skip
n4 = len([p for p in eng2.position_manager.open_positions if p.is_open])
check("G2_replay_entry_skipped", n4 == 0, n4, "0 positions (DB replay skip)")

eng2._on_fill(f_close)  # close fill already persisted after atomic close -> skip
n5 = len([p for p in eng2.position_manager.open_positions if p.is_open])
check("G2_replay_close_skipped", n5 == 0, n5, "0 positions (no phantom short)")

check("G2_dedup_marks_persisted",
      "F_OPEN_1" in eng2.fill_dedup._processed_fills
      and "F_CLOSE_1" in eng2.fill_dedup._processed_fills,
      sorted(eng2.fill_dedup._processed_fills), "both ids marked")

# ---- G3: new engine, replay the already-closed trade's entry fill only ----
eng3, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eng3, L.CSVFeedAdapter())
L.wire_trade_close(eng3)
eng3._on_fill(f_open)
n6 = len([p for p in eng3.position_manager.open_positions if p.is_open])
check("G3_replay_entry_of_closed_trade_skipped", n6 == 0, n6, "0 (entry fill in DB)")

# ---- G5 safe-mode gates entries, not exits ----
eng4, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eng4, L.CSVFeedAdapter())
L.wire_trade_close(eng4)
eng4.execution_engine.update_price("GOLDM", 150000.0)

strat = eng4.strategies["gold_01"]
sig_in = Signal(signal_type=SignalType.LONG, instrument="GOLDM",
                strategy_id="gold_01", timestamp=datetime.now().timestamp(),
                trigger_price=150010.0, stop_price=149800.0, quantity=1, side="LONG")
eng4.safe_mode.enter_safe_mode("test_gate")
eng4._process_signal(sig_in)
norders_blocked = len(eng4.order_manager.get_active_orders())
check("G5_entry_blocked_in_safe_mode", norders_blocked == 0
      and strat.state.value in ("flat",), norders_blocked,
      "0 orders, strategy FLAT (ghost cleared)")

# open a real LONG position first, then a safe-mode real exit must close it
eng4.safe_mode.exit_safe_mode()
eng4._on_fill(mk(eng4, "F_G5_OPEN", "O_G5_OPEN", "LONG"))
npos = len([p for p in eng4.position_manager.open_positions if p.is_open])
check("G5_precond_position_open", npos == 1, npos, "1 open position before exit")
sig_out = Signal(signal_type=SignalType.SHORT, instrument="GOLDM",
                 strategy_id="gold_01", timestamp=datetime.now().timestamp(),
                 trigger_price=150000.0, stop_price=151000.0, quantity=1, side="SHORT",
                 metadata={"exit": True})
eng4.safe_mode.enter_safe_mode("test_gate2")
eng4._process_signal(sig_out)
nleft = len([p for p in eng4.position_manager.open_positions if p.is_open])
check("G5_exit_rides_through_safe_mode", nleft == 0,
      nleft, "exit closed the position despite safe mode")

# ---- G6 session transition anchoring (crash-consistent day folding) ----
from core.timeframe_engine import Bar, BarState
from full_simulator import build_bars
cfg_w = L.write_config(L.fresh_run_root("regr_g6"), warmup={"keep_partial": True})
engW, perW = L.build_engine(cfg_w, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(engW, L.CSVFeedAdapter())
engW._warmup_from_rest()
b5 = build_bars("GOLDM", L.load_csv_rows("GOLDM", L.LAST5[0], L.LAST5[-1]))[0]
e15 = engW.htf_engine._engines["GOLDM:15m"]
e1h = engW.htf_engine._engines["GOLDM:1h"]


def isod(ts):
    return L.ist_from_epoch(ts).replace(tzinfo=None)


def hm(ts):
    return isod(ts).strftime("%Y-%m-%d %H:%M:%S")


d, nd = L.LAST5[0], L.LAST5[1]
first_fast = [b for b in b5 if isod(b.start_ts).strftime("%Y-%m-%d") == nd
              and isod(b.start_ts).strftime("%H:%M") == "09:00"][0]
bar = Bar("GOLDM", "5m", first_fast.start_ts, first_fast.end_ts,
          first_fast.open, first_fast.high, first_fast.low, first_fast.close,
          first_fast.volume, BarState.CLOSED)
m15 = engW.htf_engine._map_htf_to_fast(bar, "15m")
m1h = engW.htf_engine._map_htf_to_fast(bar, "1h")
last15_d = max(e for e in e15.end_times if isod(e).strftime("%Y-%m-%d") == d)
last1h_d = hm(max(e for e in e1h.end_times
                   if isod(e).strftime("%Y-%m-%d") == d
                   or (isod(e).strftime("%Y-%m-%d") == nd and isod(e).strftime("%H") == "00")))
okg6 = (m15.htf_confirmed and hm(m15.htf_source_timestamp) == hm(last15_d)
        and m1h.htf_confirmed and hm(m1h.htf_source_timestamp) == last1h_d
        and isod(last15_d).strftime("%H:%M") == "23:30")
check("G6_day_fold_anchor", okg6,
      f"15m->{hm(m15.htf_source_timestamp)} 1h->{hm(m1h.htf_source_timestamp)}",
      f"15m->{hm(last15_d)} (23:30) 1h->{last1h_d} (00:00 {nd})")
L.teardown(engW, perW)

L.teardown(eng, per)
L.teardown(eng2, per2)
L.teardown(eng3, None)
L.teardown(eng4, None)

L.append_rows(REPORT, rows)
print(f"\n=== CRASH / REGRESSION ({len(rows)} checks) ===")
for r in rows:
    print(f"  {r['pass']}  {r['check']:<42s} {r['value'][:80]}")
print(f"REPORT -> {REPORT}")
print(f"RESULT: {'ALL PASSED' if all_ok else 'FAILURES PRESENT'}")
sys.exit(0 if all_ok else 1)