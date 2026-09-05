# MCX-TRADER: Full Architecture & Lifecycle Diagrams

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MCX-TRADER (GoldSilverLiveTrader)               │
│                    PAPER Trading System v1.0.0                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │  GOLDM   │  │ GOLDM    │  │ SILVERM  │  │ SILVERM  │          │
│  │ gold_01  │  │ gold_02  │  │ silver_01│  │ silver_02│          │
│  │ 5m/1h    │  │ 15m/1h   │  │ 15m/1h   │  │ 5m/1h    │          │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│       └──────────────┴──────────────┴──────────────┘               │
│                          │                                         │
│              ┌───────────┴───────────┐                             │
│              │    Trading Engine     │                             │
│              │  (trading_engine.py)  │                             │
│              └───────────┬───────────┘                             │
│                          │                                         │
│  ┌───────────┬───────────┼───────────┬───────────┬───────────┐    │
│  │           │           │           │           │           │    │
│  ▼           ▼           ▼           ▼           ▼           ▼    │
│ ┌─────┐  ┌─────┐  ┌─────────┐  ┌─────────┐  ┌──────┐  ┌──────┐ │
│ │Data │  │Exec │  │Position │  │  Risk   │  │ P&L  │  │Health│ │
│ │Adapt│  │Engin│  │Manager  │  │ Engine  │  │Engine│  │Monitor│ │
│ └──┬──┘  └─────┘  └─────────┘  └─────────┘  └──────┘  └──────┘ │
│    │                                                              │
│    ▼                                                              │
│ ┌──────────────────────────────────────────┐                     │
│ │         Dhan WebSocket Feed              │                     │
│ │    wss://api-feed.dhan.co                │                     │
│ │    GOLDM(569003) + SILVERM(483080)       │                     │
│ └──────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        LAYER 1: ENTRY POINTS                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  main.py                        dashboard/server.py                 │
│  ├─ PersistenceManager          ├─ FastAPI + WebSocket              │
│  ├─ TradingEngine               ├─ REST API (/api/*)               │
│  └─ Signal handlers (SIGINT)    ├─ WS Push (engine_state)          │
│                                  └─ Static file serving             │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                      LAYER 2: TRADING ENGINE                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  trading_engine.py (2086 lines) — Central Orchestrator              │
│  ├─ _init_data_adapter()      → DhanDataAdapter                     │
│  ├─ _init_timeframe_engine()  → CandleFetcher (REST)               │
│  ├─ _init_indicator_engines() → DEMAATR per instrument/timeframe   │
│  ├─ _init_htf_engine()        → BacktestStyleHTFEngine             │
│  ├─ _init_strategies()        → GoldStrategy01-04, Silver01-04     │
│  ├─ _init_execution()         → PaperExecutionEngine + OrderManager│
│  ├─ _init_portfolio()         → PositionManager + PNLEngine x4     │
│  ├─ _init_risk()              → RiskEngine                         │
│  ├─ _init_monitoring()        → HealthMonitor                      │
│  └─ _init_notifications()     → TelegramRouter                     │
│                                                                     │
│  Key Handlers:                                                      │
│  ├─ _on_tick(tick)            → WebSocket tick processing           │
│  ├─ _on_bar_closed(bar)       → REST candle processing             │
│  ├─ _process_signal(signal)   → Signal → Order → Fill              │
│  └─ _process_deferred_exit()  → Reversal exit at next bar open     │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                       LAYER 3: DATA FEED                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  DhanDataAdapter (adapter.py)                                       │
│  ├─ WebSocket Client          → Live LTP only (NO candle building) │
│  ├─ REST Client               → Historical candles (source of truth)│
│  ├─ Instrument Mapper         → security_id ↔ symbol mapping       │
│  └─ Auto token renewal        → PIN+TOTP at 7AM + safety checks    │
│                                                                     │
│  CandleFetcher (candle_fetcher.py)                                  │
│  ├─ 5m candles (clock-aligned)                                      │
│  ├─ 15m candles (native Dhan offset)                               │
│  └─ 1h candles (native Dhan offset)                                │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                      LAYER 4: STRATEGY                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  BaseDEMAStrategy (base_dema_strategy.py) — 719 lines              │
│  ├─ on_bar(bar, htf_mapped, fast_dema_atr, mid_mapped)             │
│  ├─ on_tick(ltp, timestamp)                                         │
│  ├─ _detect_signal()          → Crossover detection                │
│  ├─ _create_pending_signal()  → Arms breakout entry                │
│  ├─ _check_pending_entry()    → Triggers when bar crosses trigger  │
│  ├─ _check_stop_loss()        → SL hit detection                   │
│  ├─ _create_reversal_signal() → Exit old + arm opposite entry      │
│  └─ _consume_same_bar_stop()  → Entry + SL on same bar            │
│                                                                     │
│  Gold/Silver strategy wrappers (thin, just set instrument + TF)     │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    LAYER 5: EXECUTION                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  OrderManager → PaperExecutionEngine                                │
│  ├─ create_order(signal)                                             │
│  ├─ submit_order(order)                                              │
│  ├─ _execute_order(order)  → Slippage + fill generation            │
│  └─ Fill objects with UUID IDs                                      │
│                                                                     │
│  MCXFeeModel (fee_model.py)                                        │
│  └─ Brokerage + STT + Exchange + SEBI + GST + Stamp Duty           │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    LAYER 6: PORTFOLIO                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PositionManager (position_manager.py)                              │
│  ├─ open_position() / close_position()                              │
│  ├─ update_mark(ltp)           → Unrealized P&L                    │
│  └─ MFE/MAE tracking                                                │
│                                                                     │
│  PNLEngine (per-strategy) (pnl.py)                                  │
│  ├─ Realized P&L (charges deducted)                                │
│  ├─ Win/loss tracking                                               │
│  └─ Trade count                                                     │
│                                                                     │
│  AccountEngine (per-strategy) (account.py)                          │
│  ├─ Starting capital: ₹3,00,000 per strategy                       │
│  ├─ Available margin = equity - used_margin                         │
│  └─ Unrealized P&L marking                                          │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    LAYER 7: RISK & SAFETY                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  RiskEngine (risk_engine.py)                                        │
│  ├─ Per-strategy position cap (1)                                   │
│  ├─ Global position cap (8)                                         │
│  ├─ Daily loss limit                                                │
│  ├─ Max drawdown %                                                  │
│  └─ Kill switch                                                     │
│                                                                     │
│  SafeModeManager (safe_mode.py)                                     │
│  ├─ Triggered by: reconciliation failure, stale data, etc.         │
│  └─ Blocks all new entries, allows exits                           │
│                                                                     │
│  MarketStatus (market_status.py)                                    │
│  ├─ State machine: OVERNIGHT→PRE_MARKET→MARKET_OPEN→LIVE_TRADING   │
│  │  →MARKET_CLOSE→AFTER_MARKET→OVERNIGHT                           │
│  ├─ Engine status: INITIALIZING→RESTORING→RECONCILING→WARMING_UP   │
│  │  →READY→TRADING                                                 │
│  └─ Data status: NO_DATA→CONNECTED→STALE→DISCONNECTED              │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    LAYER 8: PERSISTENCE                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PersistenceManager (manager.py)                                    │
│  ├─ JSON: system_state.json (atomic writes)                        │
│  └─ SQLite: trading.db (WAL mode, serialized writes)               │
│                                                                     │
│  Database Tables (trading.db):                                      │
│  ├─ trades              → Canonical trade lifecycle                 │
│  ├─ orders              → Order audit trail                        │
│  ├─ fills               → Fill records with trade_id links         │
│  ├─ signals             → Signal candle audit                      │
│  ├─ trade_signal_link   → FK relationships                        │
│  ├─ account_snapshots   → Equity curve data                       │
│  └─ events              → System audit log                         │
│                                                                     │
│  analytics.db:                                                      │
│  ├─ trade_analytics     → Rich lifecycle audit                     │
│  └─ trade_events        → Event stream                             │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    LAYER 9: MONITORING & NOTIFY                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  HealthMonitor (health.py)                                          │
│  ├─ Components: data_adapter, timeframe_engine, htf_engine,        │
│  │  strategy, execution, risk                                      │
│  └─ Tick/bar/signal/fill/error counters                            │
│                                                                     │
│  TelegramRouter (telegram_router.py)                                │
│  ├─ Startup report                                                  │
│  ├─ Signal candle alerts                                            │
│  ├─ Trade entry/exit notifications                                  │
│  └─ Risk alerts                                                     │
│                                                                     │
│  EventStore (analytics/event_store.py)                              │
│  └─ Writes to analytics.db for dashboards                          │
│                                                                     │
│  TradeLedger (analytics/trade_ledger.py)                            │
│  └─ MFE/MAE tracking, lifecycle events                             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Engine Startup Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ENGINE STARTUP STATE MACHINE                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐                                                   │
│  │ INITIALIZING │  ← main.py: TradingEngine()                      │
│  └──────┬───────┘     _init_data_adapter()                         │
│         │              _init_timeframe_engine()                     │
│         │              _init_indicator_engines()                    │
│         ▼              _init_htf_engine()                          │
│  ┌──────────────┐     _init_strategies()                           │
│  │  RESTORING   │  ← persistence.load_state()                      │
│  └──────┬───────┘     engine.restore(saved_state)                   │
│         │                                                          │
│         ▼                                                          │
│  ┌──────────────┐                                                   │
│  │ RECONCILING  │  ← ReconciliationEngine.reconcile(phase="startup")│
│  └──────┬───────┘     Check trades vs positions vs fills           │
│         │              If errors → safe_mode.enter_safe_mode()      │
│         ▼                                                          │
│  ┌──────────────┐                                                   │
│  │  WARMING_UP  │  ← _warmup_from_rest()                           │
│  └──────┬───────┘     Fetch 5 days historical candles              │
│         │              Resample to 5m/15m/1h                       │
│         │              Pre-populate HTF engine                     │
│         │              Pre-populate DEMAATR indicators              │
│         ▼                                                          │
│  ┌──────────────┐                                                   │
│  │    READY     │  ← candle_fetcher.start()                        │
│  └──────┬───────┘     data_adapter.connect()                       │
│         │              Wait for WebSocket connection               │
│         ▼                                                          │
│  ┌──────────────┐                                                   │
│  │   TRADING    │  ← _maybe_enable_trading()                       │
│  └──────────────┘     Requires:                                    │
│                       MarketState.LIVE_TRADING                     │
│                       EngineStatus.READY                           │
│                       has_live_market_data = True                  │
│                                                                     │
│  ═══════════════════════════════════════════════════════════════   │
│                                                                     │
│  EXTERNAL OVERRIDES:                                                │
│                                                                     │
│  ┌──────────┐  reconciliation_failure / stale data / etc.          │
│  │SAFE_MODE │  ← SafeMode blocks all new entries                   │
│  └──────────┘    Exits still execute                               │
│                                                                     │
│  ┌──────────┐  ← Manual emergency stop                             │
│  │  HALTED  │    Everything blocked                                │
│  └──────────┘                                                       │
│                                                                     │
│  ┌──────────┐  ← Ctrl+C / SIGTERM                                  │
│  │ STOPPED  │    engine.stop() + final persist                     │
│  └──────────┘                                                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Tick Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WEBSOCKET TICK DATA FLOW                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Dhan WebSocket (wss://api-feed.dhan.co)                            │
│  │                                                                  │
│  │  Binary Packet                                                   │
│  │  ├─ Code 4 (Quote): sid|ltp|ltq|ltt|cumvol                     │
│  │  └─ Code 2 (Price): sid|ltp|ltt                                │
│  │                                                                  │
│  ▼                                                                  │
│  DhanWebSocketClient._on_message()                                  │
│  ├─ _parse_packet(data)           → {security_id, ltp, ltt, ...}   │
│  ├─ Dedup check: sid|ltt|ltp key                                   │
│  └─ on_tick(tick)                → adapter._process_tick()         │
│                                                                     │
│  ▼                                                                  │
│  DhanDataAdapter._process_tick(raw_tick)                            │
│  ├─ _normalize_tick(raw)         → {instrument, ltp, event_ts, ...} │
│  │   ├─ security_id → symbol mapping                               │
│  │   └─ LTT (IST epoch) → UTC timestamp                           │
│  ├─ _instrument_ticks[instrument] += 1                             │
│  └─ on_tick(tick)                → trading_engine._on_tick()       │
│                                                                     │
│  ▼                                                                  │
│  TradingEngine._on_tick(tick)                                       │
│  ├─ [1] execution_engine.update_price(instrument, ltp)             │
│  ├─ [2] position.update_mark(ltp)          (unrealized P&L)        │
│  ├─ [3] risk_engine.update_peak_equity()   (drawdown tracking)     │
│  ├─ [4] _process_deferred_exit()          (reversal at open)       │
│  └─ [5] strat.on_tick(ltp, timestamp)     (SL + pending trigger)  │
│                                                                     │
│  ═══════════════════════════════════════════════════════════════   │
│                                                                     │
│  REST Candle Data Flow (separate from WebSocket):                   │
│                                                                     │
│  CandleFetcher._check_and_fetch()                                   │
│  ├─ Every 30s, checks if any candle just closed                    │
│  ├─ REST: GET /charts/intraday?securityId=...&interval=5           │
│  ├─ Creates Bar(instrument, timeframe, OHLCV)                      │
│  └─ on_candle_closed(bar)  → trading_engine._on_bar_closed()      │
│                                                                     │
│  TradingEngine._on_bar_closed(bar)                                  │
│  ├─ indicator.update(O, H, L, C)                                   │
│  ├─ htf_engine.on_htf_bar_closed(bar)    (1h/15m HTF values)      │
│  ├─ htf_engine.map_to_fast_bar(bar)     (HTF → fast bar mapping)  │
│  ├─ strat.on_bar(bar, htf_mapped, fast_val, mid_mapped)           │
│  └─ signal → _process_signal(signal)                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Trade Lifecycle Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TRADE LIFECYCLE (Signal → Close)                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌────────────────────┐                                             │
│  │   STRATEGY SIGNAL  │  strat.on_bar() detects crossover          │
│  │   (candle close)   │  close > htf_dema_atr (LONG)               │
│  └─────────┬──────────┘  close < htf_dema_atr (SHORT)              │
│            │                                                        │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  PENDING BREAKOUT  │  _create_pending_signal()                   │
│  │  Armed at trigger  │  trigger = signal_bar HIGH (LONG)          │
│  │  price             │  trigger = signal_bar LOW  (SHORT)         │
│  └─────────┬──────────┘  stop = opposite extreme                    │
│            │                                                        │
│            │  Strategy state: PENDING_LONG / PENDING_SHORT         │
│            │  bars_pending incremented each bar                    │
│            │  Expires after 50 bars                                │
│            │                                                        │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  TRIGGER FIRES     │  Either:                                    │
│  │  (tick or bar)     │  A) Tick: ltp > trigger_price              │
│  │                    │  B) Bar: bar.high > trigger_price           │
│  └─────────┬──────────┘                                             │
│            │                                                        │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  ENTRY SIGNAL      │  Signal(signal_type, trigger_price,        │
│  │                    │          stop_price, quantity)              │
│  └─────────┬──────────┘                                             │
│            │                                                        │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  RISK CHECK        │  risk_engine.check_order()                 │
│  │                    │  ├─ Per-strategy position cap               │
│  │                    │  ├─ Global position cap                     │
│  │                    │  ├─ Available margin check                  │
│  │                    │  └─ Daily loss / drawdown check            │
│  └─────────┬──────────┘                                             │
│            │  If rejected → strategy reset to FLAT                 │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  ORDER SUBMITTED   │  order_manager.submit_signal()             │
│  │  (PaperExecution)  │  PaperExecutionEngine.create_order()       │
│  └─────────┬──────────┘  .submit_order() → _execute_order()       │
│            │              fill_price = LTP ± slippage               │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  FILL RECEIVED     │  fill = Fill(fill_id, order_id, price)     │
│  │                    │  _on_fill(fill)                             │
│  └─────────┬──────────┘                                             │
│            │                                                        │
│            ▼                                                        │
│  ┌────────────────────┐                                             │
│  │  POSITION OPENED   │  position_manager.open_position()          │
│  │  Strategy: LONG/   │  position.update_mark(ltp) on each tick   │
│  │  SHORT_POSITION    │  account_engine.update_unrealized_pnl()   │
│  └─────────┬──────────┘                                             │
│            │                                                        │
│            │  ══ MONITORING (every tick) ═══════════════════════   │
│            │                                                        │
│            │  ┌─────────────────────┐                               │
│            │  │ STOP LOSS HIT?     │  strat.on_tick(ltp, ts)       │
│            │  │ ltp <= stop_price   │  _check_stop_loss(bar)       │
│            │  │   (LONG)            │                               │
│            │  │ ltp >= stop_price   │                               │
│            │  │   (SHORT)           │                               │
│            │  └─────────┬───────────┘                               │
│            │            │ If hit → _create_exit_signal()            │
│            │            ▼                                           │
│            │  ┌─────────────────────┐                               │
│            │  │ SAME-BAR STOP?     │  Entry + SL on same bar      │
│            │  │ _consume_same_bar   │  Exit at bar CLOSE           │
│            │  └─────────────────────┘                               │
│            │                                                        │
│            │  ┌─────────────────────┐                               │
│            │  │ REVERSAL SIGNAL?    │  Opposite crossover detected │
│            │  │ _create_reversal    │  pending_exit_at_open = True │
│            │  │ _signal()           │  Exit at NEXT bar OPEN       │
│            │  └─────────┬───────────┘  + arm opposite pending      │
│            │            │                                           │
│            ▼            ▼                                           │
│  ┌────────────────────────────┐                                     │
│  │      POSITION CLOSED       │  _close_position()                 │
│  │      (any exit type)       │  position_manager.close_position() │
│  └─────────────┬──────────────┘  pnl_engine.realize_pnl()         │
│                │               account_engine.update_capital()     │
│                ▼               trade_ledger.close()                │
│  ┌────────────────────────────┐                                     │
│  │   TRADE CLOSED             │  status → CLOSED                   │
│  │   P&L finalized            │  gross_pnl, charges, net_pnl      │
│  │   Persisted to DB          │  saved to trading.db              │
│  │   Telegram notified        │  telegram.on_trade()              │
│  └────────────────────────────┘                                     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Database Schema

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DATABASE: trading.db (SQLite WAL)                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  signals                                                            │
│  ├─ signal_id (PK, UNIQUE)                                         │
│  ├─ strategy_id, instrument, side, signal_type                    │
│  ├─ timestamp, trigger_price, stop_price, quantity                 │
│  ├─ candle_data (JSON), indicator_data (JSON)                     │
│  └─ created_at                                                     │
│                                                                     │
│  trades                                                             │
│  ├─ trade_id (PK, UNIQUE)                                          │
│  ├─ strategy_id, instrument, side                                  │
│  ├─ entry_timestamp, entry_price                                   │
│  ├─ exit_timestamp, exit_price                                     │
│  ├─ quantity, multiplier                                           │
│  ├─ gross_pnl, charges, net_pnl                                   │
│  ├─ exit_reason, status                                            │
│  ├─ entry_signal_id → signals.signal_id (FK)                      │
│  └─ exit_signal_id                                                 │
│                                                                     │
│  orders                                                             │
│  ├─ order_id (PK, UNIQUE)                                          │
│  ├─ strategy_id, instrument, side, quantity                        │
│  ├─ order_type, price, state                                      │
│  ├─ filled_quantity, average_fill_price                           │
│  ├─ entry_signal_id → signals.signal_id (FK)                      │
│  └─ trade_id → trades.trade_id (FK)                               │
│                                                                     │
│  fills                                                              │
│  ├─ fill_id (PK, UNIQUE)                                           │
│  ├─ order_id → orders.order_id (FK)                               │
│  ├─ strategy_id, instrument, side, quantity, price                 │
│  ├─ timestamp                                                      │
│  ├─ entry_signal_id → signals.signal_id (FK)                      │
│  └─ trade_id → trades.trade_id (FK)                               │
│                                                                     │
│  account_snapshots                                                  │
│  ├─ timestamp, equity, realized_pnl, unrealized_pnl               │
│  ├─ used_margin, available_margin                                 │
│  └─ (equity curve data source)                                     │
│                                                                     │
│  events                                                             │
│  ├─ timestamp, event_type, strategy_id, instrument                │
│  └─ details (JSON)                                                 │
│                                                                     │
│  ═══════════════════════════════════════════════════════════════   │
│                                                                     │
│  DATABASE: analytics.db                                             │
│  ├─ trade_analytics   → Rich lifecycle audit per trade             │
│  └─ trade_events      → Event stream for dashboards               │
│                                                                     │
│  FILE: system_state.json                                            │
│  ├─ market_status      → Engine state for crash recovery           │
│  ├─ strategies         → Per-strategy state (position, stop, etc.) │
│  ├─ positions          → Open position snapshot                    │
│  ├─ account            → Global account state                      │
│  ├─ accounts_by_strategy → Per-strategy capital                    │
│  ├─ pnl                → Per-strategy P&L stats                    │
│  ├─ risk               → Kill switch, daily P&L, peak equity      │
│  └─ execution          → Current prices, orders, fills            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Dashboard Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD (FastAPI + React)                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Backend: dashboard/server.py                                       │
│  ├─ FastAPI app + CORS                                              │
│  ├─ WebSocket /ws → broadcasts engine_state @ 0.5s                │
│  ├─ REST API:                                                     │
│  │   ├─ /api/overview          → Portfolio summary                │
│  │   ├─ /api/overview/GOLDM   → Gold instrument overview          │
│  │   ├─ /api/overview/SILVERM → Silver instrument overview        │
│  │   ├─ /api/strategies       → Strategy states + P&L            │
│  │   ├─ /api/positions        → Open positions                    │
│  │   ├─ /api/orders           → Order history                     │
│  │   ├─ /api/fills            → Fill records                      │
│  │   ├─ /api/trades           → Trade history                     │
│  │   ├─ /api/pnl              → P&L breakdown                    │
│  │   ├─ /api/market-data      → Live LTP + tick counts           │
│  │   ├─ /api/risk             → Risk engine state                │
│  │   ├─ /api/health           → System health                    │
│  │   ├─ /api/indicators       → DEMAATR values                   │
│  │   ├─ /api/settings         → Config management                │
│  │   ├─ /api/reconciliation   → Trade reconciliation            │
│  │   └─ /api/audit            → Audit log                        │
│  └─ API key gate (optional)                                        │
│                                                                     │
│  Frontend: dashboard-ui/ (React + TypeScript + Vite)                │
│  ├─ Pages:                                                        │
│  │   ├─ Overview        → Portfolio summary + equity              │
│  │   ├─ LiveTrading     → Positions + fills + strategies          │
│  │   ├─ MarketData      → Live LTP per instrument + tick counts   │
│  │   ├─ Strategies      → Per-strategy detail + P&L              │
│  │   ├─ Positions       → Open + closed positions                 │
│  │   ├─ Orders          → Order audit trail                       │
│  │   ├─ Trades          → Trade history with P&L                 │
│  │   ├─ Pnl             → P&L breakdown                          │
│  │   ├─ Indicators      → DEMAATR values live                     │
│  │   ├─ Risk            → Risk limits + kill switch               │
│  │   ├─ Health          → Component health status                 │
│  │   ├─ Settings        → Config editor                           │
│  │   └─ AuditLog        → System event log                       │
│  ├─ DataProvider.tsx     → WebSocket + REST state management      │
│  └─ useDataSelector      → Selective re-render optimization       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. Instrument Configuration

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INSTRUMENTS (config/settings.json)               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  GOLDM (Gold Mini)                                                  │
│  ├─ Symbol: MCX:GOLDM202610                                        │
│  ├─ Security ID: 569003                                             │
│  ├─ Exchange: MCX_COMM / FUTCOM                                    │
│  ├─ Multiplier: 10.0                                               │
│  ├─ Tick size: 1.0                                                 │
│  ├─ Lot size: 1                                                    │
│  ├─ Session: 09:00 - 23:30 (870 min)                              │
│  └─ Margin model: slope=0.125, intercept=126930                   │
│                                                                     │
│  SILVERM (Silver Mini)                                              │
│  ├─ Symbol: MCX:SILVERM202611                                      │
│  ├─ Security ID: 483080                                             │
│  ├─ Exchange: MCX_COMM / FUTCOM                                    │
│  ├─ Multiplier: 5.0                                                │
│  ├─ Tick size: 1.0                                                 │
│  ├─ Lot size: 1                                                    │
│  ├─ Session: 09:00 - 23:30 (870 min)                              │
│  └─ Margin model: slope=0.0625, intercept=142900                  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                    STRATEGIES                                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  gold_01: GOLDM  5m fast + 15m mid + 1h HTF   ₹3L capital         │
│  gold_02: GOLDM  15m fast + 15m mid + 1h HTF  ₹3L capital         │
│  silver_01: SILVERM 15m fast + 15m mid + 1h HTF ₹3L capital       │
│  silver_02: SILVERM 5m fast + 15m mid + 1h HTF  ₹3L capital       │
│                                                                     │
│  Total capital: ₹12,00,000 (₹3,00,000 per strategy)               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. Indicator Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DEMA-ATR INDICATOR PIPELINE                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Per instrument × timeframe:                                        │
│  GOLDM:5m, GOLDM:15m, GOLDM:1h                                   │
│  SILVERM:5m, SILVERM:15m, SILVERM:1h                             │
│                                                                     │
│  DEMAATR (dema_atr.py)                                              │
│  ├─ DEMA(3) — Double Exponential Moving Average                   │
│  │   └─ EMA1 = EMA(close, period=3)                               │
│  │      EMA2 = EMA(EMA1, period=3)                                │
│  │      DEMA = 2*EMA1 - EMA2                                      │
│  │                                                                 │
│  ├─ ATR(6) — Average True Range                                   │
│  │   └─ TR = max(H-L, |H-prevC|, |L-prevC|)                      │
│  │      ATR = EMA(TR, period=6)                                   │
│  │                                                                 │
│  └─ DEMA-ATR = DEMA + ATR × factor(1.0)                          │
│     (upper band for trend confirmation)                            │
│                                                                     │
│  HTF Engine (backtest_style_htf.py)                                │
│  ├─ 1h signal line: DEMA-ATR on 1H bars                          │
│  ├─ 15m confirmation: DEMA-ATR on 15m bars                       │
│  └─ map_to_fast_bar(): searchsorted mapping to fast TF            │
│                                                                     │
│  Signal Logic:                                                      │
│  ├─ LONG:  close > htf_dema_atr AND prev_close <= prev_htf       │
│  │         AND mid_dema_atr < htf_dema_atr (15m confirms)         │
│  │                                                                 │
│  └─ SHORT: close < htf_dema_atr AND prev_close >= prev_htf       │
│            AND mid_dema_atr > htf_dema_atr (15m confirms)         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Deployment

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT                                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Docker:                                                            │
│  ├─ Dockerfile (Python 3.11)                                       │
│  ├─ docker-compose.yml                                             │
│  └─ mcx-trader.env (secrets)                                       │
│                                                                     │
│  Entry points:                                                      │
│  ├─ main.py           → CLI mode (headless)                       │
│  └─ dashboard/run.py  → Dashboard mode (web UI)                   │
│                                                                     │
│  VPS deployment:                                                    │
│  ├─ deploy_vps.py     → SCP + Docker rebuild                      │
│  └─ verify_vps.py     → Post-deploy health check                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Key Design Principles

```
1. REST = Source of Truth for Candles
   WebSocket is ONLY for live LTP display.
   All signals come from REST candles (backtest parity).

2. One Trade = One trade_id
   TradeContext is immutable identity. Every lifecycle object
   references it. No component independently creates trade identity.

3. Paper Only
   PaperExecutionEngine is the ONLY execution mode.
   Live trading is explicitly blocked.

4. Crash Recovery
   system_state.json saved every 60s + on shutdown.
   Restored on startup via engine.restore().

5. Deduplication
   WebSocket: (security_id, ltt, ltp) dedup key.
   DB: INSERT OR IGNORE / ON CONFLICT for idempotency.

6. Safe Mode
   Automatic on reconciliation failure or stale data.
   Blocks new entries but allows exits.

7. Backtest Parity
   Live strategy uses EXACT same logic as backtest.
   Candles from REST (not built from ticks).
   HTF mapping via searchsorted (not time-based).
```
