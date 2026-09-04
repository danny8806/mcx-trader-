# FRONTEND API MIGRATION REPORT

**Date:** 2026-09-05
**Status:** All frontend data flows through the canonical REST/WS API backed by `trading.db`.
Zero direct DB access.

---

## 1. Architecture

```
FRONTEND (dashboard-ui React)
   │  fetch() / axios? — no, uses fetchJSON wrapper (lib/api.ts)
   │  WebSocket (/ws)
   ▼
REST API  → dashboard/routes/*  +  analytics/routes/*   (all backed by trading.db)
WebSocket → dashboard/ws + engine event bus
   ▼
trading.db (single canonical)
```

- **Frontend never opens a DB file.** Verification: `dashboard-ui/src` contains no
  `sqlite`, no `.db`, no file read of DB, no `fetch` to anything but `/api` and `/ws`. (Checklist
  #34 ✅)
- Frontend does not reconstruct trades client-side from orders+fills+positions — it renders the
  trade DTO returned by `/api/trades` (see Tradebook section). (Checklist #35 ✅)

## 2. REST API methods (`dashboard-ui/src/lib/api.ts`)

All methods call the canonical REST API; no endpoint targets an old standalone analytics DB.

| Method | Endpoint | Backing (verified in COMPLETE_API_MIGRATION_REPORT.md) |
|---|---|---|
| health | `/api/health` | engine health |
| overview | `/api/overview` | canonical aggregates |
| overviewInstrument | `/api/overview/{instrument}` | canonical |
| strategies | `/api/strategies` | canonical (aggregates trade_id, not legs) |
| strategy | `/api/strategies/{id}` | canonical |
| strategyParams | `/api/strategies/{id}/parameters` | strategy config |
| controlStrategy | `/api/strategies/{id}/control` | engine control |
| positions | `/api/positions` | PositionManager (trading.db, separate position_id/trade_id) |
| position | `/api/positions/{id}` | canonical |
| orders | `/api/orders` | canonical orders (trade_id present) |
| fills | `/api/fills` | canonical fills |
| trades | `/api/trades` | canonical lifecycle / persistence |
| trade | `/api/trades/{id}` | exact trade_id lookup |
| pnl | `/api/pnl` | canonical trade/fill P&L |
| pnlInstrument | `/api/pnl/{instrument}` | canonical |
| equityCurve | `/api/equity-curve` | canonical realized P&L / account_snapshots |
| marketData | `/api/market-data` | market data cache |
| marketDataInstrument | `/api/market-data/{instrument}` | cache |
| risk | `/api/risk` | RiskEngine |
| healthSystem | `/api/health/system` | health monitor |
| indicators | `/api/indicators` | cache |
| indicatorsInstrument | `/api/indicators/{instrument}` | cache |
| htf | `/api/htf` | cache |
| htfInstrument | `/api/htf/{instrument}` | cache |
| alerts | `/api/alerts` | alerts store |
| reconciliation | `/api/reconciliation` | ReconciliationEngine (trading.db) |
| orphanScan | `/api/trades/orphan-scan` | lifecycle orphan scan (trading.db) |
| lifecycleReconcile | `/api/trades/lifecycle-reconcile` | lifecycle reconcile (trading.db) |
| settings | `/api/settings` | config |
| refreshSettings | `/api/settings/refresh` | config |
| audit | `/api/audit` | audit store |
| replayStatus | `/api/replay/status` | replay control |

## 3. Analytics-specific frontend calls

`components/strategies/StrategyDetail.tsx` calls **only** the migrated `/api/analytics/*` routes
(lines 233-237):
- `/api/analytics/strategies/{strategyId}`
- `/api/analytics/strategies/{strategyId}/trades?limit=5`
- `/api/analytics/strategies/{strategyId}/equity`
- `/api/analytics/strategies/{strategyId}/drawdown`
- `/api/analytics/events?strategy_id=...&limit=20`

These routes are initialized from the **canonical trading.db** (`dashboard/server.py:35-36`),
so all strategy-matrix/equity/drawdown/event values originate from trading.db (checklist #36 ✅).

## 4. Page → data source map

| Page | Components | Data source |
|---|---|---|
| Tradebook (Trades.tsx) | api.trades() / api.trade() | `/api/trades` — canonical trade DTO (trade_id, entry/exit signal, orders, fills, P&L). Does NOT rebuild trades client-side. |
| Open Positions (Positions.tsx) | api.positions() | `/api/positions` |
| Orders (Orders.tsx) | api.orders() / api.fills() | `/api/orders`, `/api/fills` |
| P&L (Pnl.tsx) | api.pnl(), api.equityCurve() | `/api/pnl`, `/api/equity-curve` |
| Strategy Matrix (StrategyMatrix.tsx) | api.strategies(), StrategyDetail analytics calls | `/api/strategies` + `/api/analytics/*` (trading.db) |
| Analytics / strategy detail | StrategyDetail.tsx | `/api/analytics/strategies/*` (trading.db) |
| Equity Curve | EquityCurveChart.tsx, StrategyEquityChart.tsx | `/api/equity-curve`, `/api/analytics/strategies/{id}/equity` |
| Reconciliation | Reconciliation.tsx | `/api/reconciliation`, `/api/trades/orphan-scan`, `/api/trades/lifecycle-reconcile` |
| Overview | Overview.tsx | `/api/overview` |
| Market Data | MarketData.tsx | `/api/market-data` |
| Indicators | Indicators.tsx | `/api/indicators`, `/api/htf` |
| Risk | Risk.tsx | `/api/risk` |
| Health | Health.tsx | `/api/health/system` |
| Alerts | Alerts.tsx | `/api/alerts` |
| Audit | AuditLog.tsx | `/api/audit` |
| Settings | Settings.tsx | `/api/settings` |
| LiveTrading | DataProvider.tsx (WS) | WebSocket + overview/positions/trades |

## 5. WebSocket (checklist #37)

- Endpoint: `/ws` (dashboard WebSocket via `dashboard/ws_manager.py` + `dashboard/server.py`).
- Payloads carry canonical identity: `trade_id`, `position_id`, `order_id`, `fill_id`,
  `signal_id` from engine lifecycle/positions (trading.db-backed memory state). WebSocket does
  NOT create independent identities; it pushes engine state that is persisted to trading.db.
- Verified: WS message types (tick/position/trade/order/account etc.) are emitted from the live
  engine objects whose state reconciles to the canonical DB.

## 6. Checklist #71 (frontend final check)

All frontend surfaces (Tradebook, Open Positions, Orders, Fills, P&L, Strategy Matrix, Analytics,
Equity Curve, Trade Details) use the new API architecture backed by trading.db. No browser
request targets an unmigrated analytics endpoint. ✅