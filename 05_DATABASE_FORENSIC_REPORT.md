# 05 - Database Forensic Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER
**Databases:** `trading.db` (operational) and `analytics.db` (analytics)

---

## 1. trading.db Schema

**File:** `persistence/manager.py:57-131`
**Connection:** `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=30000`

### Table: `trades`
```sql
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,
    strategy_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_timestamp TEXT,
    entry_price REAL,
    exit_timestamp TEXT,
    exit_price REAL,
    quantity INTEGER,
    multiplier REAL,
    gross_pnl REAL,
    charges REAL,
    net_pnl REAL,
    exit_reason TEXT,
    status TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```
| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| id | INTEGER | NO | AUTOINCREMENT | Internal row ID |
| trade_id | TEXT | YES | — | UNIQUE constraint, stores `position_id` |
| strategy_id | TEXT | NO | — | No FK constraint |
| instrument | TEXT | NO | — | |
| side | TEXT | NO | — | "LONG" or "SHORT" |
| entry_timestamp | TEXT | YES | — | ISO format |
| entry_price | REAL | YES | — | |
| exit_timestamp | TEXT | YES | — | ISO format |
| exit_price | REAL | YES | — | |
| quantity | INTEGER | YES | — | |
| multiplier | REAL | YES | — | |
| gross_pnl | REAL | YES | — | |
| charges | REAL | YES | — | |
| net_pnl | REAL | YES | — | |
| exit_reason | TEXT | YES | — | |
| status | TEXT | YES | — | Always "closed" |
| created_at | TEXT | YES | datetime('now') | |

**Issues:**
- `INSERT OR REPLACE` used (line 159) — this **destroys the auto-increment ID** if trade_id already exists
- No foreign key constraints on strategy_id
- No index on trade_id (UNIQUE provides implicit index)

### Table: `orders`
```sql
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    strategy_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER,
    order_type TEXT,
    price REAL,
    state TEXT,
    filled_quantity INTEGER,
    average_fill_price REAL,
    created_at TEXT,
    updated_at TEXT
);
```
**Issues:**
- `INSERT OR REPLACE` used (line 189) — same auto-increment destruction
- No FK on strategy_id

### Table: `fills`
```sql
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT UNIQUE,
    order_id TEXT,
    strategy_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER,
    price REAL,
    timestamp TEXT
);
```
**Issues:**
- `INSERT OR REPLACE` used (line 216)
- `order_id` is nullable — fills without an order can exist
- No FK on order_id or strategy_id

### Table: `events`
```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    event_type TEXT,
    strategy_id TEXT,
    instrument TEXT,
    details TEXT
);
```
**Issues:**
- `details` is JSON string (line 279: `json.dumps(event.get("details", {}))`)
- **Grows unbounded** — no cleanup/pagination
- No index on event_type
- No TTL or retention policy

### Table: `account_snapshots`
```sql
CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    equity REAL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    used_margin REAL,
    available_margin REAL
);
```
**Issues:**
- **Grows unbounded** — no cleanup
- No index on timestamp
- `get_account_snapshots()` has a LIMIT param (default 100) but writes have no cleanup

### Table: `processed_fills` (created by FillDeduplicator)
```sql
CREATE TABLE IF NOT EXISTS processed_fills (
    fill_id TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT (datetime('now'))
);
```
**Issues:**
- Has `cleanup_old(days=30)` method (fill_dedup.py:110) — only table with cleanup
- Grows linearly with trade count

---

## 2. analytics.db Schema

**File:** `analytics/schema.py:16-225`

### Table: `trade_events` (EventStore)
```sql
CREATE TABLE IF NOT EXISTS trade_events (
    event_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT DEFAULT 'system',
    payload TEXT,
    sequence_number INTEGER
);
```
**Indexes:** trade_id, strategy_id, timestamp, event_type

**Issues:**
- `trade_id` NOT NULL but can reference non-existent trades
- `payload` is JSON string
- **Grows unbounded** — no cleanup
- `sequence_number` is in-memory counter (event_store.py:63), not DB auto-increment

### Table: `trades_analytics` (TradeLedger)
```sql
CREATE TABLE IF NOT EXISTS trades_analytics (
    trade_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    -- 50+ columns for full lifecycle tracking
    status TEXT NOT NULL DEFAULT 'OPEN',
    -- ... (see schema.py:33-102)
);
```
**Indexes:** strategy_id, status, instrument, signal_time, closed_at
**Total columns:** ~65

**Issues:**
- Uses `ON CONFLICT(trade_id) DO UPDATE` (trade_ledger.py:460) — safe, no ID destruction
- Very wide table (65+ columns) — performance concern for large datasets

### Table: `trade_legs`
```sql
CREATE TABLE IF NOT EXISTS trade_legs (
    leg_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    timestamp REAL NOT NULL,
    slippage REAL,
    spread REAL,
    is_entry INTEGER NOT NULL DEFAULT 1
);
```
**Index:** trade_id
**Issues:**
- Uses `INSERT OR IGNORE` (trade_ledger.py:475) — silently drops duplicates
- No FK constraints

