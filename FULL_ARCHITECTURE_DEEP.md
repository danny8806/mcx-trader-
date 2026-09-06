# MCX-TRADER — FULL ARCHITECTURE (DEEP) v1.0

Reference build: real-native historical replay verified 2026-09-05/06; regression suite 1226 passed.
This document is the canonical deep architecture. Every section cites `file:line`.

---

## 1. System Overview

MCX-TRADER is a **finite, deterministic, paper-execution algorithmic futures trader**
for two MCX commodity futures contracts (GOLDM, SILVERM), driven by a
**MTF DEMA–ATR crossover** model run by **four independent strategy instances**
on shared indicators.

### 1.1 The five layers

```
┌────────────────────────────────────────────────────────────────────────┐
│ 7. UI             dashboard-ui/  (React 19 SPA, Vite, Tailwind)        │
│ 6. API            dashboard/ + analytics/  (FastAPI, uvicorn :8000)    │
│ 5. ENGINE CORE    trading_engine.py  (signals → execution → fills)     │
│                   core/ (lifecycle, trade_close, market_status, risk)  │
│ 4. STRATEGY       strategies/  (4 StrategyInstance, DEMA-ATR/HTF gating)│
│ 3. INDICATORS     indicators/ + htf/  (6 shared DEMA/ATR streams)      │
│ 2. DATA           data/ (Dhan WS+REST adapter), core/candle_fetcher.py,│
│                    data/native_router.py + native_streams.py            │
│ 1. PERSISTENCE    persistence/ (SQLite trading.db, WAL, FK, triggers); │
│                    analytics/ (read-model ledger)                       │
└────────────────────────────────────────────────────────────────────────┘
        cross-cutting: events/ (bus), portfolio/ (accounting),
                       reconciliation/, monitoring/, notifications/
```

### 1.2 Core design principles (enforced, not aspirational)

| Principle | Where enforced |
|---|---|
| One process / single writer | `persistence/database.py:578-603` — one shared connection per db path + one re-entrant write lock; `BEGIN IMMEDIATE` transactions `:685-696` |
| Persistence-before-memory | `core/trade_close.py:1-15`; `execution/order_manager.py:104-106` |
| Zero lookahead in replay | `tools/dhan_historical_replay.py:57-113` (`cutoff_ts`) |
| No strategy-math modification | verification in `tools/parity_signal_harness.py` (0 forced-grid mismatches) |
| Identity / lineage rigidity | DB triggers `persistence/database.py:467-528`; `position_id != trade_id` trigger `:523-527` |
| Determinism (bit-exact) | `tools/replay_determinism_test.py` (VERIFIED) |
| Isolation of strategies | per-strategy `StrategyRuntime` + facades `strategies/runtime.py`; quarantine `trading_engine.py:967-998` |

### 1.3 The four deployed strategies

| SID | Instrument | Fast | Mid | HTF | Factory | Multiplier |
|---|---|---|---|---|---|---|
| gold_01 | GOLDM | 5m | 15m | 1h | `strategies/gold/__init__.py:5-17` | 10.0 |
| gold_02 | GOLDM | 15m | 15m | 1h | `:20-32` | 10.0 |
| silver_01 | SILVERM | 15m | 15m | 1h | `strategies/silver/__init__.py:20-32` | 5.0 |
| silver_02 | SILVERM | 5m | 15m | 1h | `:5-17` | 5.0 |

Indicator parameters (`config/settings.json:64-68` + `strategies/instance.py:26-30`):
`DEMA_PERIOD=3`, `ATR_PERIOD=6`, `ATR_FACTOR=1.0`.

Sessions: `09:00 – 23:30 IST` (`settings.json:35-37, 53-55`), 870 minutes.

Paper execution (`settings.json:107-111`): `slippage_ticks=1`, `latency_ms=100`,
`partial_fill_probability=0.0`.

---

## 2. Deployment Topology

```
VPS 200.234.44.93
└─ docker container "mcx-trader" (restart: unless-stopped)
   ├─ CMD: python dashboard/run.py  → uvicorn :8000 (Dockerfile:74, dashboard/run.py:28)
   ├─ env: mcx-trader.env (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TRADING_PIN,
   │                     TOTP_SECRET, DASHBOARD_API_KEY, DASHBOARD_HOST=0.0.0.0)
   ├─ volume: mcx_trader_data → /app/data/db  (trading.db survives rebuilds)
   └─ healthcheck: GET /api/health every 30s / 5s timeout / 3 retries / 20s start
        (docker-compose.yml:1-28; Dockerfile:71-72)
```

- **Two-stage Dockerfile**: Node 24-alpine builds the SPA (`Dockerfile:4-22`); Python 3.14-slim runs the app (`:27-74`).
- **`deploy_vps.py`**: SFTP sync → `docker build --no-cache` → stop/rm → `docker run` with `-p 8000:8000 -v …/data/db:/app/data/db` → verify health/WS (`deploy_vps.py:74-161`).
- **`deploy_restart.py`**: thin stop/remove/rerun + health poll (`deploy_restart.py:9-47`).
- **No systemd**; process supervision = Docker only.

---

## 3. Entry Points & Boot Sequence

### 3.1 Production entry: `dashboard/run.py`
`run.py:28` → `uvicorn.run(app, host=DASHBOARD_HOST, port=8000)`.

The engine is booted **inside the FastAPI lifespan** (`dashboard/server.py:181-287`):

| Step | Code |
|---|---|
| Build `TradingEngine(event_callback=_on_engine_event)` | `:188-189` |
| Analytics starting equity | `:196-202` |
| `PersistenceManager` | `:204-212` |
| `set_persistence` + `load_state` + `engine.restore(saved)` | `:216-226` |
| `engine.start()` | `:229-234` |
| Route-module `init(engine, event_bus, persistence)` | `:237-248` |
| 3 asyncio tasks: `_push_updates` (0.5s), `_push_events` (0.5s), `_periodic_save_state` (60s) | `:250-252` |
| Graceful shutdown: cancel tasks → `rest.stop_scheduler()` → `engine.stop()` → save → `persistence.close()` | `:255-287` |

