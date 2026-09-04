# ANALYTICS MIGRATION REPORT

**Date:** 2026-09-05
**Status:** Analytics fully migrated to derive from `trading.db`. No standalone analytics.db.

---

## 1. Migration principle

Analytics is now **DERIVED FROM trading.db**. There is no second canonical trade ledger.

- Canonical source tables (authoritative): `signals`, `trades`, `orders`, `fills`,
  `positions`, `trade_signal_link`, `events`, `trade_events`.
- Derived tables (rebuildable read-model, never authoritative): `trades_analytics`,
  `trade_legs`, `trade_snapshots`, `strategy_daily_performance`,
  `strategy_monthly_performance`, `strategy_parameter_results`,
  `strategy_performance_snapshots`.

## 2. What changed for each old consumer

### TradeLedger (`analytics/trade_ledger.py`)
- **OLD:** independent authority writing `trades_analytics` in `analytics.db`.
- **NEW:** derived projection writing derived tables inside `trading.db`.
  - `TradeLedger(db_path="trading.db")` default (line 104) — production passes canonical path.
  - `create_trade(trade_id=...)` REQUIRED (line 154-155, `raise ValueError` if None) — the ledger
    never invents trade identity.
  - `record_fill` idempotent on `fill_id` (line 199-202) — duplicate fills produce one leg.
  - `record_fill` falls back to DB aggregate when `_open_trades` cache misses (line 223-225)
    — memory is NOT authoritative; this directly removes the `baa04bef` divergence pattern.
  - `_open_trades` (line 107) is a cache loaded from DB on start (line 122) and corrected after
    every fill (CLOSED evicted, line 235-238).

### EventStore (`analytics/event_store.py`)
- **NEW:** wraps `persistence.database.Database(db_path)` (line 38-39) — writes `trade_events`
  inside trading.db.

### PerformanceEngine (`analytics/performance.py`)
- **NEW:** `__init__(db_path="trading.db")` (line 103) — reads derived tables inside trading.db.

### Analytics routes (`analytics/routes.py`)
- **NEW:** `init(db_path)` (line 62) stores the canonical path; all handlers use it.
- Production wiring: `dashboard/server.py:35-36` inits from `Config.resolve_path(...trading.db)`.

### `analytics/schema.py:init_analytics_db`
- **OLD:** standalone utility creating legacy analytics tables anywhere.
- **NEW:** NOT called by production. Only MIGRATION-ONLY/TOOLING scripts call it
  (`full_simulator.py`, `seed_replay.py`, `_replay.py`, etc.). It cannot be triggered by the
  production runtime; no production import path reaches it.

## 3. API surface (all backed by trading.db)

All `/api/analytics/*` endpoints (see `COMPLETE_API_MIGRATION_REPORT.md`) now source from:
- derived tables inside trading.db (via TradeLedger/PerformanceEngine/EventStore constructed on
  the canonical path), or
- canonical tables via repositories.

## 4. Rebuild / determinism

Derived tables are declared rebuildable (`DERIVED_TABLES`, `persistence/database.py:52-60`).
Every migration/verification test run seeds → runs a full lifecycle → reconciles canonical vs
derived (see `TRADE_LIFECYCLE_VERIFICATION.md`, `test_master_parity_audit.py`,
`test_audit_reversal_sl_all_strategies.py`). Re-running the same scenario twice yields the same
records (deterministic) — confirmed by the idempotency and replay tests.

## 5. Analytics.db at runtime

- Legacy `data/db/analytics.db`: **archived** (empty, dormant).
- No production module references the string `analytics.db` or any analytics path config
  (see `DATABASE_CONNECTION_AUDIT.md`).
- Starting the app with legacy analytics.db absent/renamed: **successful** — the app only needs
  trading.db (validated in `FINAL_DATABASE_VERIFICATION.md`).

## 6. Result

| Old | New |
|---|---|
| analytics.db trade_ledger authority | trading.db-derived projection |
| second P&L authority | single canonical P&L |
| analytics.db files | 0 (archived) |
| routes on stale analytics data | all routes on canonical trading.db |
| rebuildable | yes (derived tables, deterministic) |