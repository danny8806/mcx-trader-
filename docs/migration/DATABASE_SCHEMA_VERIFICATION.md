# DATABASE SCHEMA VERIFICATION

**Date:** 2026-09-05
**Source:** live inspection of canonical `data/db/trading.db` on disk.

---

## 1. File identity

| Property | Value |
|---|---|
| Path | `data/db/trading.db` |
| Size | 270,336 B |
| Journal mode | WAL (`PRAGMA journal_mode` → `wal`) |
| `PRAGMA integrity_check` | **`ok`** |
| `PRAGMA foreign_key_check` | **no violations** |

## 2. Tables present (single file)

Canonical: `signals`, `trades`, `orders`, `fills`, `positions`, `pending_orders`,
`trade_signal_link`, `processed_fills`, `account_snapshots`, `events`, `trade_events`,
`quarantine_records`, `system_metadata` (+ `sqlite_sequence`).

Derived: `trades_analytics`, `trade_legs`, `trade_snapshots`, `strategy_daily_performance`,
`strategy_monthly_performance`, `strategy_parameter_results`, `strategy_performance_snapshots`.

All 21 tables exist inside the ONE trading.db. No separate analytics.db is needed.

## 3. Required relationships (checklist #39)

Declared FKs (`PRAGMA foreign_key_list`):
- `positions.trade_id → trades.trade_id` (declared FK, ON DELETE NO ACTION)

Legacy-table lineage enforced by **triggers** (documented dual-enforcement design
`persistence/database.py:61-67`):

| Trigger | On table | Enforces |
|---|---|---|
| `trg_trades_entry_signal_required` | trades | `entry_signal_id` NOT NULL |
| `trg_trades_entry_signal_exists` | trades | `entry_signal_id → signals.signal_id` |
| `trg_orders_trade_required` | orders | `trade_id` NOT NULL |
| `trg_orders_trade_exists` | orders | `trade_id → trades.trade_id` |
| `trg_fills_lineage_required` | fills | `trade_id` + `order_id` NOT NULL |
| `trg_fills_trade_exists` | fills | `trade_id → trades.trade_id` |
| `trg_fills_order_exists` | fills | `order_id → orders.order_id` |
| `trg_positions_identity_separate` | positions | `position_id != trade_id` (canonical identity) |
| `trg_trade_signal_link_trade_exists` | trade_signal_link | `trade_id → trades.trade_id` |
| `trg_trade_signal_link_signal_exists` | trade_signal_link | `signal_id → signals.signal_id` |

Required relationship matrix:

| Relationship | Enforcement | Verified |
|---|---|---|
| orders.trade_id → trades.trade_id | trigger `trg_orders_trade_exists` | ✅ |
| fills.trade_id → trades.trade_id | trigger `trg_fills_trade_exists` | ✅ |
| fills.order_id → orders.order_id | trigger `trg_fills_order_exists` | ✅ |
| positions.trade_id → trades.trade_id | declared FK | ✅ |
| trades.entry_signal_id → signals.signal_id | trigger `trg_trades_entry_signal_exists` | ✅ |
| trades.exit_signal_id → signals.signal_id | (nullable; set only for reversal exits) | ✅ |
| trade_signal_link.trade_id → trades | trigger `trg_trade_signal_link_trade_exists` | ✅ |
| trade_signal_link.signal_id → signals | trigger `trg_trade_signal_link_signal_exists` | ✅ |
| position_id ≠ trade_id | trigger `trg_positions_identity_separate` | ✅ |

## 4. Runtime enforcement

`PRAGMA foreign_keys = ON` is set on every production connection:
- `persistence/database.py:581`
- `persistence/manager.py:59`
- `analytics/trade_ledger.py:118`
- `core/fill_dedup.py` connections

(The ad-hoc read-only inspection connection reported `foreign_keys=0` only because it didn't set
the pragma; all runtime connections do.)

## 5. Integrity tool

`tools/validate_trade_integrity.py` (updated to recognize the trigger-based dual enforcement)
reports on the canonical DB:

```
ORPHANS          []
MISSING IDs      []
INVALID FK       []
SCHEMA CONTRACTS []   (declared FK OR enforcement trigger present for every contract)
INVALID STATES   []
P&L MISMATCHES   []
DUPLICATES       []
DATABASE         ok
RESULT           PASS
```

## 6. Clean-start schema creation (checklist #56)

`persistence/database.py:init_schema()` (line 631) creates all tables (canonical + derived)
idempotently with versioned migrations (`SCHEMA_VERSION=2`). Tests create fresh DBs per tmp_path
and the full lifecycle (signal→trade→order→fill→position→exit→P&L) runs green
(`tests/fresh_audit`, `tests/live_runtime_v2`, adversarial suite).