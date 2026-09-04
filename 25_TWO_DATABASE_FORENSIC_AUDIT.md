# TWO DATABASE FORENSIC AUDIT

**Date**: 2026-09-04
**Scope**: Read-only forensic investigation of the trading.db vs analytics.db architecture, data flow, ownership, and the `baa04bef` / `81aeeff0` status mismatch.
**Evidence base**: Actual current source code + actual live container databases + on-disk backup files. No prior audit report was used as evidence.

---

## 1. Executive Finding

**What are trading.db and analytics.db?**

- `trading.db` is the **transactional persistence store** for the trade lifecycle and execution artifacts. It holds the flat canonical `trades` row (trade_id, strategy, side, entry/exit price, P&L, status) plus `orders`, `fills`, `signals`, `trade_signal_link`, `account_snapshots`, `events`. Owned by `persistence/manager.py` (`PersistenceManager`).
- `analytics.db` is the **rich analytics / reporting ledger**. It holds `trades_analytics` (an extended per-trade record with entry/exit legs, MFE/MAE, r-multiple, duration, bar snapshots), `trade_legs`, `trade_events`, and performance projection tables. Owned by `analytics/trade_ledger.py` (`TradeLedger`), `analytics/event_store.py`, `analytics/performance.py`.

**Why do both exist?**
They are two layers with different purposes:
- `trading.db` = the durable source-of-truth for **what happened** (execution + closed-trade P&L record), used for recovery and the trades API.
- `analytics.db` = the **derived/rich analytics projection** of the same round trip (fills as legs, MFE/MAE, performance), used for the analytics/performance/equity dashboard.

**Are they supposed to match?**
**Yes, for the trade lifecycle state they overlap.** `trades_analytics.trade_id` is intended to be the **same trade_id as** `trades.trade_id` (both position-anchored 1:1), and that trade's `status`, `entry_price`, `exit_price`, `net_pnl`, `gross_pnl`, `fees` are **supposed to agree**. The two tables represent the *same trade*, at different levels of detail. analytics.db is not a second, independent, canonical ledger — it is a projection of the same canonical identity.

**Which is authoritative?**
`trading.db` is the canonical/operational source of truth for the trade's existence, status, and final P&L. `analytics.db` is a derived projection that must be kept in lockstep (the entry/exit legs *plus* the status/P&L). Memory (`TradeLifecycleManager`, `PositionManager`) is the live working set.

**Is there currently more than one source of truth?**
**Yes, operationally.** trading.db + analytics.db + in-memory lifecycle are each written by overlapping code paths. The `baa04bef` case proved analytics.db could silently diverge from trading.db. There is **no transaction spanning both DBs** and **no automated cross-DB reconciliation** after the fact.

**Why can status mismatch occur?**
Because analytics.db is written through a **separate, cache-dependent code path** (`TradeLedger.record_fill` + `close_trade`) that can be skipped or partially applied when the trade is missing from the in-memory `_open_trades` cache. When that write is skipped, trading.db (also written by an independent `save_trade_and_fill`/`save_trade` path) can be `closed` while analytics.db stays `OPEN`.

---

## 2. Actual Database Locations

| Database | Host path | Container path | Evidence |
|---|---|---|---|
| trading.db | `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/trading.db` | `/app/data/db/trading.db` | `docker inspect mcx-trader --format "{{json .Mounts}}"` shows bind mount `.../data/db -> /app/data/db`; `find / -name '*.db'` leaves only `/app/data/db/...`; no `/app/trading.db` |
| analytics.db | `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/analytics.db` | `/app/data/db/analytics.db` | same mount / find |

- Runtime entry point is `dashboard/run.py` (Docker `CMD=[python dashboard/run.py]`, `WORKDIR=/app`).
- `dashboard/server.py:35` resolves analytics path as `<root>/data/db/analytics.db`.
- `dashboard/server.py:209-210` resolves trading.path as `<root>/data/db/trading.db`.
- `trading_engine.py:92-107` derives `analytics_db` from the same `system.db_path` (`data/db/trading.db`) directory → both colocated in `/app/data/db/`.
- **The stale `trading.db` at the local repo root is NOT used by the running system** (confirmed `NO_ROOT_TRADING_DB` in container). It is a local dev artifact.

