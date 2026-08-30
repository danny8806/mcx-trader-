# PHASE-1 ACCEPTANCE — Part 52

Decision authority: the consolidated Phase-1 evidence generated in this run.
Acceptance evaluates only what is verifiable offline; deploy-time steps
(Docker build, container runtime, live-account checks) are recorded as
NOT-EXECUTED-OFFLINE and are gated on the offline result below.

## Acceptance Gates

| Gate | Criterion | Evidence | Result |
|------|-----------|----------|--------|
| A1 | Warmup window contains ≥5 real trading dates even under weekend/holiday clusters; caps safely, warns at cap | `_p1_warmup_test.py` 6/6; `WARMUP_VALIDATION_REPORT.csv` | **PASS** |
| A2 | Live warmed 15m/1h bucket sets are byte-identical to the backtest aggregation incl. trailing partials; no cross-midnight contamination | `RESAMPLING_VALIDATION_REPORT.csv` (T3/T4/T6a/T6b/T7); `FIVE_DAY_CONTINUITY_REPORT.csv` | **PASS** |
| A3 | DEMA-ATR line == independent reference within 1e-6 on the last bar; 0 mismatches over 734 (290/75 × 2 instruments) rows | `INDICATOR_PARITY_REPORT.csv` | **PASS** |
| A4 | HTF→fast mapping == reference for all 870 real 5m bars, both timeframes; no-lookahead holds | `MAPPING_PARITY_REPORT.csv` | **PASS** |
| A5 | Five-day stream == backtest LAST5 seed per-day (174/58/15) with correct anchors and day-folding; no reseed mid-week | `FIVE_DAY_CONTINUITY_REPORT.csv` | **PASS** |
| A6 | Restart determinism: same code+data ⇒ identical state; partial-history restart recomputes to the uninterrupted result; snapshot/restore round-trips live state and never resurrects stale candle state | `RESTART_PARITY_REPORT.csv` (R1-R4) | **PASS** |
| A7 | Crash idempotency: duplicate fills ignored in-process and after crash-replay; no phantom positions; no orphan fills; close-then-replay is a no-op | `REGRESSION_REPORT.csv` (G1-G4) | **PASS** |
| A8 | Safety gates: entries blocked in safe mode; exits ride through; paper-mode ceiling enforced at construction | `REGRESSION_REPORT.csv` (G5, G4) | **PASS** |
| A9 | Full pipeline end-to-end per instrument: warmup→stream→orders→fills→persisted trades→reconciliation→P&L ledger; margin consistent; safe-mode clean | `GOLDM_VALIDATION_REPORT.csv`, `SILVERM_VALIDATION_REPORT.csv` (V1-V9) | **PASS** |
| A10 | Reproducibility: one command re-runs all 8 gates on fresh isolated runs | `_p1_run_all.py` → `PHASE1_RUN_SUMMARY.csv` (8/8) | **PASS** |
| A10b | ALL-4-LIVE-STRATEGY fresh re-verification, value-level at every fast bar (raw 5m identity, 15m/1h bucket OHLCV incl. keep-all 1h, 5m/15m/1h DEMA-ATR lines, and the exact htf/mid/fast mapped values consumed by the four strategies; 2,300 fast-bar mappings, 0 midmatch) — no earlier results trusted | `_p1_map4_test.py` → `FOUR_STRATEGY_HTF_MAPPING_SUMMARY.md` | **PASS** |
| A10c | ALL-4-LIVE-STRATEGY crossing-signal-candle re-verification (three-way: live engine vs independent backtest tracker vs batch reference) — every crossing candle, side, and every DEMA-ATR value + prev value + mapped source timestamp identical; 79 candles, 0 mismatches; strategy per-candle action consistent | `_p1_signal4_test.py` → `FOUR_STRATEGY_SIGNAL_CANDLE_SUMMARY.md` | **PASS** |
| A11 | Change scope discipline: only warmup-fetch + warmup-resample code changed (trading_engine.py:1338/1381/1475) + warmup config; no strategy/indicator/backtest/mapping/09:00-anchor rewrites | `CHANGED_FILES.csv`, git diff scope | **PASS** |
| A12 | Deploy steps (Docker build, container health, live-account sync, WS reconnect) | not executed offline | **NOT-EXECUTED-OFFLINE** |

## Final Decision

**STATUS: `READY_FOR_DOCKER`**

Rationale: every offline-verifiable Phase-1 gate (A1–A11, A10b, A10c, 15/15 matrix rows)
passes on the current code with independent reference math; the documented
intentional live-vs-backtest differences remain unchanged; and the system is
paper-only by hard ceiling (A8). The single execute-only-later item is A12,
which is a run/deploy activity, not a code-readiness gate.