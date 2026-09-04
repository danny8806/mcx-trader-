# 02 - Actual Runtime Flow: Startup to Shutdown

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Application Startup (`main.py`)

```
main.py
  └── uvicorn.run(server.app)
       └── FastAPI lifespan context manager (server.py:177)
```

## 2. Lifespan Startup Sequence (`server.py:177-239`)

| Step | File:Line | Function | Action |
|------|-----------|----------|--------|
| 2.1 | `server.py:184` | `TradingEngine(event_callback=_on_engine_event)` | Create engine instance |
| 2.2 | `server.py:192` | `PersistenceManager(state_path=..., db_path=...)` | Create persistence layer |
| 2.3 | `server.py:203` | `_engine.set_persistence(_persistence)` | Wire persistence to engine |
| 2.4 | `server.py:204` | `_persistence.load_state()` | Load saved JSON state |
| 2.5 | `server.py:206` | `_engine.restore(saved)` | Restore in-memory state |
| 2.6 | `server.py:216` | `_engine.start()` | Start the engine |
| 2.7 | `server.py:222-232` | Route module `init()` calls | Initialize all route handlers |
| 2.8 | `server.py:235` | `asyncio.create_task(_push_updates())` | Start WS push task |
| 2.9 | `server.py:236` | `asyncio.create_task(_push_events())` | Start event push task |
| 2.10 | `server.py:237` | `asyncio.create_task(_periodic_save_state())` | Start periodic save task |

## 3. TradingEngine.__init__ (`trading_engine.py:43-101`)

| Step | File:Line | Component | Init Method |
|------|-----------|-----------|-------------|
| 3.1 | `trading_engine.py:46-49` | Config | `Config().load()` |
| 3.2 | `trading_engine.py:55` | MarketStatus | `_init_market_status()` → `MarketStatus(session_open, session_close)` |
| 3.3 | `trading_engine.py:56` | DataAdapter | `_init_data_adapter()` → `DhanDataAdapter(client_id, token_file, on_tick, on_status)` |
| 3.4 | `trading_engine.py:57` | TimeframeEngine | `_init_timeframe_engine()` → `CandleFetcher(data_adapter, instruments, on_candle_closed)` |
| 3.5 | `trading_engine.py:58` | Indicators | `_init_indicator_engines()` → Creates `DEMAATR` per instrument/timeframe |
| 3.6 | `trading_engine.py:59` | HTF Engine | `_init_htf_engine()` → `BacktestStyleHTFEngine()` + register per instrument |
| 3.7 | `trading_engine.py:60` | Strategies | `_init_strategies()` → Creates `Gold/SilverStrategy01-04` instances |
| 3.8 | `trading_engine.py:61` | Execution | `_init_execution()` → `PaperExecutionEngine()` + `OrderManager()` |
| 3.9 | `trading_engine.py:62` | Portfolio | `_init_portfolio()` → `PositionManager()` + per-strategy `PNLEngine` + `AccountEngine` |
| 3.10 | `trading_engine.py:63` | Risk | `_init_risk()` → `RiskEngine(max_positions, max_daily_loss, ...)` |
| 3.11 | `trading_engine.py:64` | Monitoring | `_init_monitoring()` → `HealthMonitor()` |
| 3.12 | `trading_engine.py:65` | Notifications | `_init_notifications()` → `TelegramRouter()` |
| 3.13 | `trading_engine.py:68-72` | EventStore | `EventStore(analytics.db)` |
| 3.14 | `trading_engine.py:75-78` | TradeLedger | `TradeLedger(analytics.db)` |
| 3.15 | `trading_engine.py:82` | FillDedup | `FillDeduplicator(trading.db)` |
| 3.16 | `trading_engine.py:85` | SafeMode | `SafeModeManager(market_status)` |

## 4. Engine.start() (`trading_engine.py:337-565`)

| Step | File:Line | Action |
|------|-----------|--------|
| 4.1 | `trading_engine.py:342` | `market_status.set_engine_status(INITIALIZING)` |
| 4.2 | `trading_engine.py:347-358` | Wire `TradeCloseManager(position_manager, pnl_engines, account_engines, risk_engine, persistence, event_store, telegram, trade_ledger)` |
| 4.3 | `trading_engine.py:362` | `fill_dedup.load_from_database()` — load dedup state from DB |
| 4.4 | `trading_engine.py:368` | `market_status.set_engine_status(RECONCILING)` |
| 4.5 | `trading_engine.py:370-386` | `ReconciliationEngine.reconcile(phase="startup")` — compare DB vs memory |
| 4.6 | `trading_engine.py:382` | If inconsistent: `safe_mode.enter_safe_mode("reconciliation_failed")` |
| 4.7 | `trading_engine.py:389` | `market_status.set_engine_status(WARMING_UP)` |
| 4.8 | `trading_engine.py:390` | `_warmup_from_rest()` — fetch 7-day 5m candles via REST, resample, feed indicators |
| 4.9 | `trading_engine.py:391` | `market_status.mark_warmup_done()` |
| 4.10 | `trading_engine.py:394` | `candle_fetcher.start()` — start polling thread |
| 4.11 | `trading_engine.py:396` | `data_adapter.connect()` — connect WebSocket for LTP |
| 4.12 | `trading_engine.py:400-404` | Wait up to 10s for WebSocket connection |
| 4.13 | `trading_engine.py:407` | Sleep 2s to collect first ticks |
| 4.14 | `trading_engine.py:409-492` | Collect 13 health checks (WS, LTP, CandleFetcher, Indicators, HTF, Strategies, Capital, P&L, Risk, Execution, Telegram, Persistence, Analytics) |
| 4.15 | `trading_engine.py:496-555` | Format startup report + send via Telegram |
| 4.16 | `trading_engine.py:565` | `market_status.set_engine_status(READY)` |

