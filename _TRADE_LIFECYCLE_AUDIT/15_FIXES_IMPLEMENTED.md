# 15 — FIXES IMPLEMENTED

Master Prompt report #15.

## This session (commit 0d5d4b9) — deployed live & verified
1. **State desync (BUG-A)**
   - File: `dashboard/routes/strategies.py`
   - Added `_reconcile_open_position(strategy_id, snap)` — derives the reported
     strategy `position_side`/`state`/`stop_price` from the strategy's open
     position in the position manager (authoritative source).
   - Applied in `_list_strategies_sync` and `_get_strategy_sync`.
   - File: `dashboard/server.py` — applied in WS `_enrich_strategies` so
     engine_state carries the same contract as /api/strategies.
2. **Equity baseline (BUG-B)**
   - File: `analytics/routes.py` — added `set_default_starting_equity(value)` and
     made `get_strategy_equity`/`get_strategy_drawdown` use it when the caller omits
     `starting_equity`.
   - File: `dashboard/server.py` — lifespan seeds the baseline from
     `account.starting_capital` (1,200,000).
3. **Regression tests**
   - File: `tests/fresh_audit/test_audit_fix_state_equity.py` (6 tests).

## Prior sessions (already deployed)
- BUG-3 ledger idempotency (commit 51ce0fa): schema UNIQUE index +
  `TradeLedger.record_fill` dedup on fill_id.
- BUG-1/BUG-2 / Defect 6: ledger/event backfill for open positions; dual-write DB
  guards; guard against DB-split/silent-loss.
- Broad `_on_fill` dispatch guard against ghost long/short (7b5b124).
- Remove EOD force-close so positions carry (c9e677e).
- Price-sentinel/NaN-LTP guards (6afa160).

## What is intentionally NOT changed
- Production trading config, instruments, session times, margin model — untouched.
- No real orders (paper execution only).
- No resampling subsystem touched (system does not use resampling; skipped per
  master prompt, all else tested).
- Engine trading logic (the open trades are intentionally governed by
  position_manager; the serializers were reconciled, not the trading behaviour).