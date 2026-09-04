# CRASH RECOVERY REPORT

**Date:** 2026-09-05
**Status:** Crash/restart and empty-cache recovery verified against canonical trading.db.

---

## 1. Objective

Prove the application can restart after a crash at ANY lifecycle point and reconstruct the same
trade (same IDs, same lifecycle, no duplicates) from **trading.db alone** — no analytics.db.

## 2. Crash points covered (#64)

| Crash at | What must survive restart |
|---|---|
| signal persisted | signal row (signal_id) |
| trade persisted | trade row (trade_id, entry_signal_id) |
| order created | orders row (order_id, trade_id) |
| order submitted | orders status |
| fill received | fills row (fill_id, trade_id, order_id) |
| fill persisted | fills row survives; no re-apply (idempotent) |
| position opened | positions row (position_id, trade_id) |
| exit triggered | exit signal relationship |
| exit fill | fills + derived trade_legs |
| trade closed | trades status CLOSED, P&L |

## 3. Restart mechanics (canonical)

- `portfolio/position_manager.py:restore` rebuilds positions from trading.db, preserving
  `trade_id` for both open and closed positions.
- `TradeLedger._load_open_trades` reloads the `_open_trades` cache from `trades_analytics`
  (inside trading.db) on startup — memory is not authoritative (`trade_ledger.py:122-136`).
- `TradeLifecycleManager` reconstructs the lifecycle from canonical `signals/trades/orders/fills`.
- `recovery.py` restores state from the canonical database.

## 4. Empty-cache recovery (#65)

`tests/adversarial_trade_lifecycle/test_memory_cache_not_authoritative.py` (NEW, all pass):
- **exit-fill cache-miss:** with `_open_trades` empty, an exit fill loads the DB aggregate and
  closes it (fixes the `baa04bef` divergence).
- **duplicate exit fill:** idempotent — no double close, no double P&L.
- **duplicate entry fill:** idempotent — one leg, no duplicated filled_quantity.
- **guarded projection heal:** `TradeCloseManager` rebuilds a missing analytics projection from
  canonical data instead of raising.

Additional cache tests: `test_ledger_open_trades_cache.py` (fresh_audit),
`test_phase14_recovery.py` (live_runtime_v2), `test_lifecycle_persistence_failure.py`.

## 5. Full crash/restart test

- `test_crash_api_replay.py` (fresh_audit, passes) — crash-replay across phase boundaries with
  canonical trade_id model; same IDs, same lifecycle, no duplicates.
- 162-suite `test_phase14_recovery.py` (passes) — full restart recovery through the engine.

## 6. Duplicate-event idempotency (#66)

- `test_memory_cache_not_authoritative.py` — duplicate entry/exit fills idempotent.
- `test_master_parity_audit.py` — concurrent duplicate writes idempotent (DB-level).
- `test_forensic_trade_lifecycle.py` — fill replay does not duplicate leg.
- `test_memory_db_reconciliation.py` (adversarial) — DB rejects true orphans; ON CONFLICT
  preserves row identity.

## 7. Conclusion

**Crash recovery and empty-cache recovery work against trading.db alone.** ✅