### 3.2 Legacy headless: `main.py`
`main.py:32-88` — builds PersistenceManager + TradingEngine directly (`main.py:37-43`),
restores state (`:45-51`), SIGINT/SIGTERM handler saves on exit (`:53-68`), 60s periodic save
(`:74-84`). This entry is NOT the container entry; both boot the same core engine.

### 3.3 Boot state machine (`trading_engine.py`, `core/market_status.py:50-60`)

```
INITIALIZING → RESTORING → RECONCILING → WARMING_UP → READY → TRADING
```

- `start()` (`trading_engine.py:1010-1039`): `_running=True` → RECONCILING → wiring of
  TradeCloseManager → WARMING_UP → `_warmup_from_rest()` (`:1029`, implementation `:1049-1108`)
  → `candle_fetcher.start()` → `data_adapter.connect()` (starts Dhan WS) → READY.
- READY→TRADING only when READY **and** `MarketState.LIVE_TRADING` **and**
  `has_live_market_data` (`_maybe_enable_trading`, `:1245-1255`; called from ticks `:485`
  and candle handler `:400`).
- `stop()` (`:1041-1047`): `_running=False`, stop fetcher, disconnect adapter.

---

## 4. Configuration & Credentials

### 4.1 `config/settings.json` (canonical, 151 lines)
Full layout with `file:line`:
- `system` — `db_path=data/db/trading.db`, `state_path=data/db/system_state.json` (`:2-9`)
- `dhan` — client_id/access_token/ws_url/rest_base/token_file/pin/totp_secret with `${ENV}` placeholders (`:10-18`)
- `warmup` — `last_trading_days=5`, `fetch_calendar_days=14`, `keep_partial=true` (`:19-25`)
- `instruments` — GOLDM (`security_id 569003`, mult 10, margin slope 0.125 / intercept 126930) (`:26-44`); SILVERM (`security_id 483080`, mult 5, slope 0.0625 / intercept 142900) (`:45-63`)
- `indicators` — dema 3 / atr 6 / factor 1.0 (`:64-68`)
- `strategies` — the 4 (fast/mid/htf, qty 1, capital 300k, enabled) (`:69-106`)
- `paper_execution` (`:107-111`), `charges` (`:112-129`), `risk` (`:130-137`),
  `account` (total 1,200,000 / per-strategy 300,000, INR) (`:138-142`),
  `telegram` (`:143-147`), `dashboard.api_key` (`:148-150`).

### 4.2 Config loader — `config/__init__.py`
- `Config` singleton (`:12-90`); `load` resolves `${ENV}` recursively (`:52-62`);
  dot-path accessor `get("dhan.client_id")` (`:64-80`); `resolve_path` anchors relative
  paths to project root so `trading.db` resolves identically regardless of cwd (`:34-50`).

### 4.3 Credential chain
1. `TradingEngine._init_data_adapter` reads `dhan.client_id / token_file / pin / totp_secret`
   (`trading_engine.py:178-189`).
2. `DhanDataAdapter.__init__` immediately mints a fresh token via
   `DhanLogin.generate_token(pin, totp)` (`data/dhan/adapter.py:56`), loads it into
   `dhan_token.json` (`data/db/`), and hands it to both REST and WS clients.
3. `DhanRESTClient` auto-renews via scheduler (`rest_client.py:225-292`) and re-mints;
   `DhanWebSocketClient` re-mints on reconnect (`websocket_client.py:144-150`).

---

## 5. Market-Data Layer

### 5.1 Dhan native contract
- REST `POST /charts/intraday` → candle rows **`[timestamp, open, high, low, close, volume]`**
  where `timestamp` = IST bucket-open epoch (`data/dhan/rest_client.py:385-443`).
- Intervals are native Dhan strings: `"5"`, `"15"`, `"60"` (`core/candle_fetcher.py:220-221`).
- Adapter surface: `fetch_historical_candles(symbol, timeframe, from_date, to_date)` (`:203-226`).
- WS: binary LTP packets (RequestCode 17 subscribe), deduped by
  `(security_id, ltt, ltp)`, stale watchdog force-reconnect (<60s silence) + raises
  `on_status("stale_reconnect")` (`websocket_client.py:105-135, 210-240, 311-315`).

### 5.2 `Bar` dataclass — `core/timeframe_engine.py:19-48`
`instrument, timeframe, start_ts, end_ts, open, high, low, close, volume, state, source, tick_count`.
- `start_ts` = **bucket-open epoch**; `end_ts = start_ts + tf_minutes*60` (live convention, `core/candle_fetcher.py:272-299`).
- `BarState`: FORMING / CLOSED / PROCESSED (`:13-16`).
- Related: `BarAggregator` (tick→bar banking, non-primary path, `:60-166`).

### 5.3 `CandleFetcher` — `core/candle_fetcher.py:35-335`
- 30s polling daemon thread (`:79-90`); skips when `market_status.should_fetch_candles` false.
- **5m**: clock-aligned to session 09:00; emits the completed bucket at
  `candle_start = session_start + (completed_buckets-1)*5min`; dedup key
  `f"{name}:{timeframe}:{candle_start}"` (`:107-162`).
- **15m/1h native**: fetches `fetch_historical_candles(name, "15"/"60", now, now)`, filters
  `c[0] + tf*60 <= now`, picks most recent closed, dedups by native start (`:164-204`).
- `_create_bar` (`:272-299`); `_aggregate_candles` 5m→HTF fallback (`:301-335`).

### 5.4 Router & distributor — single candle choke point
`data/native_router.py:60-115`:
- Identity: `(security_id, timeframe, candle_end_ts)` (`:60-62`).
- Dedups live candles by identity; drops out-of-order; rejects incomplete candles.
`data/native_streams.py:31-58` — `NativeCandleDistributor.on_candle_closed` publishes
`candle:{inst}:{tf}` and `candle:{inst}:*` on the engine EventBus.

