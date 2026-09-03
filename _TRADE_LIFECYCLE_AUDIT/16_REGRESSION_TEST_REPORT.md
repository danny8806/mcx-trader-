# 16 — REGRESSION TEST REPORT

Master Prompt report #16. New tests added in this audit and the full suite result.

## New regression tests (this audit) — tests/fresh_audit/test_audit_fix_state_equity.py
| Test | Covers | Result |
|---|---|---|
| test_reconcile_sets_long_state_when_open_position | BUG-A LONG | PASS |
| test_reconcile_sets_short_state_when_open_position | BUG-A SHORT | PASS |
| test_reconcile_keeps_flat_when_no_open_position | BUG-A no-op | PASS |
| test_reconcile_no_engine_is_noop | BUG-A defensiveness | PASS |
| test_equity_curve_uses_configured_starting_capital | BUG-B baseline 1.2M | PASS |
| test_equity_net_pnl_consistent_with_frontend_subtraction | BUG-B net P&L -803.97 | PASS |

These isolate the two root causes at the unit level (independent of the live DB).

## Full suite (committed state, 2-pass clean)
- Pass 1: 609 passed, 32 skipped, 0 failures.
- Pass 2: 609 passed, 32 skipped, 0 failures.
- One pre-existing PytestReturnNotNoneWarning (test_backtest_vs_live_crossover)
  — not a failure.

## Prior audit/regression suites already green
- Prior 2-pass 107/107; combined 113; test_regressions/analytics_linkage/reconciliation
  suites — all pass (re-confirmed after these fixes).

## No test was modified to hide a defect
Regression tests were written for the fixes and for issues where the underlying
code was correct. UNCERTAIN results were labelled, never promoted to PASS.