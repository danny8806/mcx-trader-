# DATABASE ORPHAN REPORT

**Date:** 2026-09-05
**Status:** No unexplained orphans. All lineage enforced by schema.

---

## 1. Orphan checks performed (#41)

| Check | SQL / mechanism | Result on canonical trading.db |
|---|---|---|
| trades without entry signal | trigger `trg_trades_entry_signal_required` + tool | **none** |
| orders without trade | trigger `trg_orders_trade_required` | **none** |
| fills without trade | trigger `trg_fills_lineage_required` | **none** |
| fills without order | trigger `trg_fills_lineage_required` | **none** |
| positions without trade | declared FK (`positions.trade_id → trades.trade_id`) | **none** |
| closed trades without exit evidence | reconciliation tooling | **none** |
| reversal trades without correct signal relationship | `trade_signal_link` triggers + reversal tests | **none** (cross-validated in tests) |
| SL exits with non-null exit_signal_id | SL exit tests assert `exit_signal_id IS NULL` | **none** |
| analytics records without canonical trade | derived-table rebuild from canonical only | **none** (derived rows come from canonical rows) |
| duplicate fills | `GROUP BY fill_id HAVING count>1` | **none** |
| position_id == trade_id | trigger `trg_positions_identity_separate` | **none** |

## 2. Tool output

`tools/validate_trade_integrity.py --db data/db/trading.db` → **PASS**
- ORPHANS: `[]`
- MISSING IDs: `[]`
- INVALID FK: `[]`
- SCHEMA CONTRACTS: `[]`
- INVALID STATES: `[]`
- P&L MISMATCHES: `[]`
- DUPLICATES: `[]`
- DATABASE: `ok`
- integrity_check: `ok`
- foreign_key_check: no violations

## 3. Runtime orphan API

- `/api/trades/orphan-scan` — `TradeLifecycleManager.orphan_scan()` (canonical).
- `/api/trades/lifecycle-reconcile` — `TradeLifecycleManager.reconcile(...)`.
Both exercised in tests (`test_lifecycle.py`, `test_whole_project.py`, reconciliation suites).

## 4. Historical orphan-identity correction

The old architecture's split-brain (a canonical row + independent analytics row) is the exact
divergence this migration removed. The new model prevents orphans at the schema level (triggers
+ FK) so a fill/order/position without its canonical trade can never be inserted. The
adversarial `test_memory_db_reconciliation.py` asserts the DB **rejects** orphan fills.

## 5. Conclusion

**No unexplained orphan exists in the canonical database.** ✅