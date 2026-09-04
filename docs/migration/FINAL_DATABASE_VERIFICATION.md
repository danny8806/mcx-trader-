# FINAL DATABASE VERIFICATION

**Date:** 2026-09-05
**Status:** trading.db is the sole canonical database; analytics.db fully retired.

---

## 1. Final DB file check (#68) — local machine

| File | Role | Exists? |
|---|---|---|
| `data/db/trading.db` | **CANONICAL** | Yes (270,336 B, WAL) |
| `data/db/analytics.db` | retired legacy | **No** — archived to `archive/legacy/` |
| `trading.db` (repo root) | stale duplicate (not referenced) | Yes (gitignored) |

## 2. Canonical DB health (live inspection)

```
PRAGMA integrity_check     → ok
PRAGMA foreign_key_check   → [] (no violations)
PRAGMA journal_mode        → wal
Tables                     → all 21 canonical+derived present in ONE file
tools/validate_trade_integrity → PASS
```

## 3. Runtime proof (checklist #6, #48, #50)

- **Active DB = trading.db**: `config/settings.json:7`; `dashboard/server.py:35`;
  `trading_engine.py:91-102`; `main.py:37-40`.
- **analytics.db NOT used by runtime**: zero production imports/connects/config references
  (`DATABASE_CONNECTION_AUDIT.md`); `analytics.routes.init()` receives the canonical path.
- **No re-creation of analytics.db** (#51): no runtime path constructs `analytics.db`;
  `init_analytics_db` only reachable from standalone tooling, never startup
  (`dashboard/run.py`).

## 4. Clean-start / zero-legacy test (#76) — local

1. ✅ Legacy analytics.db archived (removed from `data/db/`).
2. ✅ No analytics DB env vars/config paths exist.
3. ✅ No analytics DB Docker mount (single `data/db` bind mount).
4. ✅ No analytics DB connection code in production.
5. ✅ All suites pass with ONLY trading.db: fresh_audit 823, live_runtime_v2 162, adversarial 81,
   regressions 6. **(Total 1072 green.)**
6. ✅ Post-startup check: no analytics.db recreated.

## 5. VPS / Docker (follow-up per user sequencing)

Local verification is complete. The user's instruction sequences **VPS Docker verification
after** local work. The planned VPS checks (validate in `FINAL_VERIFICATION_REPORT.md`):
- confirm running container `mcx-trader` uses only `/app/data/db/trading.db`;
- confirm no `/app/data/db/analytics.db` and no process opens it;
- run `PRAGMA integrity_check` + `foreign_key_check` + orphan scan on the live DB;
- confirm a fresh restart of the new image does not recreate analytics.db.

## 6. Conclusion

**Local: trading.db is the sole canonical database.** VPS Docker verification follows per user
sequencing.