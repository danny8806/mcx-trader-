# OLD VS NEW DATABASE ARCHITECTURE

**Date:** 2026-09-05
**Status:** Migration complete — ONE canonical database.

---

## OLD ARCHITECTURE (two independent databases)

```
                 ┌──────────────────────────┐         ┌──────────────────────────┐
                 │        trading.db        │         │       analytics.db       │
                 │  (operational)           │         │  (analytics / ledger)    │
                 ├──────────────────────────┤         ├──────────────────────────┤
                 │ signals                  │         │ trades_analytics         │
                 │ trades (thin row)        │         │   (trade_id = position_id)│
                 │ orders                   │         │ trade_legs               │
                 │ fills                    │         │ trade_events             │
                 │ positions                │         │ trade_snapshots          │
                 │ system_state.json        │         │ strategy_*_performance   │
                 └───────────┬──────────────┘         └───────────┬──────────────┘
                             │                                    │
        TradeLifecycleManager│                                    │TradeLedger
                             ▼                                    ▼
                       (writes)                            (writes independently)
                             │                                    │
                             └────── BOTH call themselves "trade truth" ─────┘

PROBLEMS:
  - Two independent trade authorities → divergence (the `baa04bef` pattern).
  - trade_id was conflated with position_id.
  - Duplicate-fill handling was inconsistent across stores.
  - Analytics could not be proven equivalent to operational trades.
```

## NEW ARCHITECTURE (ONE canonical database)

```
                 ┌──────────────────────────────────────────────┐
                 │                  trading.db                  │
                 │                                              │
                 │   CANONICAL TABLES (authoritative):          │
                 │   signals, trades, orders, fills, positions, │
                 │   pending_orders, trade_signal_link,         │
                 │   processed_fills, account_snapshots,        │
                 │   events, trade_events, quarantine_records,  │
                 │   system_metadata                            │
                 │                                              │
                 │   DERIVED TABLES (rebuildable read-model,    │
                 │   NEVER authoritative):                      │
                 │   trades_analytics, trade_legs,              │
                 │   trade_snapshots, strategy_*_performance,   │
                 │   strategy_parameter_results                 │
                 └───────────────┬──────────────────────────────┘
                                 │
              Canonical Repository / Database (persistence.database)
                                 │
                    ┌────────────┴─────────────┐
                    │                          │
               REST API                    WebSocket
              (/api/*)                        │
                    │                          │
                    └─────────────┬────────────┘
                                  │
                              FRONTEND
                              ┌─────┴─────┐
                          TRADEBOOK    ANALYTICS
                                        (derived from trading.db)
```

### Key differences

| Aspect | OLD | NEW |
|---|---|---|
| Number of DBs | 2 (trading.db + analytics.db) | 1 (trading.db) |
| Trade authority | TradeLedger(analytics.db) + TradeLifecycleManager(trading.db) | single canonical lifecycle in trading.db |
| `_open_trades` | memory truth | cache only; DB fallback on miss |
| trade_id generation | implicit, often = position_id | explicit, immutable, generated once at creation |
| position_id | alias of trade_id | separate identity (`position_id != trade_id`) |
| Derived analytics | separate file `analytics.db` | derived tables *inside* trading.db |
| P&L authority | two sources | one (canonical trades/fills) |
| `init_analytics_db` | part of runtime setup | dead for production (never called); MIGRATION-ONLY |
| FK enforcement | none across stores | declarative FK + triggers (dual enforcement) |
| Rebuild | not possible | derived tables can be cleared and rebuilt from canonical tables |

### Identity model (canonical)

```
signal_id  -> signals (signal candle snapshot)
trade_id   -> trades (entry_signal_id, exit_signal_id)        [IMMUTABLE]
order_id   -> orders (trade_id)
fill_id    -> fills (trade_id, order_id)                      [idempotent]
position_id-> positions (trade_id, position_id != trade_id)
```

- **SL exit:** same `trade_id` closes; `exit_signal_id IS NULL`; `exit_reason=STOP_LOSS`; no second trade.
- **Reversal:** one signal is `exit_signal_id` of old trade AND `entry_signal_id` of new trade;
  two distinct `trade_id`s; same trigger price.

### Write order enforced (triggers)

```
signal ──> trade(entry_signal_id) ──> order(trade_id) ──> fill(trade_id, order_id) ──> position(trade_id)
```

Enforced by triggers `trg_trades_entry_signal_required`, `trg_trades_entry_signal_exists`,
`trg_orders_trade_required`, `trg_orders_trade_exists`, `trg_fills_lineage_required`,
`trg_fills_trade_exists`, `trg_fills_order_exists`, `trg_positions_identity_separate`,
`trg_trade_signal_link_trade_exists`, `trg_trade_signal_link_signal_exists`.

---

## Runtime proof

| Check | Result |
|---|---|
| `config/settings.json:7` | sole `db_path = data/db/trading.db` |
| `dashboard/server.py:35-36` | analytics routes inited from **canonical db** |
| `trading_engine.py:91-102` | EventStore + TradeLedger constructed with **canonical db_path** |
| `persistence/database.py:1-17` | ONE canonical database; derived tables inside it |
| Production code references to `analytics.db` | **ZERO** |
| Legacy `data/db/analytics.db` read/written by production | **NO** (archived, empty) |

See `DATABASE_CONNECTION_AUDIT.md`, `DATABASE_SCHEMA_VERIFICATION.md`, `DATABASE_ORPHAN_REPORT.md`.