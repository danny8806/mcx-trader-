# 18 — COMPLETE ARCHITECTURE (Final Consolidated)

Master Prompt report #18. Produced after the full live forensic audit and the
two HIGH-severity fixes (state desync + equity baseline) were root-caused,
fixed, regression-tested, deployed to the live container, and verified.

---

## 1. Topology

```
Internet / Dhan websocket
        |
        v
[ data_adapter ] --ticks--> [ timeframe_engine (5m/15m/1h) ]
        |                        +--> [ htf_engine (1h) ]
        +                        +--> [ indicators (DEMA/ATR) ]
        v
 [ TradingEngine (mcx-trader container, fastapi :8000) ]
   |- strategies (BaseDEMAStrategy) : gold_01, gold_02, silver_01, silver_02
   |- position_manager  (authoritative open positions)
   |- execution_engine (paper_broker -> fills)
   |- account_engine / per-strategy PNLEngine (realized/unrealized)
   |- risk_engine
   |- persistence (trading.db, system_state.json)
   |- trade_ledger / analytics (analytics.db) -- backfilled for open positions
   |
   +--> Dashboard (READ/CONTROL layer) -- REST: /api/*  + WS /ws engine_state
              (dashboard/routes/*, analytics/routes/*)
              +--> React Dashboard (dashboard-ui) -- panels, Strategy Matrix,
                   Strategy Detail, Equity Graph, Positions, Overview, ...
```

Two persistent SQLite DBs:
- `trading.db` — trades, orders, fills, processed_fills, account_snapshots, events.
  Timestamps as UTC ISO strings (account snapshots) and epoch floats (orders/fills).
- `analytics.db` — trades_analytics, trade_legs, trade_events. Epoch float timestamps.

## 2. Data-source authority per concern

| Concern | Authoritative source | Consumers |
|---|---|---|
| Open position | position_manager | /api/positions, overview, BS matrix position |
| Strategy realized P&L | per-strategy PNLEngine snapshot | /api/strategies, matrix, pnl, overview |
| Closed trade record | analytics.db (trades_analytics) + trading.db trades | equity/drawdown, trade history |
| Equity curve baseline | account.starting_capital (1,200,000) | equity graph (after fix #2) |
| Strategy position/state | position_manager (reconciled in serializer) | matrix state, WS engine_state (after fix #1) |
| Orders / fills | execution_engine in-memory | /api/orders, /api/fills (NOT trading.db-backed) |

## 3. Key invariants verified live

- Every open position has a trades_analytics row (OPEN), entry leg, and
  POSITION_OPENED event (BUG-1/BUG-3 backfill). No missing legs.
- TradeLedger.record_fill is idempotent on fill_id (BUG-3 UNIQUE index) — no
  duplicate legs on duplicated fills / crash-restart.
- Reversal must mint a NEW trade_id (never reuse); LONG==SHORT mirror P&L math.
- GOLDM and SILVERM never contaminate each other's identity.
- Equity = starting_capital + realized + unrealized (account.py:57-59).

## 4. Live reconciliation (after fixes, verified)

| Metric | Value | Source |
|---|---|---|
| realized_pnl | -2,437.97 | gold_01 -803.97 + gold_02 -1634.0 (trading.db closed) |
| open_positions_count | 2 | silver_01 + silver_02 LONG (position_manager) |
| strategy state (silver) | long_position / LONG | HTTP + WS (after fix #1) |
| equity baseline | 1,200,000 | account.starting_capital (after fix #2) |
| gold_01 equity net P&L | -803.97 | 1,199,196.03 - 1,200,000 |
| gold_02 equity net P&L | -1,634.00 | 1,198,366.00 - 1,200,000 |

DB counts (post-deploy, unchanged from pre): analytics 4/6/12, trading 2/6/6,
PRAGMA integrity_check = ok for both.

## 5. Responsibility boundaries

- Engine/execution/portfolio = trading correctness & authoritative position state.
- Dashboard routes = READ/CONTROL serialization, MUST stay consistent with engine.
- Analytics = derived performance/equity/drawdown from analytics.db.
- Frontend = rendering only; must not invent baselines (starting_capital used).