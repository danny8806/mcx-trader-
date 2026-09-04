# 19 - Mutation Test Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Corruption Detection Capabilities

### A. Reconciliation Engine

**File:** `reconciliation/engine.py`

**Detects:**
| Corruption Type | Detection | Severity |
|----------------|-----------|----------|
| FILLED order with no fills | `_check_orders_vs_fills` | ERROR |
| Fill referencing non-existent order | `_check_orders_vs_fills` | ERROR |
| Closed trade with open position in memory | `_check_positions_vs_trades` | ERROR |
| Closed position with no trade row | `_check_positions_vs_trades` | ERROR |
| P&L sum mismatch | `_check_trades_vs_pnl` | ERROR |
| Trade count mismatch | `_check_trades_vs_pnl` | ERROR |
| Account margin mismatch | `_check_accounts_vs_positions` | ERROR |
| Duplicate fill_id | `_check_duplicate_fills` | ERROR |
| Duplicate order_id | `_check_duplicate_orders` | ERROR |
| DB vs memory order state | `_check_db_vs_memory_orders` | ERROR |
| DB vs memory fill price/qty | `_check_db_vs_memory_fills` | ERROR |

**Does NOT detect:**
| Corruption Type | Status |
|----------------|--------|
| Missing foreign keys | Not checked |
| Orphan events | Not checked |
| analytics.db vs trading.db drift | Not checked |
| TradeLedger vs PositionManager drift | Not checked |
| Indicator state corruption | N/A (not persisted) |

### B. Fill Deduplication

**File:** `core/fill_dedup.py`

**Detects:**
- Duplicate fill processing (in-memory + DB)
- Race condition on concurrent fill marking

### C. Signal Deduplication

**File:** `execution/order_manager.py:47-50`

**Detects:**
- Duplicate signals from same strategy+instrument+timestamp

---

## 2. Data Integrity Invariants

### Invariant 1: Every Fill References an Order

**Enforcement:** `reconciliation/engine.py:246-252`

```python
order_ids = {o.get("order_id") for o in db_orders}
for f in db_fills:
    if oid and oid not in order_ids:
        result.add_error(f"Fill references non-existent order")
```

**Status:** DETECTED but not enforced at write time (no FK constraint).

### Invariant 2: Every Closed Trade Has an Exit

**Enforcement:** `analytics/routes.py:551-556`

```python
closed = _trade_ledger.get_closed_trades(strategy_id=sid)
for t in closed:
    if not t.average_exit_price:
        issues.append({"type": "CLOSED_NO_EXIT", "trade_id": t.trade_id})
```

**Status:** DETECTED via reconciliation.

### Invariant 3: Position-Trade 1:1 Mapping

**Enforcement:** `reconciliation/engine.py:294-328`

```python
# Trade closed in DB but position open in memory → ERROR
# Closed position with no trade row in DB → ERROR
```

**Status:** ENFORCED at runtime (TradeCloseManager atomic persist-before-memory).

### Invariant 4: P&L Consistency

**Enforcement:** `reconciliation/engine.py:330-360`

```python
db_pnl = sum of net_pnl from DB trades
mem_pnl = PNLEngine.realized_net
if abs(db_pnl - mem_pnl) > TOLERANCE → ERROR
```

**Status:** ENFORCED at startup.

---

## 3. Mutation Test Results

### Test: Double-Write Trade

**Scenario:** Same trade_id written twice to trading.db

**Result:** INSERT OR REPLACE silently replaces. Auto-increment ID changes. No error raised.

**Impact:** External references to old auto-increment ID would break. But no such references exist.

### Test: Fill Without Order

**Scenario:** Write fill row with order_id that doesn't exist in orders table

**Result:** No error at write time (no FK constraint). Reconciliation detects on next startup.

**Impact:** Delayed detection.

### Test: Trade Close Without Position

**Scenario:** Call TradeCloseManager.close_position() with non-existent position

**Result:** `position_manager.close_position()` raises `ValueError("Position not found")`. Caught by try/except in trade_close.py:144.

**Impact:** Close fails gracefully.

### Test: Concurrent Fill Processing

**Scenario:** Two threads process same fill simultaneously

**Result:** `fill_dedup.mark_processed()` uses `INSERT INTO processed_fills` — second thread gets IntegrityError, returns False. `is_duplicate()` also checks in-memory set.

**Impact:** One thread wins, other skips. Correct behavior.

### Test: State Save During Mutation

**Scenario:** `persistence.save_state()` called while position is being opened

**Result:** `engine.snapshot()` acquires `_lock` (RLock). Mutations also hold `_lock`. No concurrent access.

**Impact:** Snapshot is consistent.

---

## 4. Corruption Resistance Summary

| Corruption Vector | Detection | Prevention | Recovery |
|-------------------|-----------|------------|----------|
| Double fill | FillDeduplicator | UUID + DB constraint | Skip duplicate |
| Missing order | Reconciliation | None (no FK) | Safe mode |
| P&L drift | Reconciliation | Atomic persist-before-memory | Safe mode |
| Position orphan | Reconciliation | Atomic TradeCloseManager | Safe mode |
| DB write failure | Try/catch in TradeCloseManager | Return False, no memory update | Safe mode |
| JSON state corruption | JSON parse error | Atomic write (.tmp + rename) | Fresh start |
| Memory corruption | N/A | Python GIL + threading.RLock | N/A |
