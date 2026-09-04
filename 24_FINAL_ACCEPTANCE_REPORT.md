# 24 - Final Acceptance Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER
**Auditor:** opencode (automated)

---

## Acceptance Criteria Matrix

### P0 - Critical Findings

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| FAIL-TL-001 | Lifecycle persistence silent failure — `core/lifecycle.py:146` omits 'side' column | **NOT VERIFIED** | `core/lifecycle.py` does NOT exist in this codebase. The file path referenced in the audit brief is invalid. Trade lifecycle is managed by `core/trade_close.py` (TradeCloseManager). |
| Split-Brain Identity | `lifecycle.trade_id` differs from `position.position_id` | **NOT VERIFIED** | No `lifecycle.py` exists. In `trade_close.py:101`, `trade_id` is explicitly set to `position.position_id`. In `trading_engine.py:1079`, `trade_ledger.create_trade()` receives `trade_id=position.position_id`. No split-brain exists. |
| OBS-003 | P&L always 0.0 in lifecycle — `_on_fill()` passes `gross_pnl=0.0` | **FALSE** | `trading_engine.py:978-1271`: `_on_fill()` does NOT pass P&L to lifecycle. The `TradeCloseManager.close_position()` (line 1153) calculates P&L internally via `PNLEngine.calculate_realized_pnl()` (trade_close.py:78). P&L is calculated correctly. |

### P1 - Important Findings

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| DB-001 | Zero foreign key constraints | **TRUE** | `persistence/manager.py:64-131`: No FK clauses in any CREATE TABLE. `analytics/schema.py:16-225`: No FK clauses despite `PRAGMA foreign_keys=ON` (line 267). |
| DB-002 | INSERT OR REPLACE destroys auto-increment IDs | **TRUE** | `persistence/manager.py:159,189,216,238`: All use `INSERT OR REPLACE`. |
| DB-003 | No `get_fills()` public method | **TRUE** | `persistence/manager.py` has no `get_fills()` method. `PaperExecutionEngine.get_fills()` exists (paper_broker.py:191) but returns in-memory fills, not DB fills. |
| DB-004 | No LIMIT on `get_trades()` | **TRUE** | `persistence/manager.py:283-297`: `get_trades()` returns ALL rows without LIMIT. |
| DB-005 | `events` and `account_snapshots` grow unbounded | **TRUE** | `persistence/manager.py:266-281`: `save_event()` appends without cleanup. `save_account_snapshot()` appends without cleanup. No TTL or retention. |
| DB-006 | `processed_fills` grows unbounded | **PARTIAL** | Has `cleanup_old(days=30)` method (fill_dedup.py:110) but NOT called automatically. |

### P2 - Design Observations

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| ARCH-001 | Two separate databases | **TRUE** | `trading.db` (operational) + `analytics.db` (analytics). No cross-DB FK or consistency mechanism. |
| ARCH-002 | Indicator state not persisted | **TRUE** | `trading_engine.py:1420-1422`: Intentionally NOT persisted. Recomputed from REST on startup. |
| ARCH-003 | No ORM used | **TRUE** | Raw SQL with `sqlite3` throughout. |
| ARCH-004 | No authentication on dashboard | **TRUE** | `server.py:309`: WebSocket accepts all connections. No auth middleware. |
| ARCH-005 | Paper-only execution enforced | **TRUE** | `trading_engine.py:244-248`: Raises RuntimeError if not paper mode. |

---

## Functional Verification

| Function | Status | Notes |
|----------|--------|-------|
| Signal generation (DEMA-ATR crossover) | **WORKING** | `base_dema_strategy.py:229-268` |
| Pending breakout entry | **WORKING** | `base_dema_strategy.py:334-393` |
| Stop loss execution | **WORKING** | `base_dema_strategy.py:507-521` |
| Tick-level stop | **WORKING** | `base_dema_strategy.py:587-595` |
| Same-bar stop | **WORKING** | `base_dema_strategy.py:523-547` |
| Reversal execution | **WORKING** | `base_dema_strategy.py:395-450` |
| Atomic trade close | **WORKING** | `trade_close.py:51-297` |
| Fill deduplication | **WORKING** | `fill_dedup.py` |
| P&L calculation | **WORKING** | `pnl.py:52-78` |
| Fee model | **WORKING** | `fee_model.py:49-91` |
| Risk checks | **WORKING** | `risk_engine.py:52-96` |
| Reconciliation | **WORKING** | `reconciliation/engine.py` |
| Market session management | **WORKING** | `market_status.py` |
| Safe mode | **WORKING** | `safe_mode.py` |
| REST candle fetching | **WORKING** | `candle_fetcher.py` |
| WebSocket LTP | **WORKING** | `data/dhan/websocket_client.py` |
| Dashboard API | **WORKING** | `dashboard/server.py` |
| Analytics API | **WORKING** | `analytics/routes.py` |
| Telegram notifications | **WORKING** | `notifications/telegram_router.py` |
| State persistence | **WORKING** | `persistence/manager.py` |

---

## Data Integrity Summary

| Invariant | Status | Enforcement |
|-----------|--------|------------|
| Every fill references an order | **VIOLABLE** | No FK constraint. Detected by reconciliation. |
| Every trade has 1:1 with position | **ENFORCED** | TradeCloseManager atomic design. |
| P&L consistency (DB vs memory) | **ENFORDED** | Reconciliation check on startup. |
| No duplicate fills | **ENFORDED** | FillDeduplicator (in-memory + DB). |
| No duplicate orders | **ENFORDED** | UUID generation + dedup key. |
| Margin consistency | **ENFORCED** | Reconciliation check on startup. |

---

## Final Verdict

| Category | Score | Notes |
|----------|-------|-------|
| Core trading logic | **PASS** | Signals, execution, P&L, risk all working correctly |
| Data persistence | **PARTIAL** | INSERT OR REPLACE, no FK, unbounded growth |
| Crash recovery | **PARTIAL** | Atomic close design, but no automated recovery |
| Testing | **PARTIAL** | Good coverage for core logic, missing integration tests |
| Operations | **PARTIAL** | Basic monitoring, no logging framework, no auth |
| Documentation | **PARTIAL** | Some docs exist, but no API docs, no runbooks |

### Overall Assessment

**The MCX-TRADER system is FUNCTIONAL for paper trading.** The core trading logic is well-implemented with correct signal generation, execution, P&L calculation, and risk management. The atomic trade close design provides strong crash recovery guarantees for the critical path.

**Key areas needing attention:**
1. Database schema hardening (FK constraints, indexes, retention)
2. Dashboard security (authentication, rate limiting)
3. Operational maturity (logging, metrics, alerting)
4. Test coverage gaps (integration, crash recovery, concurrency)

**The audit brief's P0 findings (FAIL-TL-001, Split-Brain, OBS-003) are NOT VERIFIED in this codebase.** The referenced file paths and code patterns do not exist. The actual implementation is correct for the identified critical paths.
