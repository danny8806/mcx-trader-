# LIVE_REFERENCE_SPEC — LIVE DEMA-ATR Multi-TF engine (as deployed for paper trading)

> Forensic extraction of the CURRENT live system (`C:\Users\pc\Desktop\MCX-TRADER`), the exact
> components a 15m/15m/60m user would run: strategy `gold_02`/`silver_01`, 15m fast, 15m mid
> confirmation, 1h HTF signal line, D=3 A=6 F=1.0. Compare against `BACKTEST_REFERENCE_SPEC.md`.

## 1. Configuration

- `config/settings.json`: instruments GOLDM (563946, mult 10.0, session 09:00-23:30/870min) &
  SILVERM (483080, mult 5.0) — **same contracts as the backtest reference** (`:19-54`).
- Indicators `dema_period 3, atr_period 6, atr_factor 1.0` (`:55-59`).
- Strategies: `gold_02` & `silver_01` = `fast_timeframe 15m, mid_timeframe 15m, htf_timeframe 1h,
  quantity 1, capital 300000, enabled true` (`:60-97`).
- `paper_execution.slippage_ticks 1, latency_ms 100, partial_fill_probability 0.0` (`:98-102`).
- Charges per instrument: brokerage 20/side, stt_sell 0.01%, exchange 0.0026%, sebi 0.0001%,
  gst 18%, stamp 0.0 (`:103-120`).
- Risk `margin_per_trade_pct 6.5`; account `starting_capital_per_strategy 300000` (`:121-133`).
- Strategy classes: `GoldStrategy02`/`SilverStrategy01` = pure `BaseDEMAStrategy` with fast=15m
  (`strategies/gold/__init__.py:17-26`, `strategies/silver/__init__.py:5-14`).

## 2. Runtime bars (data path)

- **CandleFetcher (REST)** is the ONLY bar source in production (`trading_engine.py:146-160`;
  docstring `core/candle_fetcher.py:1-17`); WebSocket is LTP-only for fills/marks/P&L.
- Emission rule (`candle_fetcher.py:_check_timeframe/_fetch_candle/:191-200`): a 15m/1h candle is
  emitted ONLY when its window `[start, start+tf)` contains EXACTLY `tf//5` five-minute candles
  (`expected_count`, `:194`). **Partial end-of-session windows (e.g. 23:00-24:00 1h slot) are
  DROPPED.**
- 5m candles: emitted when a 5m candle's start timestamp == target (`:168-177`), session-aware
  (no fetch before 09:00 / at/after 23:30, `:115-123`).
- Bar object: `start_ts` = bucket start IST epoch, `end_ts = start_ts + tf*60`
  (`_create_bar/_aggregate_candles`, `:218-228/:254-264`).

## 3. Startup warm-up (indicators + HTF engine)

`TradingEngine._warmup_from_rest` (`trading_engine.py:1338-1448`):
- Fetches **7 days** of 5m candles back from IST now (`:1347-1352`).
- Resets the 5m/15m/1h DEMAATR indicators + HTF engine per instrument (`:1369-1373`).
- Feeds raw 5m OHLC into the 5m indicator (`:1386-1393`).
- Resamples 1h & 15m with the **complete-window rule** (`groupby size == tf_minutes//5`) anchored at
  `session_open` 09:00 (`:1397-1413`) — same rule as CandleFetcher (§2), partial windows skipped.
- Loads the resampled bars into `htf_engine.load_batch_htf` and updates each timeframe's DEMAATR
  (`:1432-1443`).

## 4. Indicators (exact formulas)

`indicators/dema_atr.py` DEMAATR (stateful, incremental) — verified equal to the backtest batch
`dema_atr`:
- `DEMA = 2*EMA1 − EMA2`; `EMA` via `indicators/dema.py` alpha `2/(period+1)`, first value seeded to
  the first input (= pandas ewm `min_periods=1`).
- ATR via `indicators/atr.py` (Wilder): TR from H/L/prevC; first ATR = mean of first N TRs; then Wilder smoothing.
- `band = atr*factor; upper=dema+band; lower=dema−band`; recursive clamp
  `cur = prev_output (or dema if first); if lower>cur: cur=lower; if upper<cur: cur=upper`
  (`dema_atr.py:56-98`; batch twin `calculate_batch:100-146`).

## 5. HTF engine (mapping)

`htf/backtest_style_htf.py`:
- `register(instrument, tf, dema_p, atr_p, atr_f, session_open)` per 1h and 15m (`:32-46`).
- `on_htf_bar_closed` updates DEMAATR + appends `(bar.end_ts, value)` (`:48-66`).
- `map_to_fast_bar` (1h) / `map_mid_to_fast_bar` (15m) → `bisect_right(end_times, fast_bar.end_ts) − 1`
  (= backtest `searchsorted(src_avail, target_close, side="right") − 1` with
  `target_close = base_dt + base_min`, `src_avail = htf_end`); value usable once the fast bar's END
  reaches the HTF bar's END (`:84-131`). **Mathematically identical to the backtest.** For the 15m
  MID line on 15m fast: 1:1 equal-time (bar END == its own HTF bar END).