## 5. Live Trading Event Loop

### 5a. WebSocket Tick → `trading_engine._on_tick()` (`trading_engine.py:585-674`)

```
WebSocket tick arrives
  → _on_tick(tick)
    → execution_engine.update_price(instrument, ltp)        [trading_engine.py:628]
    → position_manager positions.update_mark(ltp)            [trading_engine.py:633]
    → account_engines.update_unrealized_pnl()                [trading_engine.py:638-639]
    → pnl_engines.update_unrealized_pnl()                   [trading_engine.py:642]
    → risk_engine.update_peak_equity()                       [trading_engine.py:650]
    → _process_deferred_exit(strat, None, ltp)               [trading_engine.py:666] (if tick_signal_processing)
    → strat.on_tick(ltp, timestamp)                          [trading_engine.py:672]
      → If pending_entry triggered → _process_signal(signal)
```

### 5b. CandleFetcher → `trading_engine._on_bar_closed()` (`trading_engine.py:676-729`)

```
CandleFetcher detects candle close
  → REST API fetch → Bar object created
    → on_candle_closed(bar)
      → _on_bar_closed(bar)
        → indicator.update(OHLC)                             [trading_engine.py:689]
        → htf_engine.on_htf_bar_closed(bar) (if 1H/15m)     [trading_engine.py:693]
        → strat.on_bar(bar, htf_mapped, fast_value, mid)     [trading_engine.py:720]
          → If signal: _process_signal(signal)
        → strat._consume_same_bar_stop(bar)                  [trading_engine.py:727]
          → If stop: _process_signal(stop_signal)
```

### 5c. Signal Processing → `trading_engine._process_signal()` (`trading_engine.py:799-957`)

```
_process_signal(signal)
  → Check safe_mode (entries blocked, exits allowed)         [trading_engine.py:806-816]
  → Check market_status.is_trading_allowed                   [trading_engine.py:813-816]
  → risk_engine.check_order()                                [trading_engine.py:833-840]
  → order_manager.submit_signal(signal)                      [trading_engine.py:889]
    → PaperExecutionEngine.create_order(signal)              [paper_broker.py:101-124]
    → PaperExecutionEngine.submit_order(order)               [paper_broker.py:126-145]
      → _execute_order(order)                                [paper_broker.py:147-185]
        → slippage simulation, create Fill
  → persistence.save_order(order)                            [trading_engine.py:895-912]
  → order_manager.drain_fills()                              [trading_engine.py:915]
  → _on_fill(fill) for each fill                             [trading_engine.py:916]
```

### 5d. Fill Processing → `trading_engine._on_fill()` (`trading_engine.py:978-1271`)

```
_on_fill(fill)
  → fill_dedup.is_duplicate(fill.fill_id)                   [trading_engine.py:981]
  → fill_dedup.mark_processed(fill.fill_id)                 [trading_engine.py:984]
  → Check if entry or exit:
    ENTRY (no open position):
      → account.block_margin(margin)                         [trading_engine.py:1003]
      → global_account.block_margin(margin)                  [trading_engine.py:1004]
      → persistence.save_fill(fill)                          [trading_engine.py:1018-1027]
      → position_manager.open_position(fill)                 [trading_engine.py:1029-1033]
      → event_store.record(POSITION_OPENED)                  [trading_engine.py:1047-1056]
      → trade_ledger.create_trade(position_id=position_id)   [trading_engine.py:1070-1090]
      → trade_ledger.record_fill(is_entry=True)              [trading_engine.py:1091-1101]
      → telegram.on_fill()                                   [trading_engine.py:1104-1117]
    
    EXIT (has open position):
      → trade_close_manager.close_position(fill, position)   [trading_engine.py:1153-1157]
        Step 1: Calculate P&L (pure)                          [trade_close.py:64-84]
        Step 2-3: Persist trade + fill in transaction         [trade_close.py:98-130]
        Step 4: Close position in memory                      [trade_close.py:138-145]
        Step 5: Update account P&L                            [trade_close.py:148-157]
        Step 6: Update risk engine                            [trade_close.py:160-167]
        Step 6b: Close trade in ledger                        [trade_close.py:170-207]
        Step 7: Record event                                  [trade_close.py:210-227]
        Step 7b: Publish to EventBus                          [trade_close.py:230-260]
        Step 8: Send Telegram                                 [trade_close.py:263-294]
```

## 6. Periodic Background Tasks

| Task | File:Line | Interval | Action |
|------|-----------|----------|--------|
| WS Push | `server.py:132-148` | 1s | `engine.snapshot()` → `ws_manager.broadcast("engine_state")` |
| Event Push | `server.py:151-168` | 0.5s | `event_bus.get_recent()` → `ws_manager.broadcast("events")` |
| State Save | `server.py:111-125` | 60s | `engine.snapshot()` → `persistence.save_state(state)` |
| Candle Check | `core/candle_fetcher.py:90` | 30s | Check if any candle closed, fetch from REST |

## 7. Shutdown Sequence (`server.py:240-268`)

| Step | File:Line | Action |
|------|-----------|--------|
| 7.1 | `server.py:241-243` | Cancel background tasks |
| 7.2 | `server.py:249-253` | Shutdown thread pool executors |
| 7.3 | `server.py:257` | `engine.snapshot()` — final state capture |
| 7.4 | `server.py:259` | `persistence.save_state(state)` — persist to disk |
| 7.5 | `server.py:262-263` | Stop token scheduler, stop engine |
| 7.6 | `server.py:265-266` | `persistence.close()` — close DB connection |
