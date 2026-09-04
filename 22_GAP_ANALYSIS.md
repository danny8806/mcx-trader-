# 22 - Gap Analysis: Intended vs Actual Architecture

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Missing Components (Compared to Typical Trading Systems)

| Missing Component | Impact | Priority |
|------------------|--------|----------|
| Authentication/Authorization | Dashboard open to anyone on the network | HIGH |
| Rate limiting on API | API abuse possible | MEDIUM |
| Live trading mode | System is paper-only by design | N/A (by design) |
| Order management system (OMS) | No order routing, amendment, or cancellation | LOW (paper) |
| Position sizing models | Fixed quantity per strategy | LOW |
| Trailing stop loss | Only fixed stop loss | MEDIUM |
| Multiple timeframe confirmation | Only 1H + 15m confirmation | LOW (by design) |
| News/event integration | No fundamental data | LOW |
| Backtesting integration | Backtest code separate from live | LOW |
| Performance attribution | Basic P&L only | LOW |
| Risk reporting | Basic daily P&L and drawdown | MEDIUM |
| Audit trail for all changes | Events logged but not all mutations | MEDIUM |
| Database migrations | Manual ALTER TABLE with try/except | MEDIUM |
| Configuration versioning | No config version tracking | LOW |

---

## 2. Architecture Deviations

### Deviation 1: Two Separate Databases

**Intended:** Likely single database for all data

**Actual:**
- `trading.db` — operational (orders, fills, trades, events, snapshots)
- `analytics.db` — rich lifecycle (trade_events, trades_analytics, trade_legs)

**Impact:** Data duplication, no cross-DB consistency, two connection pools.

### Deviation 2: Indicator State Not Persisted

**Intended:** Likely persist indicator state for fast restart

**Actual:** Indicators recomputed from REST on every startup (7-day backfill)

**Impact:** Startup takes 10-30 seconds for warmup. But ensures consistency.

### Deviation 3: No ORM

**Intended:** Likely SQLAlchemy or similar

**Actual:** Raw SQL with `sqlite3` throughout

**Impact:** No schema versioning, no migration framework, manual query construction.

### Deviation 4: INSERT OR REPLACE Instead of UPSERT

**Intended:** Safe upsert pattern

**Actual:** `INSERT OR REPLACE` destroys auto-increment IDs

**Impact:** Theoretical data corruption if same key written twice. Mitigated by write-once semantics.

### Deviation 5: No Foreign Keys

**Intended:** Referential integrity

**Actual:** Zero FK constraints in either database

**Impact:** Orphan records possible. Detected by reconciliation but not prevented.

---

## 3. Missing Database Features

| Feature | Status | Impact |
|---------|--------|--------|
| Foreign key constraints | Missing | Orphan records possible |
| CHECK constraints | Missing | Invalid data possible |
| Indexes on events table | Missing (trading.db) | Slow queries |
| TTL/retention policy | Missing | Unbounded growth |
| Migration framework | Missing | Manual schema changes |
| Connection pooling | Single connection | Limited concurrency |
| Backup strategy | Not implemented | Data loss risk |

---

## 4. Missing Testing Features

| Feature | Status | Impact |
|---------|--------|--------|
| Unit tests for all modules | Partial | Some modules untested |
| Integration tests | Limited | End-to-end coverage gaps |
| Load testing | Missing | Unknown performance limits |
| Chaos testing | Missing | Crash behavior unverified |
| Mutation testing | Missing | Test quality unknown |
| Coverage reporting | .coverage file exists | May be outdated |

---

## 5. Missing Operational Features

| Feature | Status | Impact |
|---------|--------|--------|
| Logging framework | Print statements only | No log levels, no rotation |
| Metrics/monitoring | Basic HealthMonitor | No Prometheus/Grafana |
| Alerting | Telegram only | No email, SMS, PagerDuty |
| Deployment automation | Docker + manual | No CI/CD |
| Configuration management | YAML/JSON files | No env-based config |
| Secret management | Token file | No vault integration |

---

## 6. Intended vs Actual Signal Flow

### Intended (likely)
```
WebSocket ticks → Form candles → Indicators → Strategy → Order → Exchange
```

### Actual
```
REST API → Form candles → Indicators → HTF Engine → Strategy → Paper Broker → Fill
WebSocket ticks → LTP only (for mark-to-market and pending trigger checks)
```

**Key difference:** Candles come from REST API, not from WebSocket ticks. WebSocket is only for LTP.

---

## 7. Recommendations Summary

| Recommendation | Priority | Effort |
|---------------|----------|--------|
| Add foreign key constraints | HIGH | Low |
| Add indexes on events table | HIGH | Low |
| Implement TTL for events/snapshots | HIGH | Low |
| Add authentication to dashboard | HIGH | Medium |
| Replace INSERT OR REPLACE with UPSERT | MEDIUM | Low |
| Add rate limiting to API | MEDIUM | Low |
| Implement trailing stop loss | MEDIUM | Medium |
| Add proper logging framework | MEDIUM | Medium |
| Add database migration tool | LOW | Medium |
| Add metrics/monitoring | LOW | High |
