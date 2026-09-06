# DATABASE LINEAGE REPLAY REPORT — trading.db Integrity

## Purpose
Prove the isolated replay `trading.db` (and analytics.db) is a faithful,
lineage-clean reflection of the replayed execution: no orphans, no duplicate ids,
no cross-strategy contamination, no violations of SL invariants, exact
analytics↔trades reconciliation, and all entities referenced by trade_id.

## Source of the check
`tools/replay_live_architecture.py:db_forensics()` over the replay's isolated
`data/db/trading.db` → `replay_output/historical_replay/single_run/db_integrity.json`.

## Results
| Check | Result |
|---|---|
| `PRAGMA foreign_keys` | **1 (ON)** |
| duplicate signal_id / trade_id / order_id / fill_id / position_id | **none** |
| duplicate trade events (idempotency) | none (109 distinct event rows, no dup keys) |
| trades without entry signal (`trade_signal_link`) | **0** |
| `position_id == trade_id` collisions | **0** |
| orphans (pending_orders, orders, fills, positions, legs, links, events, snapshots, fills_without_order) | **all empty** |
| unknown broker orders | **0** |
| cross-strategy contamination (order/fill/position vs trade) | **all empty** |
| SL invariant violations | **0** |
| reversal exits (with exit signal id) | 7 (recorded) |
| processed fill dups | none |
| analytics vs trades (per strategy) | **0 diff count, 0 diff P&L** |

## Lineage rule compliance (mandated identity rules)
- All of signal_id / trade_id / order_id / fill_id / position_id are **distinct
  UUIDs** (verified: 40/23/44/44/23 unique).
- `position_id != trade_id` for all positions (0 collisions).
- Every order/fill/position references its `trade_id`; every fill links its
  `order_id` and `position_id`; every trade links its `entry_signal_id`.

## Table counts
signals=40 · trades=23 · orders=44 · fills=44 · positions=23 · events=150 ·
trade_events=109 · quarantine_records=0 · pending_orders=0
(end-of-replay has no pending orders; 2 open positions are live rows).

## Reconciliation
- `analytics_vs_trades`: each per-strategy DB trade count and P&L equals the
  analytics summary exactly (gold_01 +25,207.43, gold_02 +20,696.08,
  silver_01 -209.37, silver_02 -24,773.08).

## Determinism link
The DB-backed lineage data is identical across two isolated runs (normalized
trade/order/fill/position checksums match) — see determinism_report.json.

## Verdict
Database lineage replay — **PASS** (clean, complete, exact reconciliation).