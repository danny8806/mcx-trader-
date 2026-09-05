# Scope Report 5 — Cross-Strategy Lineage

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

Lineage is strictly per-strategy end-to-end: signal → order → fill → trade →
position. Every durable row carries the owner `strategy_id`, and no strategy's
line can reference another strategy's identity.

Durable write paths (all owner-aware):

- `trading_engine.py:644` — `_persist_signal` writes `signal_id` + `strategy_id`
- `trading_engine.py:655` — `_persist_order` writes `order_id` + `strategy_id` +
  `trade_id` + `signal_id`
- `trading_engine.py:668` — `_handle_fill` applies a fill exactly once and
  persists the fill anchored to its own order/trade
- `trading_engine.py:776` — `_persist_position` writes `position_id` +
  `strategy_id` + `trade_id`
- `persistence/manager.py:130` — `fills` table has `fill_id UNIQUE`,
  `order_id`, `strategy_id NOT NULL`, `trade_id`
- `core/lifecycle.py:269` — `resolve_trade_from_signal(signal_id)` returns the
  trade anchored to that exact entry signal (idempotent re-resolution)
- `core/lifecycle.py:275` — `resolve_trade_from_order(order_id)` resolves by
  the exact entry/exit order

## What this proves

Four simultaneous trades (TRD-A `gold_01`, TRD-B `gold_02`, TRD-C `silver_01`,
TRD-D `silver_02`) keep fully disjoint lineage: each trade/order/fill row maps
back to exactly its owner strategy, and no row crosses into another strategy's
identity. The engine-side analytics ledger projection
(`trading_engine.py` `create_trade`) writes STRATEGY side
(`LONG`/`SHORT`), matching `analytics/trade_ledger.py` P&L semantics
(`trade.side == "LONG"`).

## Verification

- `test_strategy_isolation_lineage.py::TestSection51_DatabaseLineage` — 1/1
  (four simultaneous trades, no cross-link across `trades`/`fills` tables)
- `test_forensic_trade_lifecycle.py` — ledger side convention confirmed
- Full `tests/` suite green: 1090 passed, 0 failed