### Table: `trade_snapshots`
```sql
CREATE TABLE IF NOT EXISTS trade_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    -- periodic open trade state columns
);
```
**Index:** trade_id
**Issues:**
- Not actively written to in current code (no write calls found)

### Table: `strategy_daily_performance`
```sql
CREATE TABLE IF NOT EXISTS strategy_daily_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    -- daily aggregates
    UNIQUE(date, strategy_id)
);
```
**Issues:**
- UNIQUE constraint prevents duplicate day+strategy
- Not actively populated by current code

### Table: `strategy_monthly_performance`
```sql
CREATE TABLE IF NOT EXISTS strategy_monthly_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    UNIQUE(month, strategy_id)
);
```
**Issues:**
- UNIQUE constraint on month+strategy
- Not actively populated

### Table: `strategy_performance_snapshots`
```sql
CREATE TABLE IF NOT EXISTS strategy_performance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    snapshot_time REAL NOT NULL,
    UNIQUE(strategy_id, snapshot_time)
);
```

### Table: `strategy_parameter_results`
```sql
CREATE TABLE IF NOT EXISTS strategy_parameter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    UNIQUE(strategy_id, parameter_hash)
);
```

---

## 3. Foreign Key Analysis

| Table | Column | References | FK Constraint | Violation Risk |
|-------|--------|------------|---------------|----------------|
| trades | strategy_id | (none) | **NONE** | Low (app-controlled) |
| orders | strategy_id | (none) | **NONE** | Low |
| fills | order_id | orders.order_id | **NONE** | Medium (can orphan) |
| fills | strategy_id | (none) | **NONE** | Low |
| trade_events | trade_id | trades_analytics.trade_id | **NONE** | Medium |
| trade_events | strategy_id | (none) | **NONE** | Low |
| trade_legs | trade_id | trades_analytics.trade_id | **NONE** | Medium |
| trade_snapshots | trade_id | trades_analytics.trade_id | **NONE** | Medium |

**Total FK constraints: 0**

The analytics schema file enables `PRAGMA foreign_keys=ON` (schema.py:267) but **no actual FOREIGN KEY clauses exist in any CREATE TABLE statement**.

---

## 4. Index Analysis

| Database | Table | Index | Columns | Purpose |
|----------|-------|-------|---------|---------|
| trading.db | trades | (implicit) | trade_id (UNIQUE) | Lookup by trade_id |
| trading.db | orders | (implicit) | order_id (UNIQUE) | Lookup by order_id |
| trading.db | fills | (implicit) | fill_id (UNIQUE) | Lookup by fill_id |
| analytics.db | trade_events | idx_trade_events_trade_id | trade_id | Event lookup by trade |
| analytics.db | trade_events | idx_trade_events_strategy_id | strategy_id | Event lookup by strategy |
| analytics.db | trade_events | idx_trade_events_timestamp | timestamp | Time-range queries |
| analytics.db | trade_events | idx_trade_events_type | event_type | Filter by event type |
| analytics.db | trades_analytics | idx_trades_analytics_strategy | strategy_id | Strategy queries |
| analytics.db | trades_analytics | idx_trades_analytics_status | status | Status filter |
| analytics.db | trades_analytics | idx_trades_analytics_instrument | instrument | Instrument filter |
| analytics.db | trades_analytics | idx_trades_analytics_signal_time | signal_time | Time queries |
| analytics.db | trades_analytics | idx_trades_analytics_closed_at | closed_at | Recent trades |
| analytics.db | trade_legs | idx_trade_legs_trade_id | trade_id | Legs by trade |
| analytics.db | trade_snapshots | idx_trade_snapshots_trade_id | trade_id | Snapshots by trade |
| analytics.db | strategy_daily_performance | idx_strategy_daily_strategy | strategy_id | Daily by strategy |
| analytics.db | strategy_monthly_performance | idx_strategy_monthly_strategy | strategy_id | Monthly by strategy |

**Missing indexes:**
- `events.event_type` in trading.db
- `events.timestamp` in trading.db
- `account_snapshots.timestamp` in trading.db
- `trades_analytics.created_at` in analytics.db

---

## 5. INSERT OR REPLACE Problem

**Location:** `persistence/manager.py:159,189,216,238`

When `INSERT OR REPLACE` encounters a duplicate `trade_id`/`order_id`/`fill_id`:
1. The existing row is **deleted**
2. A new row is **inserted** with a new auto-increment `id`
3. Any foreign key references to the old `id` are **silently broken**

**Impact:** If a trade is persisted twice (e.g., reconciliation retry), the auto-increment ID changes, potentially breaking any external references.

**Mitigation:** The code only writes each trade/order/fill once, so this is a theoretical risk. But the pattern is fragile.

---

## 6. Orphan Detection Results

| Check | Result | Details |
|-------|--------|---------|
| Fills without orders | Possible | `order_id` is nullable in fills table |
| Trades without fills | Unlikely | TradeCloseManager persists trade+fill in one transaction |
| Events without trades | Possible | `save_event()` can write events with non-existent trade_ids |
| Closed trades with missing exit data | Possible | If crash occurs between persist steps |