### 5.5 Event bus — `events/bus.py`
Synchronous in-thread pub/sub. Topics:
`candle:GOLDM:5m`, `candle:GOLDM:*`, `candle:*:*`, `tick:GOLDM` (`:20-27`).
Delivery: exact-topic subscribers then wildcard (`:48-74`). This guarantees
**deterministic ordering** (single-threaded dispatch — critical for replay).

### 5.6 IST session / market-state machine — `core/market_status.py`
- `MarketState`: OVERNIGHT → PRE_MARKET → MARKET_OPEN → LIVE_TRADING → MARKET_CLOSE → AFTER_MARKET (`:30-39`); recomputed on access (`:102-108`).
- `has_live_market_data`: WS connected OR REST fresh within 480s (`_rest_stale_threshold`, `:93`, `:127-138`).
- REST freshness proven by `mark_rest_data_fresh()` called from candle handler (`trading_engine.py:399`).
- Safe-mode overrides + `halt` (`:220-236`); state overrides explicitly **not persisted** (`:248-252`).

---

## 6. Indicator Engine — 6 Shared Streams

### 6.1 Indicator primitives
- **DEMA** `indicators/dema.py:9-125` — `2*EMA1 - EMA2`, period 3, `min_periods=1` seeding.
- **ATR** `indicators/atr.py:9-159` — Wilder smoothing, `alpha=1/period`, first = simple average of first N TRs.
- **DEMAATR** `indicators/dema_atr.py:12-206` — `upper = dema + atr*factor`, `lower = dema - atr*factor`,
  **recursive output clamp** to the band; output = DEMA while ATR initializing.

### 6.2 `SharedNativeIndicatorEngine` — `indicators/shared.py:399-458`
- Keyed streams `{(security_id, timeframe): IndicatorStream}` (`:416`).
- `get_or_create` is the **only** stream factory (`:418-432`).
- `IndicatorStream.feed` (`:115-193`) dedups: re-feeding an **already-accepted `end_ts`** is a no-op
  (returns the latest `IndicatorSnapshot`). Precise because warmup calls
  `warmup_htf` AND `warmup_indicator_htf` for the same bar on the same stream.
- `get_mapped_value(fast_bar)` (`:197-221`): `idx = bisect_right(_end_times, fast_bar.end_ts) - 1`
  → `HTFMappedValue(htf_value, prev_htf_value, htf_confirmed, source_ts)` (`htf/confirmation.py:8-14`).

### 6.3 Why exactly 6 streams
Across the 4 strategies the unique `(security_id, timeframe)` keys are the union:
GOLDM×{5m,15m,1h} + SILVERM×{5m,15m,1h} = **6 streams**.
For a 15m-fast strategy, fast and mid bind to the SAME 15m stream
(`strategies/instance.py:146-147`); the stream is advanced once per candle (dedup).

### 6.4 Binding — `StrategyInstance.bind_shared_indicators` (`instance.py:135-168`)
Replaces own DEMAATR/HTFState with thin views:
- `fast_indicator = StrategyIndicatorView(fast)`
- `mid_indicator / slow_indicator = StrategyIndicatorView(mid/slow)`
- `mid_htf_state / slow_htf_state = StreamHTFStateView(mid/slow)`

Views at `indicators/shared.py:276-392` expose the same method surface as the
pre-sharing per-strategy objects (`HTFState`, `strategies/htf_state.py:18-120`),
so strategy code is unchanged.

### 6.5 HTF mapping (backtest-exact) — `htf/backtest_style_htf.py`
`BacktestStyleHTFEngine` mirrors the backtest: `bisect_right(end_times, target_close) - 1`
(`:84-131`) == backtest `np.searchsorted(src_avail, target_close, "right") - 1`.
Also retained for parity tools: `_HTFInstrumentState` (`:210-218`), `load_batch_htf` (`:141-159`).

---

## 7. Strategy Layer

### 7.1 Types — `strategies/types.py`
- `SignalType`: LONG / SHORT / FLAT / REVERSAL (`:14-19`).
- `StrategyState`: FLAT…LONG_POSITION/SHORT_POSITION/EXIT_ORDER_SUBMITTED (`:22-34`).
- `Signal` dataclass: `signal_type, instrument, strategy_id, timestamp, trigger_price,
  stop_price, quantity, side, metadata, signal_id(uuid4)` (`:47-64`).
- `PendingEntry`: `signal, trigger_price, side, status, created_at, bars_pending,
  immediate=False` (`:67-76`).

### 7.2 Production strategy class — `strategies/instance.py` (`StrategyInstance`)
Per-fast-bar evaluation `on_bar(bar, htf_mapped, fast_dema_atr, mid_mapped)` (`:256-345`):

1. `just_entered = False` (`:271`).
2. **Step 1 — pending entry** (`:297-313`): timeout→FLAT; else `_check_pending_entry` →
   breakout (`bar.high > trigger` LONG / `bar.low < trigger` SHORT, `:507-535`) → fill at
   **trigger price**, arm stop, `just_entered=True`; if stop already broken on the same bar,
   set `same_bar_stop=bar.close` + reason `stop_loss_hit` (`:308-312`).
3. **Step 2 — stop loss** (`:315-328`): `_check_stop_loss` exits at **bar close** (`:537-569`),
   re-arms `_detect_signal` on the same bar.
4. **Step 3 — detect/reversal** (`:330-342`): FLAT/PENDING → `_detect_signal`; holding SHORT &
   long-cross → `_create_reversal_signal("LONG")`; holding LONG & short-cross → reversal SHORT.

### 7.3 Crossover & confirmation
- **LONG**: `close > htf_val and prev_close <= htf_val` **and** `mid_val < htf_val` (strict) (`:351-377`).
- **SHORT**: `close < htf_val and prev_close >= htf_val` **and** `mid_val > htf_val`.

