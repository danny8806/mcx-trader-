# FINAL_5_DAY_LIFECYCLE — continuous run report

Generated 2026-08-31 05:28 UTC. Harness: _audit_5day.py (real TradingEngine, AuditReplayDataAdapter).
Result: RESULT: 73/73 invariants met. Latest run: 2460 bars / 5 distinct days /
209 fills / 209 orders / 103 trades, 1,200,000 baselines, zero crashes outside the
injected ones.

## Day script
- Day1 2026-08-24 (492 bars x 2 instruments): plain run -> overnight restart
  (day1->2 clean bounds + gap). Positions 4->4.
- Day2 2026-08-25: plain -> WS disconnect window [30%,42%] injected: data_status
  DISCONNECTED observed, reconnected -> CONNECTED at close (I11 green).
- Day3 2026-08-26: as day2 (disconnect-recovery again) — I11 dichotomy.
- Day4 2026-08-27: REST outage (first backfill fails), engine stays consistent
  (status != halted), then retry warmup rebuilds all 6 indicator keys (count 870/290/70);
  mid-day restart restores identical position set (I13 pre==post).
- Day5 2026-08-28: crash at 70%: checkpoint(40%) -> restore: positions faithful
  (cp==rec); crash-window fills exist only in fills DB (documented: 4 ids);
  replayed dup fills IGNORED, position stays open (I15); no dup rows; fills/trades survive.

## Reconciliation across the whole window
ReconciliationReport live: Consistent=True, 0 errors, 0 warnings every day.
sys.settrace-safe; no exceptions surfaced during 5-day replay.
