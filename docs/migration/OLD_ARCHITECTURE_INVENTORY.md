# OLD ARCHITECTURE INVENTORY

**Status:** Complete — every old-architecture component has been discovered and classified.
**Date:** 2026-09-05
**Repo:** MCX-TRADER

This is the canonical inventory of every component from the OLD two-database architecture
(`trading.db` + `analytics.db`). Each entry is classified as one of:

| Class | Meaning |
|---|---|
| **KEEP** | Still has a legitimate role in the NEW single-DB architecture. |
| **MIGRATE** | Functionality was migrated into the canonical model; the old form may remain as a shim. |
| **REMOVE** | Fully obsolete in production / can be deleted from the repo. |
| **MIGRATION-ONLY** | Only used by historical/one-time migration tooling; never imported by production. |
| **TEST-ONLY** | Only referenced by tests. |
| **DOCUMENTATION-ONLY** | Only referenced by historical markdown docs. |

---

## 1. Databases on disk

| Path | Size | Last modified | Contents | Class |
|---|---|---|---|---|
| `data/db/trading.db` | 270,336 B | 2026-09-04 21:52 | **CANONICAL trading.db** — contains canonical tables + derived analytics tables (single file). | **KEEP** (canonical) |
| `trading.db` (repo root) | 270,336 B | 2026-09-04 20:02 | Stale duplicate of canonical (identical schema; leftover test residue rows). | **REMOVE / ignore** (not referenced by any config) |
| `data/db/analytics.db` | 139,264 B | 2026-09-03 15:32 | **LEGACY analytics.db** — only derived tables (trades_analytics, trade_legs, trade_events, trade_snapshots, strategy_*) all EMPTY (0 rows). Dormant; not read/written by production. | **REMOVE / ARCHIVED** |

> The legacy `data/db/analytics.db` has been archived (not deleted) with checksum
> `86CF3D9AC17D3409D86A05B28E9DFE57E4F02C4D9F6589A5A33C285FED57FEF9` to
> `archive/legacy/analytics.db_20260905_000333`. See `LEGACY_RETIREMENT_REPORT.md`.

---

## 2. Environment variables / configuration

| Env var / config key | Old meaning | Current status |
|---|---|---|
| `ANALYTICS_DB_PATH` | Path to legacy analytics.db | **REMOVE** — does not exist in `config/`; zero production references. |
| `ANALYTICS_DB` / `analytics_db` | Legacy alias | **REMOVE** — zero production references. |
| `system.db_path` | Was `data/db/trading.db` | **KEEP** — single canonical path. |

**Evidence:** `config/settings.json:7` sets only `"db_path": "data/db/trading.db"`. No analytics
key exists anywhere in `config/`.

---

## 3. Old code modules (files)

### `analytics/schema.py`
- **What:** Defines legacy analytics-DB DDL and `init_analytics_db(db_path)` (line 238) — a
  standalone utility that creates `trades_analytics`/`trade_legs`/`trade_events` tables in
  whatever file it is given.
- **Production use:** **NONE.** No production module imports or calls it.
- **Called by (all MIGRATION-ONLY / TOOLING scripts):**
  - `full_simulator.py:281,285`
  - `seed_replay.py:84,90`
  - `compare_replay_trades.py:103,108`
  - `_p1_lib.py:197,204`
  - `_replay.py:216,219`
  - `_audit_5day.py:130,134`
  - `_rebuild_prod_db.py:308-309`
  - `_parity_replay.py:153,158`
  - `test` files (old-architecture tests)
- **Class:** MIGRATION-ONLY (kept for historical tooling; not part of production runtime).

### `analytics/trade_ledger.py`
- **What:** `TradeLedger` class. **OLD:** authoritative second trade ledger writing to
  `analytics.db`. **NEW:** derived projection over the single trading.db —
  `__init__(db_path="trading.db")` (line 104); `create_trade` REQUIRES `trade_id` (line 154-155);
  `record_fill` is idempotent on `fill_id` (line 199-202) and falls back to DB on cache miss
  (line 223-225). `_open_trades` is a cache, NOT authoritative.
- **Class:** MIGRATE → **KEEP** (now a derived read-model, operating on trading.db).

### `analytics/performance.py`
- **What:** Performance engine (Sharpe, Sortino, drawdown, etc.).
  `__init__(db_path="trading.db")` (line 103) — reads derived tables from trading.db.