## 6. Strategy (signal, trigger, SL, reversal)

`strategies/base_dema_strategy.py`:
- Signal filter on closed fast bar (`on_bar`, `:96-213`; cross helpers `:215-254`):
  - LONG = `close > htf AND prev_close <= prev_htf AND mid < htf` (strict `mid >= htf` blocks) (`:226-234`).
  - SHORT = `close < htf AND prev_close >= prev_htf AND mid > htf` (`:247-254`).
  - Skip until HTF values confirmed (`:144-145` — `prev_htf` acts as the NaN guard).
- Entry model (`_detect_signal/_create_pending_signal`, `:256-355`): arm a **pending breakout**;
  trigger = signal bar `high` (LONG) / `low` (SHORT); SL = `min(low, prev_low)` LONG /
  `max(high, prev_high)` SHORT (`:325-334`).
- Pending fill (`_check_pending_entry`, `:414-459`): LONG when `bar.high > trigger` / SHORT `bar.low <
  trigger` → **fill PRICE = trigger level** (`:437`). **This is the live entry execution difference.**
- Same-bar stop (`:61-66, :167-170, :477-501`): if the just-filled bar also breaks the stop → exit at
  that bar's CLOSE.
- SL exit (`_check_stop_loss`, `:461-475`): `low <= stop` (LONG) / `high >= stop` (SHORT) → exit at
  bar CLOSE (`stop_loss_hit`).
- Reversal (`_create_reversal_signal`, `:357-412`): opposite cross on bar T arms opposite-side pending
  (trigger = T's high/low) AND schedules the held position's exit at the **next fast bar's OPEN**
  (reason `long_reversal`/`short_reversal`).
- Pending timeout 50 bars — an unfilled pending EXPIRES (backtest has none) (`:37, :152-159`).
- Re-arm: while flat/pending, each new signal bar **replaces** the pending (`:201-210, :277-281`) —
  matches backtest re-arm.

## 7. Engine pipeline

- `_on_bar_closed(bar)` (`:683-736`): update fast DEMAATR (`:696`); feed 1h/15m to HTF engine
  (`:699-700`); for matching fast (15m) strategies: map 1h + 15m, consume deferred reversal exit at
  this bar's OPEN (`:724`), run `strategy.on_bar` (`:727`), then consume same-bar stop (`:733-736`).
- `_on_tick(tick)` (`:585-681`): (1) update execution price, (2) mark P&L, (3) when
  `tick_signal_processing` is True: consume deferred exit at the first tick after the signal bar's end
  (price = tick LTP), and check pending-entry triggers (`ltp > trigger` / `<`) filling at the trigger
  level (`:663-681`). Default flag True in live (`:101`); the offline/bar replay path sets it False so
  bar-crossing timing reproduces the backtest model (per `:96-100` comment).
- `_process_signal` (`:863-1001`): non-exits gated by safe mode + `is_trading_allowed`; per-strategy
  account margin + RiskEngine check; `fill_price` from signal metadata **overrides the broker LTP for
  that order** (`:946-952`); submits MARKET order via OrderManager, drains fills.
- `_on_fill` (`:1042-1332`): dedup (memory+DB), opens position (margin block + rollback),
  `trade_ledger.create_trade`/`record_fill`; exit via `TradeCloseManager.close_position` then
  re-arms immediate reversal entries (`:1236-1263`) or pending state (`:1264-1273`).

## 8. Paper execution, fees, P&L

- Paper broker: MARKET order fills at `current_price ± slippage_ticks*1.0` (BUY +, SELL −)
  (`paper_broker.py:156-161`); config slippage 1 → **every fill is ±1 INR vs the model price**
  (the pinned `fill_price` from §7 is first pushed into `execution_engine.update_price`).
- Fees: `MCXFeeModel.calculate` (`fee_model.py:49-91`) — **side-aware**: for SHORT the buy/sell
  turnovers are swapped (`:71-77`), so STT attaches to the actual SELL leg; ++stamp duty (config 0).
- P&L: `PNLEngine.calculate_realized_pnl` (`pnl.py:52-78`): gross `(exit−entry)*qty*mult` (LONG) /
  `(entry−exit)*…` (SHORT); `net = gross − fees.total`. Trade recorded under per-strategy
  AccountEngine; `win` if `net >= 0`.
- **EOD force-close DISABLED** (`market_status.py:136-141` returns False): open positions carry into
  the next session until reversal or SL — mirrors the backtest model.
- Margin: `margin_per_trade_pct 6.5` of notional via linear margin_model (settings `:31-35/:48-52`);
  at 300k capital no margin blocks arise for 1 lot GOLDM/SILVERM (matches backtest).

## 9. Scope of the parity replay

The live side is replayed through the REAL `TradingEngine` (only `DhanDataAdapter` substituted by a
boundary adapter that serves the same historical 5m candles the backtest consumes), with
`tick_signal_processing=False` in the bar-model run so bar-level timing == backtest, exposing ONLY
the intentional entry-execution difference (trigger-level fill vs crossing-bar open).