### 7.4 Pending signal metadata — `_create_pending_signal` (`:397-441`)
- LONG: `trigger=high`, `stop=min(low, prev_low)`; SHORT: `trigger=low`, `stop=max(high, prev_high)` (`:402-410`).
- Metadata keys: `pending=True`, `triggered=False`, `entry_price=close`, `htf_value`,
  `mid_value`, `fast_dema_atr`, `trigger_level` (`:421-432`). **A pending signal is durable
  evidence, not a trade** — no trade id is minted until the breakout fills.
- On fill: flips `pending→False`, `triggered→True` (`:530-533`).

### 7.5 Reversal — `_create_reversal_signal` (`:443-501`)
- Builds the opposite-side pending, arms `pending_exit_at_open=True`,
  `pending_exit_reason=f"{side.lower()}_reversal"` (`:493-495`), `pending_exit_bar_start` set,
  `_close_position` the held side (`:499`). Returns None — the exit is consumed at next fast
  bar open by the engine, not immediately.

### 7.6 Tick handling — `on_tick` (`:606-640`)
Guards: not enabled / `just_entered` / `ltp<=0` / **`pending_exit_at_open`** (`:611-621`).
- Tick SL: `ltp <= stop` LONG / `ltp >= stop` SHORT → `_tick_stop_loss` (`:624-630`).
- Tick pending trigger: `ltp >= trigger` LONG / `<=` SHORT → `_tick_entry_trigger` (`:633-638`).

### 7.7 Runtimes & registry — `strategies/runtime.py`
- `StrategyRuntime` (`:23-43`): fully-isolated per-strategy container — strategy, lifecycle,
  order_manager, position_manager, trade_close_manager, current_trade_id, in-memory
  orders/fills/positions projections.
- `StrategyRuntimeRegistry` (`:46-93`): map strategy_id→runtime with duplicate guard.

### 7.8 Reference class — `strategies/base_dema_strategy.py`
Authoritative reference logic (743 lines) from which `StrategyInstance` derived; used by
reference/legacy parity. Same crossover math; metadata keys differ
(`signal_htf_dema_atr` etc. `:365-367` vs production unprefixed keys).

---

## 8. Trading Engine Core

### 8.1 Candle handler — `_make_candle_handler` (`trading_engine.py:393-423`)
- Guard `_running`; lock; `health.record_bar()`; `mark_rest_data_fresh()`; `_maybe_enable_trading()`.
- `is_fast = event.timeframe == strategy.fast_timeframe` (`:414`).
- Only on fast: `_process_deferred_exit(strategy, bar)` (`:416`) BEFORE strategy evaluation.
- `strategy.on_candle(event)` (`:417`); if a signal AND fast → `_process_signal(signal)` (`:419`)
  then `_consume_same_bar_stop(bar)` → `_process_signal` second (`:420-422`)
  (same-bar stop booked as a SECOND signal after the entry fill — matches backtest).

### 8.2 Tick handler — `_make_tick_handler` (`:425-436`)
Guard `_running` + `tick_signal_processing` + instrument match; early-out unless
`pending_entry` or `position_side`. `strategy.on_tick(event.ltp, event.timestamp)` →
`_process_signal` if a signal.

### 8.3 `engine._on_tick` (`:438-499`)
- Normalizes dict/object ticks; validates `0 < ltp finite` (`:460-462`).
- Market-data bookkeeping ALWAYS runs: `market_status.update_data_status`,
  `health` updates, and **stale-WS → safe-mode `market_data_uncertain`** (`:464-482`).
- With the lock: `execution_engine.update_price`, mark open positions (`:488-492`).
- Publishes `tick:{instrument}` TickEvent (`:494-499`) → per-strategy handlers.

### 8.4 Deferred exit — `_process_deferred_exit` (`:545-580`)
- No-op unless `pending_exit_at_open` and position (`:546-551`).
- `exit_price = fill_price or ltp or bar.open` (`:553`).
- Builds exit Signal at `timestamp = bar.start_ts + 0.5` (`:565`, "consumed at the open").
- Metadata `{exit:True, exit_reason, deferred_exit:True, source:"next_open", fill_price}` (`:570-573`).
- **SIG-X lineage invariant**: if an opposite pending_entry exists, the exit signal
  REUSES the pending entry's `signal_id` — one id for reversal-exit + opposite re-entry (`:574-577`).
- `strategy._close_position(reason)`; `_process_signal(exit_signal)`.

### 8.5 Signal processing — `_process_signal` (`:582-717`)
- `is_exit = metadata.get("exit")`; `is_pending = metadata.get("pending") and not triggered` (`:593-595`).
- Resolves the signal's OWN Runtime/lifecycle/order/position managers (`:596-611`).
- **Reversal synthesis** when a bare opposite-side signal arrives while holding — rewrites it
  into a reversal exit reusing the same `signal_id` (`:617-644`).
- Persists signal (`:646`); publishes `signal_created`.
- `if is_pending: return` — pending breakouts persist only the immutable signal (`:653-654`).
- **Entry gate** (`:658-677`): safe-mode / trading-allowed check; margin calc; `risk_engine.check_order`.
- Trade resolution: exit → find open position+trade; entry → `create_trade_from_signal` (`:688-693`).
- `order_manager.submit_signal(signal, multiplier, trade_id=trade.trade_id)` (`:697`);
  persist order (`:703`) **before** draining fills; `broker_router.route_fill(..., entry_signal_id=signal.signal_id, is_exit=...)` (`:707-717`).

### 8.6 Fill application — `_handle_fill` (`:743-879`)
- Dedup via `fill_dedup.is_duplicate/note_processed` + invalid-price guard (`:745-750`).
- **Entry fill**: resolve/validate trade (mismatch → quarantine), block margin (per-strategy +
  global `:786-796`), `position_manager.open_position(fill, multiplier, margin, stop_price=strategy.stop_price, entry_signal_id, trade_id)` (`:797-801`),
  persist fill+position, lifecycle `register_entry_fill`/`register_position`, TradeLedger
  projection lock-step (`:809-837`).
