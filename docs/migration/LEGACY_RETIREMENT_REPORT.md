# LEGACY RETIREMENT REPORT

**Date:** 2026-09-05
**Status:** Legacy `analytics.db` archived; old architecture fully disconnected from runtime.

---

## 1. What was retired

| Component | Type | Retirement action |
|---|---|---|
| `data/db/analytics.db` | legacy standalone analytics database | **Archived** (not deleted). Moved to `archive/legacy/`. |
| Separate `trades_analytics`/`trade_legs`/`trade_snapshots`/`strategy_*` in analytics.db | derived analytics tables | **Migrated** into canonical trading.db as DERIVED tables. |
| Independent `TradeLedger` authority on analytics.db | second trade truth | **Removed** — TradeLedger is a derived projection on trading.db. |
| `init_analytics_db` as a runtime concern | DB-creation utility | **Disconnected** from production; MIGRATION-ONLY. |
| runtime `analytics.db` path / config | old path | **Removed** — zero production references. |

## 2. Backup before retirement (checklist #16, #54)

Performed before any removal:

| Step | Result |
|---|---|
| Backup | `archive/legacy/analytics.db_20260905_000333` (139,264 B; identical copy) |
| Checksum (SHA256) | `86CF3D9AC17D3409D86A05B28E9DFE57E4F02C4D9F6589A5A33C285FED57FEF9` |
| Inventory | Every table row count recorded (all 0 rows). See `OLD_ANALYTICS_DATA_INVENTORY.md`. |
| Dependency scan | ZERO production imports of `analytics.db` / analytics path config. See `DATABASE_CONNECTION_AUDIT.md`. |
| Runtime scan | App starts and runs with legacy file absent (validated). See `FINAL_DATABASE_VERIFICATION.md`. |
| Migration verification | Derived tables live inside trading.db; all suites pass (823 + 162 + 87). |
| Zero active writer/reader | No process opens `data/db/analytics.db`; all writers/readers target trading.db. |

Retirement metadata recorded in `archive/legacy/ANALYTICS_DB_RETIREMENT.log`:
`archive_dest=archive/legacy/analytics.db_20260905_000333; orig_size=139264;
orig_mtime=09/03/2026 15:32:59; sha256=86CF3D9AC17D3409D86A05B28E9DFE57E4F02C4D9F6589A5A33C285FED57FEF9;
reason=legacy two-DB retirement, derived tables moved into canonical trading.db`

## 3. Old code file retirement policy (checklist #17)

Every obsolete code component was proven unreachable before any change:

- `analytics/schema.py` — **kept**, but `init_analytics_db` is never imported/called by any
  production module (config/, core/, execution/, portfolio/, persistence/, reconciliation/,
  monitoring/, notifications/, strategies/, dashboard/, main.py, trading_engine.py, recovery.py)
  → MIGRATION-ONLY.
- `_replay.py`, `_audit_5day.py`, `_rebuild_prod_db.py`, `_parity_replay.py`, `_p1_lib.py`,
  `seed_replay.py`, `full_simulator.py`, `compare_replay_trades.py`, `fix_analytics.py`,
  `_inspect_db.py`, `_bt5_backtest.py`, `_bt5_offline.py`, `_frontend_deep_check.py`,
  `_server_deep_audit.py`, `_live_flow_check.py`, `_live_e2e_test.py` — all standalone
  TOOLING/MIGRATION scripts. None is imported by production startup, API, scheduler, or Docker
  CMD (`python dashboard/run.py`). They remain as historical tooling.

## 4. Git safety (checklist #55)

- `git status`, `git diff`, `git log` recorded before any retirement work. No destructive git
  operations used; user work in the working tree is preserved.
- Legacy analytics.db was `.gitignore`d (`data/db/*.db`) and never tracked; the archive copy is
  intentionally under `archive/` (added only if the user wants it committed; recommended to keep
  `archive/` local or push intentionally).

## 5. Re-creation prevention (checklist #51)

- No production code path can call `sqlite3.connect("...analytics.db")` (SQLite auto-creates on
  connect). Source-grep for `analytics.db` in production modules: ZERO hits.
- `init_analytics_db` is only reachable from standalone scripts under `_`/tools/root tooling, not
  from `dashboard/run.py` startup.
- After startup, `data/db/analytics.db` is NOT recreated (verified; the app only opens trading.db).

## 6. Final zero-legacy test (checklist #76)

1. Legacy analytics.db renamed/archived → app operates normally with ONLY trading.db (verified locally; VPS-side step pending per user sequencing).
2. No analytics DB env vars exist.
3. No analytics DB Docker mount exists (single `data/db` mount).
4. No analytics DB connection code in production.
5. Analytics modules retained but operate on trading.db.
6. All tests run: fresh_audit 823 passed, live_runtime_v2 162 passed, adversarial+regression 87 passed.
7. All APIs backed by trading.db.
8. Frontend uses API only.
9. Reconciliation passes.
10. Repository re-search (checklist #47, #67): final production code has ZERO independent analytics.db
    connection, ZERO independent TradeLedger authority, ZERO old analytics DB runtime path, ZERO
    frontend DB access, ZERO second canonical trade ledger, ZERO position_id-as-trade_id, ZERO implicit
    trade identity vent.