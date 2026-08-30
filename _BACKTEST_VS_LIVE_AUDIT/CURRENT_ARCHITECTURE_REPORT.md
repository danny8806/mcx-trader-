# CURRENT ARCHITECTURE — Complete Reverse-Engineered Verification Report (MCX-TRADER)

Scope: `C:\Users\pc\Desktop\MCX-TRADER`. Documentation ONLY — no code was changed.
Every claim below was read directly from source (file:line exact). Companion files:
- `CURRENT_ARCHITECTURE_COMPONENTS.csv` (LAYER/COMPONENT/FILE/FUNCTION/LINE/INPUT/OUTPUT/CALLER/CALLEE/STATE/DATA_SOURCE/DATABASE/NETWORK/THREAD/ERROR_PATH)
- `DATA_SOURCE_MAP.csv` (S01–S14 authoritative data flows)

---

## PART 0. EXECUTIVE SUMMARY

1. The live system is **PAPER-ONLY by force**: `TradingEngine._init_execution` raises unless `execution_mode == "paper"` (trading_engine.py:244-248). **Dhan receives no orders**; there is no order-placement method anywhere in `data/dhan/rest_client.py` (only charts/margin). All orders/fills are internal (`execution/paper_broker.py`), filled at live LTP ±1 tick.
2. **Candles are REST-only.** WebSocket carrys LTP/display/P&L/triggers only. `data/dhan/adapter.py:1-6,27` (module contract: "Do NOT build candles from ticks").
3. **Warmup is option-2 backtest-aligned and re-runs on every startup**: fresh REST backfill → trimmed to **last 5 trading dates** (trading_engine.py:1351-1395, settings.json warmup{5,14,true}) → per-day 09:00-anchored KEEP-ALL resample to 15m/1h (1411-1430) → DRAMA `load_batch_htf` + indicator warm (1449-1458) → markers reset first (1380-1384) so it is idempotent.
4. **Live bucket anchoring never crosses dates**: `candle_fetcher._fetch_candle` fetches strictly **same-day** (from=to=candle_time.date(), :158-160) and rebuilds every window from that day's 5m rows; `_warmup_from_rest` anchors each day at 09:00. Complete windows only unless `keep_partial` (both true in settings.json).
5. **HTF DEMA-ATR lines are continuous across sessions** (correct and intended — matches the backtest/Pine reference `build_15min_enriched.py`). Only *bucket start* resets at 09:00. Verified exact in prior probe runs (290/290 & 287/287).
6. **Signal/summary trigger chain fully verified**: 5m close → indicator+HTF → `_map_htf_to_fast` (bisect_right on end_ts, hft/backtest_style_htf.py:84-131) → `BaseDEMAStrategy.on_bar` → `_process_signal` → `OrderManager.submit_signal` → `PaperExecutionEngine` → `_on_fill` → position/ledger/telegram. Entry fills at trigger/close with the recorded model price (trading_engine.py:946-952).
7. **Persistence is crash-ordered**: order row before fill dispatch (959-976); fill row before position open (1095-1104); trade+exit fill in ONE transaction before memory close (trade_close.py:97-130); durable dedup mark afterwards (_on_fill:1332). Recovery = fill_dedup + `get_fill` replay guard (1045-1062). Reconciliation is position-anchored (trade_id==position_id, reconciliation/engine.py:294-328).
8. **EOD force-close intentionally disabled**; open positions carry overnight (market_status.py:`should_force_close` → False, :136-141) mirroring the backtest.
9. **Known residual risks** (documented, not fixed — this phase is verify/document only): tick-vs-bar SL duality (live can SL on every tick, backtest on bar close), reversal re-entry price level differs (trigger vs crossing-bar open), h1 "3 NaN" reference artifact, warmup window mismatch vs backtest LAST5 if exchange calendar diverges, no real broker/order-latency realism, dashboard auth token static.

---

## PART 1. PROCESS START → Dhan (verified call chain)