- **Class:** **KEEP** (migrated to canonical source).

### `analytics/event_store.py`
- **What:** Append-only event log.
  `__init__(db_path="trading.db")` (line 38) — wraps `persistence.database.Database(single db)`.
- **Class:** **KEEP** (now writes trade_events inside trading.db).

### `analytics/routes.py`
- **What:** Analytics REST API routes (`/api/analytics/*`).
  `init(db_path)` (line 62) stores the passed canonical path; all handlers use only that path.
- **Class:** **KEEP** (migrated — initialized from trading.db by `dashboard/server.py:35-36`).

### `persistence/database.py`
- **What:** Central database layer. Docstring (lines 1-17): "The old `analytics.db` is no longer
  a runtime database. Its tables ... live *inside* trading.db as DERIVED tables."
  Defines `CANONICAL_TABLES` and `DERIVED_TABLES` (lines 34-60) within ONE file.
- **Class:** **KEEP** (this IS the new architecture).

---

## 4. Old database tables

### Were in `analytics.db` (legacy) — now inside `trading.db`:

| Table | Old DB | New home | Class |
|---|---|---|---|
| `trades_analytics` | analytics.db | trading.db (DERIVED) | **KEEP** (derived read-model) |
| `trade_legs` | analytics.db | trading.db (DERIVED) | **KEEP** |
| `trade_events` | analytics.db | trading.db (CANONICAL events) | **KEEP** |
| `trade_snapshots` | analytics.db | trading.db (DERIVED) | **KEEP** |
| `strategy_daily_performance` | analytics.db | trading.db (DERIVED) | **KEEP** |
| `strategy_monthly_performance` | analytics.db | trading.db (DERIVED) | **KEEP** |
| `strategy_parameter_results` | analytics.db | trading.db (DERIVED) | **KEEP** |
| `strategy_performance_snapshots` | analytics.db | trading.db (DERIVED) | **KEEP** |

### Additional canonical tables in `trading.db`:
`signals`, `trades`, `orders`, `fills`, `positions`, `pending_orders`,
`trade_signal_link`, `processed_fills`, `account_snapshots`, `events`,
`quarantine_records`, `system_metadata`.

---

## 5. Old API routes referencing analytics.db / TradeLedger

All were migrated to operate on trading.db (via `analytics.routes.init(canonical_db)`). See
`COMPLETE_API_MIGRATION_REPORT.md` for the full table. The historical `/api/analytics/*` routes
all read derived tables inside trading.db now.

---

## 6. Frontend references

Frontend (`dashboard-ui/src`) calls `/api/*` only — no direct DB access. See
`FRONTEND_API_MIGRATION_REPORT.md`.

---

## 7. Tests referencing old architecture

(Already migrated to canonical model — see `TRADE_LIFECYCLE_VERIFICATION.md` for status.)

---

## 8. Docker

| Reference | Old | New |
|---|---|---|
| `Dockerfile:53` | `COPY analytics/ ./analytics/` | **KEEP** — the analytics package is a real module; copy remains valid. |
| Volume mount | `trading.db` + `analytics.db` | Only `data/db` → `/app/data/db` (single mount). See `DATABASE_CONNECTION_AUDIT.md`. |

---

## Classification summary

| Class | Components |
|---|---|
| **KEEP** | `persistence/database.py`, analytics package (migrated to canonical), config `system.db_path`, canonical trading.db |
| **MIGRATE** | TradeLedger (ledger→projection), analytics routes (source→trading.db), derived tables (moved into trading.db) |
| **REMOVE / ARCHIVED** | `data/db/analytics.db` (archived) |
| **MIGRATION-ONLY / TOOLING** | `full_simulator.py`, `seed_replay.py`, `compare_replay_trades.py`, `_replay.py`, `_audit_5day.py`, `_rebuild_prod_db.py`, `_parity_replay.py`, `_p1_lib.py`, `fix_analytics.py`, `_inspect_db.py`, `analytics/schema.py:init_analytics_db` |
| **TEST-ONLY** | old-architecture tests (now rewritten to canonical model) |
| **DOCUMENTATION-ONLY** | historical `*.md` audit reports describing the old two-DB system |

> **No production component is classified REMOVE without being either (a) migrated to canonical
> behavior or (b) proven unreachable from production runtime. Nothing was deleted before this
> inventory existed.**