- **Exit fill**: requires an open position (`:842-848`).
  - **SL semantics** (`:851-858`): if `last_exit_reason in ("stop_loss_hit","stop_loss")` →
    canonical `exit_reason="STOP_LOSS"` and **`exit_signal_id=""`** (SL closes carry no fabricated signal).
  - `_trade_close_manager.close_position(...)` (atomic) (`:859-863`); lifecycle `close_trade`.
  - **Reversal keeps the opposite pending breakout** (`:875-878`): `_reset_strategy_state(keep_pending=pending_armed)`.

### 8.7 State reset — `_reset_strategy_state` (`:908-924`)
`keep_pending=True` → PENDING_* + keep pending_entry; else FLAT + clear. Always clears
`position_side`, `stop_price`, `current_trade_id`.

### 8.8 Same-bar stop — `_consume_same_bar_stop` (`instance.py:576-600`)
Exit signal at `same_bar_stop` (= entry bar close), `source="same_bar_stop"`,
timestamp `bar.start_ts + 0.25`.

---

## 9. Execution Layer

### 9.1 Order lifecycle
`OrderManager.submit_signal` (`execution/order_manager.py:33-51`) requires `trade_id`
(raises ValueError). `PaperExecutionEngine.create_order` (`paper_broker.py:118-150`):
resolves reversal side, `order_id=uuid4`, carries `entry_signal_id` + `trade_id`,
registers the broker mapping. `submit_order` `:152-171`: CREATED→SUBMITTED→(_execute)→FILLED.

`drain_fills` (`order_manager.py:101-111`): contract — caller persists the order row
BEFORE dispatching fills (protects the `fills.order_id` FK).

### 9.2 Fill model (`paper_broker.py:173-215`)
`_execute_order`: `time.sleep(latency_ms/1000)`; slippage `= ticks*1.0` applied
(BUY current+slip / SELL current−slip); fill capped at `_max_fills=500` (`:105, 207-208`).
`partial_fill_probability != 0` → **ValueError** ("Partial fills are not supported by the position ledger", `:94-98`).

### 9.3 Broker router (`execution/broker_router.py`)
`BrokerOrderMapping` (`:27-36`); single choke point (`trading_engine.py:289-294`);
unmappable events quarantined (`trading_engine.py:967-998`, `_MAX_QUARANTINE=500`).

---

## 10. Portfolio & Accounting

### 10.1 Position model — `portfolio/position_manager.py`
`Position` (`:24-51`): separate `position_id` AND `trade_id`, `entry_signal_id`,
`exit_signal_id`, `entry_fill_ids`, `exit_fills`, realized/unrealized PnL, margin,
multiplier, stop_price, current_mark.
- `open_position(..., entry_signal_id, trade_id)` — requires `trade_id`; `position_id=uuid4` (`:125-156`).
- `close_position` — appends exit fill, computes realized PnL, moves to closed (capped 500) (`:158-195`).
- `restore` — clears first, keys by `position_id` UUID, restores open+closed linkage (`:254-345`).

### 10.2 PnL — `portfolio/pnl.py`
`calculate_realized_pnl(entry_fill, exit_fill, multiplier)` is PURE: gross = `(exit−entry)*qty*mult`
(LONG) / inverse (SHORT); charges via `fee_model.calculate` (`:52-78`). `PNLEngine` tracks
realized + win/loss; `get_snapshot → PnLSnapshot` (`:116-127`).

### 10.3 Account & margin — `portfolio/account.py`
`equity = starting_capital + realized + unrealized` (`:56-59`);
`margin_required = price*qty*mult*margin_pct/100` (`:76-78`); `block_margin` / `release_margin`.
`restore` never restores starting_capital (from config) (`:142-153`).
Engine: per-strategy `AccountEngine(capital=300k, margin_pct=6.5)` + one global
`AccountEngine(capital=1,200,000)` (`trading_engine.py:296-319`).

### 10.4 Charges — `execution/fee_model.py`
`calculate()` (`:49-91`): brokerage `×2`, STT on sell turnover, exchange + SEBI both sides,
stamp on buy, GST 18% on (brokerage+exchange+sebi). GOLDM/SILVERM blocks currently
identical (`settings.json:112-129`).

### 10.5 Atomic close — `core/trade_close.py`
Ordering contract (`:1-15`): (1) pure PnL → (2) **persist trade FIRST** → (3) persist fill in
one tx (`save_trade_and_fill`, `:155-156`) → (4) close in memory → (5) account release →
(6) risk → (6b) ledger heal → (7) events → (8) Telegram (`:340-358`).
Guards invalid (`≤0/NaN/inf`) exit prices (`:67-79`); requires position.trade_id (`:112-123`);
TradeLedger projection heal never invents trade ids (`:207-282`).

---

## 11. Persistence — trading.db

### 11.1 Schema — `persistence/database.py`
- `SCHEMA_VERSION = 2` (`:30`). Canonical tables (`:34-49`): signals, trades,
  trade_signal_link, pending_orders, orders, fills, positions, trade_events,
  processed_fills, account_snapshots, events, quarantine_records, broker_order_mapping,
  system_metadata.
- DDL `:69-299`: `trades.entry_signal_id NOT NULL` (`:103-130`); `positions` has
  **`position_id` as separate PK** + `trade_id NOT NULL REFERENCES trades` (`:202-217`);
  `trade_events` append-only with unique `idempotency_key` (`:221-234`);
  `broker_order_mapping` (`:281-289`).
- Derived read-model (`:303-439`): trades_analytics, trade_legs (unique fill_id),
  trade_snapshots, strategy_daily/monthly_performance, strategy_parameter_results,
  strategy_performance_snapshots.

