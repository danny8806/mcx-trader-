# TRADE API AUDIT — backend routes ↔ DB ↔ memory

Audit date: 2026-09-03 · Live container verified over HTTP.

## 1. API routing matrix (verified against code + live requests)
| Endpoint | Source store | Verified live |
|---|---|---|
| `/api/trades` | trading.db `trades` | ✓ 2 closed GOLD rows |
| `/api/trades/{id}` | trading.db `trades` | ✓ position-anchored id lookup |
| `/api/author/orders` | trading.db `orders` | ✓ 6 orders |
| `/api/author/fills` | trading.db `fills` | ✓ 6 fills |
| `/api/positions` | in-memory engine snapshots | ✓ 2 SILVER, correct entries |
| `/api/overview` / `/api/pnl` | in-memory engine snapshots | ✓ consistent |
| `/api/analytics/strategies/silver_02/trades` | analytics.db `trades_analytics` | ✓ returns OPEN trade `baa04bef` |
| `/api/analytics/strategies/silver_01/trades` | analytics.db | ✓ returns OPEN trade `9983dd92` |
| `/api/analytics/trades/{id}` | analytics.db | ✓ returns trade + leg |

## 2. Fresh-trade propagation (the fixed path)
Before fix: an OPEN SILVER position was **invisible** to `/api/analytics/strategies/*/trades`
because no `trades_analytics` row existed. After fix (backfill + BUG-1), these endpoints return
the OPEN trade with entry price, multiplier (5.0), and live MFE/MAE.

## 3. Consistency: API value == DB value == memory value
- `/api/analytics/strategies/silver_02/trades` entry_price 236489 == analytics.db == memory (236489.0).
- gold net −803.97 / −1634.0 identical across trading.db API + analytics.db API + recompute.

## 4. VERDICT: PASS — all trade-lifecycle routes return the same values as the stores that back them.