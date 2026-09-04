# SOURCE OF TRUTH
## Gold/Silver MCX Paper Trading System

**Date:** 2026-08-27

---

## DATA SOURCES

| Data Type | Source of Truth | Location | Backup |
|-----------|----------------|----------|--------|
| OHLCV Candles | Dhan REST API | `/charts/intraday`, `/charts/historical` | None (API only) |
| Live LTP | Dhan WebSocket | `wss://api-feed.dhan.co` | Last known tick |
| Trade Records | SQLite | `trading.db` table `trades` | `system_state.json` |
| Order Records | SQLite | `trading.db` table `orders` | `system_state.json` |
| Fill Records | SQLite | `trading.db` table `fills` | `system_state.json` |
| Events | SQLite | `analytics.db` table `events` | None |
| Account State | In-memory | `AccountEngine` objects | `system_state.json` |
| Position State | In-memory | `PositionManager` | `system_state.json` |
| P&L State | In-memory | `PNLEngine` objects | `system_state.json` |
| Risk State | In-memory | `RiskEngine` | `system_state.json` |
| Strategy State | In-memory | `BaseDEMAStrategy` objects | `system_state.json` |
| Indicator State | In-memory | `DEMAATR` objects | `system_state.json` |
| HTF Engine State | In-memory | `BacktestStyleHTFEngine` | `system_state.json` |
| Market Status | In-memory | `MarketStatus` object | `system_state.json` |
| Configuration | JSON file | `config/settings.json` | None (manual) |
| Token | JSON file | `dhan_token.json` | Auto-renewed |

## PERSISTENCE PRIORITY

1. **SQLite (trading.db):** Authoritative for trades, orders, fills
2. **SQLite (analytics.db):** Authoritative for events, trade ledger
3. **JSON (system_state.json):** Authoritative for all in-memory state
4. **Config (settings.json):** Authoritative for configuration

## RESTART RECOVERY FLOW

```
Startup
  ↓
Load config (settings.json)
  ↓
Initialize all components (in-memory defaults)
  ↓
Restore from system_state.json
  ↓
  ├── Market status (daily flags reset if new day)
  ├── Strategy state (position_side, stop_price, pending_entry)
  ├── Position state (open/closed positions)
  ├── Account state (realized_pnl, unrealized_pnl, charges, used_margin)
  │   └── starting_capital: ALWAYS from config (never restored)
  ├── P&L state (realized_gross, realized_charges, trade_count)
  ├── Risk state (kill_switch, daily_pnl, peak_equity)
  ├── Execution state (current_prices, fills, orders)
  ├── Indicator state (DEMA-ATR values)
  └── HTF engine state (values, end_times)
  ↓
Load fill dedup from trading.db
  ↓
Run reconciliation (compare DB vs memory)
  ↓
Start trading
```

## FINANCIAL CALCULATIONS

| Calculation | Formula | Source |
|-------------|---------|--------|
| Equity | `starting_capital + realized_pnl + unrealized_pnl` | account.py:57-59 |
| Net P&L | `realized_pnl + unrealized_pnl` | account.py:62-64 |
| Available Margin | `equity - used_margin` | account.py:72-74 |
| Gross P&L (LONG) | `(exit - entry) × quantity × multiplier` | pnl.py:61-62 |
| Gross P&L (SHORT) | `(entry - exit) × quantity × multiplier` | pnl.py:63-64 |
| Net P&L | `gross_pnl - fees.total` | pnl.py:72 |
| Drawdown % | `(peak_equity - current_equity) / peak_equity × 100` | risk_engine.py:78 |