### 11.2 Integrity triggers (`:467-528`) — 10 total
`trg_trades_entry_signal_required`, `trg_trades_entry_signal_exists`,
`trg_orders_trade_required/_exists`, `trg_fills_lineage_required/_trade_exists/_order_exists`,
`trg_trade_signal_link_*`, and **`trg_positions_identity_separate`** which ABORTs when
`NEW.position_id = NEW.trade_id` (`:523-527`).

### 11.3 Connection model (`:578-603`)
One shared connection per db path + re-entrant write lock; WAL, `synchronous=NORMAL`,
`busy_timeout=30000`, **`PRAGMA foreign_keys=ON` on every connection**.
Real `BEGIN IMMEDIATE` transactions (`:685-696`); all queries under the shared lock.

### 11.4 Idempotent durability APIs — `persistence/manager.py`
`save_trade` UPSERT (`:241-279`), `save_order` UPSERT (`:281-311`), `save_fill`
`ON CONFLICT DO NOTHING` (`:313-333`), `save_signal` `INSERT OR IGNORE` (`:335-356`),
`save_broker_order_mapping` UPSERT (`:377-394`), `save_trade_and_fill` one tx (`:431-469`),
`save_position` (`:502-537`), `close_position_record` (`:539-565`).

### 11.5 Integrity checks
`persistence/database.py:756-763` — `integrity_check`, `foreign_key_check`,
`foreign_keys_enabled`. `recovery.py:25-49` `TradeRecoveryManager.verify()` + guarded
`restore_lifecycle` (`:51-61`). Startup canonical restore path:
`set_persistence` → `_build_runtimes` → per-lifecycle `restore_from_db` (`trading_engine.py:946, 360-361`).

---

## 12. Analytics Layer

### 12.1 Read-model ledger — `analytics/trade_ledger.py`
- `create_trade` REQUIRES the canonical trade_id — never invents identity (`:146-150`).
- `record_fill` idempotent on fill_id, weighted-average entry/exit, auto-close computes
  PnL + duration + r-multiple (`:182-311`); `close_trade` stamps authoritative fee-model PnL (`:313-349`);
  `update_mfe_mae` writes only on change (`:351-380`); UPSERT via shared transaction (`:470-497`).

### 12.2 Event store — `analytics/event_store.py`
19 `EVENT_TYPES` (`:16-36`); `record` atomic, idempotent by event_id, per-trade
`sequence_no = MAX+1` (`:43-84`).

### 12.3 Performance — `analytics/performance.py`
IST bucketing `_ist_format` (`:17-25`); performance: P&L, PF, expectancy, drawdown,
sharpe/sortino/calmar, MFE/MAE (`:190-290`); equity/drawdown curves (`:292-332`);
rolling/daily/monthly/time-of-day/day-of-week (`:334-531`); Monte Carlo (`:533-586`);
correlation/portfolio contribution (`:588-642`).

### 12.4 API — `analytics/routes.py` (21 endpoints `:86-631`)
strategies, equity, drawdown, daily/monthly, time-of-day, mae-mfe, rolling, execution,
parameters, correlation, portfolio, trade forensics, open-trades, events,
**reconciliation** (`:589-624`), status. Strategy resolution order (`:24-48`).

> Both analytics tables and canonical tables live in **the same trading.db**;
> `data/db/analytics.db` is a legacy leftover.

---

## 13. Reconciliation

### 13.1 Legacy engine — `reconciliation/engine.py`
`reconcile(phase)` (`:93-148`) runs **10 checks**: orders-vs-fills, fills-vs-positions,
positions-vs-trades, **trades-vs-PnL** (tolerance 1e-6), accounts-vs-margin,
duplicate fills/orders, DB-vs-memory orders, DB-vs-memory fills, price sanity
(rejects `<=0/NaN/inf` — guards the `-1` sentinel) (`:212-533`).

### 13.2 Lifecycle reconciliation
`engine.reconcile_trades()` aggregates per-strategy `lifecycle.reconcile()` (`trading_engine.py:1171-1182`);
`orphan_scan()` (`:1184-1204`); `_reconcile_strategy_positions` heals FLAT-from-crash desync (`:1217-1243`).

### 13.3 API surface
`GET /api/reconciliation` composes legacy engine + orphan_scan + lifecycle identity
(`dashboard/routes/reconciliation.py:17-110`). Safe-mode reasons:
`position_mismatch`, `reconciliation_failed` (`core/safe_mode.py:29,36`).

---

## 14. Monitoring, Risk, Safe-Mode

### 14.1 `monitoring/health.py`
`HealthMonitor` (`:38-124`): component heartbeats, tick/bar/signal/fill/error counters,
worst-wins overall status; `/api/health` (`dashboard/server.py:420-429`) and
`/api/health/system` (component table, `dashboard/routes/health.py:18-70`).

### 14.2 `core/risk_engine.py`
`RiskEngine.check_order` (`:9-150`): kill switch, per-strategy/total position caps,
margin, daily loss, drawdown. Exits bypass; entries gated (`trading_engine.py:658-677`).

### 14.3 `core/safe_mode.py`
Reasons catalogue (`:28-37`); `enter_safe_mode` (`:46`), `exit_safe_mode` with cooldown (`:81`),
`should_allow_trading` (`:106`). Triggered by stale WS (`trading_engine.py:476-480`)
and reconciliation failures.

---

## 15. Notifications (Telegram)

- `notifications/telegram_client.py`: queue worker (max 500), comma-separated chat ids, `send_sync`.
- `notifications/telegram_router.py:45` `on_trade_close` — the ONE hooked call site,
  executed inside `core/trade_close.py:340-358` (atomic close step 8).
- Formatters in IST (`telegram_formatter.py:22-181`; `:8`).
- `on_fill/on_signal/on_risk_alert/on_error` exist but are not wired into the runtime.

---

## 16. API Layer (Dashboard)

