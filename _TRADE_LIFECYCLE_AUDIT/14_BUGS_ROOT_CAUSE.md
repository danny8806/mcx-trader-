# 14 — BUGS & ROOT CAUSE

Master Prompt report #14. All bugs found in this forensic audit (plus prior).

## BUG-A — Strategy Matrix state desync (HIGH) — FIXED
- Symptom: /api/strategies (and WS engine_state) showed silver_01/silver_02 as
  `state=flat, position_side=None` while /api/positions returned 2 open LONG
  positions and overview `open_positions_count=2`. Strategy Matrix Position/State
  cell, the LONG/SHORT/FLAT counters, and the IN_POSITION filter all disagreed with
  the Positions panel.
- Evidence: two rapid live captures agreed; persisted system_state.json had
  strategies saved FLAT/None while positions.open_positions held the open LONGs.
- Root cause: the strategy object's `position_side`/`state` is NOT the
  authoritative source for whether a position is open — the position manager is.
  On crash-restart restore the strategy state was persisted FLAT while the
  position manager correctly retained the open position; nothing re-derived the
  strategy's reported state from the open position. Every consumer of the strategy
  snapshot (HTTP list/detail + WS) then surfaced FLAT.
- Fix (correct layer): `dashboard/routes/strategies.py._reconcile_open_position`
  derives reported `position_side`/`state`/`stop_price` from the strategy's open
  position (position manager = truth). Applied in `_list_strategies_sync`,
  `_get_strategy_sync`, and WS `_enrich_strategies` (server.py).
- Verification: live /api/strategies + /ws now both report silver = long_position/LONG.

## BUG-B — Equity graph baseline mismatch (HIGH) — FIXED
- Symptom: Strategy equity graph showed gold_01 net P&L as -200,803.97 instead of
  the true -803.97 (off by exactly -200,000); drawdown inflated by 200,000.
- Evidence: /api/analytics/strategies/gold_01/equity returned baseline 1,000,000
  (curve [1000000, 999196.03]); /api/strategies/gold_01.configuration.starting_capital
  = 1,200,000; StrategyEquityChart computes net P&L = equity - starting_capital.
- Root cause: analytics/routes.py and analytics/performance.py hardcoded
  `starting_equity=1_000_000` (fictional), but the frontend subtracts the account
  starting_capital (1,200,000). Baseline mismatch => +200,000 shift.
- Fix (correct layer): analytics/routes.py gained `set_default_starting_equity`;
  the equity/drawdown routes use it when the caller omits the param; the dashboard
  lifespan seeds it from account.starting_capital. Baseline now 1,200,000.
- Verification: gold_01 equity [1200000, 1199196.03] => -803.97; gold_02 => -1634.00.

## BUG-3 — TradeLedger.record_fill not idempotent (prior, FIXED, deployed)
- UNIQUE index idx_trade_legs_fill_id; record_fill dedups on fill_id (commit 51ce0fa).

## BUG-1/BUG-2 / Defect 6 (prior) — open-position analytics gap, DB-split, partial state
- Unaidedled ledger backfill for open positions; Dual-write guards. See prior reports.

## Non-data issues observed (NOT correctness bugs)
- BUG-MATRIX-SORT: matrix sort by Net P&L/Profit Factor/Max Drawdown reads
  `net_pnl`/`profit_factor`/`max_drawdown` fields that live strategies do not carry
  (they expose realized_net/win_rate/trade_count). Sorting compares 0 vs 0 — a UI
  functionality gap, not a data-wrongness bug. Documented; recommended follow-up.
- BUG-MATRIX-COMPARE-EQUITY: strategy-compare uses hardcoded `equityCurves={{}}`.
  UI gap; documented.
- /api/audit returns 0 entries (audit-logging is wired but has logged nothing).
  Not a data-integrity defect for the trading panels.

All UNCERTAIN cases were treated as such and not silently promoted to PASS.