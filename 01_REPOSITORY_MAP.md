# 01 - Repository Map: Complete File Inventory

**System:** MCX-TRADER (Gold/Silver Live Trading)
**Audit Date:** 2026-09-04
**Scope:** All production source files under `Gold Silver live trading/`

---

## Directory Structure

```
Gold Silver live trading/
├── trading_engine.py          # Main orchestrator
├── main.py                    # Entry point (launches server)
├── analytics.db               # Analytics database (SQLite)
├── trading.db                 # Trading database (SQLite)
├── system_state.json          # Persisted engine state (JSON)
│
├── config/
│   └── __init__.py            # Config loader (YAML/JSON)
│
├── core/
│   ├── __init__.py
│   ├── timeframe_engine.py    # Bar dataclass, BarState enum
│   ├── risk_engine.py         # Portfolio-level risk checks
│   ├── market_status.py       # Market session lifecycle state machine
│   ├── safe_mode.py           # Safe mode manager
│   ├── trade_close.py         # Atomic trade close orchestrator
│   ├── fill_dedup.py          # Fill deduplication (in-memory + DB)
│   └── candle_fetcher.py      # REST candle fetcher (periodic)
│
├── indicators/
│   ├── __init__.py
│   ├── dema.py                # DEMA (Double Exponential Moving Average)
│   ├── atr.py                 # ATR (Average True Range, Wilder smoothed)
│   └── dema_atr.py            # Combined DEMA-ATR indicator
│
├── htf/
│   ├── __init__.py
│   ├── backtest_style_htf.py  # HTF engine (backtest-style mapping)
│   └── confirmation.py        # Confirmation logic
│
├── strategies/
│   ├── __init__.py
│   ├── types.py               # Signal, SignalType, StrategyState, PendingEntry
│   ├── base_dema_strategy.py  # Base DEMA-ATR strategy (state machine)
│   ├── gold/
│   │   └── __init__.py        # GoldStrategy01-04
│   └── silver/
│       └── __init__.py        # SilverStrategy01-04
│
├── execution/
│   ├── __init__.py
│   ├── paper_broker.py        # PaperExecutionEngine, Order, Fill
│   ├── order_manager.py       # OrderManager (centralized)
│   └── fee_model.py           # MCXFeeModel, FeeBreakdown
│
├── portfolio/
│   ├── __init__.py
│   ├── position_manager.py    # PositionManager, Position, PositionSide
│   ├── pnl.py                 # PNLEngine, PnLSnapshot
│   └── account.py             # AccountEngine, AccountSnapshot
│
├── persistence/
│   ├── __init__.py
│   └── manager.py             # PersistenceManager (trading.db + system_state.json)
│
├── analytics/
│   ├── __init__.py
│   ├── schema.py              # Analytics DB schema (trades_analytics, trade_events, etc.)
│   ├── event_store.py         # EventStore (append-only event log)
│   ├── trade_ledger.py        # TradeLedger (authoritative trade lifecycle)
│   ├── performance.py         # PerformanceEngine (analytics calculations)
│   ├── reconciliation.py      # Analytics reconciliation
│   └── routes.py              # FastAPI analytics REST routes
│
├── dashboard/
│   ├── __init__.py
│   ├── server.py              # FastAPI app + lifespan + WebSocket
│   ├── event_bus.py           # In-memory event bus (50k cap)
│   ├── ws_manager.py          # WebSocket connection manager
│   └── routes/                # REST API route modules (overview, strategies, etc.)
│
├── monitoring/
│   ├── __init__.py
│   └── health.py              # HealthMonitor, ComponentStatus
│
├── notifications/
│   ├── __init__.py
│   ├── telegram_router.py     # Telegram notifications
│   └── telegram_client.py     # Telegram API client
│
├── data/
│   ├── __init__.py
│   └── dhan/                  # Dhan API adapter
│       ├── __init__.py
│       ├── adapter.py         # DhanDataAdapter (main interface)
│       ├── websocket_client.py # WebSocket client
│       ├── rest_client.py     # REST client (candle fetching)
│       └── instrument_mapper.py # Instrument ID mapping
│
├── tests/                     # Test suite
│   ├── conftest.py
│   ├── test_replay.py
│   ├── test_regressions.py
│   ├── test_deep_verification.py
│   └── fresh_audit/           # Comprehensive audit tests
│       ├── test_full_pipeline_audit.py
│       ├── test_full_deep_architecture.py
│       ├── test_financial_core.py
│       ├── test_edge_cases.py
│       └── ... (10+ test files)
│
└── dashboard-ui/              # Frontend (Vue/React built assets)
    └── dist/
```

