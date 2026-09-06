# Database Reconciliation — DATABASE_RECONCILIATION.md

## Canonical DB = `data/db/trading.db` (single writer, explicit IDs)

DB_BEFORE → DB_AFTER (runtime_v3):

| table | before | after |
|---|---|---|
| signals | 3 | 3 |
| trades | 3 | 3 |
| orders | 4 | 4 |
| fills | 4 | 4 |
| positions | 3 | 3 |
| trade_events | 12 | 13 |
| account_snapshots | 120 | 121 |
| positions_open | 2 | 2 |
| integrity | ok | ok |
| fk | 0 | 0 |

Deliberate deltas: +1 trade_event +1 account_snapshot come from the test session's own event/snapshot records (session `clm-20260906-152941`). Trade/order/fill/position lineage unchanged → live closed-market run produced NO spurious/duplicated trade rows.

## DB ↔ memory (final_reconcile)
`db_deltas == mem_deltas == {trades:0, fills:0, positions_open:0}` → **no unexplained relative divergence** during the whole runtime (also 5 reconcile snapshot checks, `unexplained=0`).

Note (startup semantics): absolute db/mem counts differ at cold start because strategy runtimes restore from DB while ledger/position managers start empty; the enforced invariant is RELATIVE agreement.

## DB lineage (db_lineage: PASS)
FK integrity + trade→signal→order→fill linkage validated on the canonical DB (signals/trades/orders/fills/trade_signal_link checked; fk=0).

## Parallel read-only observer
Spawned a read-only DB observer during the session (reconcile_watcher spawning the WAL-backed observer); 5 snapshots captured, 0 unexplained — DB stayed consistent under concurrent reads.

## DB persistence of test session
`DB_PERSISTENCE.json` → `ok: true, test_id: clm-20260906-152941` (session record persisted to canonical DB).