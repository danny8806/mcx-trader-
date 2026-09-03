# TRADE FRONTEND MAPPING AUDIT — UI ↔ API ↔ trade data

Audit date: 2026-09-03

## 1. Data selector store (Vite + Pinia)
Frontend consumes backend via HTTP + WebSocket `engine_state`. Trade-facing UI surfaces:
- **Positions** ← `/api/positions` (WebSocket live) — shows entry/stop/quantity.
- **Trades / P&L** ← `/api/trades` (trading.db) + `/api/analytics/*` (analytics.db).
- **Analytics strategy trades** ← `/api/analytics/strategies/{id}/trades` (analytics.db).

## 2. Trade identity mapping
- A closed trade is identified frontend-wide by `trade_id` (= `position_id`).
- An open trade surfaced by the analytics routes uses the same `position_id`, so an OPEN→CLOSED transition
  keeps the same identity key across the UI (no phantom/duplicate rows for the same position).

## 3. Live UI-backing values (verified via API that the UI consumes)
| Frontend block | Backing API | Live value |
|---|---|---|
| Open positions | `/api/positions` | 2 SILVER @ 236489 / 236980 |
| Closed trades | `/api/trades` | 2 GOLD: −803.97 / −1634.0 |
| Strategy trades | `/api/analytics/strategies/*/trades` | 4 rows (2 CLOSED + 2 OPEN) |

## 4. VERDICT: PASS — frontend-handled keys (position_id/trade_id) match the DB/API identity keys; no duplicates or stale OPEN/CLOSED mismatch surfaced.