10. `main.py:main()` (:32) → `PersistenceManager(data/db/trading.db, system_state.json)` (:37-40) → `TradingEngine(config_path=config/settings.json)` (:42).
11. `TradingEngine.__init__` (:43) loads `Config` then initializes in order: `_init_market_status` (321) → `_init_data_adapter` (131) → `_init_timeframe_engine` (146) → `_init_indicator_engines` (163) → `_init_htf_engine` (177) → `_init_strategies` (205) → `_init_execution` (240) → `_init_portfolio` (258) → `_init_risk` (292) → `_init_monitoring` (303) → `_init_notifications` (313). Then event_store/trade_ledger (:70-78), fill_dedup (:82), safe_mode (:85).
12. `_init_data_adapter` constructs `DhanDataAdapter` (:134) → `DhanRESTClient` (rest_client.py:67) `ensure_token()`/`start_scheduler()` (adapter:52-53) → `DhanWebSocketClient` (:56) → registers instruments with `InstrumentMeta` (adapter:91-104).
13. Auth tree: `rest_client.ensure_token` (:206) → `load_token` cache 30s (:118) → JWT `token_expires_soon` (:134) → `renew_token` (:156): `pyotp.TOTP(secret).now()` + `dhanhq.DhanLogin(client_id).generate_token(pin, totp)` (:188-190); 130s cooldown lock (:101-103,169-176); scheduler thread renews daily 07:00 + 6h safety (:249-292). WS every reconnect re-loads token via `token_loader=ensure_token` (adapter:61, websocket:98-104).
14. `engine.start()` (:337) wires `TradeCloseManager` (:347) → loads processed-fill dedup from DB (:362) → startup `ReconciliationEngine.reconcile("startup")` (:370-379; failure → safe_mode "reconciliation_failed") → `_warmup_from_rest` (:390) → `mark_warmup_done` (:391) → `candle_fetcher.start()` (:394) → `data_adapter.connect()` (WS run_loop :396) → waits for first WS ticks (:401-404) → prints 13-check startup report (:409-494) → Telegram (:560) → `EngineStatus.READY` (:565).
15. Tick (WS) thread: `ws._on_message` → dedup → `adapter._process_tick` → `engine._on_tick` (trading_engine.py:585). REST thread: `candle_fetcher._run` (30s poll, gated by `should_fetch_candles`) → `_on_bar_closed` (trading_engine.py:683).
16. **"Dhan order placement" verdict**: NONE. Orders never leave the process. The last production network actions toward Dhan are candle/margin REST endpoints and the WS LTP subscribe.

---

## PART 2. WARMUP (option-2, every start)

17. `_warmup_from_rest` (:1338) reads warmup{last_trading_days:5, fetch_calendar_days:14, keep_partial:true} (:1357-1359); from_date = now-14d, to_date=now (:1361-1363).
18. Per instrument: REST fetch 5m (:1369) → **reset** all `indicators[inst:tf]` and `htf_engine.reset_instrument(inst)` (:1380-1384) → pandas UTC→IST (:1388-1390) → **trim to last 5 distinct trading dates** (:1391-1395) → warm 5m DEMAATR (:1401-1409).
19. 15m/1h resample: `session_start = df.date + "09:00"`; `mins=(dt-session_start)//60`; `_bucket = session_start + (mins//tf)*tf` (:1417-1419). KEEP-ALL unless `keep_partial=False` (which requires group size == tf/5, :1420-1426). Reference `%TEMP%\opencode\dema_mtf_base.py` uses the same KEEP-ALL bucket formula — verified byte-for-byte alignment earlier.
20. Bar conversion with explicit IST tz (:1436-1447) → `load_batch_htf(inst, tf, bars)` (:1450) → warm tf indicators (:1452-1458). Output: **870 5m / 290 15m / 75 1H bars** with keep_partial (previous probes confirmed counts and per-day 09:00 anchoring).

---

## PART 3. LIVE 5m bar pipeline

21. `CandleFetcher._check_timeframe` (candle_fetcher.py:107) computes `minutes_since_open`, `completed_buckets` (:126), `candle_start` (:129); skips before 09:00 (:115), skips windows past 23:30 unless keep_partial (:139-140), dedup key `inst:tf:start_ts` (:143), skips old (>3 windows) (:148).
22. `_fetch_candle` (:154) fetches **same-day** 5m REST (:158-160), matches exact 5m start (:174-183) or accumulates window 5m candles sorted, `expected_count=tf/5` (:203), emits Bar only on full window or (keep_partial) partial (:205), calls `on_candle_closed` → `engine._on_bar_closed` (:209).
23. `_on_bar_closed` (:683): `indicators[inst:tf].update(o,h,l,c)` (:696) → if tf in (1h,15m) `htf_engine.on_htf_bar_closed(bar)` (:699-700) → for each matching strategy: `map_to_fast_bar` (:716), `map_mid_to_fast_bar` (:719), `_process_deferred_exit` (:724), `strat.on_bar(bar, htf_mapped, fast_value, mid_mapped)` (:727) → `_process_signal` on signal (:728-729) → `_consume_same_bar_stop` second signal (:733-736).
24. Mapping math: `bisect_right(state.end_times, fast_bar.end_ts) - 1` (backtest_style_htf.py:114), equals backtest `np.searchsorted(src_avail, target_close, side='right')-1`. Uses `bar.end_ts` (fast_bar closed) — prior probe artifact about "09:15 BAD" was an index-keying off-by-one across the overnight gap, not a signal divergence (live_mid15/live_h1 exact 290/290 & 287/287).

