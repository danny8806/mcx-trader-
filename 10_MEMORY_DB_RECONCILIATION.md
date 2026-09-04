# 10 - Memory vs DB State Reconciliation

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Reconciliation Engine

**File:** `reconciliation/engine.py`

### Checks Performed

| Check | File:Line | Compares | Severity |
|-------|-----------|----------|----------|
| Orders vs Fills | `engine.py:215-252` | DB orders ↔ DB fills | ERROR if FILLED order has no fills |
| Fills vs Positions | `engine.py:254-292` | DB fills ↔ memory positions | WARNING for orphan fills |
| Positions vs Trades | `engine.py:294-328` | Memory positions ↔ DB trades | ERROR if mismatch |
| Trades vs P&L | `engine.py:330-360` | DB trades ↔ PNLEngine | ERROR if P&L mismatch |
| Accounts vs Positions | `engine.py:362-377` | AccountEngine margin ↔ position margins | ERROR if mismatch |
| Duplicate Fills | `engine.py:379-394` | DB fills | ERROR if duplicate fill_id |
| Duplicate Orders | `engine.py:396-411` | DB orders | ERROR if duplicate order_id |
| DB vs Memory Orders | `engine.py:413-447` | DB orders ↔ memory orders | WARNING for mismatches |
| DB vs Memory Fills | `engine.py:449-489` | DB fills ↔ memory fills | WARNING for mismatches |

### Tolerance

**File:** `reconciliation/engine.py:71`
```python
TOLERANCE = 1e-6
```

### When Reconciliation Runs

1. **Startup:** `trading_engine.py:370-386`
2. **Reconnect:** (available via `phase="reconnect"`)
3. **Restart:** (available via `phase="restart"`)

---

## 2. Memory State Sources

### PositionManager (In-Memory)

**File:** `portfolio/position_manager.py:110-111`
```python
self._positions: dict[str, Position] = {}      # open positions
self._closed_positions: list[Position] = []     # last 500 closed
```

### AccountEngine (In-Memory)

**File:** `portfolio/account.py:50-54`
```python
self.cash = starting_capital
self.realized_pnl = 0.0
self.unrealized_pnl = 0.0
self.charges = 0.0
self.used_margin = 0.0
```

### PNLEngine (In-Memory)

**File:** `portfolio/pnl.py:44-50`
```python
self._realized_gross: float = 0.0
self._realized_charges: float = 0.0
self._realized_net: float = 0.0
self._unrealized_gross: float = 0.0
self._trade_count: int = 0
self._wins: int = 0
self._losses: int = 0
```

### RiskEngine (In-Memory)

**File:** `core/risk_engine.py:37-41`
```python
self._kill_switch_active = False
self._daily_pnl: float = 0.0
self._peak_equity: float = 0.0
self._last_reset_date: str = ...
```

### Strategy (In-Memory)

**File:** `strategies/base_dema_strategy.py:49-86`
```python
self.state = StrategyState.FLAT
self.position_side: Optional[str] = None
self.stop_price: Optional[float] = None
self.pending_entry: Optional[PendingEntry] = None
self.just_entered: bool = False
self.last_exit_reason: Optional[str] = None
self.pending_exit_at_open: bool = False
# ... indicator tracking vars
```

---

## 3. DB State Sources

### trading.db

| Table | Primary Key | Content |
|-------|-------------|---------|
| trades | trade_id (UNIQUE) | Closed trades with P&L |
| orders | order_id (UNIQUE) | All submitted orders |
| fills | fill_id (UNIQUE) | All fills |
| events | id (AUTOINCREMENT) | Event audit log |
| account_snapshots | id (AUTOINCREMENT) | Periodic equity snapshots |
| processed_fills | fill_id (PRIMARY KEY) | Dedup tracking |

### analytics.db

| Table | Primary Key | Content |
|-------|-------------|---------|
| trade_events | event_id | Append-only event log |
| trades_analytics | trade_id | Rich trade lifecycle |
| trade_legs | leg_id | Individual fill legs |
| trade_snapshots | snapshot_id | (Not actively written) |

---

## 4. Known Discrepancies

### A. Indicator/HTF State is NEVER Persisted

**File:** `trading_engine.py:1420-1422`
```python
# NOTE: indicator & HTF (candle-derived) state is intentionally
# NOT persisted — it is always recomputed from a fresh Dhan REST
# series at startup (_warmup_from_rest).
```

**Impact:** No discrepancy — indicators are recomputed from scratch on every startup.

### B. Closed Positions Not in DB (After Close, Before Persist)

**Scenario:** Crash between `position_manager.close_position()` and `persistence.save_trade()`

**Mitigation:** TradeCloseManager persists BEFORE closing in memory:
```python
# trade_close.py:98-130 — persist FIRST
# trade_close.py:138-145 — memory update SECOND
```

**Residual risk:** If persist succeeds but memory update fails, reconciliation catches it.

### C. Strategy State Not Persisted to DB

Strategy state (position_side, stop_price, pending_entry) is only in:
- In-memory (`BaseDEMAStrategy` attributes)
- `system_state.json` (via `strategy.snapshot()`)
- **NOT** in trading.db or analytics.db

**Recovery:** On restart, strategy state is restored from `system_state.json`.

### D. Fill Dedup State vs DB

**File:** `core/fill_dedup.py:39-50`
```python
def load_from_database(self) -> int:
    rows = conn.execute("SELECT fill_id FROM processed_fills").fetchall()
    self._processed_fills = {row[0] for row in rows}
```

**Discrepancy possible if:** processed_fills table has fills not in the fills table (or vice versa). Reconciliation does NOT check this.

### E. Two Separate Trade Tables

| Field | trades (trading.db) | trades_analytics (analytics.db) |
|-------|---------------------|-------------------------------|
| trade_id | position.position_id | position.position_id |
| status | Always "closed" | "OPEN" → "CLOSED" |
| P&L | From PNLEngine | From PNLEngine (same values) |
| Lifecycle | Minimal | Full (65+ columns) |

**No FK between them.** Both are written independently during TradeCloseManager.close_position().

---

## 5. Reconciliation Results Summary

| Check | Expected | Typical Result |
|-------|----------|----------------|
| DB orders vs memory orders | Equal | WARNING: memory has more (pruned old orders) |
| DB fills vs memory fills | Equal | WARNING: memory has fewer (pruned old fills) |
| Open positions vs DB trades | No overlap | PASS (open positions have no trade row) |
| Closed positions vs DB trades | 1:1 match | PASS (TradeCloseManager atomic) |
| PNLEngine vs DB trade sum | Equal | PASS (record_trade called after persist) |
| Account margin vs position margins | Equal | PASS (block/release is atomic) |
| Duplicate fills | 0 | PASS (UUID generation + dedup) |
| Duplicate orders | 0 | PASS (UUID generation) |

---

## 6. Missing Reconciliation Checks

| Check | Status | Impact |
|-------|--------|--------|
| Fill dedup consistency | NOT CHECKED | processed_fills table can grow |
| Analytics.db consistency | NOT CHECKED | trades_analytics can drift |
| TradeLedger vs PositionManager | NOT CHECKED | ledger trades vs memory positions |
| Events table growth | NOT CHECKED | events table grows unbounded |
| Account snapshots growth | NOT CHECKED | snapshots table grows unbounded |
