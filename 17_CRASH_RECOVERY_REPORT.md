# 17 - Crash Recovery Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Crash Recovery Architecture

### Recovery Sources

| Source | File | Content |
|--------|------|---------|
| `system_state.json` | `persistence/manager.py:135-152` | Full engine state (strategies, positions, accounts, P&L, risk, execution) |
| `trading.db` | `persistence/manager.py:57-131` | Orders, fills, trades, events |
| `analytics.db` | `analytics/schema.py` | Trade lifecycle, events, legs |
| `processed_fills` | `core/fill_dedup.py:24-37` | Dedup state |

### Recovery Sequence (on startup)

**File:** `server.py:200-219`

```
1. Load system_state.json
2. _engine.restore(saved_state)
3. _engine.start()
4. ReconciliationEngine.reconcile()
```

---

## 2. Crash at Each Lifecycle Stage

### Stage 1: Signal Generated, Before Order

**Crash point:** Between `_process_signal()` receiving signal and `order_manager.submit_signal()`

**State after crash:**
- Memory: Strategy in PENDING_LONG/PENDING_SHORT
- DB: No write
- JSON state: May have old state (if periodic save ran)

**Recovery:**
- `system_state.json` restores strategy state
- Strategy is in PENDING_LONG/PENDING_SHORT with armed entry
- Next bar/tick will re-evaluate the pending entry
- **Risk:** Low — signal may be lost if pending entry timeout expires

### Stage 2: Order Created, Before Fill

**Crash point:** Between `order_manager.submit_signal()` returning order and `_on_fill()` processing fill

**State after crash:**
- Memory: Order in execution engine, no position
- DB: Order row written (`trading_engine.py:895-912`)
- JSON state: May have old state

**Recovery:**
- Reconciliation detects: FILLED order with no fills → ERROR
- System enters safe mode
- Manual intervention required

### Stage 3: Fill Processed, Before Position Open

**Crash point:** Between `_on_fill()` receiving fill and `position_manager.open_position()`

**State after crash:**
- Memory: No position, no margin blocked
- DB: Fill row written (`trading_engine.py:1018-1027`)
- JSON state: May have old state

**Recovery:**
- Reconciliation detects: Fill without corresponding position → WARNING
- System enters safe mode
- Manual intervention required

### Stage 4: Position Open, Before Trade Persist

**Crash point:** Between `position_manager.open_position()` and `persistence.save_trade()`

**State after crash:**
- Memory: Position open, margin blocked
- DB: No trade row, fill row exists
- JSON state: May have position state

**Recovery:**
- `system_state.json` restores position
- Reconciliation detects: Position open but no trade row → WARNING
- TradeLedger may have trade record (if `create_trade` succeeded)
- System enters safe mode

### Stage 5: Trade Persisted, Before Memory Update (TradeClose)

**Crash point:** Between `persistence.save_trade_and_fill()` and `position_manager.close_position()` in TradeCloseManager

**State after crash:**
- Memory: Position still open, margin still blocked
- DB: Trade row written with status="closed"
- JSON state: May have old state with position open

**Recovery:**
- `system_state.json` restores position as open
- Reconciliation detects: Trade closed in DB but position open in memory → ERROR
- System enters safe mode
- Manual intervention: close position or reconcile

### Stage 6: Memory Updated, Before P&L/Account Update

**Crash point:** Between `position_manager.close_position()` and `account_engine.update_realized_pnl()`

**State after crash:**
- Memory: Position closed, P&L not updated
- DB: Trade row with correct P&L
- JSON state: May have stale P&L

**Recovery:**
- Reconciliation detects: P&L mismatch between DB sum and PNLEngine → ERROR
- System enters safe mode
- Manual intervention required

### Stage 7: Mid-Transaction Crash (TradeCloseManager)

**Crash point:** During `save_trade_and_fill()` transaction

**State after crash:**
- SQLite WAL mode ensures atomicity
- Either both trade+fill are written, or neither
- Memory state unchanged

**Recovery:**
- If neither written: position still open in memory, no DB record
- If both written: position still open in memory, DB has closed trade
- Reconciliation catches the discrepancy

---

## 3. Recovery Mechanisms

### A. JSON State Restore

**File:** `persistence/manager.py:135-152`

```python
def save_state(self, state):
    tmp = self.state_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(self.state_path)  # Atomic rename
```

**Periodic save:** Every 60 seconds (`server.py:111-125`)

**Atomicity:** Write to .tmp then rename — prevents partial writes.

### B. Reconciliation

**File:** `reconciliation/engine.py`

Runs on startup and detects:
- DB vs memory mismatches
- Orphan fills/orders
- P&L discrepancies
- Margin mismatches

**On failure:** Enters safe mode (no trading allowed)

### C. Fill Dedup Recovery

**File:** `core/fill_dedup.py:39-50`

```python
def load_from_database(self):
    rows = conn.execute("SELECT fill_id FROM processed_fills").fetchall()
    self._processed_fills = {row[0] for row in rows}
```

**Recovery:** Loads all processed fill IDs from DB on startup. Prevents duplicate fill processing after crash.

---

## 4. Recovery Gaps

| Gap | Impact | Mitigation |
|-----|--------|------------|
| Indicator state not persisted | Cold start on every restart | Warmup from REST (7-day backfill) |
| HTF state not persisted | Cold start | Recomputed during warmup |
| Strategy pending_entry not in DB | May be lost on crash | Restored from system_state.json |
| TradeLedger open trades | May be orphaned | Loaded from DB on startup |
| EventStore sequence counter | Resets to 0 on restart | Non-critical (cosmetic) |
| PositionManager closed list | Lost on restart | Acceptable (capped at 500) |

---

## 5. Recovery Testing

**File:** `tests/fresh_audit/test_full_pipeline_audit.py` and related

Tests verify:
- State save/restore round-trip
- Reconciliation after simulated crash
- Fill dedup after restart
- Strategy state preservation
