# 12 - Frontend Reconciliation Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Frontend Architecture

**Location:** `dashboard-ui/` (Vue/React SPA, built to `dashboard-ui/dist/`)

### Serving Strategy

**File:** `server.py:291-398`

```python
_frontend_dist = Path(__file__).resolve().parent.parent / "dashboard-ui" / "dist"
_frontend_available = _frontend_dist.exists()

# Mount static assets
if _frontend_available:
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")))

# Catch-all for SPA routing
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("ws"):
        raise HTTPException(status_code=404)
    file_path = _frontend_dist / full_path
    if file_path.is_file():
        return FileResponse(str(file_path))
    return FileResponse(str(_frontend_dist / "index.html"))
```

---

## 2. Data Consumption by Frontend

### Real-Time Data (WebSocket)

**Connection:** `ws://host:port/ws`

**Events received:**
1. `engine_state` — Full engine snapshot every 1 second
2. `events` — Recent event bus entries every 0.5 seconds

**engine_state structure (from `TradingEngine.snapshot()`):**

```json
{
  "timestamp": "ISO-8601",
  "market_status": { "market_state", "engine_status", "data_status", ... },
  "strategies": {
    "gold_01": {
      "strategy_id": "gold_01",
      "instrument": "GOLDM",
      "state": "flat|long_position|short_position|...",
      "position_side": "LONG|SHORT|null",
      "stop_price": 12345.0,
      "bars_processed": 100,
      "enabled": true,
      "fast_timeframe": "5m",
      "htf_timeframe": "1h",
      "quantity": 1,
      "realized_net": 5000.0,
      "trade_count": 10,
      "wins": 6,
      "losses": 4,
      "win_rate": 60.0
    }
  },
  "positions": {
    "open_positions": {
      "uuid-position-id": {
        "position_id": "...",
        "strategy_id": "...",
        "instrument": "GOLDM",
        "side": "LONG",
        "quantity": 1,
        "average_entry": 12345.0,
        "entry_timestamp": 1234567890.0,
        "stop_price": 12300.0,
        "unrealized_pnl": 200.0,
        "margin": 50000.0,
        "status": "open"
      }
    },
    "closed_count": 5
  },
  "account": {
    "starting_capital": 300000.0,
    "equity": 310000.0,
    "used_margin": 50000.0,
    "available_margin": 260000.0
  },
  "accounts_by_strategy": { ... },
  "pnl": { ... },
  "risk": { "daily_pnl": 0.0, "peak_equity": 0.0, ... },
  "execution": { "orders_count": 5, "fills_count": 10, ... },
  "health": { "overall_status": "healthy", ... }
}
```

### REST Data (Polling)

| Endpoint | Used For | Update Pattern |
|----------|----------|---------------|
| `/api/analytics/strategies` | Performance overview | On-demand |
| `/api/analytics/strategies/{id}/trades` | Trade history table | On-demand |
| `/api/analytics/strategies/{id}/equity` | Equity curve chart | On-demand |
| `/api/analytics/strategies/{id}/drawdown` | Drawdown chart | On-demand |
| `/api/analytics/strategies/{id}/daily` | Daily P&L table | On-demand |
| `/api/analytics/trades/{id}` | Trade detail modal | On-demand |
| `/api/analytics/open-trades` | Open trades panel | On-demand |
| `/api/analytics/events` | Event log | On-demand |

---

## 3. Frontend-Backend Data Alignment

### Strategy Display

| Frontend Field | Source | Transform |
|---------------|--------|-----------|
| Strategy name | `strategies[name].strategy_id` | None |
| Instrument | Config | None |
| State | `strategies[name].state` | Enum → string |
| Position side | `strategies[name].position_side` | None |
| Stop price | `strategies[name].stop_price` | None |
| Realized P&L | `pnl_engines[name].snapshot().realized_net` | Enriched in server |
| Trade count | `pnl_engines[name].snapshot().trade_count` | Enriched in server |
| Win rate | `pnl_engines[name].snapshot().win_rate` | Enriched in server |

### Position Display

| Frontend Field | Source |
|---------------|--------|
| Position ID | `position.position_id` |
| Strategy | `position.strategy_id` |
| Instrument | `position.instrument` |
| Side | `position.side` |
| Entry price | `position.average_entry` |
| Current price | `execution_engine._current_prices[instrument]` |
| Unrealized P&L | `position.unrealized_pnl` |
| Margin | `position.margin` |

### Account Display

| Frontend Field | Source |
|---------------|--------|
| Starting capital | Config (NOT from saved state) |
| Equity | `account_engine.equity` = starting + realized + unrealized |
| Used margin | `account_engine.used_margin` |
| Available margin | `account_engine.available_margin` = equity - used |

---

## 4. Known Discrepancies

### A. Closed positions count in WS vs DB

- WS push shows `closed_count: N` (from `PositionManager._closed_positions`)
- DB may have more closed trades (PositionManager caps at 500, trims to 250)
- **Impact:** Frontend may undercount historical trades

### B. Equity curve from analytics vs live account

- Analytics equity curve: reconstructed from `trades_analytics` (closes)
- Live equity: `AccountEngine.equity` (running total)
- **Impact:** Different values if trades_analytics has gaps

### C. P&L rounding

- Analytics API rounds to 2 decimal places: `round(perf.net_pnl, 2)`
- Live engine state shows full float precision
- **Impact:** Minor display differences

---

## 5. WebSocket Reconnection

**Not implemented in the analysis.** The frontend must handle:
1. Connection drops
2. Reconnection logic
3. State resync after reconnect

The server does not send missed events on reconnect — only current state.

---

## 6. Dashboard Commands

| Command | Frontend Trigger | Server Action |
|---------|-----------------|---------------|
| Pause strategy | Button click | `strat.enabled = False` |
| Resume strategy | Button click | `strat.enabled = True` |
| Emergency stop | Button click | All strategies set to FLAT |
| Get snapshot | Debug panel | Returns full engine state |
| Get trades | Debug panel | Returns DB trades |
