# TRADE RECONCILIATION REPORT

Audit date: 2026-09-03

## 1. In-process reconciliation (`reconciliation/engine.py`)
Validates trading.db ↔ in-memory across:
- orders vs fills (every FILLED order has fills; no orphan fill→order refs)
- fills vs positions (no orphan DB fill; every position entry fill present in DB)
- positions vs trades (position-anchored 1:1 on `trade_id = position_id`)
- trades vs P&L (DB trade net_pnl sum == PNLEngine.realized_net, per strategy)
- accounts vs positions (used_margin == sum of position margins)
- duplicate fills/orders detection
- DB-vs-memory order/fill state + price + quantity consistency
- price sanity (rejects <=0/NaN/inf — the `-1` sentinel guard)

**All checks present and executable. No in-code failures.**

## 2. GAP (accepted, documented — not blocking)
`ReconciliationEngine` is constructed with `persistence` (trading.db) + managers only.
It has **no analytics.db cross-check** (`trade_ledger`/`trades_analytics`/`trade_legs` are not wired in).
The closure of this gap was performed here by the **standalone cross-DB audit scripts**
(`audit_cross_db*.py`, executed live over both SQLite files), which proved:
- no missing fills in `trade_legs` (was 2, now 0)
- no duplicates / orphans in either store
- status consistency (2 OPEN / 2 CLOSED correct)
- matching net_pnl across both stores and the independent recompute.

Recommendation (follow-up, out of the blocking path): wire `trade_ledger` into `ReconciliationEngine`
so analytics.db is reconciled inside the standard startup reconcile.

## 3. Live reconciliation result
`trades_analytics`=4, `trade_legs`=6 (0 missing), `trades`=2 closed, `orders`=6, `fills`=6.
No errors. Consistent.

## 3b. API ↔ DB reconciliation (this audit, live)
| API endpoint | Expected | Observed | Match |
|---|---|---|---|
| `GET /api/trades` | trading.db `trades` (2 closed GOLD) | 2 closed: net −803.97 / −1634.0 | ✓ |
| `GET /api/positions` | in-memory open positions | 2 open SILVER (entry 236489 / 236980) | ✓ |
| `GET /api/analytics/open-trades` | analytics.db OPEN rows | 2 OPEN SILVER | ✓ |
| `GET /api/analytics/trades/baa04bef…` | trade + legs | OPEN/LONG + 1 entry leg `d9518514` BUY 236489 | ✓ |
| Unknown-ID `/api/trades/nope`, `/api/analytics/trades/nope` | graceful error | `200 {"error":...}`, no 5xx | ✓ |

Non-blocking note reconfirmed: `/api/orders|fills` are **in-memory only** (not trading.db-backed), so they
are consistent with memory but not with `orders`/`fills` tables after prune/restart. TRADEBOOK
(`/api/trades`) is DB-backed and durable.

## 4. VERDICT: PASS (with one accepted, non-blocking analytics-reconcile gap documented).