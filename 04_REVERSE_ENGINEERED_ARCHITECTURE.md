# 04 - Reverse-Engineered Architecture

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## Actual Architecture (As Implemented)

### Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (dashboard-ui/)                  │
│                  Vue/React SPA (built assets)                │
└────────────────────────┬────────────────────────────────────┘
                         │ WebSocket + REST
┌────────────────────────▼────────────────────────────────────┐
│                 Dashboard Server (FastAPI)                   │
│            server.py (lifespan, routes, WS)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ overview │ │strategies│ │positions │ │  trades  │       │
│  │   pnl    │ │  risk    │ │  health  │ │reconcil. │       │
│  │  alerts  │ │settings  │ │audit_log │ │indicators│       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│  ┌──────────────────────────────────────────────────┐       │
│  │        Analytics Routes (/api/analytics/*)       │       │
│  └──────────────────────────────────────────────────┘       │
└────────────────────────┬────────────────────────────────────┘
                         │ Direct function calls
┌────────────────────────▼────────────────────────────────────┐
│                    Trading Engine                            │
│            trading_engine.py (orchestrator)                  │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │           Core Layer (core/)                     │        │
│  │  MarketStatus · SafeMode · RiskEngine            │        │
│  │  TradeCloseManager · FillDeduplicator            │        │
│  │  CandleFetcher · TimeframeEngine                 │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Indicators Layer (indicators/)           │        │
│  │  DEMA · ATR · DEMAATR                            │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         HTF Layer (htf/)                         │        │
│  │  BacktestStyleHTFEngine (searchsorted mapping)   │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Strategies Layer (strategies/)           │        │
│  │  BaseDEMAStrategy · Gold01-04 · Silver01-04      │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Execution Layer (execution/)             │        │
│  │  PaperExecutionEngine · OrderManager · MCXFeeModel│       │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Portfolio Layer (portfolio/)             │        │
│  │  PositionManager · PNLEngine · AccountEngine     │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Persistence Layer (persistence/)         │        │
│  │  PersistenceManager (trading.db + state.json)    │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Analytics Layer (analytics/)             │        │
│  │  EventStore · TradeLedger · PerformanceEngine    │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Monitoring Layer (monitoring/)           │        │
│  │  HealthMonitor                                   │        │
│  └─────────────────────────────────────────────────┘        │
│                                                              │
│  ┌─────────────────────────────────────────────────┐        │
│  │         Notifications Layer (notifications/)     │        │
│  │  TelegramRouter · TelegramClient                 │        │
│  └─────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    Data Layer (data/)                        │
│            DhanDataAdapter (WebSocket + REST)               │
│            WebSocketClient · RESTClient · InstrumentMapper  │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Architecture

```
                    ┌─────────────┐
                    │ Dhan WS     │ → LTP ticks only
                    │ (real-time) │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Dhan REST   │ → Historical candles (5m)
                    │ (polling)   │
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │     CandleFetcher       │ → Polls every 30s
              │  (core/candle_fetcher)  │ → Aggregates 15m/1h from 5m
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │    TradingEngine        │
              │  _on_bar_closed()       │
              │  _on_tick()             │
              └────┬───────────┬────────┘
                   │           │
        ┌──────────▼──┐  ┌────▼──────────┐
        │  Indicators  │  │  HTF Engine   │
        │  (DEMA-ATR)  │  │ (backtest)    │
        └──────┬───────┘  └───────┬───────┘
               │                  │
        ┌──────▼──────────────────▼──────┐
        │        Strategy Layer           │
        │  BaseDEMAStrategy.on_bar()      │
        │  → Signal or None               │
        └──────────────┬──────────────────┘
                       │
        ┌──────────────▼──────────────────┐
        │     _process_signal()           │
        │  → Risk check                   │
        │  → Order submission             │
        │  → Fill generation              │
        └──────────────┬──────────────────┘
                       │
        ┌──────────────▼──────────────────┐
        │        _on_fill()               │
        │  → Entry: open_position()       │
        │  → Exit:  TradeCloseManager     │
        └──────────────┬──────────────────┘
                       │
        ┌──────────────▼──────────────────┐
        │     Persistence                 │
        │  → trading.db (orders,fills,    │
        │    trades,events,snapshots)     │
        │  → analytics.db (trade_events,  │
        │    trades_analytics, trade_legs)│
        │  → system_state.json (state)    │
        └─────────────────────────────────┘
```

### Execution Model

**Paper-Only:** The system explicitly rejects non-paper execution:
```python
# trading_engine.py:244-248
if exec_mode != "paper":
    raise RuntimeError(
        f"EXECUTION_MODE must be 'paper', got '{exec_mode}'. "
        "Live trading is not supported in this system."
    )
```

### Signal Processing Model

Two parallel paths for signal detection:

1. **Bar-based** (`on_bar_closed`): Primary signal detection on candle close
   - CandleFetcher polls REST API every 30s
   - When a candle closes, it's fed to indicators and strategies
   - Breakout entries evaluated against bar high/low

2. **Tick-based** (`on_tick`): Supplementary for pending trigger monitoring
   - Only processes when `tick_signal_processing = True`
   - Monitors pending breakout triggers
   - Monitors tick-level stop loss

### State Management Pattern

Every component follows the same pattern:
- In-memory state is primary source of truth
- `snapshot()` → dict for persistence
- `restore(dict)` → restore from persistence
- JSON `system_state.json` for fast save/load
- SQLite `trading.db` for audit trail
- SQLite `analytics.db` for rich analytics

### Concurrency Model

- **Single-threaded** for most operations (protected by `threading.RLock` on `trading_engine._lock`)
- **CandleFetcher**: background daemon thread
- **WebSocket**: async (FastAPI/uvicorn)
- **Background tasks**: asyncio tasks (push updates, events, periodic save)
- **FillDeduplicator**: thread-safe with its own `threading.Lock`

### Key Architectural Decisions

1. **No ORM**: All database access is raw SQL with `sqlite3`
2. **No message queue**: All inter-component communication is direct function calls
3. **No event sourcing**: Events are append-only but not used for state reconstruction
4. **Two separate databases**: Operational (`trading.db`) and analytical (`analytics.db`)
5. **Indicator state intentionally NOT persisted**: Recomputed from REST on every startup
6. **Atomic trade close**: TradeCloseManager persists BEFORE memory updates
7. **Position-anchored trade identity**: `trade_id == position_id`
