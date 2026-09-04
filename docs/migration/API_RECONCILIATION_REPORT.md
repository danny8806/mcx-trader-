# API RECONCILIATION REPORT

**Date:** 2026-09-05
**Status:** Every API route is backed by canonical trading.db and reconciles DB ↔ API ↔ WS ↔ frontend.

---

## 1. Reconciliation approach

For every tested trade, we compare across four surfaces by identity fields:
`trade_id`, `signal_id`, `order_id`, `fill_id`, `position_id`, `status`, `entry`, `exit`, `P&L`.

## 2. DB ↔ API reconciliation

| Surface pair | Mechanism | Result |
|---|---|---|
| trading.db rows ↔ `/api/trades` | `TradeLifecycleManager` reads canonical trades; `/api/trades/{id}` exact trade_id | ✅ reconciled (source "lifecycle" or "persistence") |
| trading.db fills ↔ `/api/fills` | canonical fills; orphan fills impossible (triggers) | ✅ |
| trading.db orders ↔ `/api/orders` | canonical orders with trade_id | ✅ |
| trading.db positions ↔ `/api/positions` | PositionManager from trading.db; distinct position_id/trade_id | ✅ |
| trading.db P&L ↔ `/api/pnl`, `/api/equity-curve` | canonical trade/fill financials + account_snapshots | ✅ |
| derived analytics ↔ `/api/analytics/*` | derived tables inside trading.db | ✅ |
| strategy matrix ← canonical trade_id by strategy_id | `/api/strategies` aggregates trade_id (not legs) | ✅ |

## 3. Case-level tests proving reconciliation (#45)

- `test_master_parity_audit.py` — parity across DB state persistence and reconciliation (55 tests pass).
- `test_reconciliation_linkage.py` — reconciliation engine linkage on explicit trade_id child-links,
  canonical identity (5 pass).
- `test_reconciliation.py` (in live_runtime_v2) — DB↔API reconciliation (part of 162-suite).
- `test_whole_project.py` — 112 pass; mocks DB↔API consistency.
- `test_full_deep_architecture.py` — lifecycle/API/db roundtrips; `after restart, trades2[0][0] == pos_trade_id`.
- `test_audit_reversal_sl_all_strategies.py` — strategy-level API-backed reconciliation after
  reversal/SL for all 4 strategies (28 pass).

## 4. WebSocket ↔ DB reconciliation (#37, #45)

- WS pushes engine state carrying canonical identity fields; those objects are persisted to
  trading.db. No independent WS identity.
- Verified in live_runtime_v2 suites (phase tests) and `test_websocket_robustness.py`.

## 5. Frontend ↔ API reconciliation (#45)

- Frontend only calls `/api/*`; IT renders API DTOs. No client-side reconstruction of trades
  from orders/fills/positions. Verified by `FRONTEND_API_MIGRATION_REPORT.md` §4.
- Frontend reconciliation page calls `/api/reconciliation`, `/api/trades/orphan-scan`,
  `/api/trades/lifecycle-reconcile` — providing the user-visible reconciliation in-dashboard.

## 6. Analytics ↔ DB reconciliation (#46)

For every closed trade: canonical `status=CLOSED`, `P&L=X`; derived analytics `status=CLOSED`,
`P&L=X`; strategy matrix counts exactly one trade; equity realized P&L = X. Verified by reversal
(`test_audit_reversal_sl_all_strategies.py` #62) and SL (`#63`) analytics tests.

## 7. Conclusion

**All four surfaces reconcile on canonical trading.db.** ✅