# Scope Report 2 — Trade Lifecycle Isolation

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

Every trade lifecycle (`TradeLifecycleManager`) belongs to exactly one
`StrategyRuntime`. The old single global `engine._lifecycle` is gone — no
shared mutable lifecycle state exists anywhere in the engine.

- `core/lifecycle.py` — per-strategy `strategy_id` filtering in `get_trade`,
  `_load_trade_by`, `restore_from_db`; trade lookups resolve only rows owned
  by the strategy
- `trading_engine.py:542-545` — `_process_signal` resolves the owning
  lifecycle from the runtime
- `trading_engine.py:990` — `engine.get_trade(trade_id)` aggregates READ-ONLY
  across runtimes for shared API/WS consumers (never mutates)
- `trading_engine.py:998` — `engine.reconcile_trades()` aggregates per-runtime
  `lifecycle.reconcile()` results (stats summed, no shared state)
- `trading_engine.py:1011` — `engine.orphan_scan()` merges per-runtime orphan
  scans
- `dashboard/routes/*` — trades/reconciliation routes consume the aggregates,
  no direct `_lifecycle` access anywhere

## Entry / exit semantics

Trade identity is severed from position identity: a ledger trade anchors the
position via `trade_id` and the position carries its own `position_id`
(verified by `test_per_strategy_lifecycle.py`).

Reverse-and-re-entry is managed inside the strategy: a bare opposite-side
signal while a position is open is recognised as a REVERSAL EXIT
(`trading_engine.py:551-578`), never a phantom trade; re-entry requires a
later breakout trigger.

## Verification

- `test_per_strategy_lifecycle.py` + `test_reversal_exit_and_opposite_entry.py` — 34/34
- `test_audit_reversal_sl_all_strategies.py` — 28/28
- `test_strategy_isolation_lineage.py::TestSection49_StrategyIsolation` — 4/4