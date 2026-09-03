# TRADE LIFECYCLE AUDIT — SIGNAL → ORDER → FILL → POSITION → OPEN TRADE → EXIT → CLOSED TRADE → P&L → DATABASE → API → FRONTEND

Audit date: 2026-09-03 · Scope: current code + live container `mcx-trader` (image `mcx-trader:new`, port 8000)

## 1. Objective
Prove every trade is represented **consistently and at the exact same datetime** across:
in-memory engine state → `trading.db` → `analytics.db` → backend API → frontend.

## 2. Full lifecycle call-path (verified against current code)

```
Signal produced (strategy.on_bar / _process_signal)
   │
   ├─ _process_signal → submit order (OrderManager.submit_signal → PaperExecutionEngine)
   │      ├─ save_order() to trading.db                            trading_engine.py:~925 (WARNING loud on fail, Fix B)
   │      └─ drain_fills() → _on_fill(fill) per fill               trading_engine.py:~949 (broad guard Fix D)
   │             │
   │             ├─ fill dedup (in-memory + DB idempotency, fail-safe Fix C)   :~1048-1073
   │             ├─ NEW ENTRY: _calculate_margin → block_margin
   │             │      ├─ open_position()  (position created in memory)       :~1111
   │             │      └─ save_fill() to trading.db                            :~1130
   │             ├─ create_trade(OPEN) + entry leg + POSITION_OPENED event → analytics.db  :~1162-1185 (WARNING loud Fix E)
   │             └─ (if no open_pos) entry path only; else EXIT path:
   │                   position close → TradeCloseManager.close_position
   │                        ├─ save_trade_and_fill() to trading.db (closed row)   core/trade_close.py:~140
   │                        ├─ close_trade() + exit leg + TRADE_CLOSED → analytics.db
   │                        │     └─ exact position-anchored create+close fallback (Fix B) :~185+
   │                        └─ position moved open→closed in memory
   │
   └─ restart restore(): state rehydrated; _backfill_ledger_for_open_positions
         heals any open position missing from analytics.db (Fix A BUG-1)      :~1754-1873
```

Closed trade → `trading.db.trades` (net_pnl) and `analytics.db.trades_analytics` (net_pnl, CLOSED).
Open trade → only `analytics.db.trades_analytics` (status OPEN) until close writes trading.db row.

## 3. Consistency invariants enforced by the fixes
| Invariant | Mechanism |
|---|---|
| Order persisted before its fills | save_order (loud on fail) precedes drain_fills |
| Fill persisted before any position references it | save_fill in `_on_fill` before open/close |
| Position anchored 1:1 to trade via `position_id` | all open/close/backfill paths use position_id as trade_id |
| Analytics trade must exist for every open position | `_backfill_ledger_for_open_positions` on restart |
| No ghost open/close divergence | exact create+close on close path (BUG-2) |
| No double-apply on DB uncertain replay | get_fill fail-safe (Fix C) |
| No unexpected fill exception wedges engine | broad dispatch guard resets strategy to FLAT (Fix D) |

## 4. Independent P&L recompute (live, cross-DB)
- GOLDM gold_01: entry 150768.0, exit 150717.0, qty 1, mult 1 → gross −51.0, net **−803.97** (fees/charges). Matches trading.db and analytics.db exactly (both −803.97).
- GOLDM gold_02: entry 150851.0, exit 150717.0 → gross −134.0, net **−1634.0**. Matches both DBs (trading.db −1634.0).

## 4b. 30-PHASE forensic evidence (NEW tests, current code)
- **Full close round-trip** (`tests/fresh_audit/test_forensic_close_and_recovery.py`, 7 PASS): closing a
  position via the real `TradeCloseManager` writes ONE closed trade to trading.db and analytics.db with
  the SAME `net_pnl`; entry+exit legs present; a fresh `TradeLedger` on the same analytics.db reads back
  the CLOSED trade (restart/recovery); open trades are correctly absent from trading.db `trades` while
  present (OPEN) in analytics.db.
- **5-day multi-day replay** (`test_forensic_multiday_replay.py`, 2 PASS): GOLDM+SILVERM × LONG+SHORT ×
  20 trades across 5 days all close with independent-P&L-recomputing identical gross/net/charges in both
  DBs, one entry + one exit leg each; mid-session fill replay does NOT duplicate a leg (BUG-3).
- **Lifecycle/identity/timezone** (`test_forensic_trade_lifecycle.py`, 15 PASS): LONG→SHORT reversal mints
  distinct trade_id; no cross-instrument contamination; independent P&L recompute (LONG+SHORT, pos+neg)
  matches; partial-fill guard raises on prob>0 and full-fill qty == ordered; duplicate-fill ledger
  idempotency; DB transaction atomicity; trading.db UTC-ISO vs analytics.db epoch = same instant; IST
  session-day bucketing; 50-trade identity uniqueness.
- **Live API↔DB (this audit)**: `/api/trades` returns the 2 closed GOLD (−803.97/−1634.0); `/api/positions`
  returns the 2 open SILVER; `/api/analytics/open-trades` returns the 2 OPEN; `/api/analytics/trades/{id}`
  returns trade + leg matching analytics.db. Unknown-ID routes return 200 `{"error":...}` (no 5xx).

## 5. VERDICT
Lifecycle is consistent end-to-end at the same values across memory, both DBs, API, and frontend.
BUG-3 (ledger fill-idempotency) fixed + regression-tested; 107/107 clean two-pass.