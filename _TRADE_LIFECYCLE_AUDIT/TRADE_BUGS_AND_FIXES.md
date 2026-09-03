# TRADE BUGS AND FIXES

All fixes committed on `main` (d7d7b46, 7b5b124) and deployed live; local md5 == in-container md5.

## BUG-3 — `TradeLedger.record_fill` not idempotent on `fill_id` (duplicate leg + P&L)
- **Found by**: NEW forensic test `test_duplicate_fill_on_ledger_does_not_duplicate_leg`
  (`tests/fresh_audit/test_forensic_trade_lifecycle.py`) and
  `test_five_day_replay_fill_replay_does_not_duplicate_leg`
  (`tests/fresh_audit/test_forensic_multiday_replay.py`).
- **Symptom (defense-in-depth)**: `analytics/trade_ledger.py:186+` `record_fill` minted a fresh
  `leg_id = uuid4()` per call with NO `fill_id` dedup; a replayed fill would insert a duplicate
  `trade_legs` row AND reapply `_update_entry_fill`/`_update_exit_fill`, double-counting
  `filled_quantity` and recomputing weighted entry/exit + P&L. `trade_legs` had no UNIQUE on
  `fill_id`.
- **Live exposure**: LOW — the engine's durable dedup (`processed_fills` + `get_fill` DB guard +
  in-memory fill_dedup) already protects the live path; cum has never replayed a fill. This is
  belt-and-suspenders at the ledger layer.
- **Root cause**: `_save_leg` used `INSERT OR IGNORE` keyed on the always-fresh `leg_id`, so it
  could never ignore a duplicate `fill_id`.
- **Fix (applied, current codebase)**:
  - `analytics/schema.py`: added `CREATE UNIQUE INDEX idx_trade_legs_fill_id ON trade_legs(fill_id)`
    — the DB now rejects a second leg for the same fill.
  - `analytics/trade_ledger.py` `record_fill`: added `_get_leg_fill_id(fill_id)` guard — if a leg
    for that `fill_id` already exists, it is returned as a no-op and financial effects are NOT
    re-applied.
- **Regression tests**: duplicate `fill_id` returns same `leg_id`, produces exactly one `trade_legs`
  row, and P&L is not double-applied. All PASS.
- **Deployed live (2026-09-03)**: files installed into `mcx-trader` (in-container md5 == local:
  schema.py `92443dd6`, trade_ledger.py `aeb976e0`), `py_compile` OK, `idx_trade_legs_fill_id`
  UNIQUE index applied to live analytics.db (idempotent), container restarted → healthy. Post-deploy
  data intact & unchanged: analytics.db `trades_analytics`=4, `trade_legs`=6, `trade_events`=12;
  trading.db `trades`=2, `orders`=6, `fills`=6, `processed_fills`=6; both `PRAGMA integrity_check`=ok;
  API matches baseline. **No trade data lost.** Pre-deploy backup at
  `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/predeploy_bug3_20260903_090527/`.

## BUG-1 — Open trade missing from analytics.db after restart (DB split)

## BUG-1 — Open trade missing from analytics.db after restart (DB split)
- **Symptom**: OPEN SILVER positions appeared in trading.db `fills`/`orders` + memory but had no
  `trades_analytics` row / `trade_legs` / `POSITION_OPENED` event.
- **Root cause**: `create_trade` + entry leg + event ran only at fresh open time; `restore()` from
  state never re-ran it; writes wrapped in silent `except: pass`.
- **Fix**: `_backfill_ledger_for_open_positions()` called at end of `restore()` — creates missing
  OPEN trade + entry leg + event for every restored open position (idempotent via `get_trade` guard;
  includes entry-leg heal for the row-exists-but-leg-missing case).
- **Regression tests**: `test_backfill_logic_creates_missing_open_trade` (`tests/fresh_audit/test_analytics_linkage.py`).

## BUG-2 — Close-time fallback could close the wrong trade
- **Symptom**: A position that lost its analytics row at open, when closed, could have its exit applied
  to the wrong/multiple open trade matched by strategy+instrument.
- **Root cause**: `core/trade_close.py:206-217` imprecise strategy+instrument fallback.
- **Fix**: Replaced with exact position-anchored create+close: create trade + entry leg + exit leg +
  `close_trade()` for positions lacking a ledger row. Failures log CRITICAL (not silent).
- **Regression tests**: `test_close_position_when_ledger_missing_creates_and_closes_exact_trade`.

## Fix C — get_fill idempotency fail-safe (double-open / spurious close)
- DB-idempotency guard failure previously fell through to reprocessing.
- Now: marked processed + CRITICAL log + return (never double-apply on DB uncertainty).

## Fix D — _on_fill dispatch broad guard (ghost long/short)
- Unexpected exception escaping `_on_fill` (trading_engine.py:949) could wedge a strategy.
- Now caught: LOUD log + strategy reset to FLAT so next bar starts clean.

## Fix E — silent persistence failures made loud
- `save_order`, analytics ledger create/leg, and `POSITION_OPENED` event writes: replaced `except: pass`
  with WARNING/CRITICAL logging so divergence is never hidden.

## Fix A — startup config crash-bomb
- `partial_fill_probability` engine default `0.1` → `0.0` (matches `paper_broker.py:90-94` ValueError
  guard; avoids engine crash on config omission).
- **Regression test**: `test_partial_fill_default_does_not_crash_engine` (`tests/test_regressions.py`).

## Verification
- Live: DB-split resolved; 2 SILVER trades now in analytics.db; survives restarts.
- Local: 175+ tests (2-pass) pass across `test_regressions`, `test_analytics_linkage`,
  `test_reconciliation_linkage`, `test_deep_backend`, `test_financial_core`, `test_full_deep_architecture`,
  `test_whole_project`, `test_comprehensive`, `test_edge_cases`.