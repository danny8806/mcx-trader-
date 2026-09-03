# TRADE DATABASE AUDIT — TradingEngine data stores (trading.db + analytics.db)

Audit date: 2026-09-03 (12:46 IST pre-fix baseline, post-fix final checks)

## 1. Two DB stores, one identity key `trade_id = position_id`

| Store | Tables | Row created | Status lifecycle |
|---|---|---|---|
| `trading.db` | `trades`, `orders`, `fills`, `account_snapshots`, `events`, `processed_fills` | `trades` row at **CLOSE** | closed only |
| `analytics.db` | `trades_analytics`, `trade_legs`, `trade_events` | `trades_analytics` row at **OPEN** | OPEN → CLOSED |

## 2. LIVE pre-fix finding (the bug the fixes address)
- 2 closed GOLD trades fully consistent across both DBs (net −803.97 / −1634.0 identical).
- **2 open SILVER positions split**: present in trading.db `fills`/`orders` (`d9518514`/`85ebbf0b`) + memory, but **absent from analytics.db** (no `trades_analytics`, no `trade_legs`, no POSITION_OPENED).
- Root cause: open-time `create_trade`+entry-leg+event only ran at fresh open; `restore()` from state never re-ran it; writes swallowed by `except: pass`.

## 3. POST-fix live state (verified after v3 deploy)
- `trades_analytics` = 4 (gold_01, gold_02 CLOSED; silver_02, silver_01 OPEN). **Split resolved.**
- `trade_legs` = 6, **0 fills missing from legs** (was 2).
- No duplicate trade_id / fill_id / order_id anywhere.
- No status mismatch; no orphans in either store.
- All 4 position_ids present across analytics.
- In-memory positions (2 SILVER) survive restarts with identical IDs/entries.

### trading.db (verified)
- `trades`: 2 closed GOLD rows, net −803.97 / −1634.0 — matches analytics exactly.
- `fills`: 6, every one has a matching order row (order-before-fill invariant intact).
- `orders`: 6, states all `filled`.

## 4. Schema / route map
- `/api/trades`, `/api/trades/{id}` → trading.db `trades` (dashboard/routes/trades.py:21,51)
- `/api/orders`, `/api/fills`, `/api/positions`, `/api/overview`, `/api/pnl` → **in-memory only** (NOT trading.db; after prune/restart these views can be incomplete vs the DB tables — documented non-blocking note).
- `/api/analytics/strategies/{id}/trades`, `/api/analytics/trades/{id}` → analytics.db (analytics/routes.py)
- There are **no `/api/author/orders|fills` routes**.

## 4b. BUG-3 schema hardening (this audit)
- Added `CREATE UNIQUE INDEX idx_trade_legs_fill_id ON trade_legs(fill_id)` in `analytics/schema.py`
  (`CREATE_INDEXES` list) — one fill maps to exactly one leg, enforced at the DB.
- `TradeLedger.record_fill` now dedups on `fill_id` (`_get_leg_fill_id`) before inserting.
- Live DB files unchanged (index is additive, `IF NOT EXISTS`); clean up any hypothetical dupes via
  the unique index reject + replay path.

## 5. VERDICT: PASS — both stores consistent, DB-split resolved, no orphans/duplicates; idempotency hardening added (BUG-3).