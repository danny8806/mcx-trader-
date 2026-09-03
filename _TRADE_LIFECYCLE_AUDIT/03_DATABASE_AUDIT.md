# 03 — DATABASE AUDIT

Master Prompt report #03. Both live DBs audited directly on the mounted volume.

## trading.db
Tables: trades(2), orders(6), fills(6), processed_fills(6), events, account_snapshots.
PRAGMA integrity_check = ok.

Closed trades:
| trade_id | strategy | instrument | side | gross | charges | net | entry | exit | mult |
|---|---|---|---|---|---|---|---|---|---|
| 58bb8ea9 | gold_01 | GOLDM | LONG | -510.00 | 293.97 | -803.97 | 150768.0 | 150717.0 | 10 |
| dbfbcbd2 | gold_02 | GOLDM | LONG | -1340.00 | 294.00 | -1634.00 | 150851.0 | 150717.0 | 10 |

Orders(6)/fills(6) cover the 2 entries + 2 exits at matching prices (150768/150851
entry, 150717 exit). processed_fills(6) == fills(6) — every fill processed.

## analytics.db
Tables: trades_analytics(4), trade_legs(6), trade_events(12).
PRAGMA integrity_check = ok.

trades_analytics:
| trade_id | strategy | instrument | side | status | avg_e | avg_x | net |
|---|---|---|---|---|---|---|---|
| 58bb8ea9 | gold_01 | GOLDM | LONG | CLOSED | 150768 | 150717 | -803.97 |
| dbfbcbd2 | gold_02 | GOLDM | LONG | CLOSED | 150851 | 150717 | -1634.00 |
| 9983dd92 | silver_01 | SILVERM | LONG | OPEN | 236980 | - | - |
| baa04bef | silver_02 | SILVERM | LONG | OPEN | 236489 | - | - |

trade_legs(6): 2 entry+2 exit for gold (CLOSED), 2 entry-only for silver (OPEN).
Trade ids in analytics == position ids in trading/position_manager (9983dd92,
baa04bef) — position-anchored mapping is consistent.

## Bugs fixed in the DB layer (prior)
- BUG-3: `idx_trade_legs_fill_id` UNIQUE index on trade_legs(fill_id) — record_fill
  idempotent; duplicate/retried fills cannot create duplicate legs.

## Route map correction from audit
No `/api/author/orders|fills` routes exist. `/api/orders` and `/api/fills` are
served from execution_engine in-memory, not trading.db-backed (documented so the
DB and these two routes are not expected to be row-for-row identical sources).

## No data loss (this deploy + BUG-3 deploy)
Row counts identical pre/post each deploy; integrity ok.

## Open positions survive restart (verified live after this deploy)
silver_01 + silver_02 LONG persisted in system_state.json and restored.