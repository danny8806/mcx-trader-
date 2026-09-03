# TRADE ARCHITECTURE — data flow and storage model

## 1. Two-store model (position-anchored)
```
trading.db (PersistenceManager)                    analytics.db (TradeLedger / EventStore)
┌──────────────────────────────┐                  ┌──────────────────────────────┐
│ orders           (row: order) │   persisted at   │ trades_analytics             │
│ fills            (row: fill)  │    order / fill  │   (row created at OPEN,      │
│ trades           (row: closed │      time        │    status OPEN→CLOSED)       │
│   trade) ───────── stock───── │                  │ trade_legs (entry+exit legs) │
│ account_snapshots/events      │                  │ trade_events                 │
└──────────────┬───────────────┘                  └──────────────┬───────────────┘
               │ identity key: trade_id = position_id            │ same key
               ▼                                                 ▼
        /api/trades, /api/orders, /api/fills          /api/analytics/*
```

## 2. Lifecycle wiring (engine)
- `TradingEngine._process_signal` → OrderManager → PaperExecutionEngine → `save_order` → `drain_fills`
  → `_on_fill` (entry: open_position + save_fill + create_trade/leg/event; exit: TradeCloseManager).
- On restart: `restore()` rehydrates state then `_backfill_ledger_for_open_positions()` guarantees every
  open position has its analytics OPEN record (BUG-1).

## 3. Why the old code split (fixed)
The analytics ledger was written **only at fresh open**; `restore()` rehydrated positions from state
without recreating the analytics rows, so any position that survived a restart was invisible to the
analytics DB and its `/api/analytics/*` endpoints — a silent divergence the frontend would show as
"position open but no trade". BUG-1's backfill + the loud-write fixes close this for good.

## 4. Reasons this is now robust
- Position-anchored identity (never fuzzy strategy+instrument matching) on every write path.
- All persistence writes audited; the ones that can fail are loud, never `except: pass`.
- DB-uncertain replay is fail-safe (never double-applies).
- `_on_fill` dispatch cannot silently wedge a strategy.