# 07 - Trade Lifecycle Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## Complete Lifecycle: Signal → Trade → Order → Fill → Position → Exit → P&L → DB → API → Frontend

### Phase 1: Signal Generation

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 1.1 | CandleFetcher | `core/candle_fetcher.py:148` | REST API fetch → Bar object |
| 1.2 | DEMAATR | `indicators/dema_atr.py:56` | Update indicator with OHLC |
| 1.3 | HTF Engine | `htf/backtest_style_htf.py` | Map 1H value to fast bar |
| 1.4 | Strategy.on_bar | `strategies/base_dema_strategy.py:104` | Evaluate crossover conditions |
| 1.5 | Strategy | `strategies/base_dema_strategy.py:334` | Create PendingEntry (breakout model) |
| 1.6 | Strategy | Returns `Signal` with trigger_price, stop_price, metadata |

### Phase 2: Signal Processing

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 2.1 | TradingEngine._process_signal | `trading_engine.py:799` | Receive signal |
| 2.2 | SafeMode check | `trading_engine.py:806` | Block if safe mode (entries only) |
| 2.3 | MarketStatus check | `trading_engine.py:813` | Block if not trading allowed |
| 2.4 | RiskEngine.check_order | `trading_engine.py:833` | Check margin, positions, drawdown |
| 2.5 | OrderManager.submit_signal | `trading_engine.py:889` | Submit to execution |

### Phase 3: Order Execution

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 3.1 | OrderManager.submit_signal | `execution/order_manager.py:33` | Deduplicate signal |
| 3.2 | PaperExecutionEngine.create_order | `execution/paper_broker.py:101` | Create Order object |
| 3.3 | PaperExecutionEngine.submit_order | `execution/paper_broker.py:126` | Submit for execution |
| 3.4 | PaperExecutionEngine._execute_order | `execution/paper_broker.py:147` | Simulate slippage, create Fill |
| 3.5 | OrderManager.drain_fills | `execution/order_manager.py:84` | Return fills to caller |

### Phase 4: Fill Processing (Entry)

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 4.1 | TradingEngine._on_fill | `trading_engine.py:978` | Receive fill |
| 4.2 | FillDeduplicator | `trading_engine.py:981` | Check for duplicate fill_id |
| 4.3 | FillDeduplicator.mark_processed | `trading_engine.py:984` | Mark as processed in DB |
| 4.4 | AccountEngine.block_margin | `trading_engine.py:1003` | Block margin (per-strategy) |
| 4.5 | Global AccountEngine.block_margin | `trading_engine.py:1004` | Block margin (global) |
| 4.6 | Persistence.save_fill | `trading_engine.py:1018` | Persist fill to DB |
| 4.7 | PositionManager.open_position | `trading_engine.py:1029` | Create Position object |
| 4.8 | EventStore.record | `trading_engine.py:1047` | Record POSITION_OPENED event |
| 4.9 | TradeLedger.create_trade | `trading_engine.py:1070` | Create trade record (trade_id = position_id) |
| 4.10 | TradeLedger.record_fill | `trading_engine.py:1091` | Record entry fill leg |
| 4.11 | TelegramRouter.on_fill | `trading_engine.py:1104` | Send Telegram notification |

### Phase 5: Position Monitoring

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 5.1 | TradingEngine._on_tick | `trading_engine.py:631` | On each tick |
| 5.2 | Position.update_mark | `trading_engine.py:633` | Update unrealized P&L |
| 5.3 | AccountEngine.update_unrealized_pnl | `trading_engine.py:638` | Update account unrealized |
| 5.4 | RiskEngine.update_peak_equity | `trading_engine.py:650` | Update drawdown tracking |

### Phase 6: Exit (TradeCloseManager)

