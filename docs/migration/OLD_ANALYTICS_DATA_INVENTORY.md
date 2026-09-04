# OLD ANALYTICS DATA INVENTORY

**Date:** 2026-09-05
**Source:** the retired legacy `data/db/analytics.db` (present on disk before archival) and the
static forensic audit of all code that read/wrote it.

---

## 1. Legacy `analytics.db` file

| Property | Value |
|---|---|
| Path | `data/db/analytics.db` |
| Size | 139,264 B |
| Last modified | 2026-09-03 15:32:59 |
| SHA256 | `86CF3D9AC17D3409D86A05B28E9DFE57E4F02C4D9F6589A5A33C285FED57FEF9` |
| Journal mode | WAL |
| integrity_check | `ok` |
| foreign_key_check | no violations |
| Tables | `sqlite_sequence`, `strategy_daily_performance`, `strategy_monthly_performance`, `strategy_parameter_results`, `strategy_performance_snapshots`, `trade_events`, `trade_legs`, `trade_snapshots`, `trades_analytics` |

## 2. Row counts at archival

| Table | Row count |
|---|---|
| `trades_analytics` | 0 |
| `trade_legs` | 0 |
| `trade_events` | 0 |
| `trade_snapshots` | 0 |
| `strategy_daily_performance` | 0 |
| `strategy_monthly_performance` | 0 |
| `strategy_parameter_results` | 0 |
| `strategy_performance_snapshots` | 0 |

**The legacy analytics.db contained ZERO rows.** It was a dormant, empty file — no historical
data to lose, nothing to migrate. This machine is a local development/post-verification box; the
runtime trade history lives on the VPS container's mounted trading.db (see `CRASH_RECOVERY_REPORT.md`
and `FINAL_DATABASE_VERIFICATION.md` for VPS-side verification).

## 3. Which code used to read/write these tables

Historical (pre-migration) consumers identified by static audit:

| Table | Historical writers | Historical readers |
|---|---|---|
| `trades_analytics` | TradeLedger.create_trade / record_fill / close_trade | analytics routes (`/api/analytics/strategies/*/trades`, `open-trades`, etc.), PerformanceEngine, reconciliation |
| `trade_legs` | TradeLedger.record_fill | `/api/analytics/trades/{id}` legs, reconciliation |
| `trade_events` | EventStore.record | `/api/analytics/events`, trade detail |
| `trade_snapshots` | periodic snapshot job | `/api/analytics/strategies/{id}/mae-mfe`-style requests |
| `strategy_daily_performance` | performance projection | `/api/analytics/strategies/*/daily` |
| `strategy_monthly_performance` | performance projection | `/api/analytics/strategies/*/monthly` |
| `strategy_parameter_results` | parameter sweep projection | `/api/analytics/strategies/*/parameters` |
| `strategy_performance_snapshots` | performance projection | analytics dashboard |

## 4. Migration disposition of each table

| Table | Disposition |
|---|---|
| `trades_analytics` | **MIGRATED** — now a DERIVED table inside trading.db (`persistence.database.py:53`, DDL lines 291+), written by `TradeLedger(db_path=trading.db)`. |
| `trade_legs` | **MIGRATED** — DERIVED table inside trading.db (`database.py:54`, DDL 350+). |
| `trade_events` | **MIGRATED** — CANONICAL table inside trading.db (`database.py:42`, DDL 220+) — events are canonical, not derived. |
| `trade_snapshots` | **MIGRATED** — DERIVED table inside trading.db (`database.py:55`). |
| `strategy_daily_performance` | **MIGRATED** — DERIVED table inside trading.db (`database.py:56`). |
| `strategy_monthly_performance` | **MIGRATED** — DERIVED table inside trading.db (`database.py:57`). |
| `strategy_parameter_results` | **MIGRATED** — DERIVED table inside trading.db (`database.py:58`). |
| `strategy_performance_snapshots` | **MIGRATED** — DERIVED table inside trading.db (`database.py:59`). |

> Because legacy analytics.db was EMPTY, no historical rows needed copy/matching. There are no
> RECOVERABLE/DUPLICATE/ORPHAN/UNKNOWN classifications because no records existed. The single
> canonical ledger is trading.db.