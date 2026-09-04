# 15 - Equity Graph Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Equity Curve Data Sources

### Source A: Analytics PerformanceEngine

**File:** `analytics/routes.py:179-186`

```python
@router.get("/api/analytics/strategies/{strategy_id}/equity")
async def get_strategy_equity(strategy_id: str, starting_equity: float = 1_000_000):
    curve = _performance_engine.calculate_equity_curve(strategy_id, starting_equity)
    return {"strategy_id": strategy_id, "equity_curve": curve}
```

**Data:** Reconstructed from `trades_analytics` table (closed trades only)

**Algorithm (from PerformanceEngine):**
```python
equity = starting_equity
curve = [{"timestamp": start, "equity": equity}]
for trade in closed_trades (ordered by closed_at):
    equity += trade.net_pnl
    curve.append({"timestamp": trade.closed_at, "equity": equity})
```

### Source B: Live Account Engine

**File:** `portfolio/account.py:56-59`

```python
@property
def equity(self) -> float:
    return self.starting_capital + self.realized_pnl + self.unrealized_pnl
```

**Data:** Real-time from `AccountEngine` (in-memory, updated on every tick and trade close)

---

## 2. Equity Curve API Response

### Response Format

```json
{
  "strategy_id": "gold_01",
  "equity_curve": [
    {"timestamp": 1234567890.0, "equity": 300000.0},
    {"timestamp": 1234567900.0, "equity": 301500.0},
    ...
  ],
  "count": 50
}
```

### Data Points

Each point represents a trade close:
- `timestamp`: `trade.closed_at` (epoch seconds)
- `equity`: Running equity after this trade

---

## 3. Drawdown Curve

**File:** `analytics/routes.py:193-200`

```python
@router.get("/api/analytics/strategies/{strategy_id}/drawdown")
async def get_strategy_drawdown(strategy_id: str, starting_equity: float = 1_000_000):
    curve = _performance_engine.calculate_drawdown_curve(strategy_id, starting_equity)
    return {"strategy_id": strategy_id, "drawdown_curve": curve}
```

**Algorithm:**
```python
peak = starting_equity
for trade in closed_trades:
    equity += trade.net_pnl
    peak = max(peak, equity)
    drawdown = (peak - equity) / peak * 100
    curve.append({"timestamp": trade.closed_at, "drawdown": drawdown})
```

---

## 4. Real-Time Equity (WebSocket)

**File:** `trading_engine.py:1393-1426` (snapshot)

```python
"account": self.account_engine.snapshot(),
```

**Snapshot contents:**
```json
{
  "starting_capital": 300000.0,
  "cash": 305000.0,
  "realized_pnl": 5000.0,
  "unrealized_pnl": 200.0,
  "charges": 500.0,
  "used_margin": 50000.0,
  "equity": 305200.0,
  "available_margin": 255200.0,
  "net_pnl": 5200.0
}
```

---

## 5. Equity Curve Consistency

### Discrepancy: Starting Equity

| Source | Default | Notes |
|--------|---------|-------|
| Analytics API | 1,000,000 (param) | User can override via query param |
| Live engine | Config value | Per-strategy or global |
| AccountEngine | 300,000 per strategy | From config |

**Impact:** Analytics equity curve may show different starting point than live engine if `starting_equity` param is not set correctly.

### Discrepancy: Closed Trade Data

| Source | Content |
|--------|---------|
| Analytics | Only trades with status='CLOSED' in trades_analytics |
| Live engine | Realized P&L includes all closed trades |

**Impact:** If a trade close was written to `trading.db` but not `analytics.db` (or vice versa), the curves diverge.

### Discrepancy: Unrealized P&L

| Source | Includes Unrealized? |
|--------|---------------------|
| Analytics equity curve | NO (closed trades only) |
| Live engine equity | YES |

**Impact:** Real-time equity is always higher/lower than analytics curve when positions are open.

---

## 6. Equity Graph Update Frequency

| Source | Update Method | Frequency |
|--------|--------------|-----------|
| WebSocket engine_state | Push | 1 second |
| Analytics equity curve | REST | On-demand (polling) |
| Equity chart (frontend) | WebSocket + REST | Depends on frontend |

---

## 7. Missing Equity Data

| Scenario | Impact |
|----------|--------|
| No trades executed | Flat line at starting equity |
| Trade in DB but not in analytics | Equity curve undercounts |
| Trade in analytics but not in DB | Equity curve overcounts |
| Starting equity mismatch | Entire curve shifted |
