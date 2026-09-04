# NEW_TEST_SUMMARY — Clean-Room Runtime Verification (v2)
# Generated: 2026-09-03
# Suite: tests/live_runtime_v2/
# Verdict: SYSTEM_VERIFIED

## Test Results

| Phase | File | Tests | Passed | Failed | Description |
|-------|------|-------|--------|--------|-------------|
| 1 | test_phase1_manifest.py | 5 | 5 | 0 | Architecture discovery & manifest |
| 2 | test_phase2_boot.py | 16 | 16 | 0 | Runtime boot & component init |
| 3 | test_phase3_config.py | 7 | 7 | 0 | Config validation |
| 4 | test_phase4_indicator.py | 8 | 8 | 0 | DEMA-ATR exact trace |
| 5 | test_phase5_htf.py | 6 | 6 | 0 | HTF mapping/lookahead |
| 6 | test_phase6_strategy.py | 8 | 8 | 0 | Strategy decision verification |
| 7 | test_phase7_execution.py | 9 | 9 | 0 | Execution verification |
| 8 | test_phase8_order_lifecycle.py | 7 | 7 | 0 | Order state machine |
| 9 | test_phase9_position_lifecycle.py | 9 | 9 | 0 | Position lifecycle |
| 10 | test_phase10_sl_exit.py | 7 | 7 | 0 | Stop-loss / exit logic |
| 11 | test_phase11_trade_lifecycle.py | 7 | 7 | 0 | Trade lifecycle (signal->fill->P&L) |
| 12 | test_phase12_db.py | 7 | 7 | 0 | Database write/read |
| 13 | test_phase13_crash.py | 6 | 6 | 0 | Crash / failure handling |
| 14 | test_phase14_recovery.py | 7 | 7 | 0 | Restart / recovery |
| 15-18 | test_phase15_18_isolation.py | 10 | 10 | 0 | Dedup & isolation |
| 19 | test_phase19_replay.py | 5 | 5 | 0 | 5-day replay verification |
| 20 | test_phase20_api.py | 6 | 6 | 0 | API / dashboard structure |
| 23 | test_phase23_session.py | 6 | 6 | 0 | Session boundary |
| 25 | test_phase25_paper_vs_real.py | 7 | 7 | 0 | Paper vs real enforcement |
| 26 | test_phase26_false_positive.py | 10 | 10 | 0 | False-positive detection |
| 30 | test_phase30_reconciliation.py | 8 | 8 | 0 | Final reconciliation |
| **TOTAL** | **20 files** | **162** | **162** | **0** | |

## Two-Pass Validation
- PASS 1: 162/162 passed
- PASS 2: 162/162 passed
- Both passes agree: YES

## Phase Coverage

### Core Signal Chain (Phases 1-7)
- [x] Architecture discovery & module manifest
- [x] All components initialize without error
- [x] Config has all 4 strategies, silver_01 fixed (15m)
- [x] DEMA-ATR incremental ≡ batch (4 data shapes)
- [x] HTF mapping uses bisect_right (no lookahead)
- [x] Strategy cross conditions verified independently
- [x] Tick-triggered entries, SL exits, paper execution

### Execution Lifecycle (Phases 8-14)
- [x] Order state machine: CREATED → SUBMITTED → FILLED/REJECTED
- [x] Slippage: BUY+1 tick, SELL-1 tick
- [x] Position open/close P&L correct for LONG and SHORT
- [x] Stop-loss triggers at exact price boundary
- [x] Trade P&L includes brokerage, STT, exchange fees, GST
- [x] Real SQLite DB write/read for trades, fills, orders, state
- [x] Fill dedup survives DB restart (atomic mark_processed)
- [x] Safe mode blocks trading, multiple reasons additive
- [x] PersistenceManager saves/loads JSON state
- [x] All snapshot/restore pairs verified (P&L, position, indicator, risk, market status)

### Isolation & Deduplication (Phases 15-18)
- [x] Duplicate fill dedup via two-tier (memory + SQLite)
- [x] Concurrent fill processing is safe
- [x] Out-of-order data handled correctly
- [x] GOLDM and SILVERM fully independent
- [x] Strategy state/snapshot independent

### Replay & Config (Phases 19-25)
- [x] 5-day HTF replay: no lookahead violations
- [x] Dashboard routes exist, config keys present
- [x] Session boundary flags work correctly
- [x] Paper mode enforced, no live broker paths

### False-Positive Detection (Phase 26)
- [x] Corrupted DEMA input detected
- [x] Wrong cross condition detected
- [x] Double-open position blocked
- [x] Risk limit breach → kill switch
- [x] Insufficient margin blocked
- [x] Market hours enforcement
- [x] Safe mode blocks entries
- [x] P&L corruption detected

### Final Reconciliation (Phase 30)
- [x] Independent P&L calculation matches runtime
- [x] SHORT trade P&L correct
- [x] Charges breakdown > brokerage alone
- [x] Strategy matrix matches trade count
- [x] Equity formula verified
- [x] Reversal P&L correct

## Prior Test Suite (fresh_audit/)
- 771 passed, 43 skipped, 0 failed

## Combined Results
- Total unique tests: 933 (771 + 162)
- Total passed: 933
- Total failed: 0
- Total skipped: 43 (from prior suite)