---

## File-by-File Inventory

| # | File Path | Purpose | Lines | Dependencies | DB Tables | API Routes | State |
|---|-----------|---------|-------|--------------|-----------|------------|-------|
| 1 | `trading_engine.py` | Main orchestrator - wires all components, processes signals/fills | 1458 | ALL modules | None directly | None | In-memory engine state |
| 2 | `main.py` | Entry point - starts FastAPI server | ~20 | server, trading_engine | None | None | None |
| 3 | `config/__init__.py` | Configuration loader | ~50 | yaml/json | None | None | Config dict |
| 4 | `core/trade_close.py` | Atomic trade close (persist-before-memory) | 297 | persistence, pnl, account, risk, telegram, trade_ledger | trades, fills, trades_analytics | None | Orchestrator only |
| 5 | `core/risk_engine.py` | Portfolio risk checks (positions, margin, drawdown, kill switch) | 150 | None | None | None | daily_pnl, peak_equity, kill_switch |
| 6 | `core/market_status.py` | Market session lifecycle (OVERNIGHT→LIVE_TRADING→etc) | 309 | None | None | None | MarketState, EngineStatus, DataStatus |
| 7 | `core/safe_mode.py` | Safe mode manager (reason-based) | 157 | market_status | None | None | Active reasons dict |
| 8 | `core/fill_dedup.py` | Fill deduplication (in-memory + DB) | 137 | None | processed_fills | None | Set of processed fill_ids |
| 9 | `core/candle_fetcher.py` | REST candle fetcher (periodic polling) | 268 | data_adapter, market_status | None | None | _last_fetched dict |
| 10 | `core/timeframe_engine.py` | Bar dataclass, BarState enum | ~40 | None | None | None | None (dataclass) |
| 11 | `indicators/dema_atr.py` | Combined DEMA-ATR indicator | 208 | dema.py, atr.py, numpy | None | None | Indicator state |
| 12 | `indicators/dema.py` | DEMA (Double EMA) calculation | ~80 | numpy | None | None | EMA state |
| 13 | `indicators/atr.py` | ATR (Wilder smoothed) calculation | ~60 | numpy | None | None | ATR state |
| 14 | `htf/backtest_style_htf.py` | HTF engine - backtest-style searchsorted mapping | ~200 | core.timeframe_engine | None | None | Engine states per instrument |
| 15 | `strategies/types.py` | Shared types (Signal, StrategyState, PendingEntry) | 104 | None | None | None | None (dataclasses/enums) |
| 16 | `strategies/base_dema_strategy.py` | Base DEMA-ATR strategy (state machine, crossover logic) | 718 | strategies.types, htf | None | None | Strategy state (side, stop, pending) |
| 17 | `strategies/gold/__init__.py` | Gold strategy instances (01-04) | ~40 | base_dema_strategy | None | None | Inherits from base |
| 18 | `strategies/silver/__init__.py` | Silver strategy instances (01-04) | ~40 | base_dema_strategy | None | None | Inherits from base |
| 19 | `execution/paper_broker.py` | Paper execution (Order, Fill, slippage sim) | 303 | strategies.types | None | None | Orders dict, fills list, prices dict |
| 20 | `execution/order_manager.py` | Centralized order management | 123 | paper_broker | None | None | Pending signals, active orders |
| 21 | `execution/fee_model.py` | MCX fee model (brokerage, STT, exchange, SEBI, GST, stamp) | 103 | None | None | None | None (pure calculation) |
| 22 | `portfolio/position_manager.py` | Position tracking (open/closed, P&L marks) | 265 | execution.paper_broker | None | None | Positions dict, closed list |
| 23 | `portfolio/pnl.py` | P&L engine (realized + unrealized, running totals) | 172 | execution.paper_broker, fee_model, position_manager | None | None | Realized/ unrealized totals |
| 24 | `portfolio/account.py` | Account engine (capital, equity, margin) | 153 | None | None | None | Capital, margin, P&L |
| 25 | `persistence/manager.py` | Persistence layer (trading.db + system_state.json) | 341 | None | trades, orders, fills, events, account_snapshots | None | SQLite connection |
| 26 | `analytics/schema.py` | Analytics DB schema (8 tables, 13 indexes) | 308 | None | All analytics tables | None | None |
| 27 | `analytics/event_store.py` | Append-only event log | 147 | None | trade_events | None | Sequence counter |
| 28 | `analytics/trade_ledger.py` | Authoritative trade lifecycle | 478 | None | trades_analytics, trade_legs | None | Open trades dict |
| 29 | `analytics/performance.py` | Performance calculations (Sharpe, Sortino, drawdown, etc.) | ~400 | trade_ledger | trades_analytics | None | None |
| 30 | `analytics/routes.py` | Analytics REST API routes | 586 | performance, trade_ledger, event_store | trades_analytics | /api/analytics/* | None |
| 31 | `dashboard/server.py` | FastAPI app, lifespan, WebSocket, static serving | 398 | trading_engine, persistence, all routes | None | /api/*, /ws | EventBus, WsManager |
| 32 | `dashboard/event_bus.py` | In-memory event bus (50k cap) | ~80 | None | None | None | Events ring buffer |
| 33 | `dashboard/ws_manager.py` | WebSocket connection manager | ~60 | None | None | None | Connections dict |
| 34 | `monitoring/health.py` | Component health tracking | 123 | None | None | None | Component statuses |
| 35 | `notifications/telegram_router.py` | Telegram notification routing | ~100 | telegram_client | None | None | Stats (sent, errors) |
| 36 | `data/dhan/adapter.py` | Dhan API adapter (WebSocket + REST) | ~300 | websocket_client, rest_client, instrument_mapper | None | None | Connection state |
| 37 | `reconciliation/engine.py` | Cross-validation engine (DB vs memory) | 489 | persistence, position_manager, pnl, account, order_manager | orders, fills, trades (read-only) | None | None |

---

## Dependency Graph (Simplified)

```
trading_engine.py
├── config/
├── data/dhan/adapter.py
├── core/
│   ├── timeframe_engine.py
│   ├── risk_engine.py
│   ├── market_status.py
│   ├── safe_mode.py
│   ├── trade_close.py
│   ├── fill_dedup.py
│   └── candle_fetcher.py
├── indicators/dema_atr.py
├── htf/backtest_style_htf.py
├── strategies/base_dema_strategy.py
├── execution/
│   ├── paper_broker.py
│   ├── order_manager.py
│   └── fee_model.py
├── portfolio/
│   ├── position_manager.py
│   ├── pnl.py
│   └── account.py
├── persistence/manager.py
├── analytics/
│   ├── event_store.py
│   └── trade_ledger.py
├── monitoring/health.py
└── notifications/telegram_router.py
```

---

## Database Interactions

| Component | trading.db | analytics.db | system_state.json |
|-----------|-----------|-------------|-------------------|
| PersistenceManager | Read/Write (trades, orders, fills, events, snapshots) | — | Read/Write |
| EventStore | — | Write (trade_events) | — |
| TradeLedger | — | Read/Write (trades_analytics, trade_legs) | — |
| FillDeduplicator | Read/Write (processed_fills) | — | — |
| ReconciliationEngine | Read-only (orders, fills, trades) | — | — |
| Dashboard Server | — | Read-only (via analytics routes) | — |

---

## Key Architectural Notes

1. **Two separate databases**: `trading.db` (operational) and `analytics.db` (rich lifecycle)
2. **Two separate DB paths in different locations**: `trading.db` at project root AND at `data/db/trading.db` (potential confusion)
3. **No ORM used**: Raw SQL with sqlite3 throughout
4. **All state is in-memory**: DB is persistence layer, not primary source of truth for live decisions
5. **Paper-only execution**: PaperExecutionEngine with slippage simulation
6. **No external dependencies beyond**: fastapi, uvicorn, numpy, pandas, pyyaml
