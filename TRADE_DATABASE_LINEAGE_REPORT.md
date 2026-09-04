# TRADE DATABASE LINEAGE REPORT

**Date**: 2026-09-04
**Status**: COMPLETE — Full signal lineage tracked across all DB tables

---

## Schema Overview

### Tables with Lineage Columns

| Table | lineage_column | Links to | Nullable | Purpose |
|---|---|---|---|---|
| `trades` | `entry_signal_id` | `signals.signal_id` | NO (enforced by app) | Which signal triggered this trade |
| `trades` | `exit_signal_id` | `signals.signal_id` | YES (NULL for SL) | Which signal triggered the exit |
| `orders` | `entry_signal_id` | `signals.signal_id` | YES | Which signal triggered this order |
| `orders` | `trade_id` | `trades.trade_id` | YES | Which trade this order belongs to |
| `fills` | `entry_signal_id` | `signals.signal_id` | YES | Which signal triggered this fill |
| `fills` | `trade_id` | `trades.trade_id` | YES | Which trade this fill belongs to |
| `signals` | `signal_id` | (primary key) | NO | Unique signal identifier |
| `trade_signal_link` | `trade_id` | `trades.trade_id` | NO | Trade-signal relationship |
| `trade_signal_link` | `signal_id` | `signals.signal_id` | NO | Trade-signal relationship |
| `trade_signal_link` | `relationship_type` | — | NO | "entry" or "exit" |

### Tables WITHOUT Lineage (No Changes Needed)

| Table | Reason |
|---|---|
| `account_snapshots` | Point-in-time equity snapshots, no trade linkage |
| `events` | Audit log with strategy/instrument, trade linkage via `details` JSON |
| `system_state.json` | Engine state file, not a DB table |

---

## Lineage Flow Diagram

```
signals.signal_id
    │
    ├──→ trades.entry_signal_id     (COMPULSORY)
    ├──→ trades.exit_signal_id      (OPTIONAL, NULL for SL)
    ├──→ orders.entry_signal_id     (links order to its triggering signal)
    ├──→ fills.entry_signal_id      (links fill to its triggering signal)
    └──→ trade_signal_link.signal_id (relationship: "entry" or "exit")

trades.trade_id
    │
    ├──→ orders.trade_id            (links order to its parent trade)
    ├──→ fills.trade_id             (links fill to its parent trade)
    └──→ trade_signal_link.trade_id (links trade to its signals)
```

---

## Save Methods & What They Persist

### `save_trade(trade: dict)`
Inserts/updates into `trades` table. Includes:
- `entry_signal_id` from `trade.get("entry_signal_id")`
- `exit_signal_id` from `trade.get("exit_signal_id")`

### `save_order(order: dict)`
Inserts/updates into `orders` table. Includes:
- `entry_signal_id` from `order.get("entry_signal_id")`
- `trade_id` from `order.get("trade_id")`

### `save_fill(fill: dict)`
Inserts/updates into `fills` table. Includes:
- `entry_signal_id` from `fill.get("entry_signal_id")`
- `trade_id` from `fill.get("trade_id")`

### `save_signal(signal_data: dict)`
Inserts into `signals` table (INSERT OR IGNORE, idempotent). Stores:
- `signal_id`, `strategy_id`, `instrument`, `side`, `signal_type`
- `timestamp`, `trigger_price`, `stop_price`, `quantity`
- `candle_data` (JSON), `indicator_data` (JSON)

### `save_trade_signal_link(trade_id, signal_id, relationship_type)`
Inserts into `trade_signal_link` table (INSERT OR IGNORE). Relationship types:
- `"entry"` — this signal triggered the trade entry
- `"exit"` — this signal triggered the trade exit

### `save_trade_and_fill(trade, fill)`
Atomic transaction: persists both `trades` and `fills` rows with rollback on failure. Used by `TradeCloseManager` for exit operations.

---

## Lineage Constraints (Application-Level)

| Rule | Enforcement | Violation Detection |
|---|---|---|
| entry_signal_id NEVER NULL | `create_trade_from_signal()` always sets it | `reconcile()` checks: `if not trade.entry_signal_id` |
| exit_signal_id NULL for SL | `apply_stop_loss()` doesn't set it | `orphan_scan()` checks exit state consistency |
| position_id = trade_id | `register_entry_fill()` auto-sets | Identity map check |
| trade_id links fills to trades | `register_entry_fill/exit_fill()` sets it | `orphan_scan()` checks fill→trade linkage |
| trade_id links orders to trades | `register_order()` sets it | `orphan_scan()` checks order→trade linkage |

---

## Migration Safety

The `_migrate_db()` method ensures backward compatibility:
1. Checks `PRAGMA table_info(trades)` for `entry_signal_id` and `exit_signal_id`
2. Checks `PRAGMA table_info(orders)` for `entry_signal_id` and `trade_id`
3. Checks `PRAGMA table_info(fills)` for `entry_signal_id` and `trade_id`
4. Adds missing columns via `ALTER TABLE ADD COLUMN` (idempotent)

This means existing databases (pre-lineage) are automatically upgraded on first access.

---

## Orphan Detection Rules

### In-Memory Orphan Scan (`lifecycle.orphan_scan()`)
1. **Orphan fills**: fill_id in `_fill_to_trade` map but trade_id doesn't exist in `_trades`
2. **Orphan orders**: order_id in `_order_to_trade` map but trade_id doesn't exist in `_trades`
3. **Orphan positions**: position_id in `_position_to_trade` map but trade_id doesn't exist in `_trades`
4. **Orphan pending orders**: pending_order_id in `_pending_to_trade` but trade_id doesn't exist

### DB Orphan Scan (`lifecycle.orphan_scan()` with persistence)
1. **Fills without trade_id**: `SELECT fill_id FROM fills WHERE trade_id IS NULL OR trade_id = ''`
2. **Orders without trade_id**: `SELECT order_id FROM orders WHERE trade_id IS NULL OR trade_id = ''`
3. **Fills with invalid trade_id**: fill's trade_id not in `_trades` memory map

### Legacy Reconciliation (`dashboard/routes/reconciliation.py`)
1. Cross-checks `trades` DB table vs `trades_snapshot` memory state
2. Detects phantom trades, missing trades, P&L mismatches
3. Unified with lifecycle orphan scan and identity consistency check

---

## Test Coverage

52 tests in `tests/fresh_audit/test_lifecycle.py` covering:
- Trade creation with signal_id propagation
- Entry fill → trade linkage
- Exit fill → trade linkage
- Position → trade linkage
- Order → trade linkage
- Pending order → trade linkage
- Snapshot/restore preserves all identity maps
- Orphan detection for fills, orders, positions
- Reconciliation consistency checks
- Full E2E lifecycle (signal → create → order → entry fill → position → exit fill → close)

---

## API Endpoints Serving Lineage Data

| Endpoint | Data Source | Description |
|---|---|---|
| `GET /api/trades` | `lifecycle.get_trades_for_api()` | All trades with full lineage |
| `GET /api/trades/{trade_id}` | `lifecycle.get_trade()` | Single trade detail |
| `GET /api/trades/orphan-scan` | `lifecycle.orphan_scan()` | Orphan detection report |
| `GET /api/trades/lifecycle-reconcile` | `lifecycle.reconcile()` | Consistency check |
| `GET /api/reconciliation` | Legacy + lifecycle + identity check | Unified reconciliation |

---

## Database File Location

- **VPS**: `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/mcx_trader.db`
- **Docker mount**: `/app/data/db` → host `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db`
- **WAL mode**: Enabled for concurrent read/write
- **Busy timeout**: 30 seconds
