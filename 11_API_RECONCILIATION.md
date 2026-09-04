# 11 - API Reconciliation Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. API Route Inventory

### Dashboard Routes (server.py)

| Route | Method | Source File | Data Source |
|-------|--------|-------------|------------|
| `/ws` | WebSocket | `server.py:307` | Engine snapshot (1s push) |
| `/api/health` | GET | `server.py:376` | Engine + persistence status |
| `/api/overview` | GET | `dashboard/routes/overview.py` | Engine snapshot |
| `/api/strategies` | GET | `dashboard/routes/strategies.py` | Config + PnLEngine |
| `/api/positions` | GET | `dashboard/routes/positions.py` | PositionManager |
| `/api/orders` | GET | `dashboard/routes/orders.py` | OrderManager |
| `/api/trades` | GET | `dashboard/routes/trades.py` | PersistenceManager |
| `/api/pnl` | GET | `dashboard/routes/pnl.py` | PNLEngine |
| `/api/market-data` | GET | `dashboard/routes/market_data.py` | ExecutionEngine prices |
| `/api/risk` | GET | `dashboard/routes/risk.py` | RiskEngine |
| `/api/health/detail` | GET | `dashboard/routes/health.py` | HealthMonitor |
| `/api/reconciliation` | GET | `dashboard/routes/reconciliation.py` | ReconciliationEngine |
| `/api/alerts` | GET | `dashboard/routes/alerts.py` | Event bus |
| `/api/settings` | GET | `dashboard/routes/settings.py` | Config |
| `/api/audit-log` | GET | `dashboard/routes/audit_log.py` | PersistenceManager events |
| `/api/indicators` | GET | `dashboard/routes/indicators.py` | Indicator snapshots |

### Analytics Routes (analytics/routes.py)

| Route | Method | Data Source |
|-------|--------|------------|
| `/api/analytics/strategies` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/trades` | GET | TradeLedger |
| `/api/analytics/strategies/{id}/equity` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/drawdown` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/daily` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/monthly` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/time-of-day` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/day-of-week` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/mae-mfe` | GET | TradeLedger |
| `/api/analytics/strategies/{id}/rolling` | GET | PerformanceEngine |
| `/api/analytics/strategies/{id}/execution` | GET | TradeLedger |
| `/api/analytics/strategies/{id}/parameters` | GET | TradeLedger |
| `/api/analytics/correlation` | GET | PerformanceEngine |
| `/api/analytics/portfolio` | GET | PerformanceEngine |
| `/api/analytics/trades/{id}` | GET | TradeLedger + EventStore |
| `/api/analytics/open-trades` | GET | TradeLedger |
| `/api/analytics/events` | GET | EventStore |
| `/api/analytics/reconciliation` | GET | TradeLedger |
| `/api/analytics/status` | GET | Module state |

### WebSocket Events

| Event Type | Push Source | Frequency |
|------------|------------|-----------|
| `engine_state` | `TradingEngine.snapshot()` | 1 second |
| `events` | `EventBus.get_recent()` | 0.5 second |

### WebSocket Commands

| Command | Handler | Action |
|---------|---------|--------|
| `pause_strategy` | `server.py:343` | Disable strategy |
| `resume_strategy` | `server.py:354` | Enable strategy |
| `emergency_stop` | `server.py:360` | Stop all strategies |
| `get_snapshot` | `server.py:364` | Return engine snapshot |
| `get_trades` | `server.py:368` | Return DB trades |

---

## 2. Data Transformations

### Engine Snapshot → Frontend

**File:** `server.py:78-108` (_enrich_strategies)

```python
def _enrich_strategies(snap):
    for name, strat_snap in snap["strategies"].items():
        pnl_engine = _engine.pnl_engines.get(name)
        pnl_snap = pnl_engine.snapshot() if pnl_engine else {}
        enriched_strats[name] = {
            **strat_snap,
            "fast_timeframe": cfg.get("fast_timeframe"),
            "htf_timeframe": cfg.get("htf_timeframe"),
            "quantity": cfg.get("quantity"),
            "realized_net": pnl_snap.get("realized_net"),
            "trade_count": pnl_snap.get("trade_count"),
            "wins": pnl_snap.get("wins"),
            "losses": pnl_snap.get("losses"),
        }
```

**Transformation applied:** Strategy snapshot enriched with PnL data and config.

### Analytics Strategy → API Response

**File:** `analytics/routes.py:47-78`

```python
perf = _performance_engine.calculate_strategy_performance(sid)
return {
    "strategy_id": perf.strategy_id,
    "trade_count": perf.trade_count,
    "win_rate": round(perf.win_rate, 2),
    "profit_factor": round(perf.profit_factor, 2),
    "net_pnl": round(perf.net_pnl, 2),
    # ... more fields
}
```

**Transformation:** Raw PerformanceEngine output rounded to 2 decimal places.

---

## 3. Inconsistencies Found

### A. Dashboard trades vs Analytics trades

| Source | Endpoint | DB | Content |
|--------|----------|-----|---------|
| Dashboard | `/api/trades` | `trading.db:trades` | Simple trade records |
| Analytics | `/api/analytics/strategies/{id}/trades` | `analytics.db:trades_analytics` | Rich lifecycle trades |

**Issue:** Two different trade datasets from two databases. Dashboard shows operational trades; analytics shows lifecycle trades. They may have different counts if one DB write fails.

### B. Fill data availability

- Dashboard `/api/orders` shows orders from `PaperExecutionEngine._orders` (in-memory)
- No public API to query fills from `trading.db:fills` directly
- Reconciliation reads fills from DB but API serves from memory

### C. Event data split

- `trading.db:events` — written by `PersistenceManager.save_event()`
- `analytics.db:trade_events` — written by `EventStore.record()`
- Both contain events but different schemas and different content
- `/api/audit-log` reads from trading.db events
- `/api/analytics/events` reads from analytics.db trade_events

---

## 4. Missing API Endpoints

| Missing Endpoint | Impact |
|-----------------|--------|
| `GET /api/fills` | Cannot query fills from trading.db via API |
| `GET /api/fills/{fill_id}` | Cannot look up a specific fill |
| `GET /api/account-snapshots` | Cannot query account history |
| `DELETE /api/events/cleanup` | Cannot clean up unbounded events table |
| `POST /api/reconciliation/run` | Cannot trigger reconciliation on-demand |

---

## 5. Rate Limiting

**None.** All endpoints are unprotected. No rate limiting, no authentication.

---

## 6. CORS Configuration

**File:** `server.py:277-288`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Issue:** Only allows localhost origins. If deployed to a remote server, the frontend must be served from the same origin or CORS must be updated.
