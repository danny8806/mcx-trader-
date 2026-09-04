# DATABASE CONNECTION AUDIT

**Date:** 2026-09-05
**Status:** All production database connections resolve to ONE canonical `trading.db`.

---

## 1. Canonical path configuration

- `config/settings.json:7` → `"db_path": "data/db/trading.db"` (sole DB path).
- `Config.resolve_path()` (`config/__init__.py:35`) anchors relative paths to the project root.
- **No** `ANALYTICS_DB`, `ANALYTICS_DB_PATH`, `analytics_db_path` or any analytics DB key exists
  in production config. Search across `config/`, `.env*`, `mcx-trader.env`, Dockerfile,
  docker-compose: **zero** analytics-path configuration (Dockerfile line 53 is only `COPY
  analytics/` — the python package, not a DB).

## 2. Every production database connection

| Module | File:Line | Connection target | Notes |
|---|---|---|---|
| `persistence/database.py` | 575 | `self.db_path` (= trading.db) | Canonical Database singleton; sets `PRAGMA foreign_keys=ON` (581) |
| `persistence/manager.py` | 51, 65 | `self.db_path` (trading.db) | PersistenceManager; FK ON (59) |
| `core/fill_dedup.py` | 26,45,65,89,127 | `self._db_path` (trading.db) | dedup table inside trading.db |
| `reconciliation/engine.py` | 108 | `self.persistence.db_path` (= trading.db) | |
| `analytics/trade_ledger.py` | 115 | `self._db_path` (trading.db in prod) | derived projection |
| `analytics/performance.py` | 111 | `self._db_path` (trading.db in prod) | |
| `analytics/routes.py` | 39 | `_db_path` (init from trading.db) | |
| `analytics/event_store.py` | 38-39 | wraps `Database(db_path)` | |
| `trading_engine.py` | 2048 | `self._persistence.db_path` (= trading.db) | |

**Production path resolution:**
- `trading_engine.py:91-92` → `Config.resolve_path(config.system.db_path)` → trading.db; passed
  to `EventStore` (95) and `TradeLedger` (102).
- `dashboard/server.py:35` → same resolve → passed to `analytics_routes.init()` (36).
- `main.py:37-40` → hardcoded `data/db/trading.db`.

## 3. Connections that could reach analytics.db

Override-by-default only (`analytics/trade_ledger.py:104`, `performance.py:103`,
`event_store.py:38` use `db_path="trading.db"` defaults). In production every caller passes the
canonical path — proven above. `analytics/schema.py:254` (`init_analytics_db`) connects to
whatever path it is given, but is **never called by production**.

## 4. sqlite3.connect audit

All `sqlite3.connect` calls in production code (only these):
`core/fill_dedup.py`, `persistence/database.py:575`, `persistence/manager.py:51,65`,
`reconciliation/engine.py:108`, `analytics/trade_ledger.py:115`, `analytics/performance.py:111`,
`analytics/routes.py:39`, `analytics/schema.py:254` (standalone utility only). **None** connects
to a path containing `analytics.db` in production.

Tooling scripts that DO connect to `analytics.db` (MIGRATION-ONLY / TOOLING, never imported by
runtime): `fix_analytics.py:17,49`, `_inspect_db.py:25,48`. Others derive the path from
`init_analytics_db` calls only in standalone contexts.

## 5. Database factory / singleton (checklist #52, #53)

- `persistence/database.py` is the canonical factory (`Database`), used by event_store,
  repositories, validation tooling, engine, dashboard.
- `config.Config` provides the single canonical path.
- No service constructs `analytics.db` independently.

## 6. SQLAlchemy

No SQLAlchemy engine/session factory for analytics exists anywhere in the repo.

## Conclusion

**ZERO production database connections target anything other than trading.db.**
Starting the application in the absence of `analytics.db` is successful (see
`FINAL_DATABASE_VERIFICATION.md`).