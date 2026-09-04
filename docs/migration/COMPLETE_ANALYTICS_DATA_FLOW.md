# COMPLETE ANALYTICS DATA FLOW

**Date:** 2026-09-05
**Status:** Analytics is derived from `trading.db`; no independent ledger.

---

## 1. The data flow (single source → derived read-model → API/frontend)

```
trading.db (CANONICAL)
   signals  ──────────────────────────┐
   trades   ─── entry/exit signal ids │
   orders   ─── trade_id              │
   fills    ─── trade_id + order_id   │
   positions ── trade_id              │
   trade_signal_link ────────────────┤
   events / trade_events             │
   account_snapshots                 │
                                     v
        TradeLedger(db_path=trading.db)   ← derived projection writer
        EventStore(db_path=trading.db)    ← canonical events inside trading.db
        PerformanceEngine(db_path=trading.db)
                                     │
                      DERIVED TABLES inside trading.db
                      trades_analytics, trade_legs, trade_snapshots,
                      strategy_daily/monthly_performance,
                      strategy_parameter_results, strategy_performance_snapshots
                                     │
                                     v
                 analytics/routes.py (init from canonical db)
                                     │
                                     v
              /api/analytics/*  ──>  Frontend Analytics / Strategy Matrix / Equity
```

## 2. Who writes derived rows

| Derived table | Writer | Triggered by |
|---|---|---|
| `trades_analytics` | `TradeLedger.create_trade` (trading.db) | canonical trade creation |
| `trades_analytics` (close) | `TradeLedger.close_trade` / `record_fill` exit leg (trading.db) | canonical exit (SL/reversal) |
| `trade_legs` | `TradeLedger.record_fill` (trading.db), idempotent on `fill_id` | each canonical fill |
| `trade_events` | `EventStore.record` (canonical table inside trading.db) | lifecycle events |
| `trade_snapshots` | periodic projection | snapshot job |
| `strategy_*_performance` | PerformanceEngine projection | on-request / scheduled |

Every writer is constructed with the **canonical** db_path. No writer can reach a standalone
analytics.db in production.

## 3. Who reads derived rows

| Reader | Read target |
|---|---|
| `analytics/routes.py` handlers | derived tables (trading.db) |
| `analytics/performance.py` | derived tables (trading.db) |
| `/api/analytics/*` endpoints | derived tables (trading.db) |
| frontend analytics components | `/api/analytics/*` |

## 4. Correctness guarantees

- **No second audit source:** derived tables are a read-model; every row is produced from a
  canonical row (trade_id/leg identity linked). `TradeLedger._open_trades` is only a cache;
  on miss it reloads from `trades_analytics` (trading.db) (`trade_ledger.py:223-225`).
- **Idempotency:** `record_fill` dedups by `fill_id` (`trade_ledger.py:199-202`); `trade_legs`
  leg insert is `INSERT OR IGNORE` on leg_id; `trades_analytics` uses `ON CONFLICT(trade_id)
  DO UPDATE`. Duplicate signal/order/fill/exit events cannot double-count.
- **Determinism:** rebuild = clear derived → re-run canonical lifecycle → identical records.
  Verified across the deterministic replay/idempotency tests (`test_master_parity_audit.py`,
  `test_audit_reversal_sl_all_strategies.py`, adversarial duplicate-fill tests).
- **P&L authority:** single source (canonical trades/fills). Derived `net_pnl` mirrors it;
  reconciliation checks equate them (`test_audit_reversal_sl_all_strategies.py` #62/#63).

## 5. Analytics after Reversal (#62) / SL (#63)

- **Reversal:** old trade = CLOSED once, new trade = OPEN; the reversal signal appears as
  old.exit_signal_id AND new.entry_signal_id; strategy matrix counts each trade once; realized
  P&L counted once, unrealized not folded in. Verified by `test_audit_reversal_sl_all_strategies.py`
  (28/28 PASS) and reversal tests.
- **SL:** one trade, CLOSED, correct P&L, `exit_signal_id IS NULL`, no duplicate analytics row.
  Verified (see `TRADE_LIFECYCLE_VERIFICATION.md`).

## 6. Checklist #72 (analytics rebuild)

The derived tables are rebuildable (`DERIVED_TABLES`, `persistence/database.py:52-60`).
Rebuild procedure documented in `ANALYTICS_MIGRATION_REPORT.md` §4. Re-running seeded scenarios
twice yields identical results (test-proven).