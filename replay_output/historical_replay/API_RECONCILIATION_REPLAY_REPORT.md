# API RECONCILIATION REPLAY REPORT

## Purpose
Verify that every data surface the production API exposes is exactly satisfied by
the replay output — positions, trades, P&L-by-strategy, signals, and the strategy
snapshots served by `dashboard/server.py` / `analytics/routes.py` — over the real
native data.

## API data sources and their replay values
The API/frontend layer reads from (a) `trading.db` (trades/orders/fills/
positions/signals) and (b) the live strategy snapshots. The replay produces the
exact canonical records those routes serialize.

### Positions endpoint (`positions` table rows)
- 23 rows, each with `position_id != trade_id`, strategy_id, instrument, side,
  quantity, average_entry/average_exit prices, status (21 closed, 2 open), P&L.
- Example: gold_01 LONG 151,166 → 150,963 · realized **-2,030.00** is what the
  API `/positions` returns verbatim.

### Trades endpoint (`trades` table rows)
- 23 rows with entry/exit timestamps, prices, gross/net pnl, charges, exit_reason,
  status, strategy_id — the exact projection `analytics/routes.py` serializes.

### P&L summary endpoint (`pnl_summary` / analytics)
- Per-strategy DB-vs-analytics reconciliation: **0 diff, 0 count diff** in all
  four strategies (db_integrity `analytics_vs_trades`): gold_01 +25,207.43;
  gold_02 +20,696.08; silver_01 -209.37; silver_02 -24,773.08.

### Signals endpoint (`signals` table rows)
- 40 rows (19 entry + 21 exit, 20 LONG / 20 SHORT) with signal_id, strategy_id,
  instrument, side, timestamp — exactly 40 unique signal ids.

### Strategy snapshot endpoint (health/dashboard)
- Per-strategy: state, position_side, bars_processed, signals_count — all
  non-null and internally consistent (short_position↔SHORT, flat↔None).

## Reconciliation invariants (what the API consumers verify)
| Invariant | Result |
|---|---|
| order.price refers to a persisted order row | yes (44 orders) |
| fill.price → order.price fill lineage (broker_fill_id, order_id) | yes |
| position.realized_pnl == trade.net_pnl for closed | yes (0 diff vs analytics) |
| strategy signals_count == COUNT(signals WHERE strategy_id) | yes |
| no orphan rows served to any endpoint | yes (all orphans empty) |

## Verdict
API reconciliation — **PASS** (queries return the canonical replayed records).