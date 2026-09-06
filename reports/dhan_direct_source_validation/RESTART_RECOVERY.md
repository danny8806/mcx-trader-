# Restart Recovery — RESTART_RECOVERY.md

## Phase result (runtime_v3)
`restart_test` = **CHECK FAILED/UNSURE** (documented, environment-unsatisfiable; NOT counted as a failure → verdict unaffected).

| check | result |
|---|---|
| strategy_ids_match | true (gold_01, gold_02, silver_01, silver_02) |
| engine_ready | true (READY) |
| positions restored | true (position_manager snapshot from canonical DB) |
| snapshot_account_present | true |
| `bars_processed > 0` gate | **false — all 0** (open-market-only) |

## Why it cannot be satisfied in a closed market
The restart gate requires at least one live candle event after warm start (`bars_processed > 0`, `closed_market_runtime_test.py:1181-1183`). Closed market ⇒ zero candle events ⇒ structurally impossible. In an open market this gate is the intended proof that post-restart live evaluation resumed.

## Evidence of actual recovery on a second cold start (from the run itself)
During `restart_test` a fresh engine was built on the SAME canonical DB: warmup completed to READY, WS re-subscribed both instruments, snapshot ticks arrived (GOLDM 152950.0 / SILVERM 239495.0), lifecycle restored 3 trade rows from DB. The restart data path (DB restore → warmup → WS) demonstrably works; only the open-market live-bar gate is unsatisfiable here.

## User position
Trade/data integrity is the reconciliation target; indicator state always re-warms on restart (warmup is deterministic from REST). Consistent with the documented warmup-window fix (see INDICATOR_FORENSIC).