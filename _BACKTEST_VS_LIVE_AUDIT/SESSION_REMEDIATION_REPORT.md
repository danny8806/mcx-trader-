# SESSION REMEDIATION REPORT — Part 2 / Part 44-48

**Scope:** `C:\Users\pc\Desktop\MCX-TRADER` (Phase-1 remediation of the verified
warmup / trading-session-history gap). No strategy, indicator, backtest,
mapping, or 09:00-anchor logic was rewritten. No real Dhan order placement,
Docker, or deployment was performed.

## 1. Verified defect (root cause)

On startup the engine seeded indicators from a **fixed** REST window
(`now - fetch_calendar_days`). When that window straddled weekends/holidays it
contained **fewer than the backtest LAST5 trading dates**, so the live DEMA-ATR
line was seeded from a different, shorter history than the reference backtest —
a cold-start divergence between live and backtest on identical market data.

Two secondary hazards were also present in the warmup resampler:

1. A **next-day pre-session print** (e.g. a `00:10` 5m candle dated the
   following morning) could form/merge into a bucket, injecting a cross-session
   ohlcv into a 15m/1h bucket.
2. Re-invoking `_warmup_from_rest` (double start) could **double-feed** the
   indicator/HTF state because neither was reset before warming.

## 2. Remediation (current code)

| # | Change | Location |
|---|--------|----------|
| 1 | **Guaranteed session window** — `_fetch_history_with_session_guarantee` extends the REST window backward in `fetch_extend_step_days` (7d) steps up to `max_fetch_calendar_days` (62) until `>= last_trading_days` distinct trading dates are present; warns at the cap | `trading_engine.py:1338` |
| 2 | **Authoritative re-warm** — `_warmup_from_rest` now resets every `:5m/:15m/:1h` indicator **and** the HTF instrument state before warming, then consumes the fresh REST series only (never a restored/stale DB series) | `trading_engine.py:1381` |
| 3 | **Pre-session hardening** — `d = d[mins >= 0]` after bucket assignment: pre-session rows can never form/merge a bucket | `trading_engine.py:1475` |
| 4 | **Keep-all alignment** — `keep_partial: true` keeps the trailing 23:00 1H bucket so the live line equals the backtest KEEP-ALL reference (DEMA from bar 0, `min_periods=1`) | `config/settings.json` (warmup + per-instrument) |

**Intentional differences preserved (documented, not changed):** backtest
crossing-bar-open fill vs live trigger-fill; bar-SL + tick-SL additivity;
EOD force-close disabled. Behavior contract intact: `snapshot()/restore()`
never persist candle-derived state (recomputed from fresh REST at startup),
verified by R3.

## 3. Verification evidence (all files in `_BACKTEST_VS_LIVE_AUDIT\`)

| Gate | Deliverable | Result |
|------|-------------|--------|
| Warmup session guarantee (6 scenarios: holiday cluster, clean window, cap warn, 2× e2e, seed dates) | `WARMUP_VALIDATION_REPORT.csv` | ALL PASSED |
| Resampler (11 scenarios: counts, boundary, cross-midnight, dedup, missing, shuffled, warmup parity incl. partials) | `RESAMPLING_VALIDATION_REPORT.csv` | ALL PASSED |
| Indicator parity (GOLDM+SILVERM × 15m/1h; DEMA mismatch 0, ATR exact, 734 rows) | `INDICATOR_PARITY_REPORT.csv` | ALL PASSED |
| Mapping parity (boundary points + 870 real 5m bars ↔ reference, both TFs, no-lookahead) | `MAPPING_PARITY_REPORT.csv` | ALL PASSED |
| Five-day session continuity (LAST5 session set, per-day 174/58/15, 09:00 anchor / 23:30 close, day-folding 15m→23:30 & 1h→00:00, no reseed) | `FIVE_DAY_CONTINUITY_REPORT.csv` | ALL PASSED |
| Restart/determinism (two-warm identical, partial→full recompute parity, snapshot/restore round-trip, double-warm idempotent) | `RESTART_PARITY_REPORT.csv` | ALL PASSED |
| Crash/regression (double-delivery dedup, DB replay skip, close-then-replay, paper ceiling, safe-mode entry/exit, day-fold anchor) | `REGRESSION_REPORT.csv` | ALL PASSED |
| Full lifecycle per instrument — warmup→870-bar stream→order→fill→trade→reconciliation→PNL-in-ledger (GOLDM, SILVERM) | `GOLD_VALIDATION_REPORT.csv`, `SILVER_VALIDATION_REPORT.csv` | ALL PASSED |
| One-command aggregate re-run (Pass-2 fresh runs, subprocess-isolated) | `PHASE1_RUN_SUMMARY.csv` | all gates re-passed |

## 4. Part 46 — resampler audit (repo-wide)

All additional resamplers in the tree use the **same session-anchored
bucketing math** (`session_open` + `(minutes_since_open // tf_min) * tf_min`)
as the live path, with **complete-window-only** filtering where they are
audit/scoring tools:

- `verify_live_signals.py:85-137` — reference resample (complete-window
  `== tf_minutes//5`) for signal verification; unaffected by the live
  keep-partial path.
- `_step2_15m_check.py`, `_step3_1h_check.py`, `_step4_dema_check.py`,
  `_step5_htf_check.py`, `_step6_warmup_alignment_check.py` — prior-phase audit
  helpers, read-only, do not feed the engine.
- Conclusion: the `trading_engine.py:1475` guard changes **only** the warmup's
  keep-partial path (pre-session row suppression); complete-window and
  drop-partial behavior is untouched anywhere, so no secondary resampler
  diverges from the reference.

## 5. Outcome

Every Phase-1 gate passes on the current code with independent reference math.
Reproducibility: `python _p1_run_all.py` → `PHASE1_RUN_SUMMARY.csv` (7/7
passed). See `PHASE1_RESULT_MATRIX.md` and `PHASE1_ACCEPTANCE.md` for the
consolidated matrix and acceptance decision.