### 16.1 FastAPI app — `dashboard/server.py`
- `app = FastAPI(title="GoldSilver Trading Dashboard")`, lifespan-managed engine (`:290-294`).
- CORS allows `localhost:5173/5174` (`:296-307`).
- **Auth gate**: when `DASHBOARD_API_KEY` set, `x-api-key` required on `/api/*` except
  `/api/health` (constant-time compare) (`:326-342`); `/ws` requires `?key=` (`:349-353`).
- Static SPA: `/assets` mount + SPA catch-all (`:433-442`); frontend dist at
  `dashboard-ui/dist` (`:313-314`).

### 16.2 Route modules (registered `:317-323`)
overview, strategies (incl. `POST /strategies/{id}/control`), positions, orders, trades
(incl. `orphan-scan`, `lifecycle-reconcile`), pnl, equity-curve, market-data
(LTP from `execution_engine._current_prices`), risk, health, replay, reconciliation,
alerts, settings, audit_log, indicators/htf.

### 16.3 Live push — WebSocket `/ws`
`ConnectionManager` (`dashboard/ws_manager.py:15-98`); `_push_updates` broadcasts
`engine_state` every 0.5s (`server.py:137-153`); `_push_events` broadcasts `events` (`:156-173`);
WS actions: `subscribe` / `ping` / `command` (pause/resume/emergency_stop/get_snapshot/get_trades) (`:362-417`).

---

## 17. Frontend — `dashboard-ui/`

- **Stack**: React 19 + TypeScript + Vite 8 + Tailwind 4 + recharts + lightweight-charts (`package.json:12-33`).
- **DataProvider** (`src/store/DataProvider.tsx`): opens ONE WS to `/ws` (`:320-321`),
  subscribes `["all"]`, reconnects on 3s close (`:327-331`); while WS live it **pauses**
  REST polling of overview/strategies/positions (`:138, 167, 175`) but keeps other slices
  polling (2–10s intervals) (`:294-315`).
- 16 routed pages (`App.tsx:21-47`): Overview, LiveTrading, Strategies, StrategyMatrix,
  Positions, Orders, Trades, Pnl, Risk, MarketData, Indicators, Reconciliation, Alerts,
  Health, Settings, AuditLog.
- REST client `src/lib/api.ts` maps fn→endpoint (`:19-73`). UI contract requires
  `strategy_id/trade_id/position_id/entry_signal_id` (test `test_frontend_contracts.py`).
- Re-render isolation via `useSyncExternalStore` slice equality (`DataProvider.tsx:62-83`).

---

## 18. Warmup / Restart / Recovery

### 18.1 `_warmup_from_rest` — `trading_engine.py:1049-1108`
1. Per strategy: fetch fast candles `fetch_historical_candles(inst, fast_id, base_from, to_date)`.
2. IST **calendar-day filtering only** via pandas (host-tz drift safe) (`:1069-1075`).
3. Keep last `last_trading_days=5` distinct IST dates (`:1078-1081`).
4. Fast bars fed via `strategy.warmup_indicator(Bar(start_ts=open_ts, end_ts=open_ts+fast_min*60, ...))` (`:1084-1092`).
5. For `("15", mid)` and `("60", htf)`: feed `warmup_htf(bar)` THEN `warmup_indicator_htf(bar)`
   — both route to the same shared stream; the **stream dedup makes the second feed a no-op** (`:1094-1106`).
6. `bar_count` recorded per stream (`:1107-1108`).

### 18.2 Restore
`engine.restore(saved)` (`:1110-1145`): per-strategy `restore`, `PositionManagerFacade.restore`,
**margin reconstitution** from restored open positions (`:1133-1142`), runtime `current_trade_id`.
`restore_from_db` rebuilds identity maps from DB trades (`core/lifecycle.py:862-910`) under the
`TradeRecoveryManager.verify()` guard (`recovery.py:51-61`).

### 18.3 Replay checkpoint determinism
`tools/dhan_historical_replay.py`: `_FrozenDT` freezes `trading_engine.datetime` for warmup;
`HistoricalDhanReplaySource` serves only bars with `end_ts <= cutoff_ts` (= replay start)
→ **zero lookahead**; two isolated builds compare bit-exact
(`tools/replay_determinism_test.py`, VERIFIED). Warmup parity fixed
(`tool` outputs in `replay_output/historical_replay/determinism/`).

---

## 19. Backtest & Replay Tooling

### 19.1 Reference simulator — `full_simulator.py`
Real `TradingEngine` + real strategies + session-anchored aggregation:
`build_bars` (`:165-235`) — 5m 1:1 via `_create_bar`; 15m/1h windows anchored to
**09:00 IST** per day, emitted only when complete (`expected = tf_min//5`),
else dropped unless `keep_partial` (`:213`). `_TF_RANK` ordering `{1h:0, 15m:1, 5m:2}` (`:72-74`).
Verification: real `ReconciliationEngine` + independent gross/fees/net within ±1.0 (`:348-519`).

### 19.2 Tools inventory (`tools/`)
| Tool | Purpose |
|---|---|
| `replay_live_architecture.py` | foundational 4-strategy replay scaffolding (`write_config/build_engine/run_replay/collect/dump/db_forensics/install_crossover_loggers`) |
| `dhan_historical_download.py` | REST `/charts/intraday` snapshot downloader → `replay_output/dhan_snapshot/` + SHA-256 |
| `dhan_historical_replay.py` | deterministic real-data replay driver (adapter, warmup, chronological) |
| `replay_determinism_test.py` | two-isolated-run bit-exact proof |
| `replay_backtest_parity.py` | native vs session-anchored-aggregated HTF parity |
| `replay_warmup_parity.py` | router path vs warmup path stream parity |
| `replay_restart.py` | crash-restart continuation forensic |
| `parity_signal_harness.py` | live-vs-reference per-bar signal parity (0 mismatches) |
| `replay_mcx_from_2026_09_02.py` | independent oracle comparison |

