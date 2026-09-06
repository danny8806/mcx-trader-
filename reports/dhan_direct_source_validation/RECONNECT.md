# Reconnect — RECONNECT.md

## Phase result (runtime_v3): PASS
```
ws_reconnected = true
attempts_observed = 2
on_status_events = ["closed","disconnected","connected"]
```

## Stats before → after
| metric | before | after |
|---|---|---|
| recv | 4 | 8 |
| parse_err | 0 | 0 |
| sub | 1 | 2 (resubscribed both instruments) |
| tick | 2 | 4 (one snapshot tick per symbol per connect) |

## Boundedness
- reconnect delay 10 s, backoff capped at 30 s, watchdog stale threshold 60 s, token reload per attempt.
- No duplicate subscription frames, no duplicate candles created (parse_err=0; bus counts: candle GOLDM/SILVERM 0 — closed market).

## REST fault handling (rest_fault_test: PASS)
Injected bounded REST failure during reconciliation → engine stayed alive, gracefully returned `[]` (adapter `reconcile_candles` tolerates `DhanAuthError` by design), no crash, no retry storm.

## User position
Trade integrity is the reconciliation target; transient feed/REST faults are contained by bounded reconnect + idle-safe operation.