Live container flow confirmed: `python dashboard/run.py` → `server.py lifespan`:
1. `TradingEngine(event_callback=...)` → constructs `EventStore(analytics.db)` + `TradeLedger(analytics.db)` (trading_engine.py:95-103).
2. `PersistenceManager(state_path=.../system_state.json, db_path=.../trading.db)` (server.py:208-211) → `_engine.set_persistence()`.
3. `_engine.start()` wires `TradeCloseManager(..., trade_ledger=self.trade_ledger)` and `TradeLifecycleManager(..., persistence, event_store, trade_ledger)` (trading_engine.py:379-397), runs startup reconciliation + state restore.

---

## 3. Database Architecture

```
                     +---------------------------+
                     |   TradingEngine (memory)   |
                     |  _lifecycle (TradeCtx map) |
                     |  position_manager          |
                     |  execution_engine (fills)  |
                     |  pnl_engines / accounts    |
                     +-------------+--------------+
                                  |
            open  (trading_engine._on_fill)          close (TradeCloseManager.close_position)
              |                                                  |
   +----------v-----------+                    +-----------------v--------------------+
   | trade_ledger.create_ |                    | persistence.save_trade_and_fill      |
   | trade + record_fill  |                    |  (trading.db trades+fill)            |
   |  (analytics.db)      |                    | trade_ledger.record_fill(exit)       |
   +----------+-----------+                    |   + close_trade  (analytics.db)      |
              |                                +-----------------+--------------------+
   OWNS analytics.db trades_analytics/trade_legs          | owns trading.db trades/orders/fills
```

Two independent stores written by overlapping-but-not-atomic code paths.

---

## 4. Complete Schema Comparison

**TRADING.DB** (`/app/data/db/trading.db`) tables:
- `trades` (trade_id PK-UNIQUE, strategy_id, instrument, side, entry/exit_timestamp, entry/exit_price, quantity, multiplier, gross_pnl, charges, net_pnl, exit_reason, status, created_at, entry_signal_id, exit_signal_id)
- `orders` (order_id UNIQUE, ..., trade_id, entry_signal_id)
- `fills` (fill_id UNIQUE, ..., trade_id, entry_signal_id)
- `signals`, `trade_signal_link`, `account_snapshots`, `events`

**ANALYTICS.DB** (`/app/data/db/analytics.db`) tables:
- `trades_analytics` (trade_id PK, strategy_id, status, entry/exit prices, filled/remaining/exit quantity, legs/P&L/MFE/MAE/duration, position_id, closed_at, created_at, updated_at, ...)
- `trade_legs` (leg_id PK, trade_id, fill_id, order_id, side, quantity, price, timestamp, is_entry)
- `trade_events` (event_id PK, trade_id, strategy_id, timestamp, event_type, payload, sequence_number)
- `trade_snapshots`, `strategy_*_performance`, `strategy_parameter_results`, `strategy_performance_snapshots`

**Relationship**: `trades_analytics.trade_id` == `trades.trade_id` (both = the position_id). `trade_legs.trade_id` == same.

---

## 5. Trade Lifecycle Architecture

```
Signal  (strategies → trading_engine)
   │  _lifecycle.create_trade_from_signal()  → trade_id = uuid4()
   ▼
Trade (TradeLifecycleManager in-memory)  -- persists to trading.db via _persist_trade
   │  register_order / activate_pending
   ▼
Order  (order_manager → execution_engine)  -- trading.db orders
   │  register_fill
   ▼
Fill   (paper_broker → _on_fill)           -- trading.db fills + trade_ledger entry leg
   │  register_position (migrates trade_id ↔ position_id)
   ▼
Position (position_manager)  position_id = uuid4() (independent)
   │  1:1 trade_id == position_id
   ▼
Exit   (TradeCloseManager.close_position)
   │   ├─ trading.db: save_trade_and_fill (closed row + exit fill)
   │   ├─ analytics.db: trade_ledger.record_fill(exit) + close_trade
   │   └─ lifecycle: register_exit_fill + close_trade (P&L)
   ▼
Closed trade → analytics projection + API + performance/equity
```

