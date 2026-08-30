# DEEP AUDIT — LIVE STRATEGY vs BACKTEST STRATEGY (fresh code walk, 2026-08-30)

This audit was produced from a **fresh read of the actual code** on both sides — no past
result files were used. Sources:
- Backtest reference: `C:\Users\pc\Desktop\nifty dema backtest\project`
  (`goldm_dema_mtf_futures.py`, `core\dema_mtf.py`, `build_15min_enriched.py`,
  `show_15_15_60.py`, and the KEEP-ALL resample `%TEMP%\opencode\dema_mtf_base.py`
  which `show_15_15_60.py:12-17` loads and overrides).
- Live system: `C:\Users\pc\Desktop\MCX-TRADER`
  (`trading_engine.py`, `strategies\base_dema_strategy.py`, `htf\backtest_style_htf.py`,
  `indicators\dema_atr.py` / `dema.py` / `atr.py`, `core\candle_fetcher.py`,
  `execution\paper_broker.py` / `fee_model.py`, `portfolio\pnl.py`, `core\market_status.py`,
  `config\settings.json`).

## Short answer — the core question

The **strategy is the same on both sides**. The full trading rule — DEMA-ATR(3,6,1.0),
09:00-anchored 15m/1H resampling, exact `searchsorted = bisect_right` HTF mapping,
the 3-condition LONG/SHORT crossover with the 15m confirmation filter, trigger = signal-bar
high/low, SL = min/max of two bars, next-bar-open reversal exits, bar-close SL, same-bar-stop,
carry-forward overnight, and gross `(exit-entry)*mult*qty` — is **code-identical**.

**Where they differ (each with its exact file:line, see matrix):**

| # | What differs | Where it lives now |
|---|---|---|
| E2 | **Entry fill PRICE**: backtest fills the breakout at the crossing bar's OPEN; live fills at the **trigger level** (signal candle's high/low) | `base_dema_strategy.py:437` vs `goldm_dema_mtf_futures.py:177` |
| F2 | **STT side for SHORT**: backtest bills STT on the *exit* turnover for shorts; live bills it on the *entry* (sell) leg | `execution/fee_model.py:71-75` vs `goldm_dema_mtf_futures.py:70-73` |
| X1 | **Slippage ±1 tick** per fill (live paper only) | `execution/paper_broker.py:156-161`, `settings.json:108` |
| X2 | **Latency 100 ms** simulated (live paper only) | `execution/paper_broker.py:149-150` |
| X3 | **Pending 50-bar timeout** (live robustness, no expiry in backtest) | `base_dema_strategy.py:152-159` |
| X4 | **Tick-level SL** on top of bar-close SL (live can stop out intra-bar) | `base_dema_strategy.py:541-549` |
| X5 | **Margin formula**: backtest 6.5% of notional vs live Dhan linear model + risk-engine check | `trading_engine.py:840-861` vs `goldm_dema_mtf_futures.py:82-85` |
| X6/X7 | **Order/broker/accounting infra**: backtrader in-memory cash ledger vs OrderManager+paper broker+per-strategy accounts (outcome-equivalent) | `execution/*`, `portfolio/*` |
| X8/X9 | **Engine gating**: live waits for `fast_indicator.initialized` and seeds the strategy prev-chain at the first live bar (1-bar skip on a cold start) — warmup now feeds 5 days so only the day-1 open band is affected | `trading_engine.py:712`, `base_dema_strategy.py:69-73,:144` |

Gaps D2 (partial 23:00 1H bucket) and D3 (warmup depth 7d vs 5d) that existed earlier are
**gone**: `keep_partial=true` and `warmup.last_trading_days=5` now make the live feed exactly
equal to the backtest's KEEP-ALL LAST5 feed (`settings.json:19-25,:38,:56`).

## What was proven identical (with the code evidence)

1. **Parameters** — D3/A6/F1.0, session 09:00, qty 1, mult 10/5, capital 300k.
   `goldm_dema_mtf_futures.py:55-58` == `settings.json:64-105`.
2. **DEMA** — `pine_ema` (`ewm`, `min_periods=1`, α=2/(n+1)) and 2\*EMA1−EMA2
   (`build_15min_enriched.py:84-86,107`) == incremental `DEMA.update`,
   seed EMA1=EMA2=first, same α (`indicators/dema.py:47-64`).
3. **ATR** — first = mean(first N), then Wilder α=1/N (`build_15min_enriched.py:88-102`)
   == `indicators/atr.py:57-86`.
4. **DEMA-ATR clamp** — recursive `cur=prev or dema; if lower>cur lower; if upper<cur upper`,
   NaN-band skip (`build_15min_enriched.py:116-123`) == `indicators/dema_atr.py:83-96`.
5. **Resample** — bucket = session_open + floor(minutes/tf)\*tf, O/H/L/C/V agg
   (`core/dema_mtf.py:47-89`, KEEP-ALL `dema_mtf_base.py:47-70`)
   == live warmup (`trading_engine.py:1464-1486`, keep_partial) == live runtime
   CandleFetcher aggregation (`candle_fetcher.py:189-262`).
6. **HTF mapping** — `src_avail=end+tf; target=base_end; searchsorted(right)-1`
   (`core/dema_mtf.py:106-117`) == `bisect_right(end_times, fast_bar.end_ts)-1`
   (`htf/backtest_style_htf.py:111-114`). Mathematically identical.
7. **Signals** — LONG `close>h1 & prev≤prev_h1 & h15<h1`; SHORT mirror; 3-way NaN skip
   (`core/dema_mtf.py:176-183`) == `base_dema_strategy.py:226-234,:247-254`.
8. **Trigger/SL** — signal-bar high/low; SL min(2 lows)/max(2 highs)
   (`goldm_dema_mtf_futures.py:273-276,287-288`) == `base_dema_strategy.py:325-334`.
9. **Fills/state machine** — breakout-bar selection, next-bar-open reversal exit,
   bar-close SL, same-bar stop, pending re-arm — all bar-for-bar equal
   (`goldm_dema_mtf_futures.py:150-258,279-302` == `base_dema_strategy.py` + `trading_engine.py:683-736`).
10. **EOD** — both carry positions overnight (`market_status.py:136-141`).
11. **P&L formula** — identical (`goldm_dema_mtf_futures.py:221-224` == `portfolio/pnl.py:63-67`).

## Disposition

- **E2 is the single intentional trade-level difference** (backtest spec: breakout fills at
  crossing-bar open; live: fills at the trigger level). Per-trade P&L delta =
  `(crossing_open − trigger) × multiplier × qty`, direction mixed (favourable and adverse).
- **F2, X1, X3, X4, X5** are small/mechanical; none changes *which* signal fires.
- **X5 margin** uses different formulae and neither side blocked a trade in the audited window.
- **X8/X9** only affect a truly fresh cold start and are now masked by the 5-day warmup.
- No logic/rule mismatch exists between the two codebases after the `keep_partial`/LAST5 fix.

Companion file: `LIVE_VS_BT_DIFF_MATRIX.csv` (36-row side-by-side with file:line evidence).