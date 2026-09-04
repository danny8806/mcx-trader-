# 20 - Orphan Record Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Orphan Detection Results

### A. Fills Without Orders

**Check:** `reconciliation/engine.py:246-252`

```python
order_ids = {o.get("order_id") for o in db_orders}
for f in db_fills:
    oid = f.get("order_id", "")
    if oid and oid not in order_ids:
        result.add_error(f"Fill references non-existent order")
```

**Result:** Possible if crash occurs after fill persist but before order persist. The code writes order BEFORE fills (`trading_engine.py:895-916`), so this requires a crash between the two.

**Risk:** Low — order is persisted first.

### B. Orders Without Fills

**Check:** `reconciliation/engine.py:227-242`

```python
for order in db_orders:
    if state == "filled" and not recorded_fills:
        result.add_error(f"Order is FILLED but has zero fills")
```

**Result:** Possible if crash occurs after order persist but before fill persist.

**Risk:** Low — fills are persisted immediately after order.

### C. Fills Without Positions

**Check:** `reconciliation/engine.py:254-292`

```python
orphan_fills = db_fill_ids - entry_fill_ids - exit_fill_ids
if orphan_fills:
    result.add_warning(f"{len(orphan_fills)} fill(s) not linked to any position")
```

**Result:** Orphan fills detected as WARNING. Possible if:
- Entry fill was persisted but position open failed
- Exit fill was persisted but position close failed

**Risk:** Medium — requires manual investigation.

### D. Closed Positions Without Trade Rows

**Check:** `reconciliation/engine.py:323-328`

```python
missing = [p.position_id for p in closed_positions if p.position_id not in db_trade_ids]
if missing:
    result.add_error(f"{len(missing)} closed position(s) have no trade row")
```

**Result:** Should never happen — TradeCloseManager persists BEFORE closing in memory. But if persistence fails, this is caught.

**Risk:** Very low — atomic close design.

### E. Trade Rows Without Closed Positions

**Check:** `reconciliation/engine.py:312-318`

```python
for trade in db_trades:
    if trade.get("status") == "closed" and tid in open_position_ids:
        result.add_error(f"Trade closed in DB but position still open in memory")
```

**Result:** Possible if crash occurs between trade persist and memory close.

**Risk:** Very low — persist-before-memory design. But crash between the two creates this state.

---

## 2. Potential Orphan Sources

### Source 1: Events Without Trades

**File:** `trading_engine.py:853-878`

```python
self.event_store.record(
    trade_id=f"rejected:{signal.strategy_id}:{signal.timestamp}",
    ...
)
```

**Issue:** Rejected order events use synthetic trade_ids that don't exist in trades table.

**Impact:** Events with non-existent trade_ids. Not a data integrity issue, but complicates event querying.

### Source 2: TradeLedger Trades Without Positions

**File:** `analytics/trade_ledger.py:134-146`

```python
def _load_open_trades(self):
    rows = conn.execute(
        "SELECT * FROM trades_analytics WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
    ).fetchall()
    for row in rows:
        trade = TradeRecord(**...)
        self._open_trades[trade.trade_id] = trade
```

**Issue:** On restart, open trades are loaded from analytics.db. If the corresponding position no longer exists in memory (crash), these trades are orphaned in the ledger.

**Mitigation:** TradeLedger.close_trade fallback (trade_close.py:196-206) handles this case.

### Source 3: processed_fills Table Growth

**File:** `core/fill_dedup.py:110-131`

```python
def cleanup_old(self, days=30) -> int:
    cursor = conn.execute(
        "DELETE FROM processed_fills WHERE processed_at < datetime('now', ?)",
        (f"-{days} days",),
    )
```

**Issue:** Only cleanup mechanism in the system. Must be called explicitly (not automatic).

**Impact:** Table grows linearly with trade count. No automatic cleanup.

---

## 3. Orphan Tolerance Matrix

| Orphan Type | Detection | Auto-Recovery | Manual Action |
|-------------|-----------|---------------|---------------|
| Fill without order | Reconciliation (ERROR) | None | Investigate and manually fix |
| Order without fills | Reconciliation (WARNING) | None | Usually transient |
| Fill without position | Reconciliation (WARNING) | None | May need to reverse fill |
| Trade without position | Reconciliation (ERROR) | None | Manual position close |
| Event without trade | Not detected | None | Cosmetic only |
| Ledger trade without position | TradeLedger fallback | Partial | Close via fallback |

---

## 4. Orphan Prevention

| Prevention Mechanism | File | Protection |
|---------------------|------|------------|
| Atomic trade close | `trade_close.py` | Persist before memory |
| Order before fill | `trading_engine.py:895-916` | DB invariant |
| Fill dedup | `fill_dedup.py` | Prevents double processing |
| Signal dedup | `order_manager.py:47-50` | Prevents double submission |
| Margin rollback on failure | `trading_engine.py:1004-1014` | Partial failure handling |
