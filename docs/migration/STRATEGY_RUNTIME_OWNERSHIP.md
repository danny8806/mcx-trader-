# Scope Report 4 — Strategy Runtime Ownership

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

A `StrategyRuntime` is the explicit ownership record binding a strategy to its
lifecycle, order manager and position manager. The registry is the single
authoritative enumeration of runtime memberships and mirrors the runtime's
current trade back into the owner.

- `strategies/runtime.py:24` — `StrategyRuntime`:
  - `strategy` · `lifecycle` · `order_manager` · `position_manager`
  - `instrument` / `security_id` / `fast_timeframe` — the identity the runtime
    is owned by
  - `current_trade_id` — the runtime's currently assigned trade
  - `strategy_id`, `display_name`, `group`
- `strategies/runtime.py:46` — `StrategyRuntimeRegistry`:
  - `register` / `register_or_replace` (rebuild-safe)
  - `get` / `require` / `__getitem__` / `all` / `strategy_ids` / `snapshot`
- `trading_engine.py:296` — `_build_runtimes(persistence)` registers one
  runtime per enabled strategy and registers each strategy's order manager and
  position manager with the facades
- `trading_engine.py:974` — `restore()` mirrors each restored strategy's
  `current_trade_id` into its runtime (ownership survives restart)
- `trading_engine.py:1044` — `_reconcile_strategy_positions()` heals a FLAT
  strategy that still holds an open per-strategy position: state/side/stop are
  re-derived from the OWNED position so a restart never double-enters

## Ownership invariants

1. Every mutable per-strategy object is reachable from exactly one runtime.
2. A signal is processed exclusively through its owner runtime
   (`trading_engine.py:542-545`).
3. No two runtimes share a lifecycle, order manager or position manager.

## Verification

- `test_strategy_isolation_lineage.py::TestSection64_RestartIsolation` —
  runtime `current_trade_id` mirrors the restored owner trade
- `test_rest_trading_gate.py` — 13/13 (incl. `_reconcile_strategy_positions`)
- Full `tests/` suite green: 1090 passed, 0 failed