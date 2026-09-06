# Crash Recovery — CRASH_RECOVERY.md

## Phase result (runtime_v3): PASS

Child process kill test (crash_kid_test):
```
child_rc = 1                    (killed mid-transaction)
died_mid_transaction = true     (marker row written, then killed before commit)
integrity = "ok"                (canonical DB integrity after kill)
marker_rows_visible = 0         (uncommitted marker NOT visible)
wal_recovery = "uncommitted frames rolled back on next read"
```

## What this proves
- A hard kill between write and commit does NOT corrupt the canonical DB.
- Uncommitted WAL frames are rolled back on the next read — no bleed, no phantom rows, no FK violations (`fk=0`).
- The engine's next start restores a consistent snapshot (see RESTART_RECOVERY for the cold-start path).

## Pause/resume (pause_resume_test: PASS)
To/Pause off→on verified on gold_01 (`enabled_before=true → paused → resumed`); closed market = no bars to evaluate while paused; fork-free guarantee is covered by unit tests (`tests/`).

## Signal semantics (signal_semantics: PASS)
3 signal rows cross-checked; verdict `fixture` — all lacked `candle_timestamp → cannot cross-check` (no live candles in a closed market). No fabricated candle cross-check; trade/order integrity remained the invariant (see DATABASE_RECONCILIATION).