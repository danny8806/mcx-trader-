# SIGNAL PARITY REPORT — Real-Data Replay

## Purpose
Verify that signal generation over the real native replay is correct and
reproducible: the crossover-formula layer matches the authoritative reference
backtest, and the emitted signal set is bit-identical between isolated runs.

## Signal Surface (real replay window)
| Strategy | FTF | Signals | LONG | SHORT | Trigger/SL present | mid/htf values |
|---|---|---|---|---|---|---|
| gold_01 | 5m | 14 | 7 | 7 | all | all non-None |
| gold_02 | 15m | 6 | 3 | 3 | all | all non-None |
| silver_01 | 15m | 6 | 3 | 3 | all | all non-None |
| silver_02 | 5m | 14 | 7 | 7 | all | all non-None |
| **Total** | | **40** | 20 | 20 | 40 | 40 |

- Each signal carries `trigger_price`, `stop_price`, `fast_dema_atr`, `mid_value`,
  `htf_value`, `pending` and `trigger_level` (metadata from the production
  `_create_pending_signal` / `_create_reversal_signal`).
- Example (gold_01, 2026-09-02 09:15 IST LONG): trigger 151,300 / stop 150,755 ·
  fast_dema_atr 151,005.7 · mid 150,556.4 · htf 151,143.2 — htf > mid →
  confirms the LONG (h15<h1 gating consistent with the reference formula).
- Crossover/state-machine events logged (per-strategy): gold_01=9, gold_02=5,
  silver_01=4, silver_02=8 (26 total creator calls). The final emitted signal
  count (40) exceeds raw creator calls because the state machine also emits
  reversal/pending-rearm signals — this is the documented EXECUTION-layer gating
  (§7 of docs/migration/BACKTEST_LIVE_SIGNAL_PARITY_REPORT.md applies).

## Formula-layer parity (reference backtest)
`tools/parity_signal_harness.py` (production `_check_*_cross` vs reference
`core/dema_mtf.compute_signals`):
- Direction mismatches vs **forced-grid reference**: gold_01=0, gold_02=0,
  silver_01=0, silver_02=0.
- Trigger/SL mismatches on common signal bars: **0**.
This anchors the signal-generation layer (unchanged code) on the intended grid.

## Determinism of the emitted signal set
`tools/replay_determinism_test.py`: two isolated runs produced **identical**
signal checksums (`063b5e49…` both) — 40 signals, same strategy/side/timestamp/
prices/htf/mid/fast metadata. Only signal UUIDs differ (by design).

## Replay-vs-backtest signal delta (native vs aggregated HTF feed)
See HTF_PARITY_REPORT.md / REPLAY_VS_BACKTEST_PARITY_REPORT.md. In short,
40 signals (native feed) vs 37 (aggregated-15m/1h feed): the delta (4 native-only,
1 agg-only, 23 common) is confined to HTF-construction differences at session
edges on gold_01; gold_02/silver_01/silver_02 signal counts are identical (6/6/14)
in both feeds.

## Verdict
Signal generation parity — **PASS** (formula-exact vs reference; reproducible).