### 19.3 Evidence hierarchy — `replay_output/`
`replay_manifest.json` (native intervals, `resampling_used:false`, `lookahead_used:false`),
`checksums.json`, `dhan_snapshot/`, `historical_replay/` (single_run, determinism,
backtest_parity, warmup_checkpoint, 14 forensic `.md` reports incl.
`FINAL_HISTORICAL_REPLAY_VERIFICATION.md` = VERIFIED), `live_replay/` (parallel,
sequential, restart with verdict, forensics), `replay_2026-09-02_to_latest/`.

---

## 20. Test Suite

- `pytest.ini`: `testpaths = tests` (`:1-2`).
- **1226 collected when run** (1196 `def test`, plus unittest regressions):
  - `tests/fresh_audit/` — 869 (deep audit incl. `test_comprehensive.py` 146, `test_whole_project.py` 112, `test_full_pipeline_audit.py` 104)
  - `tests/live_runtime_v2/` — 164 (phase-gated 1→30, conftest emits SYSTEM_VERIFIED verdicts)
  - `tests/new_architecture/` — 76 (identity/lineage, API lineage, frontend contracts, isolation)
  - `tests/adversarial_trade_lifecycle/` — 81 (trade-identity divergence, signal-id immutability)
  - `tests/test_regressions.py` — 6 (unittest)
- Identity/lineage enforcement tests: `test_database_constraints.py` (schema), `test_four_strategy_parallel.py`,
  `test_execution_isolation.py`, `test_strategy_runtime_isolation.py`, `test_api_lineage.py`,
  `test_frontend_contracts.py`, `test_sequential_vs_parallel.py`, `test_websocket_lineage.py`.
- Last full run: **1226 passed, 44 skipped** (2026-09-06).

---

## 21. Identity & Lineage Rules (mandated)

| Rule | Enforcement |
|---|---|
| `signal_id` fresh uuid4 per Signal | `strategies/types.py:64` |
| `trade_id` minted ONLY in `create_trade_from_signal` | `core/lifecycle.py:399-491` |
| `order_id/fill_id/position_id` uuid4 at creation | `paper_broker.py:135,193`; `position_manager.py:139` |
| `position_id != trade_id` | DB trigger `persistence/database.py:523-527`; `test_database_constraints.py:82-84` |
| `entry_signal_id` compulsory + refers to existing signal | `persistence/database.py:467-479` |
| entry_signal_id immutable across lifecycle | `tests/adversarial_trade_lifecycle/test_signal_id_immutability.py` |
| STOP_LOSS exits carry NO signal (`exit_signal_id=""`) | `trading_engine.py:851-858` |
| Reversal exit reuses opposite pending signal_id (SIG-X) | `trading_engine.py:574-577`; `core/lifecycle.py:673-728` |
| fill→order→trade→strategy consistency | `db_forensics` cross-strategy checks `tools/replay_live_architecture.py:358-375` |

---

## 22. End-to-end Data-Flow Trace (one 5m bar)

```
CandleFetcher (30s)                        core/candle_fetcher.py:79-90
  → DhanREST fetch_intraday "5"             rest_client.py:424-443
  → _create_bar (start_ts=bucket-open)      core/candle_fetcher.py:272-299
  → NativeCandleRouter.on_candle_closed     data/native_router.py:117  (dedup by (sid,tf,end_ts))
  → NativeCandleDistributor                 data/native_streams.py:31-58
  → EventBus.publish("candle:GOLDM:5m")     events/bus.py:48
  → engine._make_candle_handler             trading_engine.py:393
      ├─ record_bar / mark_rest_data_fresh / maybe_enable  :398-400
      ├─ (fast) _process_deferred_exit                      :416  → reversal exit at open
      ├─ strategy.on_candle → _on_fast_candle               instance.py:174,189
      │    ├─ fast_indicator.update (5m shared stream)      :207
      │    ├─ slow/mid_htf_state.get_mapped_value (bisect)  :216-217
      │    └─ on_bar (Pending→SL→Detect/Reversal)           instance.py:256
      └─ if signal & fast:_process_signal THEN              trading_engine.py:419
           same-bar stop (_consume_same_bar_stop)→signal    :420-422
Ticks: Dhan WS → _on_tick → EventBus tick:GOLDM            trading_engine.py:438-499
  → strategy.on_tick (tick SL / tick trigger)               instance.py:606-640
Signal → _process_signal                                    trading_engine.py:582
  → lifecycle.create_trade_from_signal                      core/lifecycle.py:399
  → order_manager.submit_signal (trade_id)                  order_manager.py:33
  → PaperExecutionEngine → fill                             paper_broker.py:118-215
  → broker_router.route_fill → _handle_fill                 trading_engine.py:707,743
  → position_manager.open_position (stop from strategy)     :797-801
  → TradeCloseManager.close_position (atomic + Telegram)    core/trade_close.py:53,340
  → Persistence (WAL tx) + TradeLedger projection           persistence/manager.py; trade_ledger.py
  → WS broadcast engine_state → SPA                          dashboard/server.py:137-153
```

---

## 23. Verification Status (as of this document)

| Dimension | Verdict | Evidence |
|---|---|---|
| Real native data source | VERIFIED | `replay_output/dhan_snapshot/`, manifest, checksums |
| Full historical replay | VERIFIED | 40 signals / 23 trades / 44 orders+fills / +20,921.06 |
| Determinism (2 runs) | VERIFIED | `replay_output/historical_replay/determinism/` |
| Native-vs-aggregated HTF parity | PASS w/ documented delta | `replay_output/historical_replay/backtest_parity/` |
| Formula layer parity | 0 mismatches | `tools/parity_signal_harness.py` |
| DB lineage | clean | `db_integrity.json` (0 orphans/dups/cross-strategy) |
| Warmup parity | bit-exact (fast), bounded (15m/1h provenance) | `live_replay/restart/warmup_parity.json` |
| Restart recovery | PASS (real-native path) | `RESTART_RECOVERY_REPLAY_REPORT.md` |
| Full test suite | 1226 passed / 44 skipped | `python -m pytest` (2026-09-06) |