---

## PART 4. TICK flow

25. `_on_tick` (:585): `update_data_status` (:598) → stale → safe_mode "market_data_uncertain" (:613-616) → READY→TRADING promotion (:624-627) → under lock: EOD guard (:631) → `execution_engine.update_price` (:635) → mark open positions + per-strategy/global unrealized (:638-652) → peak equity (:655-657) → reversal-deferred exits triggered strictly after the signal bar's end (:663-673) → pending-breakout tick triggers `strat.on_tick` (:676-681) → `_process_signal`.
26. Strategy tick semantics (`on_tick`, base_dema_strategy.py:528-583): suppressed when `just_entered` or `pending_exit_at_open` (:534-539); tick-level SL first (:541-549); then pending break >/=< trigger (:551-581) — entry fills at the trigger level.

---

## PART 5. Strategy state machine & signal production

27. States (strategies/types.py:21-33): FLAT, SIGNAL_LONG/SIGNAL_SHORT, PENDING_LONG/PENDING_SHORT, ENTRY_TRIGGERED, LONG/SHORT_POSITION, STOP_ACTIVE, EXIT_PENDING, EXIT_ORDER_SUBMITTED.
28. `on_bar` (:96-213): clear just_entered → lock prev values (:122-142) → pending check+timeout (50 bars) (:150-160) → pending trigger → same-bar stop branch (:161-178) → SL check (skip if just_entered) + re-detect (:181-194) → free cross detection re-arms pending each signal bar while flat (:201-206) → reversals while in position (:207-210).
29. Crosses: LONG = close > h1 AND prev_close ≤ prev_h1 AND h15 < h1 (:215-234); SHORT mirror with h15 > h1 (:236-254). Text exactly matches backtest `goldm_dema_mtf_futures` rules.
30. Entry model: `_detect_signal` only **arms a pending breakout** (return None, :256-281); `_create_pending_signal` sets trigger=high(LONG)/low(SHORT), stop=min/max of low+prev (LONG) etc (:320-355). Fill at trigger level on break (:437, 562-581) or direct-market reversal re-entry (:1236-1263).
31. Stopping exits fill at bar close (bar model) `_check_stop_loss`/`_consume_same_bar_stop` (:461-501); reversal exits scheduled at next open `_create_reversal_signal`/`_process_deferred_exit` (:357-412, trading_engine.py:738-781).

---

## PART 6. Order → fill → position → P&L

32. `_process_signal` (:863): exit signals skip safe-mode/market gates (:870-880); non-exit: risk `check_order` (:897-904) + margin `_calculate_margin` (slope·px+intercept, :840-861); `fill_price` override from model (`metadata.fill_price`) (:946-952).
33. `OrderManager.submit_signal` (order_manager.py:33): dedup key `strategy:instrument:timestamp` (:48), cleanup >1h (:53-59) → `PaperExecutionEngine.create_order` (side BUY/SELL from signal) (:101-124) → `submit_order` (:126-145) → `_execute_order` waits latency, uses `_current_prices`, ±slippage_ticks=1, creates Fill uuid (:147-185); no LTP → REJECTED.
34. Order row persisted BEFORE fill dispatch (trading_engine.py:959-976) → drain_fills (:979) → `_on_fill` per fill (:980).
35. `_on_fill` (:1042): 3-stage dedup (in-mem :1045, DB replay via `get_fill` :1052-1059, in-process `note_processed` :1062). Entry path: block per-strategy then global margin with rollback (:1079-1091) → persist fill (:1095-1104) → `position_manager.open_position` (:1106) → events/ledger/Telegram (:1124-1180). Exit path: `TradeCloseManager.close_position` atomic (:1215), safe mode if persistence failed (:1222), immediate reversal re-entry (:1236-1263), else clear to pending/flat (:1264-1273). Durable `mark_processed` at very end (:1332).
36. `TradeCloseManager.close_position` (trade_close.py:51) order: PnL calc pure (:65-84) → persist trade+exit fill in ONE transaction (:97-130; on failure return False, no memory change) → `record_trade` (:132-133) → close position memory (:138-143) → accounts+margin release (:148-157) → risk pnl/peak (:160-167) → ledger close (:170-203) → event store (:206-223) → dashboard callback + db event (:226-256) → Telegram (:259-277).
37. Financial math: `PNLEngine.calculate_realized_pnl` (pnl.py:52-78) long=(exit-entry)·qty·mult, short mirrored; fees via `MCXFeeModel.calculate` (fee_model.py:49-91, brokerage 2×20 + STT on sell + exchange+sebi+turnover + GST + stamp). Equity = starting_capital + realized_net + unrealized (account.py:57-59); margin block/release (:95-107).

