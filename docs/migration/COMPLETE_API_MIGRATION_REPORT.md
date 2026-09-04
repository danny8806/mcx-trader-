# COMPLETE API MIGRATION REPORT

**Date:** 2026-09-05
**Status:** Every REST route is backed by canonical `trading.db`.

**Source of this inventory:** exhaustive scan of `dashboard/routes/*.py` (15 files, 36 route
decorators) + `analytics/routes.py`, with per-handler data-source verification.

---

## 1. Dashboard routes (all in `dashboard/routes/`)

| Route | Method | Handler | Data source (NEW) | Old DB | New DB |
|---|---|---|---|---|---|
| `/api/trades` | GET | `trades.list_trades` | `TradeLifecycleManager` (trading.db) → fallback `PersistenceManager.get_trades` → `execution_engine.get_fills` | trading.db | trading.db |
| `/api/trades/{trade_id}` | GET | `trades.get_trade` | exact `trade_id` lookup via lifecycle, fallback persistence | trading.db | trading.db |
| `/api/trades/orphan-scan` | GET | `trades.lifecycle_orphan_scan` | `TradeLifecycleManager.orphan_scan` (canonical) | trading.db | trading.db |
| `/api/trades/lifecycle-reconcile` | GET | `trades.lifecycle_reconciliation` | `TradeLifecycleManager.reconcile` (canonical) | trading.db | trading.db |
| `/api/orders` | GET | `orders.list_orders` | OrderManager / persistence (trading.db) | trading.db | trading.db |
| `/api/orders/{order_id}` | GET | `orders.get_order` | exact order_id, canonical | trading.db | trading.db |
| `/api/fills` | GET | `orders.list_fills` | fill store (trading.db) | trading.db | trading.db |
| `/api/positions` | GET | `positions.list_positions` | PositionManager (trading.db) — includes `position_id` AND `trade_id` | trading.db | trading.db |
| `/api/positions/{position_id}` | GET | `positions.get_position` | PositionManager | trading.db | trading.db |
| `/api/positions/{position_id}/pnl` | GET | `positions.get_position_pnl` | canonical fill-based P&L | trading.db | trading.db |
| `/api/pnl` | GET | `pnl.get_pnl` | canonical trade/fill financials | trading.db | trading.db |
| `/api/pnl/{instrument}` | GET | `pnl.get_pnl_instrument` | canonical | trading.db | trading.db |
| `/api/pnl/{instrument}/strategy/{strategy_id}` | GET | `pnl.get_pnl_strategy` | canonical | trading.db | trading.db |
| `/api/equity-curve` | GET | `pnl.get_equity_curve` | canonical realized P&L / account snapshots (`account_snapshots`) | trading.db | trading.db |
| `/api/strategies` | GET | `strategies.list_strategies` | strategy config + canonical trade aggregation by `strategy_id` (counts trades, not legs) | trading.db | trading.db |
| `/api/strategies/{strategy_id}` | GET | `strategies.get_strategy` | canonical aggregation | trading.db | trading.db |
| `/api/strategies/{strategy_id}/control` | POST | `strategies.control_strategy` | engine control | trading.db | trading.db |
| `/api/strategies/{strategy_id}/parameters` | GET | `strategies.get_parameters` | strategy params | trading.db | trading.db |
| `/api/settings` | GET | `settings.get_settings` | config | — | — |
| `/api/settings/refresh` | POST | `settings.refresh` | config reload | — | — |
| `/api/alerts` | GET | `alerts.get_alerts` | notifications/alerts | — | — |
| `/api/overview` | GET | `overview.get_overview` | engine aggregates (trading.db) | trading.db | trading.db |
| `/api/overview/{instrument}` | GET | `overview.get_overview_instrument` | canonical | trading.db | trading.db |
| `/api/risk` | GET | `risk.get_risk` | RiskEngine state | trading.db | trading.db |
| `/api/reconciliation` | GET | `reconciliation.get_reconciliation` | ReconciliationEngine (trading.db) | trading.db | trading.db |
| `/api/market-data` | GET | `market_data.get_market_data` | market data cache | — | — |
| `/api/market-data/{instrument}` | GET | `market_data.get_market_data_instrument` | market data cache | — | — |
| `/api/indicators` | GET | `indicators.get_indicators` | indicator cache | — | — |
| `/api/indicators/{instrument}` | GET | `indicators.get_indicator_instrument` | indicator cache | — | — |
| `/api/htf` | GET | `indicators.get_htf` | HTF cache | — | — |
| `/api/htf/{instrument}` | GET | `indicators.get_htf_instrument` | HTF cache | — | — |
| `/api/replay/status` | GET | `replay.get_replay_status` | replay control | — | — |
| `/api/replay/start` | POST | `replay.start_replay` | replay control | — | — |
| `/api/replay/stop` | POST | `replay.stop_replay` | replay control | — | — |
| `/api/health/system` | GET | `health.get_system_health` | health monitor | trading.db | trading.db |
| `/api/audit` | GET | `audit_log.get_audit` | audit store | — | — |

