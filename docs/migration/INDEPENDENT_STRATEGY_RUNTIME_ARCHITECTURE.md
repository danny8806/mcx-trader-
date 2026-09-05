# Scope Report 1 — Independent Strategy Runtime Architecture

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

Each of the four live strategies (`gold_01`, `gold_02`, `silver_01`, `silver_02`)
now executes inside its own `StrategyRuntime`. A runtime is the unit of
per-strategy mutable identity: lifecycle caches, the order manager's
pending/active sets and the position manager all route by `strategy_id`.

- `strategies/runtime.py:24` — `StrategyRuntime`
- `strategies/runtime.py:46` — `StrategyRuntimeRegistry` (`register`, `get`,
  `require`, `all`, `snapshot`)
- `trading_engine.py:296` — `_build_runtimes(persistence)` constructs one
  runtime (lifecycle + order manager + position manager) per enabled strategy
- `trading_engine.py:340` — `_runtime(strategy_id)` resolves the owning runtime
- `trading_engine.py:542-545` — `_process_signal` binds lifecycle / order
  manager / position manager from the signal's OWN runtime: a signal can never
  touch another strategy's caches, order state or positions

## Shared infrastructure

One canonical set of system-wide services is shared (not duplicated):

| Component | Owner |
|---|---|
| `PaperExecutionEngine` broker transport | `engine.execution_engine` |
| `trading.db` (positions/orders/fills/signals/trades) | engine persistence |
| `TradeLedger` (analytics) | engine |
| `EventBus` + `NativeCandleDistributor` | engine |
| `FillDeduplicator` (durable fill idempotency) | `engine.fill_dedup` |

## Compat facades

`engine.position_manager`, `engine.order_manager` remain single access points
for dashboard/reconciliation/scripts but dispatch by strategy underneath.

## Verification

- `tests/fresh_audit/test_strategy_isolation_lineage.py` — 13/13 pass
- Full `tests/` suite green: 1090 passed, 0 failed