---

## PART 7. Persistence / restore / reconciliation / recovery

38. Persistence: JSON state (system_state.json, atomic tmp+replace:135-142) + SQLite trading.db WAL (tables trades/orders/fills/account_snapshots/events:64-131); single persistent connection + RLock (:32,40-55). Analytics: analytics.db (EventStore :70, TradeLedger :76).
39. `mark_processed` crash-window: `note_processed` holds memory lock at delivery; durable SQLite mark occurs only after all financial effects (fill_dedup.py:106-115; trading_engine.py:1062/1332). Crash between rows → `get_fill` replay guard skips re-apply (:1052-1059).
40. `restore()` (trading_engine.py:1502-1532): market → strategies → positions → accounts → pnl → risk → execution. Indicator/HTF **never restored** (recomputed via `_warmup_from_rest`; comment :1494-1496,1507-1508). `market_status.restore` resets daily flags when session_date changes (market_status.py:230-251).
41. Reconciliation (reconciliation/engine.py:93-151): DB ro-conn vs memory; 9 checks incl. position-anchored trade linkage (trade_id==position_id, :294-328), DB-union-negative-fill invariants, dup ids, order/fill state+price parity. Failure → safe mode at engine.start (:381-382).

---

## PART 8. Session / day rollover / safe mode

42. MarketStatus states (market_status.py:30-39) & rule table (:273-323): overnight before 08:55, pre_market 08:55-09:00, market_open 09:00-09:01, live 09:01-23:25, market_close 23:25-23:30, after 23:30-24:00, weekend → overnight; daily flags reset on date change (:279-283); `should_force_close` False (:136-141); `restore` treats new date as new flags (:232-236).
43. Safe mode (core/safe_mode.py:25-157): 8 reason codes (:28-37), reason-set with 5s exit cooldown (:90-92), gates every non-exit signal via `should_allow_trading` (:106-120) + `market_status.is_trading_allowed`. Kill switch (risk_engine.py:109-114) disabled in settings.json (kill_switch_enabled false).

---

## PART 9. Current-vs-expected table (option-2 parity)

44. | Area | Expected (option-2 / backtest) | Current implementation | Verdict |
    |---|---|---|---|
    | Warmup depth | Last 5 trading days | fetch 14 cal days → trim last 5 dates (1351-1395) | PASS |
    | Resample anchor | per-day 09:00 KEEP-ALL | `session_start+(mins//tf)*tf` (1417-1419) | PASS |
    | keep_partial | True | top-level warmup.keep_partial=true (1358); live CandleFetcher cfg keep_partial=true (204) | PASS |
    | HTF-D1 DEMA/ATR | continuous (Pine) | incremental, no day reset (dema_atr.py:56, backtest_style_htf.py:48) | PASS |
    | Live 1h window | must not cross dates | same-day fetch from/to (candle_fetcher:158-160) | PASS |
    | Mapping | searchsorted right-1 | bisect_right(end_ts)-1 (htf.py:114) | PASS |
    | Crossover | close vs 1h + 15m confirm | base_dema_strategy:215-254 | PASS |
    | SL exits | at bar close | bar model + tick SL | PARTIAL (tick path is live-additive) |
    | Reversal exit | next bar open | `_process_deferred_exit` next-bar open | PASS (bar model) |
    | Entry price | backtest fills cross-open | live fills trigger level | INTENTIONAL DIFF |
    | Order persistence | build before fill notify | 959-976 order→fills | PASS |
    | Trade close | one tx before memory | trade_close.py:97-130 | PASS |

---

## PART 10. Risks (current, unfixed — verify/doc phase)

45. Tick vs bar SL duality — live can stop on any tick where the backtest only checks bar close (documented in on_tick/bar SL).
46. Reversal/breakout entry *price* differs from backtest (trigger level vs crossing-bar open) → P&L level drift per trade.
47. Warmup `fetch_calendar_days=14` must always cover 5 trading dates across weekends/holidays; no exchange-calendar awareness.
48. Reference 1H emits NaN at 3 buckets (channel-reveal) so live publishes 287/290 confirmed — a reporting nuance, not a drift source; re-confirmed parity.
49. No real-broker realism: paper slippage fixed tick, latency fixed, no partial fills (guard: paper_broker.py:83-88).
50. `_last_fetched` memory dedup in CandleFetcher is process-lifetime; a crash mid-window relies on the 3×tf staleness skip, live fetch pattern is otherwise trust-in-REST.
51. `restore()` trusts saved position/strategy state vs DB — reconciliation is the only cross-check; indicator/HTF deliberately rebuilt (no drift).
52. Static-token dashboard API key; Telegram/bot renotifies no throttling beyond Telegram limitations.
53. Single-process, single-engine: candle-fetcher thread + WS thread + main-loop; all trades serialized under `engine._lock`.
54. Two `OrderState` enums coexist (types.py:36 vs paper_broker.py:14) — must be kept in sync mentally; no cross-assignment asserted.

