# MCX-TRADER: Complete System Architecture

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Directory Structure](#2-directory-structure)
3. [Data Layer](#3-data-layer)
4. [Core Engine Layer](#4-core-engine-layer)
5. [Strategy Layer](#5-strategy-layer)
6. [Execution Layer](#6-execution-layer)
7. [Portfolio Layer](#7-portfolio-layer)
8. [Indicator Layer](#8-indicator-layer)
9. [HTF Layer](#9-htf-layer)
10. [Persistence Layer](#10-persistence-layer)
11. [Dashboard Backend](#11-dashboard-backend)
12. [Dashboard Frontend](#12-dashboard-frontend)
13. [Monitoring & Notifications](#13-monitoring--notifications)
14. [Analytics](#14-analytics)
15. [Complete Data Flow](#15-complete-data-flow)
16. [State Machines](#16-state-machines)
17. [Startup Lifecycle](#17-startup-lifecycle)
18. [Trade Lifecycle](#18-trade-lifecycle)

---

## 1. System Overview

MCX-TRADER is a **paper-only** automated trading system for MCX commodity futures (GOLDM, SILVERM). It uses a dual-channel architecture: REST API for completed candles (source of truth) and WebSocket for live LTP only.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MCX-TRADER SYSTEM                                │
│                                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   GOLDM      │  │   GOLDM      │  │  SILVERM     │  │  SILVERM     │  │
│  │  gold_01     │  │  gold_02     │  │  silver_01   │  │  silver_02   │  │
│  │  5m→15m→1H   │  │  15m→15m→1H  │  │  15m→15m→1H  │  │  5m→15m→1H   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         └─────────────────┼──────────────────┼─────────────────┘          │
│                           │                  │                             │
│              ┌────────────┴──────────────────┴────────────┐               │
│              │          TRADING ENGINE (2306 lines)       │               │
│              │         Central Orchestrator               │               │
│              └────────────────────┬───────────────────────┘               │
│                                   │                                        │
│  ┌────────────────────────────────┼────────────────────────────────────┐  │
│  │                                │                                    │  │
│  ▼                                ▼                                    ▼  │
│ ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│ │  Data    │  │Execution │  │Portfolio │  │   Risk   │  │Persisten │  │
│ │ Adapter  │  │  Engine  │  │ Manager  │  │  Engine  │  │  ce Mgr  │  │
│ └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    DASHBOARD (FastAPI + React)                    │   │
│  │    Backend: 442 lines  │  Frontend: 3602 lines  │  16 pages     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Capital:** ₹12,00,000 total (₹3,00,000 per strategy)

---

## 2. Directory Structure

```
MCX-TRADER/
├── main.py                          (90 lines)   Entry point
├── trading_engine.py                (2306 lines) Central orchestrator
├── config/
│   ├── __init__.py                  (90 lines)   Config singleton
│   └── settings.json                (151 lines)  All configuration
├── data/
│   ├── dhan/
│   │   ├── adapter.py               (331 lines)  Data adapter facade
│   │   ├── websocket_client.py      (315 lines)  WebSocket feed
│   │   ├── rest_client.py           (527 lines)  REST API client
│   │   └── instrument_mapper.py     (89 lines)   Security ID mapping
│   ├── dhan_token.json                           JWT token cache
│   ├── system_state.json                        Engine state snapshot
│   └── db/
│       ├── trading.db                           SQLite (canonical)
│       └── analytics.db                         SQLite (analytics)
├── core/
│   ├── timeframe_engine.py          (168 lines)  Bar dataclass + aggregator
│   ├── candle_fetcher.py            (335 lines)  REST candle poller
│   ├── market_status.py             (360 lines)  Market session state machine
│   ├── risk_engine.py               (150 lines)  Portfolio risk management
│   ├── fill_dedup.py                (148 lines)  Fill deduplication
│   ├── safe_mode.py                 (157 lines)  Degraded mode manager
│   ├── lifecycle.py                 (1062 lines) Trade lifecycle authority
│   └── trade_close.py               (361 lines)  Atomic trade close
├── indicators/
│   ├── dema.py                      (125 lines)  Double EMA
│   ├── atr.py                       (159 lines)  Wilder ATR
│   └── dema_atr.py                  (206 lines)  Combined DEMA+ATR bands
├── htf/
│   ├── confirmation.py              (14 lines)   HTFMappedValue dataclass
│   └── backtest_style_htf.py        (218 lines)  HTF mapping engine
├── strategies/
│   ├── types.py                     (97 lines)   Shared type vocabulary
│   ├── base_dema_strategy.py        (743 lines)  Core strategy state machine
│   ├── gold/__init__.py             (50 lines)   GoldStrategy01-04
│   └── silver/__init__.py           (50 lines)   SilverStrategy01-04
├── execution/
│   ├── paper_broker.py              (335 lines)  Simulated broker
│   ├── order_manager.py             (140 lines)  Signal→Order orchestrator
│   └── fee_model.py                 (103 lines)  MCX fee calculator
├── portfolio/
│   ├── position_manager.py          (345 lines)  Position lifecycle
│   ├── pnl.py                       (172 lines)  P&L calculation
│   └── account.py                   (153 lines)  Capital & margin
├── persistence/
│   ├── database.py                  (788 lines)  Canonical SQLite layer
│   └── manager.py                   (507 lines)  JSON + SQLite persistence
├── dashboard/
│   ├── server.py                    (442 lines)  FastAPI + WebSocket
│   ├── event_bus.py                              Event broadcasting
│   ├── ws_manager.py                             WebSocket connection mgmt
│   └── routes/                                   REST API endpoints
├── dashboard-ui/src/
│   ├── App.tsx                      (48 lines)   Route definitions
│   ├── store/DataProvider.tsx       (430 lines)  State management
│   ├── lib/api.ts                   (73 lines)   API client
│   ├── lib/utils.ts                 (91 lines)   Formatters
│   ├── components/layout/                        Sidebar, TopBar
│   └── pages/                                    16 page components
├── monitoring/
│   └── health.py                    (124 lines)  Component health tracking
├── notifications/
│   ├── telegram_router.py           (81 lines)   Alert routing
│   ├── telegram_client.py                        HTTP Telegram client
│   └── telegram_formatter.py                    Message formatting
├── analytics/
│   ├── event_store.py                            Event persistence
│   └── trade_ledger.py                           Trade analytics projection
└── reconciliation/
    └── reconciliation.py                        Trade reconciliation
```

---

## 3. Data Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                       DATA LAYER ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  EXTERNAL SOURCES                                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────────────┐  │
│  │  Dhan REST API          │  │  Dhan WebSocket Feed            │  │
│  │  api.dhan.co/v2         │  │  wss://api-feed.dhan.co         │  │
│  │                         │  │                                 │  │
│  │  POST /charts/historical│  │  Binary Packets:                │  │
│  │  POST /charts/intraday  │  │  Code 4: sid|ltp|ltq|ltt|vol  │  │
│  │  POST /margincalculator │  │  Code 2: sid|ltp|ltt           │  │
│  └───────────┬─────────────┘  └──────────────┬──────────────────┘  │
│              │                                │                     │
│              ▼                                ▼                     │
│  ┌─────────────────────────┐  ┌─────────────────────────────────┐  │
│  │   DhanRESTClient        │  │   DhanWebSocketClient           │  │
│  │   (rest_client.py)      │  │   (websocket_client.py)        │  │
│  │                         │  │                                 │  │
│  │  • TokenBucket limiter  │  │  • Binary packet parser         │  │
│  │    (3.5 req/s, burst 3) │  │  • Dedup (sid|ltt|price key)   │  │
│  │  • JWT token management │  │  • Stale watchdog (60s)         │  │
│  │  • Auto-renew (PIN+TOTP)│  │  • Reconnect w/ backoff        │  │
│  │  • Retry w/ backoff     │  │  • Token reload on reconnect   │  │
│  │  • Scheduler (7AM + 6h) │  │                                 │  │
│  └───────────┬─────────────┘  └──────────────┬──────────────────┘  │
│              │                                │                     │
│              └──────────────┬─────────────────┘                     │
│                             ▼                                       │
│              ┌──────────────────────────┐                           │
│              │    DhanDataAdapter        │                           │
│              │    (adapter.py)           │                           │
│              │                           │                           │
│              │  Facade / Orchestrator    │                           │
│              │  • Tick normalization     │                           │
│              │  • IST→UTC conversion     │                           │
│              │  • Live LTP cache         │                           │
│              │  • Historical candle fetch│                           │
│              │  • Closed candle fetch    │                           │
│              │  • Reconciliation         │                           │
│              └───────────┬───────────────┘                           │
│                          │                                           │
│           ┌──────────────┴──────────────┐                           │
│           │                             │                            │
│           ▼                             ▼                            │
│  ┌─────────────────┐        ┌──────────────────────┐               │
│  │ on_tick callback │        │ REST candle methods   │               │
│  │ (live LTP only) │        │ (source of truth)    │               │
│  └────────┬────────┘        └──────────┬───────────┘               │
│           │                             │                            │
│           ▼                             ▼                            │
│  TradingEngine._on_tick()    TradingEngine._on_bar_closed()        │
│                                                                     │
│  Supporting:                                                        │
│  ┌──────────────────────────────────────────┐                      │
│  │ instrument_mapper.py                     │                      │
│  │ • INSTRUMENTS registry (symbol↔sid map)  │                      │
│  │ • InstrumentMeta dataclass               │                      │
│  │ • register_instrument() / get_instrument │                      │
│  └──────────────────────────────────────────┘                      │
│                                                                     │
│  Storage:                                                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ dhan_token.json  │  │ system_state.json│  │ trading.db      │   │
│  │ (JWT cache)      │  │ (engine snapshot)│  │ (canonical DB)  │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions
1. **REST = Source of Truth**: Candles come from REST, NOT built from WebSocket ticks
2. **WebSocket = LTP Only**: Used for live price display, P&L marking, and stop-loss monitoring
3. **Dual Token Renewal**: Daily at 7 AM IST + safety checks every 6 hours + on-demand on auth failure
4. **Stale Detection**: Separate watchdog thread checks every 15s, force-reconnects after 60s silence

---

## 4. Core Engine Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CORE ENGINE ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              TRADING ENGINE (trading_engine.py)             │   │
│  │              2306 lines, Central Orchestrator               │   │
│  │                                                             │   │
│  │  __init__() creates 18 subsystems:                         │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │MarketStatus │ │Data Adapter │ │CandleFetcher│          │   │
│  │  │(360 lines)  │ │(331 lines)  │ │(335 lines)  │          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │Indicators   │ │HTF Engine   │ │Strategies   │          │   │
│  │  │(DEMAATR x6) │ │(218 lines)  │ │(8 instances)│          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │PaperBroker  │ │OrderManager │ │PositionMgr  │          │   │
│  │  │(335 lines)  │ │(140 lines)  │ │(345 lines)  │          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │PNLEngine x4 │ │AccountEng x4│ │RiskEngine   │          │   │
│  │  │(172 lines)  │ │(153 lines)  │ │(150 lines)  │          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │HealthMonitor│ │TelegramRouter│ │FillDedup    │          │   │
│  │  │(124 lines)  │ │(81 lines)   │ │(148 lines)  │          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │   │
│  │  │SafeMode     │ │TradeClose   │ │Lifecycle    │          │   │
│  │  │(157 lines)  │ │(361 lines)  │ │(1062 lines) │          │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  KEY HANDLERS:                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ _on_tick(tick)         → Live LTP processing               │   │
│  │ _on_bar_closed(bar)    → REST candle processing            │   │
│  │ _process_signal(sig)   → Signal → Order → Fill             │   │
│  │ _on_fill(fill)         → Position open/close               │   │
│  │ _process_deferred_exit → Reversal at next bar open         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  SUBSYSTEM DETAILS:                                                 │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ MarketStatus (core/market_status.py, 360 lines)          │     │
│  │                                                           │     │
│  │ Three state machines:                                     │     │
│  │                                                           │     │
│  │ MarketState:                                              │     │
│  │  OVERNIGHT → PRE_MARKET → MARKET_OPEN → LIVE_TRADING     │     │
│  │  → MARKET_CLOSE → AFTER_MARKET → OVERNIGHT               │     │
│  │  (+ SAFE_MODE, HALTED)                                    │     │
│  │                                                           │     │
│  │ EngineStatus:                                             │     │
│  │  INITIALIZING → RESTORING → WARMING_UP → RECONCILING     │     │
│  │  → READY → TRADING (+ SAFE_MODE, HALTED, STOPPED)        │     │
│  │                                                           │     │
│  │ DataStatus:                                               │     │
│  │  NO_DATA → DISCONNECTED → STALE → CONNECTED              │     │
│  │                                                           │     │
│  │ Methods:                                                  │     │
│  │  • state (property) → lazy IST clock evaluation          │     │
│  │  • is_trading_allowed → LIVE_TRADING + TRADING + data    │     │
│  │  • should_fetch_candles → MARKET_OPEN or LIVE_TRADING    │     │
│  │  • enter_safe_mode(reason) / exit_safe_mode()            │     │
│  │  • snapshot() / restore()                                │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ CandleFetcher (core/candle_fetcher.py, 335 lines)        │     │
│  │                                                           │     │
│  │ Timer-driven REST candle poller:                          │     │
│  │ • Daemon thread, polls every 30s                         │     │
│  │ • 5m: clock-aligned (fetches last closed bucket)         │     │
│  │ • 15m/1h: native Dhan candles (NOT clock-aligned)       │     │
│  │ • Dedup via _last_fetched dict (24h pruning)             │     │
│  │ • Session-aware: skips during non-trading hours          │     │
│  │ • Produces Bar objects → _on_bar_closed callback         │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ RiskEngine (core/risk_engine.py, 150 lines)              │     │
│  │                                                           │     │
│  │ Check order logic (6 gates):                              │     │
│  │  1. Kill switch active? → BLOCK                          │     │
│  │  2. Max positions per strategy (1) → BLOCK               │     │
│  │  3. Max total positions (8) → BLOCK                      │     │
│  │  4. Margin required > available → BLOCK                  │     │
│  │  5. Daily loss limit → BLOCK + activate kill switch      │     │
│  │  6. Max drawdown % → BLOCK + activate kill switch        │     │
│  │                                                           │     │
│  │ Auto-reset daily P&L at IST day boundary                  │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ FillDeduplicator (core/fill_dedup.py, 148 lines)         │     │
│  │                                                           │     │
│  │ Two-phase dedup:                                          │     │
│  │  Phase 1: note_processed(fill_id) → in-memory lock       │     │
│  │  Phase 2: mark_processed(fill_id) → DB INSERT            │     │
│  │                                                           │     │
│  │ Closes crash window: fill marked processed only AFTER     │     │
│  │ all financial effects are applied                         │     │
│  │                                                           │     │
│  │ DB table: processed_fills (fill_id PK, processed_at)     │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ SafeModeManager (core/safe_mode.py, 157 lines)           │     │
│  │                                                           │     │
│  │ Multi-reason accumulation:                                │     │
│  │  • position_mismatch                                     │     │
│  │  • fill_ambiguity                                        │     │
│  │  • database_failure                                      │     │
│  │  • state_restore_failure                                 │     │
│  │  • market_data_uncertain                                 │     │
│  │  • persistence_failure                                   │     │
│  │  • order_state_uncertain                                 │     │
│  │  • reconciliation_failed                                 │     │
│  │                                                           │     │
│  │ ALL reasons must be cleared before exit (5s cooldown)    │     │
│  │ Blocks new entries, allows exits                         │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ TradeLifecycleManager (core/lifecycle.py, 1062 lines)    │     │
│  │                                                           │     │
│  │ THE ONLY place trade_id is born:                          │     │
│  │ create_trade_from_signal()                               │     │
│  │                                                           │     │
│  │ 5 identity maps (all in-memory):                         │     │
│  │  • _trades: trade_id → TradeContext                      │     │
│  │  • _signal_to_trade: signal_id → trade_id               │     │
│  │  • _order_to_trade: order_id → trade_id                 │     │
│  │  • _fill_to_trade: fill_id → trade_id                   │     │
│  │  • _position_to_trade: position_id → trade_id           │     │
│  │  • _pending_to_trade: pending_order_id → trade_id       │     │
│  │                                                           │     │
│  │ TradeContext dataclass (immutable identity):              │     │
│  │  • 40+ fields covering full lifecycle                    │     │
│  │  • entry_signal_id, exit_signal_id                      │     │
│  │  • entry/exit prices, timestamps, P&L                   │     │
│  │  • signal candle metadata (OHLC, DEMA-ATR values)       │     │
│  │                                                           │     │
│  │ Methods:                                                  │     │
│  │  • create_trade_from_signal() → THE birth point         │     │
│  │  • register_order/fill/position() → link lifecycle      │     │
│  │  • close_trade() → canonical close with P&L             │     │
│  │  • reverse_trade() → atomic old close + new create      │     │
│  │  • reconcile() → orphan detection + consistency check   │     │
│  │  • restore_from_db() → rebuild maps from DB             │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │ TradeCloseManager (core/trade_close.py, 361 lines)       │     │
│  │                                                           │     │
│  │ 8-step atomic close procedure:                           │     │
│  │  Step 0: Validate exit price (reject <=0, NaN, inf)      │     │
│  │  Step 1: Calculate P&L (pure, no side effects)           │     │
│  │  Step 2-3: Persist trade + exit fill (single txn)        │     │
│  │  Step 4: Close position in memory                        │     │
│  │  Step 5: Update account P&L (per-strategy + global)      │     │
│  │  Step 6: Update risk engine (daily P&L + peak equity)    │     │
│  │  Step 7: Record event + publish to dashboard             │     │
│  │  Step 8: Send Telegram notification                      │     │
│  │                                                           │     │
│  │ PRINCIPLE: Persistence BEFORE memory                      │     │
│  │ If DB write fails → return False, do NOT update memory   │     │
│  │ If memory update fails → recoverable from DB on restart  │     │
│  └───────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Strategy Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STRATEGY LAYER ARCHITECTURE                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │         BaseDEMAStrategy (strategies/base_dema_strategy.py) │   │
│  │         743 lines, Core Strategy State Machine              │   │
│  │                                                             │   │
│  │  State Machine:                                             │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │  FLAT                                               │   │   │
│  │  │    │ (crossover detected)                           │   │   │
│  │  │    ▼                                                │   │   │
│  │  │  PENDING_LONG / PENDING_SHORT                       │   │   │
│  │  │    │ (trigger broken by bar/tick)                   │   │   │
│  │  │    ▼                                                │   │   │
│  │  │  LONG_POSITION / SHORT_POSITION                     │   │   │
│  │  │    │ (stop hit OR reversal)                         │   │   │
│  │  │    ▼                                                │   │   │
│  │  │  EXIT_ORDER_SUBMITTED → FLAT                        │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  │                                                             │   │
│  │  Signal Logic:                                              │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │ LONG:                                               │   │   │
│  │  │  close > htf_dema_atr AND                           │   │   │
│  │  │  prev_close <= prev_htf_dema_atr AND                │   │   │
│  │  │  mid_15m < htf_1h                                  │   │   │
│  │  │                                                     │   │   │
│  │  │ SHORT:                                              │   │   │
│  │  │  close < htf_dema_atr AND                           │   │   │
│  │  │  prev_close >= prev_htf_dema_atr AND                │   │   │
│  │  │  mid_15m > htf_1h                                  │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  │                                                             │   │
│  │  Entry Model:                                               │   │
│  │  • Breakout-based pending entries                           │   │
│  │  • Crossover at bar T → arms pending at trigger=T.high     │   │
│  │  • Entry fills only when later bar crosses trigger          │   │
│  │  • Timeout: 50 bars                                        │   │
│  │                                                             │   │
│  │  Reversal Model:                                            │   │
│  │  • Opposite crossover on bar T while in position            │   │
│  │  → Exit current at T+1 OPEN (deferred)                     │   │
│  │  → Arm opposite as pending breakout                        │   │
│  │                                                             │   │
│  │  Stop Loss Model:                                           │   │
│  │  • Bar-level: exit fills at bar CLOSE                       │   │
│  │  • Tick-level: checked on every tick (real-time SL)         │   │
│  │  • Same-bar: entry + SL on same bar → exit at bar close    │   │
│  │                                                             │   │
│  │  Methods:                                                   │   │
│  │  • on_bar(bar, htf_mapped, fast_dema_atr, mid_mapped)     │   │
│  │  • on_tick(ltp, timestamp)                                  │   │
│  │  • _detect_signal() → crossover detection                  │   │
│  │  • _create_pending_signal() → arms breakout entry          │   │
│  │  • _check_pending_entry() → trigger check                  │   │
│  │  • _check_stop_loss() → SL detection                       │   │
│  │  • _create_reversal_signal() → exit + re-arm               │   │
│  │  • _consume_same_bar_stop() → same-bar SL exit             │   │
│  │  • snapshot() / restore()                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  CONCRETE STRATEGIES (thin wrappers):                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  GoldStrategy01:  GOLDM  5m fast → 15m mid → 1H HTF      │   │
│  │  GoldStrategy02:  GOLDM  15m fast → 15m mid → 1H HTF     │   │
│  │  GoldStrategy03:  GOLDM  5m fast → 15m mid → 1H HTF      │   │
│  │  GoldStrategy04:  GOLDM  5m fast → 15m mid → 1H HTF      │   │
│  │  SilverStrategy01: SILVERM 15m fast → 15m mid → 1H HTF   │   │
│  │  SilverStrategy02: SILVERM 5m fast → 15m mid → 1H HTF    │   │
│  │  SilverStrategy03: SILVERM 5m fast → 15m mid → 1H HTF    │   │
│  │  SilverStrategy04: SILVERM 5m fast → 15m mid → 1H HTF    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Type Vocabulary (strategies/types.py, 97 lines):                  │
│  • SignalType: LONG, SHORT, FLAT, REVERSAL                        │
│  • StrategyState: FLAT, PENDING_LONG/SHORT, LONG/SHORT_POSITION   │
│  • Signal dataclass: signal_id(UUID), instrument, trigger_price   │
│  • PendingEntry: signal, trigger_price, side, bars_pending        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Execution Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    EXECUTION LAYER ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Signal → OrderManager → PaperExecutionEngine → Fill               │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ OrderManager (execution/order_manager.py, 140 lines)       │   │
│  │                                                             │   │
│  │ submit_signal(signal):                                      │   │
│  │  1. Dedup check (strategy_id:instrument:timestamp)         │   │
│  │  2. Stale pending cleanup (>1 hour old)                    │   │
│  │  3. PaperExecutionEngine.create_order(signal) → Order      │   │
│  │  4. PaperExecutionEngine.submit_order(order) → Fill        │   │
│  │  5. Returns fills for engine to drain                      │   │
│  │                                                             │   │
│  │ DB Invariant: order MUST be persisted BEFORE draining fills │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PaperExecutionEngine (execution/paper_broker.py, 335 lines)│   │
│  │                                                             │   │
│  │ Paper-only execution (live trading explicitly blocked):     │   │
│  │ • Slippage: +1 tick BUY, -1 tick SELL                     │   │
│  │ • Latency: 100ms simulated sleep                           │   │
│  │ • Partial fills: NOT supported (ValueError if prob > 0)    │   │
│  │ • Max fills: 500 (pruned to 250)                           │   │
│  │                                                             │   │
│  │ Dataclasses:                                                │   │
│  │ • Order: order_id, strategy_id, instrument, side, qty,     │   │
│  │          price, state, fill_ids, trade_id, entry_signal_id │   │
│  │ • Fill: fill_id, order_id, instrument, side, qty, price,   │   │
│  │         timestamp, strategy_id, trade_id, entry_signal_id  │   │
│  │                                                             │   │
│  │ Thread-safe price updates via threading.Lock               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ MCXFeeModel (execution/fee_model.py, 103 lines)            │   │
│  │                                                             │   │
│  │ Fee structure (per round trip):                             │   │
│  │ • Brokerage: ₹20 per side (flat)                           │   │
│  │ • STT: 0.01% (on sell turnover)                            │   │
│  │ • Exchange: 0.0026% (both sides)                           │   │
│  │ • SEBI: 0.0001% (both sides)                               │   │
│  │ • GST: 18% (on brokerage + exchange + SEBI)                │   │
│  │ • Stamp Duty: 0.005% (on buy turnover)                    │   │
│  │                                                             │   │
│  │ Handles LONG and SHORT correctly (swaps buy/sell)          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Portfolio Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PORTFOLIO LAYER ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Fill → Position → P&L → Account                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PositionManager (portfolio/position_manager.py, 345 lines)  │   │
│  │                                                             │   │
│  │ Position dataclass:                                         │   │
│  │  • position_id, strategy_id, instrument, side              │   │
│  │  • quantity, average_entry, stop_price                     │   │
│  │  • current_mark, unrealized_pnl, realized_pnl             │   │
│  │  • margin, trade_id, entry/exit_signal_id                  │   │
│  │                                                             │   │
│  │ Methods:                                                    │   │
│  │  • open_position(fill, multiplier, stop_price) → Position  │   │
│  │  • close_position(position_id, fill, reason) → Position    │   │
│  │  • update_mark(price) → recalculates unrealized P&L        │   │
│  │  • update_marks(prices) → batch update all open positions   │   │
│  │                                                             │   │
│  │ Max closed: 500, pruned to 250                            │   │
│  │ Thread-safe via threading.Lock                             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PNLEngine (portfolio/pnl.py, 172 lines)                    │   │
│  │                                                             │   │
│  │ calculate_realized_pnl(entry, exit, multiplier):           │   │
│  │  LONG: gross = (exit - entry) × qty × mult                │   │
│  │  SHORT: gross = (entry - exit) × qty × mult               │   │
│  │  charges = MCXFeeModel.calculate(...)                      │   │
│  │  net = gross - charges                                     │   │
│  │  Returns: (gross, charges, net) — PURE, no side effects    │   │
│  │                                                             │   │
│  │ record_trade(gross, charges, net):                          │   │
│  │  Mutates running totals (_wins, _losses, _trade_count)     │   │
│  │                                                             │   │
│  │ Separation: calculate() is pure, record() mutates state    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ AccountEngine (portfolio/account.py, 153 lines)             │   │
│  │                                                             │   │
│  │ Per-strategy capital: ₹3,00,000                            │   │
│  │ Global capital: ₹12,00,000                                 │   │
│  │                                                             │   │
│  │ Key properties:                                            │   │
│  │  • equity = starting_capital + realized + unrealized       │   │
│  │  • available_margin = equity - used_margin                 │   │
│  │  • can_open_position(margin) → boolean                     │   │
│  │                                                             │   │
│  │ Margin management:                                         │   │
│  │  • block_margin(amount) → bool                             │   │
│  │  • release_margin(amount)                                  │   │
│  │  • margin_per_trade_pct: 6.5%                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. Indicator Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INDICATOR LAYER ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  DEMA (125 lines)                                                   │
│  ├── Double Exponential Moving Average                              │
│  ├── Formula: DEMA = 2 × EMA(close, 3) - EMA(EMA(close, 3), 3)  │
│  ├── Incremental, stateful (_ema1, _ema2)                          │
│  └── calculate_batch() for bulk computation                        │
│                                                                     │
│  ATR (159 lines)                                                    │
│  ├── Wilder-smoothed Average True Range                             │
│  ├── TR = max(H-L, |H-prevC|, |L-prevC|)                         │
│  ├── Alpha = 1/period (Wilder smoothing)                           │
│  └── First ATR = simple average of first `period` TR values        │
│                                                                     │
│  DEMAATR (206 lines) — THE CORE INDICATOR                          │
│  ├── Composed of DEMA + ATR                                        │
│  ├── upper = DEMA + ATR × factor                                   │
│  ├── lower = DEMA - ATR × factor                                   │
│  ├── Recursive band clamp: output ∈ [lower, upper]                 │
│  │   from previous output (prevents indicator jumps)               │
│  ├── update(open, high, low, close) → float                       │
│  └── calculate_batch() for bulk computation                        │
│                                                                     │
│  Per instrument × timeframe:                                        │
│  GOLDM:5m, GOLDM:15m, GOLDM:1h (3 instances)                     │
│  SILVERM:5m, SILVERM:15m, SILVERM:1h (3 instances)               │
│  Total: 6 DEMAATR instances                                        │
│                                                                     │
│  Parameters: DEMA period=3, ATR period=6, ATR factor=1.0          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. HTF Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HTF LAYER ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  BacktestStyleHTFEngine (htf/backtest_style_htf.py, 218 lines)     │
│                                                                     │
│  Purpose: Map higher-timeframe (1H, 15m) DEMA-ATR values to        │
│  fast-timeframe (5m, 15m) bars using backtest-compatible logic.    │
│                                                                     │
│  Mapping Algorithm:                                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ target_close = fast_bar.end_ts                              │   │
│  │ idx = bisect_right(htf_state.end_times, target_close) - 1  │   │
│  │ return htf_state.values[idx]                                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│  (EXACT backtest logic: np.searchsorted replacement)               │
│                                                                     │
│  Methods:                                                           │
│  • register(instrument, htf_timeframe, params)                    │
│  • on_htf_bar_closed(bar) → updates DEMAATR, stores value         │
│  • map_to_fast_bar(fast_bar, fast_timeframe) → HTFMappedValue     │
│  • map_mid_to_fast_bar(fast_bar, fast_timeframe) → HTFMappedValue │
│  • load_batch_htf(instrument, htf_timeframe, bars)                │
│  • snapshot() / restore()                                          │
│                                                                     │
│  HTFMappedValue dataclass (htf/confirmation.py, 14 lines):        │
│  • htf_value: float (current HTF DEMA-ATR)                        │
│  • prev_htf_value: float (previous HTF DEMA-ATR)                  │
│  • htf_confirmed: bool (passed warmup)                            │
│  • htf_source_timestamp: float (when HTF bar closed)              │
│                                                                     │
│  Registered engines:                                                │
│  • GOLDM:1H → 1H signal line                                      │
│  • GOLDM:15m → 15m confirmation line                              │
│  • SILVERM:1H → 1H signal line                                    │
│  • SILVERM:15m → 15m confirmation line                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Persistence Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PERSISTENCE LAYER ARCHITECTURE                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Database (persistence/database.py, 788 lines)               │   │
│  │                                                             │   │
│  │ Central SQLite access — ONE canonical trading.db            │   │
│  │                                                             │   │
│  │ Features:                                                   │   │
│  │ • Process-wide shared connection registry                   │   │
│  │ • Single write lock per DB file (no concurrent contention) │   │
│  │ • PRAGMA foreign_keys=ON on every connection               │   │
│  │ • WAL journal, busy_timeout=30s                            │   │
│  │ • Real transactions via BEGIN IMMEDIATE / COMMIT / ROLLBACK│   │
│  │ • Versioned schema (SCHEMA_VERSION = 2)                    │   │
│  │ • Idempotent migrations (ALTER TABLE ADD COLUMN)           │   │
│  │                                                             │   │
│  │ 13 Canonical Tables:                                       │   │
│  │  signals, trades, trade_signal_link, pending_orders,       │   │
│  │  orders, fills, positions, trade_events, processed_fills,  │   │
│  │  account_snapshots, events, quarantine_records,             │   │
│  │  system_metadata                                           │   │
│  │                                                             │   │
│  │ 7 Derived Tables (rebuildable from canonical):             │   │
│  │  trades_analytics, trade_legs, trade_snapshots,            │   │
│  │  strategy_daily_performance, strategy_monthly_performance, │   │
│  │  strategy_parameter_results, strategy_performance_snapshots│   │
│  │                                                             │   │
│  │ 10 Integrity Triggers (FK enforcement on legacy tables):   │   │
│  │  trg_trades_entry_signal_required, trg_trades_entry_exists,│   │
│  │  trg_orders_trade_required, trg_orders_trade_exists,       │   │
│  │  trg_fills_lineage_required, trg_fills_trade_exists,       │   │
│  │  trg_fills_order_exists, trg_trade_signal_link_*,          │   │
│  │  trg_positions_identity_separate                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PersistenceManager (persistence/manager.py, 507 lines)     │   │
│  │                                                             │   │
│  │ Dual persistence:                                           │   │
│  │  • JSON: system_state.json (atomic writes)                 │   │
│  │  • SQLite: trading.db (via Database class)                 │   │
│  │                                                             │   │
│  │ Methods:                                                    │   │
│  │  • save_state(state) → atomic JSON write                   │   │
│  │  • load_state() → JSON read                                │   │
│  │  • save_trade(trade) → INSERT OR UPDATE                    │   │
│  │  • save_order(order) → INSERT OR UPDATE                    │   │
│  │  • save_fill(fill) → INSERT OR IGNORE                      │   │
│  │  • save_signal(signal_data) → INSERT OR IGNORE             │   │
│  │  • save_trade_and_fill(trade, fill) → single transaction   │   │
│  │  • save_event(event) → audit log                           │   │
│  │  • get_fill(fill_id) → DB-backed idempotency guard        │   │
│  │                                                             │   │
│  │ Thread safety: shared_path_lock (re-entrant)               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  What gets persisted:                                               │
│  ┌──────────────────┬──────────────────┬───────────────────────┐  │
│  │ What             │ Where            │ When                  │  │
│  ├──────────────────┼──────────────────┼───────────────────────┤  │
│  │ System state     │ system_state.json│ Every 60s + shutdown  │  │
│  │ Trades           │ trading.db       │ On lifecycle event    │  │
│  │ Fills            │ trading.db       │ On fill               │  │
│  │ Orders           │ trading.db       │ On order submit       │  │
│  │ Signals          │ trading.db       │ On signal             │  │
│  │ Trade events     │ trading.db       │ On state transition   │  │
│  │ Fill dedup       │ trading.db       │ After financial effects│ │
│  │ Analytics        │ trading.db       │ On fill open/close    │  │
│  │ Indicators/HTF   │ NOT persisted   │ Recomputed from REST  │  │
│  └──────────────────┴──────────────────┴───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Dashboard Backend

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD BACKEND ARCHITECTURE                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  FastAPI server (dashboard/server.py, 442 lines)                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Startup:                                                    │   │
│  │  1. Create FastAPI app with CORS middleware                 │   │
│  │  2. Create EventBus (50K events) + WS ConnectionManager    │   │
│  │  3. Create PersistenceManager + TradingEngine              │   │
│  │  4. Wire engine → persistence → EventBus                   │   │
│  │  5. Register all route modules                              │   │
│  │  6. Start engine (triggers full startup lifecycle)          │   │
│  │  7. Start background tasks (state broadcast, health check) │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  REST API Endpoints (37+ endpoints):                               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ GET  /api/health              → System health status       │   │
│  │ GET  /api/overview            → Portfolio summary          │   │
│  │ GET  /api/overview/:inst      → Per-instrument overview    │   │
│  │ GET  /api/strategies          → All strategy states        │   │
│  │ GET  /api/strategies/:id      → Strategy detail            │   │
│  │ POST /api/strategies/:id/control → Enable/disable strategy │   │
│  │ GET  /api/positions           → Open/closed positions      │   │
│  │ GET  /api/orders              → Order history              │   │
│  │ GET  /api/fills               → Fill records               │   │
│  │ GET  /api/trades              → Trade history              │   │
│  │ GET  /api/pnl                 → P&L breakdown              │   │
│  │ GET  /api/pnl/:inst           → Per-instrument P&L        │   │
│  │ GET  /api/equity-curve        → Equity curve data          │   │
│  │ GET  /api/market-data         → Live LTP + tick counts     │   │
│  │ GET  /api/market-data/:inst   → Per-instrument market data │   │
│  │ GET  /api/risk                → Risk engine state          │   │
│  │ GET  /api/indicators          → DEMAATR values             │   │
│  │ GET  /api/indicators/:inst    → Per-instrument indicators  │   │
│  │ GET  /api/htf                 → HTF values                 │   │
│  │ GET  /api/htf/:inst           → Per-instrument HTF         │   │
│  │ GET  /api/health/system       → Component health           │   │
│  │ GET  /api/reconciliation      → Trade reconciliation       │   │
│  │ GET  /api/alerts              → Alert history              │   │
│  │ GET  /api/settings            → Current config             │   │
│  │ POST /api/settings/refresh    → Reload config              │   │
│  │ GET  /api/audit               → Audit log                  │   │
│  │ GET  /api/replay/status       → Replay engine status       │   │
│  │ GET  /api/analytics/strategies/:id → Analytics detail      │   │
│  │ GET  /api/analytics/strategies/:id/trades → Trade analytics│   │
│  │ GET  /api/analytics/strategies/:id/equity → Equity curve   │   │
│  │ GET  /api/analytics/strategies/:id/drawdown → Drawdown     │   │
│  │ GET  /api/analytics/events    → Event stream               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  WebSocket (/ws):                                                   │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ • Broadcasts engine_state every 0.5s to all connected clients│  │
│  │ • Channels: ["all"]                                         │   │
│  │ • Message types: engine_state, events                       │   │
│  │ • ConnectionManager tracks all active WS connections        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Supporting:                                                        │
│  • EventBus (dashboard/event_bus.py) — in-memory event ring buffer │
│  • ConnectionManager (dashboard/ws_manager.py) — WS connections   │
│  • API key gate (optional, from config)                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 12. Dashboard Frontend

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD FRONTEND ARCHITECTURE                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  React + TypeScript + Vite (3602 lines total)                      │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ App.tsx (48 lines) — Route Definitions                      │   │
│  │                                                             │   │
│  │ Routes:                                                     │   │
│  │  /             → Overview       (318 lines)                │   │
│  │  /live         → LiveTrading    (167 lines)                │   │
│  │  /strategies   → Strategies     (54 lines)                 │   │
│  │  /matrix       → StrategyMatrix (683 lines)                │   │
│  │  /positions    → Positions      (123 lines)                │   │
│  │  /orders       → Orders         (45 lines)                 │   │
│  │  /trades       → Trades         (242 lines)                │   │
│  │  /pnl          → Pnl            (75 lines)                 │   │
│  │  /risk         → Risk           (69 lines)                 │   │
│  │  /market-data  → MarketData     (59 lines)                 │   │
│  │  /indicators   → Indicators     (69 lines)                 │   │
│  │  /reconciliation → Reconciliation (80 lines)               │   │
│  │  /alerts       → Alerts         (49 lines)                 │   │
│  │  /health       → Health         (60 lines)                 │   │
│  │  /settings     → Settings       (48 lines)                 │   │
│  │  /audit        → AuditLog       (43 lines)                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ DataProvider.tsx (430 lines) — State Management             │   │
│  │                                                             │   │
│  │ Architecture:                                               │   │
│  │  • React Context + useSyncExternalStore                     │   │
│  │  • 25 state slices (overview, strategies, positions, etc.) │   │
│  │  • 20 REST fetch functions with polling intervals          │   │
│  │  • WebSocket connection (ws://host/ws)                     │   │
│  │                                                             │   │
│  │ Polling intervals:                                          │   │
│  │  • 2s: positions, marketData                               │   │
│  │  • 3s: overview, strategies, orders, fills, risk           │   │
│  │  • 5s: trades, pnl, indicators, htf, alerts               │   │
│  │  • 10s: health, audit, equityCurve                         │   │
│  │                                                             │   │
│  │ WebSocket handlers:                                         │   │
│  │  • engine_state → updates overview, strategies, positions  │   │
│  │  • events → appends to wsEvents (max 200)                  │   │
│  │                                                             │   │
│  │ useDataSelector(selector) → per-slice subscriptions        │   │
│  │ (minimizes re-renders via useSyncExternalStore)            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ API Client (lib/api.ts, 73 lines)                           │   │
│  │                                                             │   │
│  │ 33 API functions (fetchJSON / postJSON wrappers)           │   │
│  │ Covers all /api/* endpoints                                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Key Components:                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Layout: Sidebar (149 lines) + TopBar (149 lines)           │   │
│  │  • 16 nav items with icons (lucide-react)                  │   │
│  │  • Connection status dot with pulse animation              │   │
│  │  • Market hours indicator (IST)                            │   │
│  │  • Gold/Silver LTP display                                 │   │
│  │  • PAPER mode indicator                                    │   │
│  │                                                             │   │
│  │ StrategyDetail (754 lines):                                 │   │
│  │  • Three-column grid (State, Indicators, Parameters)       │   │
│  │  • Equity curve with period selector (StrategyEquityChart) │   │
│  │  • Performance metrics (profit_factor, sharpe, etc.)       │   │
│  │  • Recent trades table                                     │   │
│  │  • Strategy events log                                     │   │
│  │                                                             │   │
│  │ StrategyMatrix (683 lines):                                 │   │
│  │  • Filterable/sortable strategy table                      │   │
│  │  • Expandable rows (renders StrategyDetail)                │   │
│  │  • Strategy comparison panel (StrategyCompare)             │   │
│  │  • Summary stats (running, long, short, flat, etc.)       │   │
│  │                                                             │   │
│  │ StrategyCompare (292 lines):                                │   │
│  │  • Normalized equity curve comparison                      │   │
│  │  • Side-by-side performance metrics                        │   │
│  │                                                             │   │
│  │ EquityCurveChart (64 lines):                                │   │
│  │  • SVG chart with gradient fill                            │   │
│  │  • Summary stats (Start, Current, Net, Last)              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Styling:                                                           │
│  • Dark theme (custom CSS properties)                              │
│  • Tailwind CSS                                                    │
│  • Custom animations: tick-flash, pulse-dot, fade-in-up, shimmer  │
│  • Responsive grid (8 cols → 4 → 2)                               │
│  • Inter font (Google Fonts)                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 13. Monitoring & Notifications

```
┌─────────────────────────────────────────────────────────────────────┐
│                 MONITORING & NOTIFICATIONS                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ HealthMonitor (monitoring/health.py, 124 lines)             │   │
│  │                                                             │   │
│  │ Tracks 6 components:                                        │   │
│  │  • data_adapter, timeframe_engine, htf_engine              │   │
│  │  • strategy, execution, risk                                │   │
│  │                                                             │   │
│  │ Counters: tick_count, bar_count, signal_count, fill_count  │   │
│  │ Status: HEALTHY, DEGRADED, STOPPED, ERROR                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ TelegramRouter (notifications/telegram_router.py, 81 lines) │   │
│  │                                                             │   │
│  │ Routes:                                                     │   │
│  │  • on_fill(fill, strategy, account) → trade entry alert    │   │
│  │  • on_signal(signal_data) → signal candle alert            │   │
│  │  • on_trade_close(close_data) → trade exit alert           │   │
│  │  • on_risk_alert(risk_data) → risk alert                   │   │
│  │  • on_error(error_data) → error alert                      │   │
│  │  • on_daily_summary(summary) → daily summary               │   │
│  │                                                             │   │
│  │ Uses: TelegramClient (HTTP) + TelegramFormatter            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ EventBus (dashboard/event_bus.py)                           │   │
│  │                                                             │   │
│  │ • In-memory ring buffer (50K events max)                   │   │
│  │ • Real-time events broadcast to WebSocket clients          │   │
│  │ • Event types: SIGNAL, ORDER, FILL, RISK, ERROR           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Reconciliation (reconciliation/)                            │   │
│  │                                                             │   │
│  │ • Trade reconciliation at startup                          │   │
│  │ • Orphan detection (fills, orders, positions)              │   │
│  │ • Consistency checks                                       │   │
│  │ • Triggered by: reconciliation_failed safe mode reason     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 14. Analytics

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ANALYTICS LAYER                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ EventStore (analytics/event_store.py)                        │   │
│  │                                                             │   │
│  │ • Writes trade events to analytics.db                      │   │
│  │ • Event types: SIGNAL, ORDER, FILL, CLOSE, REVERSAL       │   │
│  │ • Used by: TradeLifecycleManager._record_event()           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ TradeLedger (analytics/trade_ledger.py)                     │   │
│  │                                                             │   │
│  │ • Rich lifecycle audit per trade                           │   │
│  │ • MFE/MAE tracking (Max Favorable/Adverse Excursion)      │   │
│  │ • Duration tracking                                        │   │
│  │ • Derives from canonical trades table                      │   │
│  │ • Healed from DB if missing (guarded rebuild path)         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Analytics Routes (dashboard/routes/analytics/):                   │
│  • GET /api/analytics/strategies/:id → strategy analytics          │
│  • GET /api/analytics/strategies/:id/trades → trade list           │
│  • GET /api/analytics/strategies/:id/equity → equity curve         │
│  • GET /api/analytics/strategies/:id/drawdown → drawdown curve     │
│  • GET /api/analytics/events → event stream                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 15. Complete Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COMPLETE DATA FLOW                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ══════════════════ LIVE TICK FLOW ══════════════════              │
│                                                                     │
│  Dhan WSS                                                         │
│    │ (binary packets)                                              │
│    ▼                                                               │
│  DhanWebSocketClient._on_message()                                │
│    │ _parse_packet() → {sid, ltp, ltt, ...}                       │
│    │ dedup (sid|ltt|price key)                                    │
│    ▼                                                               │
│  DhanDataAdapter._process_tick()                                  │
│    │ _normalize_tick() → {instrument, ltp, event_ts, ...}         │
│    │ update _live_ltp cache                                       │
│    │ on_tick callback                                             │
│    ▼                                                               │
│  TradingEngine._on_tick(tick)                                     │
│    │ [1] execution_engine.update_price(instrument, ltp)           │
│    │ [2] position.update_mark(ltp) → unrealized P&L             │
│    │ [3] risk_engine.update_peak_equity()                        │
│    │ [4] _process_deferred_exit() → reversal at open             │
│    │ [5] strat.on_tick(ltp, timestamp)                           │
│    │     ├── stop loss check (real-time SL)                       │
│    │     └── pending entry trigger check                         │
│    ▼                                                               │
│  Dashboard WS broadcast → Frontend real-time update                │
│                                                                     │
│  ══════════════════ REST CANDLE FLOW ══════════════════            │
│                                                                     │
│  CandleFetcher._check_and_fetch() (every 30s)                     │
│    │                                                               │
│    ├── 5m: clock-aligned fetch                                    │
│    ├── 15m/1h: native Dhan candles                               │
│    │                                                               │
│    ▼                                                               │
│  DhanRESTClient.fetch_intraday/fetch_daily()                      │
│    │ _to_candles() → [[ts, O, H, L, C, V], ...]                  │
│    ▼                                                               │
│  CandleFetcher._create_bar() → Bar object                        │
│    │ on_candle_closed callback                                    │
│    ▼                                                               │
│  TradingEngine._on_bar_closed(bar)                                │
│    │ [1] indicator.update(O, H, L, C) → DEMAATR value           │
│    │ [2] htf_engine.on_htf_bar_closed(bar) (if HTF)             │
│    │ [3] htf_engine.map_to_fast_bar(bar) → HTFMappedValue       │
│    │ [4] strat.on_bar(bar, htf_mapped, fast_dema_atr, mid)     │
│    │     ├── check pending entry                                  │
│    │     ├── check stop loss                                      │
│    │     └── detect new signals                                  │
│    ▼                                                               │
│  Signal → _process_signal(signal)                                 │
│                                                                     │
│  ══════════════════ SIGNAL → TRADE FLOW ══════════════════         │
│                                                                     │
│  _process_signal(signal):                                         │
│    │ [1] safe_mode check → BLOCK if active                       │
│    │ [2] risk_engine.check_order() → 6-gate check                │
│    │ [3] lifecycle.create_trade_from_signal() → trade_id born   │
│    │ [4] order_manager.submit_signal(signal)                     │
│    │     ├── PaperExecutionEngine.create_order() → Order         │
│    │     └── PaperExecutionEngine.submit_order() → Fill          │
│    │         (slippage +1 tick BUY, -1 tick SELL)               │
│    │ [5] lifecycle.register_order(order_id, trade_id)           │
│    ▼                                                               │
│  _on_fill(fill):                                                   │
│    │ [1] fill_dedup.is_duplicate() → skip if duplicate          │
│    │ [2] fill_dedup.note_processed() → in-memory lock           │
│    │ [3a] ENTRY FILL:                                            │
│    │     ├── lifecycle.register_entry_fill()                     │
│    │     ├── position_manager.open_position()                    │
│    │     ├── lifecycle.register_position()                       │
│    │     └── account_engine.block_margin()                       │
│    │ [3b] EXIT FILL:                                             │
│    │     ├── _trade_close_manager.close_position()               │
│    │     │   (8-step atomic close)                               │
│    │     │   Step 1: P&L calculation                             │
│    │     │   Step 2-3: DB persist (BEFORE memory)               │
│    │     │   Step 4: Memory update                               │
│    │     │   Step 5: Account P&L update                         │
│    │     │   Step 6: Risk engine update                         │
│    │     │   Step 7: Event store + dashboard publish            │
│    │     │   Step 8: Telegram notification                      │
│    │     └── lifecycle.close_trade()                             │
│    │ [4] fill_dedup.mark_processed() → DB INSERT                │
│    ▼                                                               │
│  Dashboard update → Frontend re-render                             │
│                                                                     │
│  ══════════════════ DASHBOARD FLOW ══════════════════              │
│                                                                     │
│  Backend (FastAPI):                                                │
│    engine.snapshot() → JSON → REST API + WebSocket                │
│                                                                     │
│  Frontend (React):                                                 │
│    DataProvider                                                   │
│    ├── REST polling (2-10s intervals) → api.*()                  │
│    ├── WebSocket (ws://host/ws) → engine_state + events          │
│    └── useDataSelector(selector) → per-slice subscriptions       │
│                                                                     │
│  Pages:                                                            │
│    Overview → LiveTrading → Strategies → StrategyMatrix            │
│    → Positions → Orders → Trades → P&L → Risk                    │
│    → MarketData → Indicators → Reconciliation                     │
│    → Alerts → Health → Settings → AuditLog                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 16. State Machines

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ALL STATE MACHINES                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. MARKET SESSION (market_status.py)                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Weekend: always OVERNIGHT                                   │   │
│  │                                                             │   │
│  │ Weekday:                                                    │   │
│  │  BEFORE (open-5min)  → OVERNIGHT                           │   │
│  │  (open-5min to open) → PRE_MARKET                          │   │
│  │  (open to open+1min) → MARKET_OPEN                         │   │
│  │  (open+1min to close-5min) → LIVE_TRADING                  │   │
│  │  (close-5min to close) → MARKET_CLOSE                      │   │
│  │  (close to close+30min) → AFTER_MARKET                     │   │
│  │  AFTER (close+30min) → OVERNIGHT                           │   │
│  │                                                             │   │
│  │ Overrides: SAFE_MODE (error), HALTED (manual)              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  2. ENGINE STATUS (market_status.py)                               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ INITIALIZING → RESTORING → WARMING_UP → RECONCILING        │   │
│  │ → READY → TRADING                                          │   │
│  │                                                             │   │
│  │ Any → SAFE_MODE (error) → READY (recovered)               │   │
│  │ Any → HALTED (manual)                                      │   │
│  │ Any → STOPPED (graceful shutdown)                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  3. DATA STATUS (market_status.py)                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ NO_DATA → DISCONNECTED → STALE → CONNECTED                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  4. BAR STATE (timeframe_engine.py)                                │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ FORMING → CLOSED → PROCESSED                               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  5. TRADE STATUS (lifecycle.py)                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PENDING → OPEN → EXIT_PENDING → CLOSED                     │   │
│  │                                                             │   │
│  │ PENDING → REJECTED                                         │   │
│  │ PENDING → CANCELLED                                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  6. STRATEGY STATE (strategies/types.py)                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ FLAT → PENDING_LONG/SHORT → LONG/SHORT_POSITION            │   │
│  │ → EXIT_ORDER_SUBMITTED → FLAT                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  7. SAFE MODE (safe_mode.py)                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Multi-reason accumulation:                                  │   │
│  │  enter(reason) → add to reason set                         │   │
│  │  clear_reason(reason) → remove from set                    │   │
│  │  exit() → only when ALL reasons cleared + 5s cooldown     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  8. FILL DEDUP (fill_dedup.py)                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Phase 1: note_processed(fill_id) → in-memory               │   │
│  │ Phase 2: mark_processed(fill_id) → DB INSERT               │   │
│  │ (crash-safe: effects applied BETWEEN phases)               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 17. Startup Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STARTUP LIFECYCLE                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  main.py                                                           │
│    │                                                               │
│    ├── PersistenceManager(state_path, db_path)                    │
│    │     └── Database(db_path).close() → init schema              │
│    │                                                               │
│    ├── TradingEngine(config)                                       │
│    │     ├── _init_market_status() → MarketStatus                 │
│    │     ├── _init_data_adapter() → DhanDataAdapter               │
│    │     │     ├── DhanRESTClient → renew_token() (ALWAYS fresh) │
│    │     │     ├── rest.start_scheduler() (7AM + 6h)            │
│    │     │     └── DhanWebSocketClient (with fresh token)         │
│    │     ├── _init_timeframe_engine() → CandleFetcher             │
│    │     ├── _init_indicator_engines() → 6 × DEMAATR             │
│    │     ├── _init_htf_engine() → BacktestStyleHTFEngine         │
│    │     ├── _init_strategies() → 8 strategy instances            │
│    │     ├── _init_execution() → PaperBroker + OrderManager      │
│    │     ├── _init_portfolio() → PositionMgr + PNL/Acct engines  │
│    │     ├── _init_risk() → RiskEngine                           │
│    │     ├── _init_monitoring() → HealthMonitor                  │
│    │     └── _init_notifications() → TelegramRouter              │
│    │                                                               │
│    ├── persistence.load_state() → saved state (if any)           │
│    │                                                               │
│    ├── engine.start()                                              │
│    │     ├── Wire TradeCloseManager (11 subsystems)              │
│    │     ├── Wire TradeLifecycleManager                          │
│    │     ├── fill_dedup.load_from_database()                      │
│    │     │                                                       │
│    │     ├── Phase 1: RECONCILING                                │
│    │     │     └── ReconciliationEngine.reconcile("startup")    │
│    │     │         ├── orphan_scan()                              │
│    │     │         └── consistency checks                         │
│    │     │             (failures → safe_mode.enter_safe_mode())  │
│    │     │                                                       │
│    │     ├── Phase 2: WARMING_UP                                 │
│    │     │     └── _warmup_from_rest()                           │
│    │     │         ├── REST fetch 5 days historical candles      │
│    │     │         ├── Resample to 5m/15m/1h                    │
│    │     │         ├── Pre-populate HTF engine                   │
│    │     │         └── Pre-populate DEMAATR indicators           │
│    │     │                                                       │
│    │     ├── Phase 3: READY                                      │
│    │     │     ├── candle_fetcher.start() (30s polling)          │
│    │     │     └── data_adapter.connect() (WebSocket)            │
│    │     │                                                       │
│    │     └── Phase 4: TRADING                                    │
│    │           └── _maybe_enable_trading()                       │
│    │               (requires: LIVE_TRADING + TRADING + live data)│
│    │                                                               │
│    ├── SIGINT/SIGTERM handler registered                          │
│    │                                                               │
│    └── Main loop: sleep 60s → persistence.save_state()           │
│                                                                     │
│  On shutdown (SIGINT/SIGTERM):                                     │
│    engine.stop()                                                   │
│      ├── data_adapter.disconnect()                                │
│      ├── candle_fetcher.stop()                                    │
│      └── telegram.stop()                                          │
│    persistence.save_state() (final)                                │
│    persistence.close()                                             │
│    sys.exit(0)                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 18. Trade Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TRADE LIFECYCLE (Signal → Close)                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 1. SIGNAL DETECTED (strategy)                              │   │
│  │    Bar T closes → crossover detected → _detect_signal()    │   │
│  │    Creates PendingEntry at trigger = T.high (LONG)         │   │
│  │                                       T.low  (SHORT)       │   │
│  │    State: FLAT → PENDING_LONG / PENDING_SHORT             │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 2. TRIGGER FIRED (tick or bar)                             │   │
│  │    Later bar/tick crosses trigger_price                     │   │
│  │    _check_pending_entry() or on_tick() triggers            │   │
│  │    fill_px = trigger_price                                 │   │
│  │    State: PENDING_* → LONG/SHORT_POSITION                  │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 3. TRADE CREATED (lifecycle)                               │   │
│  │    lifecycle.create_trade_from_signal()                    │   │
│  │    trade_id born (THE ONLY place)                          │   │
│  │    5 identity maps populated                               │   │
│  │    Persisted to DB (signals table first, then trades)      │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 4. ORDER SUBMITTED (order_manager)                         │   │
│  │    order_manager.submit_signal(signal)                     │   │
│  │    → dedup check → create_order → submit_order             │   │
│  │    → PaperExecutionEngine fills with slippage              │   │
│  │    → Fill object produced                                  │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 5. FILL RECEIVED (engine)                                  │   │
│  │    _on_fill(fill)                                          │   │
│  │    fill_dedup.is_duplicate() → skip if dup                 │   │
│  │    fill_dedup.note_processed() → in-memory lock            │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 6. POSITION OPENED (portfolio)                             │   │
│  │    lifecycle.register_entry_fill(fill_id, price, ts)      │   │
│  │    position_manager.open_position(fill) → Position         │   │
│  │    lifecycle.register_position(position_id)                │   │
│  │    account_engine.block_margin(amount)                     │   │
│  │    Strategy: LONG_POSITION / SHORT_POSITION                │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 7. MONITORING (every tick)                                 │   │
│  │    strat.on_tick(ltp, timestamp)                           │   │
│  │    ├── STOP LOSS? ltp <= stop_price (LONG)                │   │
│  │    │              ltp >= stop_price (SHORT)                │   │
│  │    │   → _create_exit_signal("stop_loss_hit")             │   │
│  │    │                                                       │   │
│  │    ├── REVERSAL? Opposite crossover detected              │   │
│  │    │   → _create_reversal_signal()                         │   │
│  │    │   → pending_exit_at_open = True (defer to next bar)  │   │
│  │    │   → arm opposite pending entry                        │   │
│  │    │                                                       │   │
│  │    └── SAME-BAR STOP? Entry + SL on same bar             │   │
│  │        → exit at bar CLOSE                                │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 8. EXIT SIGNAL (strategy)                                  │   │
│  │    exit_signal with reason (stop_loss_hit, reversal, etc.) │   │
│  │    → _process_signal(exit_signal)                          │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 9. TRADE CLOSED (8-step atomic close)                      │   │
│  │    _trade_close_manager.close_position()                   │   │
│  │                                                             │   │
│  │    Step 0: Validate exit price                             │   │
│  │    Step 1: Calculate P&L (pure)                            │   │
│  │      LONG: gross = (exit-entry) × qty × mult              │   │
│  │      SHORT: gross = (entry-exit) × qty × mult             │   │
│  │      charges = MCXFeeModel.calculate()                     │   │
│  │      net = gross - charges                                 │   │
│  │                                                             │   │
│  │    Step 2-3: DB persist (BEFORE memory)                    │   │
│  │      persistence.save_trade_and_fill(trade, exit_fill)     │   │
│  │                                                             │   │
│  │    Step 4: Memory update                                   │   │
│  │      position_manager.close_position()                     │   │
│  │                                                             │   │
│  │    Step 5: Account P&L                                     │   │
│  │      pnl_engine.record_trade(gross, charges, net)          │   │
│  │      account_engine.update_realized_pnl(net, charges)      │   │
│  │      account_engine.release_margin()                        │   │
│  │                                                             │   │
│  │    Step 6: Risk engine                                     │   │
│  │      risk_engine.update_daily_pnl(net)                     │   │
│  │      risk_engine.update_peak_equity()                       │   │
│  │                                                             │   │
│  │    Step 7: Events + Dashboard                              │   │
│  │      event_store.record()                                  │   │
│  │      EventBus.publish()                                    │   │
│  │                                                             │   │
│  │    Step 8: Telegram                                        │   │
│  │      telegram.on_trade_close(close_data)                   │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                              │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │ 10. TRADE FINALIZED                                       │   │
│  │    lifecycle.close_trade()                                 │   │
│  │    Status → CLOSED                                         │   │
│  │    fill_dedup.mark_processed(fill_id) → DB INSERT         │   │
│  │    Strategy state → FLAT                                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Summary: Key Numbers

| Metric | Value |
|--------|-------|
| Total Python source lines | ~15,000+ |
| Trading engine | 2,306 lines |
| Trade lifecycle manager | 1,062 lines |
| Strategy base class | 743 lines |
| Database layer | 788 lines |
| Frontend (React) | 3,602 lines |
| REST API endpoints | 37+ |
| Database tables (canonical) | 13 |
| Database tables (derived) | 7 |
| State machines | 8 |
| Strategy instances | 8 (4 gold + 4 silver) |
| DEMAATR indicators | 6 (2 instruments × 3 timeframes) |
| Capital per strategy | ₹3,00,000 |
| Total capital | ₹12,00,000 |
