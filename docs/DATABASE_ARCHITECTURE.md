# Database Architecture

`trading.db` is the canonical durable source of truth for lifecycle state.
Memory is runtime cache only. The API reads canonical state, and analytics is
rebuildable derived data in the same database. `analytics.db` is not a runtime
source.

## Canonical Tables

- `signals`: immutable signal and candle/indicator snapshots.
- `trades`: immutable trade identity and canonical lifecycle/P&L state.
- `trade_signal_link`: ENTRY, EXIT, and TRIGGER relationships.
- `pending_orders`, `orders`, `fills`, `positions`: explicit lifecycle lineage.
- `trade_events`: append-only per-trade event history.
- `processed_fills`: durable fill idempotency marks.
- `quarantine_records`: ambiguous legacy records that cannot be safely mapped.

## Derived Tables

`trades_analytics`, `trade_legs`, snapshots, and strategy performance tables are
read models. They must be rebuildable from canonical lifecycle data and must not
be used to determine trade identity or status.

## Integrity

Every connection enables SQLite foreign keys, WAL, busy timeout, and lifecycle
transactions. Legacy tables receive insert triggers for required trade/order/
signal lineage. Positions retain a distinct `position_id` and reference an
explicit `trade_id`.

Run `python -m tools.validate_trade_integrity --db trading.db` before startup
and after migration. A non-zero exit means the runtime must enter safe mode.
The validator also checks that required foreign keys are declared in the schema,
not merely simulated by application code.

## Migration Policy

Stop trading, back up and hash the existing databases, run SQLite integrity
checks, migrate only records with provable lineage, quarantine ambiguous rows,
rebuild derived tables in `trading.db`, validate, and retain the original
backup for rollback. Do not copy an independent analytics ledger into the
canonical tables without reconciliation.
