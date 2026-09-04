# 03 - Identity Lineage Map

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## ID Types and Their Lifecycle

### 1. `trade_id` (TradeCloseManager / Persistence trades table)

| Aspect | Detail |
|--------|--------|
| **Created by** | `TradeCloseManager.close_position()` → `trade_close.py:101` |
| **Format** | `position.position_id` (reuses position_id as trade_id) |
| **Stored in** | `trading.db` → `trades.trade_id` (UNIQUE) |
| **Looked up by** | Reconciliation engine, `persistence.get_trades()` |
| **Can change?** | No (immutable once written) |
| **Source of truth** | `position.position_id` |

**CRITICAL NOTE:** There is NO `lifecycle.py` file in this codebase. The user's audit brief references `core/lifecycle.py:146` — this file does not exist. The trade lifecycle is managed by `core/trade_close.py` (TradeCloseManager) and `analytics/trade_ledger.py` (TradeLedger).

### 2. `trade_id` (TradeLedger / analytics.db trades_analytics table)

| Aspect | Detail |
|--------|--------|
| **Created by** | `TradeLedger.create_trade()` → `trade_ledger.py:168` |
| **Format** | Passed as `position_id` from trading_engine.py:1079 |
| **Stored in** | `analytics.db` → `trades_analytics.trade_id` (PRIMARY KEY) |
| **Looked up by** | `TradeLedger.get_trade()`, analytics routes |
| **Can change?** | No |
| **Source of truth** | `position.position_id` (position-anchored 1:1) |

**Linkage:** `trading_engine.py:1079` explicitly passes `trade_id=position.position_id` to `trade_ledger.create_trade()`.

### 3. `position_id` (PositionManager)

| Aspect | Detail |
|--------|--------|
| **Created by** | `PositionManager.open_position()` → `position_manager.py:123` |
| **Format** | `uuid.uuid4()` (random UUID) |
| **Stored in** | In-memory `Position.position_id` |
| **Looked up by** | `position_manager.get_position()`, reconciliation |
| **Can change?** | No (immutable once created) |
| **DB representation** | Used as `trade_id` in trades table AND trades_analytics |

**This is the ANCHOR ID.** All other trade-related IDs derive from or reference this.

### 4. `fill_id` (PaperExecutionEngine)

| Aspect | Detail |
|--------|--------|
| **Created by** | `PaperExecutionEngine._execute_order()` → `paper_broker.py:165` |
| **Format** | `uuid.uuid4()` |
| **Stored in** | In-memory `Fill.fill_id`, `trading.db` → `fills.fill_id` |
| **Looked up by** | `fill_dedup.is_duplicate()`, `TradeLedger.record_fill()` |
| **Can change?** | No |
| **Linkage** | `fill.fill_id` → `Position.entry_fill_ids[]`, `Position.exit_fills[].fill_id` |

### 5. `order_id` (PaperExecutionEngine)

| Aspect | Detail |
|--------|--------|
| **Created by** | `PaperExecutionEngine.create_order()` → `paper_broker.py:115` |
| **Format** | `uuid.uuid4()` |
| **Stored in** | In-memory `Order.order_id`, `trading.db` → `orders.order_id` |
| **Looked up by** | `OrderManager.get_order()`, reconciliation |
| **Can change?** | No |
| **Linkage** | `order.order_id` → `fill.order_id` (every fill references an order) |

### 6. `strategy_id` (String identifier)

| Aspect | Detail |
|--------|--------|
| **Created by** | Config file (e.g., "gold_01", "silver_02") |
| **Format** | String literal (e.g., "gold_01") |
| **Stored in** | Strategy objects, all DB tables |
| **Looked up by** | `strategies[name]`, `account_engines[name]`, `pnl_engines[name]` |
| **Can change?** | Only via config reload |

### 7. `event_id` (EventStore)

| Aspect | Detail |
|--------|--------|
| **Created by** | `EventStore.record()` → `event_store.py:59` |
| **Format** | `uuid.uuid4()` |
| **Stored in** | `analytics.db` → `trade_events.event_id` (PRIMARY KEY) |
| **Looked up by** | `get_event_by_id()` |
| **Can change?** | No (append-only) |

### 8. `leg_id` (TradeLeg in TradeLedger)

| Aspect | Detail |
|--------|--------|
| **Created by** | `TradeLedger.record_fill()` → `trade_ledger.py:205` |
| **Format** | `uuid.uuid4()` |
| **Stored in** | `analytics.db` → `trade_legs.leg_id` (PRIMARY KEY) |
| **Looked up by** | `get_legs_for_trade()` |
| **Can change?** | No |

---

## ID Reference Diagram

```
Strategy (strat_id = "gold_01")          ← config
    │
    ├── On signal → Strategy.state = PENDING_LONG
    │
    ├── On fill (entry):
    │     ├── Position (position_id = uuid4)     ← ANCHOR
    │     │     ├── entry_fill_ids = [fill_id]
    │     │     └── strategy_id = strat_id
    │     │
    │     ├── Fill (fill_id = uuid4)
    │     │     ├── order_id → Order.order_id
    │     │     └── strategy_id → strat_id
    │     │
    │     ├── Order (order_id = uuid4)
    │     │     └── strategy_id → strat_id
    │     │
    │     ├── DB trade row:
    │     │     ├── trade_id = position.position_id  ← REUSED
    │     │     └── strategy_id = strat_id
    │     │
    │     └── TradeLedger row:
    │           ├── trade_id = position.position_id  ← REUSED
    │           ├── position_id = position.position_id
    │           └── strategy_id = strat_id
    │
    └── On fill (exit):
          ├── Fill (fill_id = uuid4)
          ├── TradeLedger record_fill(is_entry=False)
          └── TradeLedger.close_trade(position_id)
```

---

## Split-Brain Analysis

### CRITICAL FINDING: No Split-Brain

The original audit brief references a split-brain between `lifecycle.trade_id` and `position.position_id`. **This does NOT exist in this codebase.**

- There is no `core/lifecycle.py` file
- `TradeCloseManager.close_position()` explicitly uses `position.position_id` as `trade_id` (`trade_close.py:101`)
- `TradeLedger.create_trade()` receives `trade_id=position.position_id` from `trading_engine.py:1079`
- All DB writes use `position.position_id` as the trade identity

### Potential Issue: Two Databases, Two Trade Tables

| Table | Database | Purpose |
|-------|----------|---------|
| `trades` | `trading.db` | Simple operational trade record |
| `trades_analytics` | `analytics.db` | Rich lifecycle trade record |

Both use `position.position_id` as their trade ID, but they are **separate tables in separate databases** with no foreign key between them. This is an architectural choice, not a split-brain.

### Single Identity Chain Verified

```
position.position_id (UUID)
  ├── trading.db: trades.trade_id (INSERT OR REPLACE)
  ├── analytics.db: trades_analytics.trade_id (ON CONFLICT UPDATE)
  ├── analytics.db: trade_legs.trade_id (FK reference, no constraint)
  ├── analytics.db: trade_events.trade_id (FK reference, no constraint)
  └── In-memory: PositionManager._positions[position_id]
```
