# 18 - Duplicate Event Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Idempotency Mechanisms

### A. Fill Deduplication

**File:** `core/fill_dedup.py`

**Mechanism:** `FillDeduplicator` with two-layer check:

1. **In-memory set:** Fast O(1) lookup
2. **Database table:** `processed_fills` (PRIMARY KEY on fill_id)

**Process:**
```python
# trading_engine.py:981-985
if self.fill_dedup.is_duplicate(fill.fill_id):
    return  # Already processed
if not self.fill_dedup.mark_processed(fill.fill_id):
    return  # Race condition: another thread marked it first
```

**Atomic mark:** `INSERT INTO processed_fills (fill_id)` — IntegrityError if duplicate.

**Startup recovery:** `load_from_database()` loads all processed fill IDs into memory set.

### B. Signal Deduplication

**File:** `execution/order_manager.py:47-50`

```python
key = f"{signal.strategy_id}:{signal.instrument}:{signal.timestamp}"
if key in self._pending_signals:
    return None  # Duplicate signal blocked
```

**Key:** Composite of strategy_id + instrument + timestamp.

**Stale cleanup:** `order_manager.py:54-58` — removes signals older than 1 hour.

### C. Order Deduplication (DB)

**File:** `persistence/manager.py:159,189`

```sql
INSERT OR REPLACE INTO trades (trade_id, ...) VALUES (?, ...)
INSERT OR REPLACE INTO orders (order_id, ...) VALUES (?, ...)
```

**Behavior:** If trade_id/order_id already exists, the row is replaced. Prevents duplicate rows but destroys auto-increment ID.

### D. TradeLedger Dedup

**File:** `analytics/trade_ledger.py:458-462`

```python
conn.execute(
    f"""INSERT INTO trades_analytics ({col_names}) VALUES ({placeholders})
        ON CONFLICT(trade_id) DO UPDATE SET {updates}""",
    list(d.values())
)
```

**Behavior:** Upsert — updates existing trade on conflict. No duplicate rows.

### E. TradeLeg Dedup

**File:** `analytics/trade_ledger.py:474-477`

```python
conn.execute(
    f"INSERT OR IGNORE INTO trade_legs ({col_names}) VALUES ({placeholders})",
    list(d.values())
)
```

**Behavior:** Silently ignores duplicate leg_id. Prevents duplicate legs.

---

## 2. Duplicate Detection Points

| Component | Dedup Method | Granularity | Persistence |
|-----------|-------------|-------------|-------------|
| Fill processing | FillDeduplicator | fill_id (UUID) | In-memory + DB |
| Signal submission | OrderManager | strategy:instrument:timestamp | In-memory only |
| DB writes (trades) | INSERT OR REPLACE | trade_id | DB |
| DB writes (orders) | INSERT OR REPLACE | order_id | DB |
| DB writes (fills) | INSERT OR REPLACE | fill_id | DB |
| TradeLedger trade | ON CONFLICT DO UPDATE | trade_id | DB |
| TradeLedger leg | INSERT OR IGNORE | leg_id | DB |
| EventStore event | PRIMARY KEY | event_id | DB |

---

## 3. Duplicate Risk Analysis

### Risk 1: Duplicate Fill Processing

**Scenario:** Engine receives same fill twice (e.g., WebSocket replay)

**Protection:** `FillDeduplicator.is_duplicate()` checks in-memory + DB.

**Residual risk:** Very low — UUID + DB constraint.

### Risk 2: Duplicate Signal Submission

**Scenario:** Same signal emitted twice in same tick

**Protection:** `OrderManager._pending_signals` dict key.

**Residual risk:** Low — but only in-memory; lost on restart. If engine restarts during a pending signal, the signal may be re-emitted.

### Risk 3: Duplicate DB Writes

**Scenario:** Same trade written twice (e.g., retry after timeout)

**Protection:** INSERT OR REPLACE with UNIQUE constraint.

**Residual risk:** Auto-increment ID changes (see `05_DATABASE_FORENSIC_REPORT.md`).

### Risk 4: Duplicate Events

**Scenario:** Same event recorded twice

**Protection:** EventStore uses UUID event_id as PRIMARY KEY.

**Residual risk:** Low — UUID prevents duplicates.

---

## 4. Missing Idempotency

| Gap | Impact | Frequency |
|-----|--------|-----------|
| Periodic state save (every 60s) | May save during mutation | Low — RLock protected |
| WebSocket push (every 1s) | May push stale data | None — read-only |
| Telegram notifications | May send duplicate notifications | Low — possible on retry |
| EventBus publish | May publish same event twice | Low — possible on retry |
