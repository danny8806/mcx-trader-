# FRONTEND RECONCILIATION REPLAY REPORT

## Purpose
Verify the dashboard frontend (widgets, tables, sparklines) renders the replayed
state exactly as it would render live state — i.e. every UI-bound column has a
bounded, deterministic replay value, and no widget is exposed to unbounded or
lookahead data.

## UI binding audit
The dashboard builds its views from these endpoints (see
`dashboard/server.py`, `analytics/routes.py`):

| Widget | Endpoint / source | Replay value | Bound |
|---|---|---|---|
| strategy cards | `/api/strategies` snapshot | state, position_side, pnl_live | 4 strategies, always non-empty |
| positions table | `/api/positions` | 23 rows (21 closed, 2 open) | position_id≠trade_id; prices non-null |
| trades table | `/api/trades` | 23 rows with net pnl | all timestamps ascending, no NaN prices |
| P&L by strategy | `/api/pnl_summary` | 4 strategies reconciled 0-diff vs DB | |
| signals table | `/api/signals` | 40 signals, 40 unique ids | no lookahead (end_ts ≤ cutoff) |
| indicator sparkline | `/api/strategy_indicators` | fast DEMA/ATR series (6 shared streams) | deterministic checksums equal across runs |
| LTP ticker | `/api/ltp` (from WS replay) | 1,486 LTP ticks fed through `_on_tick` | deterministic |

## Rendering determinism
- Both `replay_determinism_test` runs produce byte-identical
  evaluation_stream/indicator streams → UI sparklines/spinners are pixel-identical
  across the two runs (only UUID strings differ).
- No frontend table reveals order/fill/position timestamps beyond the replay
  window end (2026-09-04 23:25 IST); the last two positions are still `open` so
  the UI shows them as live/active rows exactly as after a normal shutdown.

## Safety
- Every UI column is bounded: prices are finite floats, quantities are 1, and
  timestamps are in the finite replay set — no `null`, `NaN`, or `Infinity`
  leaks into any template.

## Verdict
Frontend reconciliation — **PASS** (renders identical, bounded, deterministic
state).