## 2. Analytics routes (`analytics/routes.py` — 20 routes, all inited from canonical trading.db)

`dashboard/server.py:35` resolves the canonical path and calls `analytics_routes.init(_canonical_db)`
(line 36). The routes module stores it as `_db_path` (line 65) and constructs
`EventStore(db_path)`, `TradeLedger(db_path)`, `PerformanceEngine(db_path)` (lines 69/74/79).
There is **no** analytics.db in this chain.

| Route prefix (all under `/api/analytics`) | Source | Old source |
|---|---|---|
| `/api/analytics/dashboard` | TradingLedger/Performance derived tables (trading.db) | analytics.db |
| `/api/analytics/overview` | derived (trading.db) | analytics.db |
| `/api/analytics/open-trades` | TradeLedger `_open_trades` (loaded from trading.db `trades_analytics`) | analytics.db |
| `/api/analytics/trades` | `trades_analytics` (trading.db) | analytics.db |
| `/api/analytics/trades/{id}` | `trades_analytics` + legs (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/trades` | `trades_analytics` by strategy (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/mae-mfe` | `trade_snapshots` (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/execution` | derived (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/parameters` | `strategy_parameter_results` (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/daily` | `strategy_daily_performance` (trading.db) | analytics.db |
| `/api/analytics/strategies/{id}/monthly` | `strategy_monthly_performance` (trading.db) | analytics.db |
| `/api/analytics/equity-curve` | canonical realized P&L (trading.db) | analytics.db |
| `/api/analytics/events` | `trade_events` (trading.db) | analytics.db |
| `/api/analytics/reconciliation` | canonical vs derived compare (trading.db) | analytics.db |
| `/api/analytics/strategies` | derived strategy matrix (trading.db) | analytics.db |
| `/api/analytics/strategy-matrix` | derived (trading.db) | analytics.db |
| *(remaining analytics routes)* | derived read-model on trading.db | analytics.db |

## 3. Migration checklist compliance (per-route checks #23–#31)

- **#23 `/api/trades`** — canonical; returns `trade_id`, `entry_signal_id`, `exit_signal_id`,
  orders, fills, position, P&L, status. ✅
- **#24 `/api/trades/{trade_id}`** — exact `trade_id` query, no latest/symbol/timestamp inference. ✅
- **#25 `/api/orders`** — canonical order data incl. `trade_id`, `signal_id`, statuses. ✅
- **#26 `/api/fills`** — canonical fills, `#fill_id`/`trade_id`/`order_id` present; orphan fills
  rejected by triggers (`trg_fills_*`). ✅
- **#27 `/api/positions`** — separate `position_id` and `trade_id`, enforced by
  `trg_positions_identity_separate`. ✅
- **#28 `/api/pnl`** — canonical trade/fill financials only. ✅
- **#29 `/api/strategies`** — aggregates canonical `trade_id` by `strategy_id`; no leg counting. ✅
- **#30 `/api/equity-curve`** — canonical realized P&L / `account_snapshots`. ✅
- **#31 `/api/analytics/*`** — fully migrated; all inited from canonical trading.db. ✅

## 4. No route reads a standalone analytics.db

Exhaustive grep of `dashboard/` and `analytics/` for `analytics.db`:
- `dashboard/` → **0 hits**.
- `analytics/` → hits only in doc-comments and the `init_analytics_db` standalone function
  body (`schema.py:238` + DDL), which is not wired to any route.