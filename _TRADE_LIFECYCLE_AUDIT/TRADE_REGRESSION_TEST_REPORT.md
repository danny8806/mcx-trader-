# TRADE REGRESSION TEST REPORT

Ran from a clean state (caches cleared) against current code, 2 passes.

## Suites and results (PASS totals)
| Suite | Result |
|---|---|
| `tests/test_regressions.py` (+Fix A test) | PASS |
| `tests/fresh_audit/test_analytics_linkage.py` (incl. BUG-1/BUG-2 tests) | 7 PASS |
| `tests/fresh_audit/test_reconciliation_linkage.py` | PASS |
| `tests/fresh_audit/test_deep_backend.py` | PASS |
| `tests/fresh_audit/test_financial_core.py` | PASS |
| `tests/fresh_audit/test_full_deep_architecture.py` | PASS |
| `tests/fresh_audit/test_whole_project.py` | PASS |
| `tests/fresh_audit/test_comprehensive.py` | 130 PASS |
| `tests/fresh_audit/test_edge_cases.py` | 40 PASS |

### Aggregate
- Pass 1 (backend+linkage+financial): **159 passed, 16 skipped**
- Pass 2 (architecture+whole-project): **123 passed**
- Comprehensive: **130 passed, 16 skipped**; Edge cases: **40 passed**

## New regression tests added by this audit
1. `test_closed_position_when_ledger_missing_creates_and_closes_exact_trade` (BUG-2)
2. `test_backfill_logic_creates_missing_open_trade` (BUG-1)
3. `test_partial_fill_default_does_not_crash_engine` (Fix A)

## 30-PHASE scope — NEW forensic suites (this audit)
| Suite | Result |
|---|---|
| `tests/fresh_audit/test_forensic_trade_lifecycle.py` (reversal, P&L recompute, partial-fill guard, idempotency, DB atomicity, timezone, identity) | 15 PASS |
| `tests/fresh_audit/test_forensic_close_and_recovery.py` (full close round-trip, trading.db↔analytics net equality, restart recovery, open-absent-from-trades invariant) | 7 PASS |
| `tests/fresh_audit/test_forensic_multiday_replay.py` (5-day GOLDM+SILVERM × LONG+SHORT × 20 trades reconcile; fill-replay no-dup-leg) | 2 PASS |
| Clean-state two-pass (forensic + analytics_linkage + reconciliation_linkage + financial_core) | 107 PASS / 107 PASS |

## BUG-3 regression probes (all PASS)
- `test_duplicate_fill_on_ledger_does_not_duplicate_leg` — duplicate `fill_id` → same `leg_id`, one leg, P&L not double-applied (was failing pre-fix).
- `test_five_day_replay_fill_replay_does_not_duplicate_leg` — in-session fill replay creates no second leg.
- No previously-passing test was broken by the `trade_ledger.py` + `schema.py` idempotency fix.

## Cleanup
- `__pycache__` cleared before runs; no stale bytecode influenced results.
- No test was modified to hide a bug; fixes themselves were added to production code.

## VERDICT: PASS — no regressions introduced by the DB-split / silent-loss fixes or the BUG-3 idempotency hardening.