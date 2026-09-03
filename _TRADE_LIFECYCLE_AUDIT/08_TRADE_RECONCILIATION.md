# 08 — TRADE RECONCILIATION

Master Prompt report #08.

One consistent trade set across trading.db, analytics.db, position manager, and
the dashboards.

## Closed trades — reconciled across all sources
| strategy | trading.db net | analytics.db net | /api/strategies realized_net | /api/pnl realized | equity-graph net (after fix) |
|---|---|---|---|---|---|
| gold_01 | -803.97 | -803.97 | -803.97 | - | -803.97 |
| gold_02 | -1634.00 | -1634.00 | -1634.00 | - | -1634.00 |
| TOTAL | -2437.97 | -2437.97 | -2437.97 | -2437.97 | -2437.97 |

Independent recompute from analytics.db closed trades (gross = (exit-entry)*mult*qty;
net = gross + fees) reproduces stored values exactly:
- gold_01 gross -510.0 + fees 293.97 = -803.97 ✔
- gold_02 gross -1340.0 + fees 294.00 = -1634.00 ✔

## Open trades — reconciled
| strategy | position_manager | trading.db | analytics.db | /api/positions |
|---|---|---|---|---|
| silver_01 | open LONG (9983dd92) | - | OPEN row + entry leg | open LONG |
| silver_02 | open LONG (baa04bef) | - | OPEN row + entry leg | open LONG |

Trade_id == position_id in analytics (position-anchored). Each open trade has its
entry leg and a POSITION_OPENED event (backfill — BUG-1 fix).

## Orders / fills
- trading.db orders(6) / fills(6) / processed_fills(6) are consistent with the
  2 entries + 2 exits (prices 150768, 150851, 150717).
- /api/orders and /api/fills are served from the execution engine (in-memory),
  not trading.db. Not a row-parity source; documented (no /api/author/* routes exist).

## State (after BUG-A fix) — reconciled
- /api/strategies state: silver_01/silver_02 = long_position/LONG (was FLAT).
- Matches /api/positions (2 open LONG) and overview open_positions_count=2.
- WS engine_state identical to HTTP.

## Verdict
All trades reconcile across SOURCE -> trading.db -> analytics.db -> API -> frontend.
No stale, duplicate, or missing trade/leg records found.