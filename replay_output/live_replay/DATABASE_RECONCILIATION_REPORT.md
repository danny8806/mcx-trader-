# DATABASE_RECONCILIATION

Phase 26/27/28/60/61/69 — Canonical trading.db schema, lineage, integrity, analytics mirror, and production dirty-DB scan.

## Evidence files

- `parallel/db_integrity.json` — full schema + integrity + orphan + cross-strategy + SL + idempotency + analytics-vs-trades forensic of the isolated replay DB
- `forensics/phase61_dirty_db_scan.json` (payload copied into `replay_output/live_replay/forensics/`)

## 1. Schema (canonical tables)

`signals, trades, pending_orders, orders, fills, positions, events, trade_events,
trade_legs, trade_signal_link, trade_snapshots, processed_fills,
broker_order_mapping, quarantine_records, account_snapshots, strategy_daily_performance,
strategy_monthly_performance, strategy_parameter_results, strategy_performance_snapshots,
system_metadata, trades_analytics`.

- Replay DB: `PRAGMA foreign_keys = ON`.
- Isolated replay row counts: signals 49, trades 31, orders 59, fills 59, positions 31, events 198, trade_events 146, quarantine_records 0.

## 2. Integrity (measured, replay DB)

- Duplicate ids: trades/orders/fills/positions/pending_orders/broker_mappings → **0 each**.
- Orphans on all 8 trade-linked tables → **0**; fills-without-order → **0**; unknown broker orders → **0**.
- Trades without entry signal → **0**; SL-with-signal violations → **0**; position_id==trade_id conflicts → **0**; processed_fill duplicates → **0**.
- Cross-strategy contamination on all 4 lineage edges → **0**.
- trade_events: 146 rows, all with NULL `idempotency_key` (schema default-key usage; no duplicate set-key rows).

## 3. Analytics mirror (Phase 70)

`trades_analytics` reconciles to canonical `trades` **exactly**: per strategy
trades count and net PnL identical, `diff_count = 0`, `diff_pnl = 0` for all four
strategies (gold_01 9/-1459.64, gold_02 7/-1230.25, silver_01 6/-4493.93,
silver_02 9/-5358.09).

## 4. Production "dirty" DB scan (read-only copies, Phase 61)

- `data/db/trading.db` (docker volume, live): 3 signals / 3 trades (1 GOLDM CLOSED + GOLDM OPEN + SILVERM OPEN) / 4 orders / 4 fills / 3 positions.
- `trading.db` (repo root, reference-tool mirror): same 3 trades / 4 orders / 4 fills; identical lineage (lacks only `broker_order_mapping` table).
- Invariant checks on both copies: trades-without-entry-signal **0**, duplicate trade ids **0**, orphan orders/fills/positions **0**, cross-strategy **0**, SL violations **0**. Status of both: **CLEAN** on the mission invariants.
- Note: raw sqlite connections report `PRAGMA foreign_keys = 0` for these copies (the engine does not rely on DB-enforced FKs; integrity is enforced at the application lineage layer and verified above).

## Status

**PASS** — isolated replay DB is logically clean; analytics mirror reconciles exactly; both production DB copies are invariant-clean.