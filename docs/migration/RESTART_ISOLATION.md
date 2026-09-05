# Scope Report 6 — Restart Isolation

**Slice:** engine-core isolation · **Status:** IMPLEMENTED & VERIFIED

## What was delivered

The engine restart path reconstructs per-strategy state ONLY — each runtime
recovers exactly its own positions/trades, never another strategy's.

- `trading_engine.py:940` — `restore(saved_state)`:
  - restores each strategy's indicator/HTF/pending state into its OWN strategy
  - `position_manager.restore(positions)` routes each restored position back
    into the owning strategy's runtime (`portfolio/position_manager.py:438`)
  - reconstitutes per-strategy and global `account.used_margin` from the
    restored open positions so startup reconciliation is exact
  - mirrors each runtime's `current_trade_id` from its own restored strategy
- `trading_engine.py:977` — `snapshot()` persists `strategies`, `positions`,
  `event_bus` and candle count
- `trading_engine.py:817` — `set_persistence()` rebuilds runtimes against the
  real persistence and restores each strategy's OWN trades from `trading.db`
  (the old wipe bug is gone — runtimes only carry lifecycle caches)
- `portfolio/position_manager.py:243` — `PositionManagerFacade.snapshot` /
  `restore` dispatch by `strategy_id`

## What this proves (mission §64)

- GOLDM_5M does NOT load GOLDM_15M's active trade after a snapshot/restore
  restart — each of the four runtimes reconstructs only its own position.
- Broker order IDs remain mapped to the correct strategy/trade in durable
  storage (`fills` rows in `trading.db` survive the restart).
- Duplicate fill redelivery is still ignored after restart (durable
  `FillDeduplicator`, mission §66).

## Verification

- `test_strategy_isolation_lineage.py::TestSection64_RestartIsolation` — 1/1
- `test_strategy_isolation_lineage.py::test_duplicate_fill_ignored_after_restart` — 1/1
- `test_full_deep_architecture.py` — 11/11 (incl. position/order/fill restore)
- `test_crash_api_replay.py` — 21 passed, 11 skipped
- Full `tests/` suite green: 1090 passed, 0 failed