# Scope Report 3 — Execution Isolation

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

Each `StrategyRuntime` owns its own `OrderManager` (its own pending-signal
dedup set, active-order cache and fill-notification slot). All strategies share
ONE `PaperExecutionEngine` broker transport — isolation is at the routing
layer, not by duplicating the broker.

- `execution/order_manager.py:10` — per-runtime `OrderManager`
- `execution/order_manager.py:143` — `OrderManagerFacade`: shared read/routing
  layer; `submit_signal`/`drain_fills` route by `signal.strategy_id`;
  `snapshot()` aggregates across runtimes into key `"orders"`
- `trading_engine.py:544` — `_process_signal` binds `runtime.order_manager`,
  so a signal's order intents are only ever managed in its own runtime

## What this proves

Two orders with the SAME security_id and SAME side (e.g. both GOLDM
`569003` LONG, one from `gold_01`, one from `gold_02`) remain two independent
orders with distinct `order_id` and `trade_id`; fills returned in any order
route to the correct strategy / trade / position.

## Verification

- `test_strategy_isolation_lineage.py::TestSection50_ExecutionIsolation` — 2/2
  (same-security/side order independence; fill → strategy/trade/position routing)
- Full `tests/` suite green: 1090 passed, 0 failed