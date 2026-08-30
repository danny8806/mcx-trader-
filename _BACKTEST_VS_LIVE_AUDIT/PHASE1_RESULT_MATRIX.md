# PHASE-1 RESULT MATRIX — Part 51

Consolidated PASS/FAIL matrix for the Phase-1 remediation gates. Every row is
backed by a current-code execution (`python _p1_run_all.py`, subprocess-isolated
fresh runs) and independent reference math (`_p1_lib.py`).

| # | Part | Gate / deliverable | Evidence file | Result |
|---|------|--------------------|---------------|--------|
| 1 | 15-22 | Warmup session-guarantee (6 scenarios: holiday cluster→extend, clean window→no extend, cap warn, e2e cold-start GOLDM+SILVERM, seed dates) | `WARMUP_VALIDATION_REPORT.csv` | **PASS** |
| 2 | 23-33 | Resampler validation (11 scenarios: bucket counts, 22:00-23:15 boundary drop/keep, cross-midnight purge, interleaved purity, duplicate-candle dedup, missing-candle window drop, shuffle invariance, warmup parity 15m/1h incl. trailing partials) | `RESAMPLING_VALIDATION_REPORT.csv` | **PASS** |
| 3 | 34-43 | Indicator parity — DEMA/ATR vs independent reference (GOLDM+SILVERM × 15m/1h; 734 rows; DEMA mismatch=0; ATR max diff 3.4e-13; incremental==batch) | `INDICATOR_PARITY_REPORT.csv` | **PASS** |
| 4 | 34-43 | Mapping parity — HTF→fast mapping vs reference (boundary points + all 870 real 5m bars, both TFs, bad=0; future-mutation no-lookahead) | `MAPPING_PARITY_REPORT.csv` | **PASS** |
| 5 | Part 34 | Five-day session continuity (LAST5 session set; per-day counts 174/58/15 incl. trailing 23:00 partial; 09:00 anchor/23:30 close; day-fold 15m→prev 23:30 & 1h→next 00:00 with value continuity; one-pass no-reseed 290/75) | `FIVE_DAY_CONTINUITY_REPORT.csv` | **PASS** |
| 6 | Part 35 | Restart & determinism (two-warm identical; partial-history warmup < full ≠ full; restart recompute == uninterrupted; snapshot/restore live-state round-trip with candle state left pristine by design; double-warm idempotent) | `RESTART_PARITY_REPORT.csv` | **PASS** |
| 7 | Part 36 | Crash/regression (fill double-delivery dedup; DB-replay skip post-crash entry+close; close-then-replay idempotency; paper-ceiling raises on live mode; safe-mode blocks entry / rides exits; day-fold anchor) | `REGRESSION_REPORT.csv` | **PASS** |
| 8 | Part 37 | Full lifecycle per instrument (warmup → 870-bar stream → order → fill → trade → reconciliation → P&L-in-ledger; orphan-fill=0; margin consistent; safe-mode clean) — GOLDM and SILVERM | `GOLDM_VALIDATION_REPORT.csv`, `SILVERM_VALIDATION_REPORT.csv` | **PASS** |
| 9 | Part 44-48 | Remediation summary (root cause, 4 changes, evidence, repo-wide resampler audit) | `SESSION_REMEDIATION_REPORT.md` | **PASS** |
| 10 | Part 49 | Change inventory (17 asset rows: trading_engine.py:1338/1381/1475, settings.json warmup+keep_partial, 12 new _p1_* harnesses/lib updates) | `CHANGED_FILES.csv` | **PASS** |
| 11 | Part 50 | Aggregate re-run (Pass-3 fresh, subprocess-isolated) | `PHASE1_RUN_SUMMARY.csv` (8/8) | **PASS** |
| 12 | Part 46 | Repo-wide resampler audit — all other resamplers use identical session-anchored buckets; complete-window audit tools unaffected; only warmup keep-partial path changed | §4 of `SESSION_REMEDIATION_REPORT.md` | **PASS** |
| 13 | Part 53 | ALL-4-LIVE-STRATEGY fresh re-verification — value-level, every fast bar (M1: 15m/1h bucket OHLCV incl. keep-all 1h; M2: raw 5m identity; M3: 5m/15m/1h DEMA-ATR lines; M4: htf/mid/fast mapped values per bar for gold_01/gold_02/silver_02/silver_01; 2,300 fast-bar mappings, 0 midmatch; 363 in-window signals as a side check) | `FOUR_STRATEGY_HTF_MAPPING_PARITY.csv`, `FOUR_STRATEGY_HTF_MAPPING_SUMMARY.md` | **PASS** |
| 14 | Part 54 | ALL-4-LIVE-STRATEGY crossing-signal-candle fresh re-verification (S1-S5): every crossing candle + every DEMA-ATR value + prev value + mapped source timestamp on it, compared live engine vs independent backtest tracker vs batch reference — gold_01 22, gold_02 16, silver_01 17, silver_02 24 candles; sets identical, 0 mismatches; strategy action per candle consistent | `FOUR_STRATEGY_SIGNAL_CANDLE_PARITY.csv`, `FOUR_STRATEGY_SIGNAL_CANDLE_SUMMARY.md` | **PASS** |
| 15 | Phase 0 | Baseline intact (architecture report/components, data-source map; 52-component / 14-row inventories unchanged) | `CURRENT_ARCHITECTURE_REPORT.md`, `CURRENT_ARCHITECTURE_COMPONENTS.csv`, `DATA_SOURCE_MAP.csv` | **PASS** |

**Total: 15/15 gates PASS, 0 FAIL, 0 WARN required.**

Phase-0 parity deliverables regenerated before this phase remain green
(`DATA_PARITY_REPORT.csv`, `SIGNAL_PARITY_REPORT.csv`,
`TRADE_PARITY_REPORT.csv`, `FINANCIAL_PARITY_REPORT.csv`,
`FINAL_PARITY_GAP_REPORT.md`, `FINAL_5DAY_LIVE_REPLAY_REPORT.md`).

**Out of scope (documented, not changed):** backtest crossing-bar-open fill vs
live trigger fill; bar-SL + tick-SL additivity; EOD force-close disabled;
real Dhan placement; Docker/deploy execution.