---

## 6. Trade ID Lineage

- `TradeContext.trade_id = str(uuid.uuid4())` — born in `TradeLifecycleManager.create_trade_from_signal` (lifecycle.py:70, 302-383). **This is the only place a trade_id is born.**
- `Position.position_id = uuid4()` in `portfolio/position_manager.py` (independent UUID).
- On entry fill, `trading_engine.py:1324` calls `_lifecycle.register_position(trade_id, position_id)`. The SPLIT-BRAIN fix in `register_position` (lifecycle.py:523-554) **renames trade_id → position_id** when they differ, so the persisted trading.db trade_id == position_id.
- On the analytics side, `trading_engine.py:1382` creates the ledger row with `trade_id = position.position_id` (already the unified identity).
- ⇒ **Both DBs and lifecycle agree on `trade_id == position_id`**. The `771` signal linkage is stored in `trade_signal_link` / `entry_signal_id` / `exit_signal_id`.

The fills/orders in trading.db have `trade_id = NULL` on the live rows (pre-existing wiring gap — broker objects don't carry trade_id), which is why the reconciliation orphan-scan reports orphan fills/orders.

---

## 7. Database Write Ownership

### trading.db `trades`
| Writer | File:function | Status written | Transaction |
|---|---|---|---|
| `TradeCloseManager` | `core/trade_close.py:119-154` `save_trade_and_fill` | `closed` (lowercase) | single py connection, commit |
| `TradeLifecycleManager` | `core/lifecycle.py:714-731` `_persist_trade` → `save_trade` | `PENDING`/`OPEN`/`CLOSED` (uppercase) | single connection, commit |

### trading.db `orders` / `fills`
- `PersistenceManager.save_order` / `save_fill` / `save_trade_and_fill` — written by order/fill/close flows.

### analytics.db `trades_analytics`
| Writer | File:function | Operation |
|---|---|---|
| `TradeLedger.create_trade` | `analytics/trade_ledger.py:136-184` | insert OPEN row |
| `TradeLedger.record_fill` | `analytics/trade_ledger.py:186-225` | write leg; **conditionally** `_update_entry_fill`/`_update_exit_fill` if trade in `_open_trades` |
| `TradeLedger._update_exit_fill` | `analytics/trade_ledger.py:260-309` | set exit fields, status=CLOSED, P&L |
| `TradeLedger.close_trade` | `analytics/trade_ledger.py:311-347` | status=CLOSED + P&L override |
| `_backfill_ledger_for_open_positions` | `trading_engine.py:2147+` | restart heal (create + entry leg) |

Callers of the ledger write path: `trading_engine.py:1356-1398` (open), `core/trade_close.py:200-264` (close), `trading_engine.py:2147+` (restart).

**`MULTIPLE WRITERS` for trade status: `lifecycle._persist_trade` (trading.db) and `TradeLedger` (analytics.db) are independent writers of the same logical trade status.**

---

## 8. Database Read Ownership

| Consumer | Data | Source | File |
|---|---|---|---|
| `/api/trades` | lifecycle `get_trades_for_api` → fallback persistence `get_trades` (trading.db) | in-memory / trading.db | dashboard/routes/trades.py:22-56 |
| `/api/trades/{id}` | lifecycle `get_trade` → fallback trading.db | in-memory / trading.db | trades.py:61-77 |
| `/api/positions` | `position_manager` (memory) | memory | positions.py |
| `/api/orders`, `/api/fills` | `execution_engine` (memory) | memory | orders.py |
| `/api/overview` | `account_engine`, `execution_engine`, `risk_engine` (memory) | memory | overview.py |
| `/api/pnl`, `/api/strategies` | `account_engine`, `pnl_engines` (memory) | memory | pnl.py, strategies.py |
| `/api/equity-curve` | `persistence.get_account_snapshots` (trading.db) then fallback account_engine | trading.db | pnl.py:125-129 |
| `/api/analytics/*` | `_trade_ledger` (analytics.db) + `_performance_engine` | analytics.db | analytics/routes.py |
| `/api/analytics/strategies` | `_trade_ledger.get_trades_for_strategy`/`get_closed_trades` | analytics.db | analytics/routes.py:607-621 |
| WebSocket `engine_state` | `engine.snapshot()` (memory) enriched by `/api/strategies`/PNL | memory | server.py:138-154, 80-113 |
| Reconciliation | `persistence.db_path` (trading.db only) | trading.db | reconciliation/engine.py:108 |

**Key point**: `/api/trades` reads trading.db (via lifecycle/DB) but **never** analytics.db. `/api/analytics/*` reads **only** analytics.db. A trade marked `OPEN` in analytics.db therefore shows up as an "open trade" in `/api/analytics/open-trades` and a "0 closed trades" in `/api/analytics/strategies`, while `/api/trades` (trading.db) shows it closed — **this is the exact frontend/API divergence observed.**

---

## 9. Status State Machine

Lifecycle (`TradeStatus`, uppercase): `PENDING → (trigger) → OPEN → (exit fill / close) → CLOSED`; `EXIT_PENDING`; `REJECTED`; `CANCELLED`.

Ledger (`TradeStatus`, uppercase): `OPEN → (exit fill full close) → CLOSED` or `PARTIALLY_CLOSED`; `close_trade` forces `CLOSED`.

trading.db `trades.status` is written **lowercase** `'closed'` by `TradeCloseManager` (trade_close.py:135) and **uppercase** `'CLOSED'` by `lifecycle._persist_trade`.

**Intentionally allowed transitions** (from code):
- `register_exit_fill` sets exit fields but **does not** change status.
- `close_trade` sets `status = TradeStatus.CLOSED.value`.
- `apply_stop_loss` sets exit fields, **doesn't** change status (a separate flow closes).
- No code path performs `CLOSED → OPEN` (no regression to open after close).

---

## 10. trading.db vs analytics.db Comparison

Both represent the **same trade** (same `trade_id == position_id`), same strategy/instrument/side/entry. They differ in **depth**:
- trading.db: single flat `trades` row (operational/execution truth + final P&L).
- analytics.db: `trades_analytics` + `trade_legs` (entry+exit legs) + `trade_events` (rich analytics).

They are **supposed to agree on status & final P&L**. analytics.db is the *derived projection*; trading.db is the *canonical operational record*. But because they are written by **separate code paths** with **no shared transaction** and analytics has a **cache dependency**, they can diverge.

---

## 11. baa04bef... Investigation

**trade_id**: `baa04bef-f0bd-47a8-acaf-66cfafb059fb` (silver_02, LONG, SILVERM, qty 1, multiplier 5)

**trading.db** (current): `status='closed'`, entry=236489, exit=242823, gross=31670, charges=244.97, net=31425.03, exit_reason='short_reversal'.

**analytics.db** (current, post earlier status-fix): `status='CLOSED'`, net=31425.03, gross=31670, fees=244.97, exit_price=242823, closed_at set, **BUT** `exit_quantity=0`, `average_exit_price=NULL`, `last_exit_fill_time=NULL`, `exit_order_id=NULL`.

**Original divergence (pre-fix, confirmed from on-disk backups)**: analytics.db `status='OPEN'`, `exit_price=None`, `net_pnl=None`, all exit fields NULL — while trading.db already said `closed` with P&L. This was the divergence reported.

**trade_legs** for baa04bef: entry leg (is_entry=1, fill `d9518514`, price 236489) **and** exit leg (is_entry=0, fill `0ab88696`, price 242823) both exist in analytics.db.

**Root cause of the analytical divergence**: The exit **leg row** was written, but the trade's **exit accounting fields were never applied** — because at the time the exit fill was processed, `baa04bef` was **not present in `TradeLedger._open_trades`**. In `TradeLedger.record_fill` (trade_ledger.py:186-225):

```python
self._save_leg(leg)                 # leg row ALWAYS written
trade = self._open_trades.get(trade_id)
if trade:                           # exit accounting ONLY if in open cache
    if is_entry: self._update_entry_fill(...)
    else: self._update_exit_fill(...)
    self._save_trade(trade)
```

So when the trade is missing from `_open_trades` (a get_trade() that returned the DB copy — `get_trade()` falls back to `_get_db_trade` in trade_ledger.py:380-400 — was used by the close path), the exit leg is appended but `exit_quantity`/`average_exit_price`/`last_exit_fill_time`/`exit_order_id` remain NULL and, in the pre-fix code, `status` was left `OPEN`.

**Historical confirmation from backups** (sorted by time, all show `baa04bef` OPEN while other closed trades are CLOSED):
- `predeploy_fixplan_sep03_...` : baa04bef OPEN, 58bb8ea9 CLOSED
- `predeploy_bugc_...` : baa04bef OPEN
- `gold_fix_backup_20260903_205640` : baa04bef OPEN (with 4 closed trades properly CLOSED)
- `predeploy_backup_20260903_161031/162800` : all 4 (baa04bef, 9983dd92, 81aeeff0, 164b4078) OPEN

`created_at` of baa04bef = 1788441403 (Sep 2 11:43, live entry). So it predates the fix at analysis time.

**Note on this audit's data touch**: A prior (non-forensic) step in this session ran a `UPDATE trades_analytics SET status='CLOSED', net_pnl, gross_pnl, fees, exit_price, exit_reason, closed_at ...` for baa04bef, which is why the current analytics.db shows `CLOSED`. That fix did **not** backfill `exit_quantity/average_exit_price/exit_order_id/last_exit_fill_time` — those remain NULL. The original forensic baseline (OPEN, no P&L) is preserved in the backups above. No further modifications were made during this read-only audit.

---

## 12. 81aeeff0... Investigation

**trade_id**: `81aeeff0-fd12-4867-95c2-9103c7b7cd7e` (gold_01, LONG, GOLDM, qty 1, multiplier 10)

**trading.db**: `status='closed'`, entry=151244, exit=154335, gross=30910, charges=298.89, net=30611.11, exit_reason='short_reversal'.

**analytics.db**: `status='CLOSED'`, net=30611.11, gross=30910, fees=298.89, exit_price=154335, **exit_quantity=1, average_exit_price=154335, last_exit_fill_time set, exit_order_id='d976a209-...', duration & r_multiple computed**.

**trade_legs**: both entry leg (is_entry=1) and exit leg (is_entry=0) present.

**Conclusion**: `81aeeff0` is **genuinely consistent** — it went through the **normal close path** where the trade WAS in `_open_trades`, so `record_fill(exit)` → `_update_exit_fill` populated all exit accounting, removed it from the open cache, and `close_trade` (via DB fallback) stamped final P&L. This is the **contrast pair** proving the root cause: same batch closed at the same time (both `short_reversal` at ~03:40:22), but `81aeeff0` had its ledger row in the open cache and `baa04bef` did not.

---

## 13. Root Cause of Mismatch

### CONFIRMED (definitive, code-supported)
**`TradeLedger.record_fill()` exit-accounting is conditioned on the trade being present in the in-memory `_open_trades` cache.** When the trade is closed while absent from the cache (get_trade() falling back to the DB), the exit leg is written but `_update_exit_fill()` is skipped, so `trades_analytics` is left `OPEN` with null exit fields. This is exactly what the `baa04bef` row shows (structurally distinct from `81aeeff0`).

Supporting code: `analytics/trade_ledger.py:186-225` (`record_fill`), `260-309` (`_update_exit_fill`), `311-347` (`close_trade`). The fix `3ab8345 fix(ledger): purge CLOSED trades from open-trades cache on exit fill` and `51ce0fa ... record_fill idempotent on fill_id (BUG-3)` are direct acknowledgments that this cache-dependency was a real, addressed defect.

### LIKELY
**Two independent, non-transactional writers of the same logical trade status.** trading.db written by `TradeCloseManager.save_trade_and_fill` (independent connection) and analytics.db by `TradeLedger` (independent connection) — no cross-DB transaction, no outbox, no compiled event feed. A crash or a skip in either path leaves them divergent.

### POSSIBLE
- A replay/offline/test process (`*_e2e_test.py`, `full_simulator.py`, `seed_replay.py`) opening analytics.db with a **fresh TradeLedger** (empty `_open_trades`) and closing restored positions would reproduce exactly this symptom on the live DB. These scripts exist in the repo and write to the same DB paths.

### NOT FOUND
- No evidence of two different `trade_id` values for the same logical trade (identity is unified via `register_position`).
- No `CLOSED → OPEN` regression path.

---

## 14. Memory vs DB

Three overlapping copies exist:
1. **Memory**: `TradeLifecycleManager._trades` (TradeContext) + `position_manager` + `execution_engine._fills`. This is the **live working set** and the primary source for `/api/trades`, `/api/positions`, `/api/fills`, and WS `engine_state`.
2. **trading.db**: canonical operational/execution + final P&L record (recovery source).
3. **analytics.db**: derived rich projection (analytics/performance/equity).

Recovery loads trading.db (`lifecycle.restore_from_db`) and re-creates/backfills analytics rows for open positions (`_backfill_ledger_for_open_positions`). Memory is authoritative for live status; trading.db is authoritative for final/closed truth; analytics.db is a projection expected to conform.

---

## 15. API Source

| Endpoint | Source | Notes |
|---|---|---|
| `/api/trades` | lifecycle (memory) → trading.db | **never** reads analytics.db |
| `/api/trades/{id}` | lifecycle (memory) → trading.db | never reads analytics.db |
| `/api/positions` | position_manager (memory) | |
| `/api/orders` `/api/fills` | execution_engine (memory) | |
| `/api/overview` | account_engine (memory) | |
| `/api/pnl` `/api/strategies` | account_engine / pnl_engines (memory) | |
| `/api/equity-curve` | trading.db account_snapshots → account_engine | |
| `/api/analytics/strategies` | **analytics.db** (TradeLedger) | reads ONLY analytics.db |
| `/api/analytics/open-trades` | **analytics.db** (TradeLedger.open_trades) | reads ONLY analytics.db |

⇒ `/api/analytics/*` can disagree with `/api/trades` on the same trade's status because they read different stores.

---

## 16. WebSocket Source

- Two message types broadcast (server.py): `engine_state` (full `engine.snapshot()` every 0.5s, enriched in `_enrich_strategies` from `/api/strategies`/PNL memory) and `events` (EventBus).
- Source is **in-memory engine state only** — it does not read analytics.db. So WS reflects memory, matching `/api/trades`/`/api/positions` (also memory), NOT `/api/analytics/*` (analytics.db). A divergence in analytics.db would be invisible to WS but visible on the analytics pages. Same root cause.

---

## 17. Frontend Source

Frontend (`dashboard-ui`) consumes:
- REST `/api/*` (memory/trading.db backed) for trades/positions/orders/overview/pnl/equity.
- REST `/api/analytics/*` (analytics.db backed) for the Analytics page / strategy matrix / performance.
- WS `engine_state` + `events` for live overview/strategies/positions (suppresses polling when connected).

Because the frontend pulls trades from `/api/trades` (trading.db/memory) and analytics/performance from `/api/analytics/*` (analytics.db), it can display **two different statuses/P&L counts for the same trade** across pages (Tradebook vs Analytics/Strategy Matrix) whenever the two DBs diverge — which is precisely the observed `baa04bef` symptom. The frontend itself does not merge/reconstruct trade status, so it does not independently cause the mismatch; it faithfully surfaces the backend split.

---

## 18. Docker Database Architecture

- Container: `mcx-trader`, image `mcx-trader:new`, `python dashboard/run.py`, workdir `/app`.
- Bind mount: host `/home/jadhavdnyaneshwar701/mcx-trader-data/data/db` → `/app/data/db` (RW).
- Active DBs: `/app/data/db/trading.db` (270KB, WAL) and `/app/data/db/analytics.db` (151KB, WAL).
- No container-local copy of the DBs (single source: the bind mount). A host of `predeploy_*` / `restart_backup_*` subdirectories hold deployment backups — evidence of multiple prior bug-fix deployments (BUG-C, ledgerfix, fixplan).

---

## 19. Transactions and Crash Scenarios

- trading.db and analytics.db are **separate SQLite files/connections** in the same process but **no multi-DB transaction** and **no outbox/event-feed** linking them.
- Close flow order (`TradeCloseManager.close_position`): trading.db persisted FIRST (step 2-3), then analytics ledger update (step 6b), then event/telegram. These are sequential synchronous calls but **not atomic across DBs**.
- Crash windows that cause divergence:
  - after trading.db commit, before analytics.db write → trading.db `closed`, analytics.db `OPEN`.
  - during analytics write (or when the trade is missing from `_open_trades` so `_update_exit_fill` is skipped) → partial analytics row.
  - The `_backfill_ledger_for_open_positions()` heal runs only for **open** positions; it does not fix an analytics row left `OPEN` after a close.

---

## 20. Reconciliation

- `ReconciliationEngine` (`reconciliation/engine.py`) checks orders/fills/positions/trades/**P&L within trading.db and in-memory** via `self.persistence.db_path` (trading.db only). It does **not** read analytics.db.
- `TradeLifecycleManager.reconcile()`/`orphan_scan()` check lifecycle ↔ trading.db (orphan fills/orders, missing signals), not analytics.db.
- ⇒ **NO COMPLETE TWO-DATABASE TRADE RECONCILIATION MECHANISM FOUND.** Nothing cross-validates `trades_analytics` (analytics.db) against `trades` (trading.db) for status/P&L agreement.

---

## 21. Architectural Options

**OPTION A — trading.db canonical, analytics.db rebuilt projection (RECOMMENDED)**
Make analytics.db purely derivable: derive `trades_analytics`/`trade_legs` from trading.db `trades`/`fills` (or from the lifecycle/event feed) on demand, or make `record_fill` DB-backed (not `_open_trades`-dependent) so closure is written unconditionally.
- Correctness: high (single canonical writer → one source of truth; analytics can't silently diverge).
- Crash recovery: strong.
- Complexity: moderate (DB-backed record_fill + projection build).
- Compatibility: API/frontend unchanged (both already present).

**OPTION B — one database only**
Merge trades into a single store with both flat and analytics tables.
- Highest consistency, but large migration risk & replay/API churn; overkill.

**OPTION C — two independently canonical ledgers**
Treat both as authoritative and add a reconciler that flags/diffs them.
- Adds operational risk; still needs a fix-winner; no true single source of truth.

**OPTION D — keep two DBs, add a two-DB reconciliation + journal**
Add a cross-DB reconciliation (compare trades vs trades_analytics by trade_id; flag/heal status+P&L divergence) and make close writes durable/ordered.

---

## 22. RECOMMENDED SOLUTION

**Adopt OPTION A** (trading.db canonical, analytics.db derived/kept-in-lockstep), with an immediate hardening step:

1. **Make `TradeLedger.record_fill` DB-backed, not `_open_trades`-dependent.** In `_update_exit_fill`, when the trade is not in `_open_trades`, load from the DB and still apply the exit accounting + status=CLOSED + P&L, then write back. This closes the exact `baa04bef` hole.
2. **Make the close deduct the exit financials through one code path** so `close_trade` (P&L override) is always preceded by a durable `record_fill(exit)` that fully populates exit fields.
3. **Add a two-DB reconciliation** (DIFF `trades` in trading.db vs `trades_analytics` in analytics.db by `trade_id`: compare status, net_pnl, gross_pnl, fees, exit_price; report/heal). Wire it into the existing `/api/trades/lifecycle-reconcile` and startup.

This preserves the two-table split (flat vs rich) while making trading.db the single canonical operational truth and analytics.db a lockstep projection.

---

## 23. Required Changes

- `analytics/trade_ledger.py` `record_fill`/`_update_exit_fill`: DB-backed exit-accounting path (not dependent on `_open_trades`); ensure `close_trade` fully fills exit fields when called after a DB-only `record_fill`.
- `reconciliation/engine.py` (or a new module): add cross-DB trade reconciliation (trading.db ↔ analytics.db) on startup + on-demand.
- Optional: derive `trades_analytics` from trading.db `trades`/lifecycle snapshot instead of maintaining an independent open-cache write (long-term).

**Do NOT implement now** — this is the read-only forensic baseline.

---

## 24. Migration Risks

- Changing `record_fill` semantics could double-apply P&L if not guarded (the fill_id idempotency guard in `_update_exit_fill`/leg write must be preserved for crash-replay).
- A cross-DB reconciler must have a defined fix-winner (trading.db) and must not blindly rewrite analytics rows that are legitimately mid-open.
- Deploying while positions are open requires care (backfill of open positions + heal of stale OPEN rows).
- Backups already staged under `/app/data/db/predeploy_*` must be retained before any DB write.

---

## 25. Final Verdict

```
CURRENT ARCHITECTURE:
  Two separate SQLite stores in /app/data/db/:
    trading.db  = transactional/operational persistence (PersistenceManager)
    analytics.db = rich projection/analytics ledger (TradeLedger/EventStore/PerformanceEngine)
  In-memory TradeLifecycleManager + PositionManager operate on both.
  Close writes both DBs through separate, non-atomic code paths.

CURRENT SOURCE OF TRUTH:
  trading.db (canonical operational/execution + final P&L; recovery source)
  In-memory lifecycle is the live authoritative set for /api/trades, /api/positions, WS.

SECONDARY/DERIVED SOURCE:
  analytics.db (rich projection; expected to match trading.db on status & P&L).

NUMBER OF INDEPENDENT TRADE STATE WRITERS:
  2 to 3 (lifecycle._persist_trade → trading.db; TradeCloseManager.save_trade_and_fill → trading.db;
          TradeLedger.record_fill/close_trade → analytics.db).

TWO-DB CONSISTENCY:
  FAIL  (no atomic cross-DB write, no cross-DB reconciliation; baa04bef proved divergence).

baa04bef...:
  trading.db = closed (net 31425.03); analytics.db was OPEN with no P&L (structure persisted in
  backups), now CLOSED with P&L but exit accounting fields NULL (exit_quantity=0, avg_exit=NULL,
  exit_order_id=NULL) reflecting a DB-fallback close where _update_exit_fill was skipped.

ROOT CAUSE:
  TradeLedger.record_fill() applies exit accounting ONLY when the trade is in the in-memory
  _open_trades cache; when a close resolves the trade from the DB fallback, the exit leg is written
  but the trade row is not financially closed in analytics.db — leaving it OPEN while trading.db
  says closed. This is a cache-dependency + two-writer (non-transactional) design flaw.

RECOMMENDED FIX:
  Make analytics exit-close DB-backed (not _open_trades-dependent), fully populate exit fields in
  close_trade, and add a two-database (trading.db ↔ analytics.db) status/P&L reconciliation.

SAFE TO MODIFY DATABASE NOW:
  NO  — this phase is read-only. No further DB/code changes were made during this audit beyond the
        baa04bef status/P&L update performed by a prior step (disclosed in §11), which did not
        backfill the exit accounting fields.
```

**END OF REPORT**