---

## PART 11. MASTER MERMAID FLOWCHART

```mermaid
flowchart TB
  subgraph STARTUP
    M[main.py:32] --> P[PersistenceManager data/db]
    M --> E[TradingEngine.__init__:43]
    E --> C[Config settings.json + ${ENV}]
    E -->|_warmup_from_rest:1338| W[REST backfill 14d -> trim 5 trading dates]
    W --> R1[09:00 per-day KEEP-ALL resample 15m/1h]
    R1 --> H[HTF load_batch_htf + indicators]
    E -->|start:337| REC[ReconciliationEngine:379]
    REC -->|fail| SM[SafeMode reconciliation_failed]
  end
  subgraph LIVE
    WS[Dhan WS LTP] -->|_parse_packet:199| AD[adapter._process_tick:116]
    AD -->|_on_tick:585| ENG
    CF[CandleFetcher 30s:79] -->|same-day REST 5m :154| CO[engine._on_bar_closed:683]
    CO --> IND[DEMAATR.update:696]
    CO --> HTF[on_htf_bar_closed:48 + searchsorted map:84]
    CO --> STR[BaseDEMAStrategy.on_bar:96]
    STR -->|Signal| SIG[engine._process_signal:863]
    SIG -->|safe/risk gates| SIG
    SIG --> OMS[OrderManager.submit_signal:33]
    OMS --> PB[PaperExecutionEngine:147 slippage+latency+lto]
    PB -->|Fill| FILL[engine._on_fill:1042]
    FILL -->|entry| PM[PositionManager.open_position:114]
    FILL -->|exit| TC[TradeCloseManager.close_position:51]
    TC --> DB[(trading.db single-tx trades+fills)]
    TC --> ACC[AccountEngine update_realized/release]
    TC --> RSK[RiskEngine update_daily_pnl]
    TC --> LED[TradeLedger close_trade]
    TC --> TG[Telegram on_trade_close]
  end
  subgraph PERSIST
    MAIN[main 60s:75] -->|snapshot:1467| SJ[system_state.json atomic]
    FILL -->|mark_processed:1332| DD[(processed_fills)]
    SIG -->|save_order before fills:959| DB
  end
```

---

## PART 12. COMPLETE FILE/FUNCTION MAP (+ CSV + DATA_SOURCE_MAP + call graphs referenced above)

55. Full component-level table with exact function/line/caller/callee: **`CURRENT_ARCHITECTURE_COMPONENTS.csv`** (46 rows).
56. Authoritative data-element registry with producer/consumer/persistence/authoritativeness: **`DATA_SOURCE_MAP.csv`** (S01–S14).
57. Call graph (startup depth): main→PersistenceManager→TradingEngine.init→[data_adapter→rest_client.token/scheduler→ws→*], [timeframe_engine→CandleFetcher], [indicators 6x], [htf engine 4], [strategies 4], [execution 2], [portfolio], [risk], [health], [telegram]; start→TradeClose→safe→reconcile→warmup→fetcher.start→connect→report.
58. Call graph (signal, one 5m bar): `_on_bar_closed(683) → indicator.update(696) → htf.on_htf_bar_closed(700) → map_to_fast_bar(716)/map_mid_to_fast_bar(719) → _process_deferred_exit(724) → strat.on_bar(727) → _process_signal(729) [→ OrderManager.submit_signal(33) → PaperExecutionEngine(126/147) → save_order(959) → drain_fills(979) → _on_fill(1042) → open_position/close_position → ledger/event/telegram] → _consume_same_bar_stop(733)`.
59. Threads: main (orbit+restore loop), WS notifier (daemon, own thread), candle-fetcher (daemon), token-scheduler (daemon), Telegram router (own). All financial mutations under `engine._lock` (RLock).
60. State machines enumerated in full (SERVER startup/stop, MARKET 8-state, DHAN token/ws, STRATEGY 9-state, ORDER 7-state, POSITION open/closed, TRADE closed/DB). See parts 5/8 and Components CSV.