| Step | Component | File:Line | Action |
|------|-----------|-----------|--------|
| 6.1 | TradingEngine._on_fill (exit) | `trading_engine.py:1146` | Detect exit fill |
| 6.2 | TradeCloseManager.close_position | `trading_engine.py:1153` | Enter atomic close |
| 6.3 | PNLEngine.calculate_realized_pnl | `core/trade_close.py:64-84` | Calculate P&L (pure) |
| 6.4 | Persistence.save_trade_and_fill | `core/trade_close.py:98-130` | **Persist FIRST** (transaction) |
| 6.5 | PositionManager.close_position | `core/trade_close.py:138-145` | Close in memory |
| 6.6 | AccountEngine.update_realized_pnl | `core/trade_close.py:148-157` | Update realized P&L |
| 6.7 | AccountEngine.release_margin | `core/trade_close.py:152` | Release blocked margin |
| 6.8 | RiskEngine.update_daily_pnl | `core/trade_close.py:162` | Update daily P&L |
| 6.9 | TradeLedger.close_trade | `core/trade_close.py:186` | Close trade in analytics |
| 6.10 | EventStore.record | `core/trade_close.py:212` | Record TRADE_CLOSED event |
| 6.11 | EventBus publish | `core/trade_close.py:232` | Publish to dashboard |
| 6.12 | TelegramRouter.on_trade_close | `core/trade_close.py:279` | Send Telegram notification |

### Phase 7: P&L Calculation

**File:** `portfolio/pnl.py:52-78`

```python
# LONG position:
gross = (exit_price - entry_price) * quantity * multiplier

# SHORT position:
gross = (entry_price - exit_price) * quantity * multiplier

# Charges (MCXFeeModel):
fees = brokerage + stt + exchange + sebi + gst + stamp_duty
net = gross - fees.total
```

### Phase 8: Database Persistence

| Table | Database | When Written | Key |
|-------|----------|-------------|-----|
| orders | trading.db | Before fill processing | order_id (UNIQUE) |
| fills | trading.db | Before position open | fill_id (UNIQUE) |
| trades | trading.db | On trade close (atomic) | trade_id (UNIQUE) = position_id |
| events | trading.db | On every event | Auto-increment id |
| account_snapshots | trading.db | (not actively written) | Auto-increment id |
| trade_events | analytics.db | On every lifecycle event | event_id (PRIMARY KEY) |
| trades_analytics | analytics.db | On trade create/close | trade_id (PRIMARY KEY) = position_id |
| trade_legs | analytics.db | On each fill leg | leg_id (PRIMARY KEY) |
| processed_fills | trading.db | On fill dedup check | fill_id (PRIMARY KEY) |

### Phase 9: API Exposure

| Endpoint | Data Source | Update Frequency |
|----------|------------|-----------------|
| WebSocket `/ws` engine_state | `TradingEngine.snapshot()` | 1 second |
| `/api/strategies` | Config + PnLEngine snapshots | On request |
| `/api/positions` | PositionManager.open_positions | On request |
| `/api/trades` | PersistenceManager.get_trades() | On request |
| `/api/analytics/strategies/*` | TradeLedger + PerformanceEngine | On request |
| `/api/analytics/trades/{id}` | TradeLedger + EventStore | On request |

### Phase 10: Frontend Display

| Component | Data Source | Update Method |
|-----------|------------|---------------|
| Strategy cards | WebSocket engine_state | Real-time push |
| Position list | WebSocket engine_state | Real-time push |
| P&L chart | Analytics API | REST polling |
| Trade history | Analytics API | REST polling |
| Event log | WebSocket events | Real-time push |

---

## Atomicity Guarantees

| Operation | Atomic? | Mechanism |
|-----------|---------|-----------|
| Trade close (persist + memory) | YES | TradeCloseManager: persist BEFORE memory |
| Order + fill | PARTIAL | Order persisted before fill, but crash between them possible |
| Fill + position open | PARTIAL | Fill persisted before position open |
| Risk check + order submit | YES | Single thread, RLock protected |

## Crash Recovery Points

| Crash Point | State | Recovery |
|-------------|-------|----------|
| After signal, before order | No DB write | Strategy restarts from FLAT |
| After order, before fill | Order in DB | Reconciliation detects no fill |
| After fill, before position | Fill in DB | Reconciliation detects orphan fill |
| After position, before trade | Position in memory | Reconciliation detects missing trade |
| After trade persist, before memory | Trade in DB, position open in memory | Reconciliation: trade closed but position open = ERROR |
