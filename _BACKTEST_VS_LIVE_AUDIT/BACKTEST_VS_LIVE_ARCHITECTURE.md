# BACKTEST VS LIVE ARCHITECTURE — 15m/15m/60m DEMA-ATR BASE

## Purpose
Show, side by side, where the BACKTEST reference (`nifty dema backtest\project`) and the LIVE
system (`MCX-TRADER`) implement each stage of the same strategy, and where the two diverge.

```
                  ┌──────────────── BACKTEST (reference) ─────────────────┐  ┌─────────────── LIVE (production) ───────────────┐
  DATA            5m MCX CSV (GOLDM/SILVERM, 24-28 Aug 2026)              │  5m MCX candles (Dhan REST, same contracts 569003/483080)
                  load_5m (validate/dedupe)              goldm_dema_mtf_futures.load_5m   CandleFetcher._fetch_candle/create_bar
                  filter LAST5 (5 days)                  show_15_15_60.py:60              _warmup_from_rest (7 days) trading_engine.py:1351
                  ├─ 15m base: resample KEEP-ALL buckets (dema_mtf_base)                  ├─ 15m/1h bars COMPLETE WINDOWS ONLY
                  └─ 1h line:  resample KEEP-ALL buckets                                  │   (candle_fetcher.py:191-200, trading_engine.py:1409)
                                                                                          │
  INDICATORS      pine_ema / wilder_atr / dema_atr (band clamp)                            DEMAATR incremental (identical formula)
                  build_15min_enriched.py:84-123                                          indicators/dema_atr.py:56-98
                  DEMA=2EMA1-EMA2, ATR(6)x1.0, recursive clamp                            (verified byte-identical)
  MAPPING         src_avail=bucket+rule_min; target=base_dt+base_min                      htf_engine: end_times=bar.end_ts; target=fast_bar.end_ts
                  searchsorted(src_avail,target,right)-1   core/dema_mtf.py:117           bisect_right(end_times,ts)-1  backtest_style_htf.py:114
  SIGNALS         close vs h1 cross + h15 filter (strict < / >)                            _check_long_cross/_check_short_cross (strict)
                  goldm_dema_mtf_futures.py:271-276                                        base_dema_strategy.py:226-254
  STATE MACHINE   next(): pending exec -> SL -> signals-for-next                          on_bar(): pending fill/same-bar-stop -> SL -> detect
                  always-in-market reversal (exit @next open)                              deferred reversal exit @next open (trading_engine.py:724)
                  pending re-armed on each new signal                                     pending re-armed (base_dema_strategy.py:201-210)
  ENTRY FILL      crossing-bar OPEN                       *DIFFERS INTENTIONALLY*          trigger LEVEL (signal-bar HIGH/LOW)  :437
  SL / EXIT       SL @bar CLOSE; reversal @next bar OPEN                                   SL @bar CLOSE; reversal @next bar OPEN (bar-model)
                  same-bar stop @close                                                     same-bar stop @close
  FEES & P&L      futures_charges (brokerage/STT/exch/sebi/gst)                            MCXFeeModel (side-aware STT)
                  gross=(exit-entry)*mult*qty; net=gross-charges                           PNLEngine gross/net (identical formula)
  SLIPPAGE        none                                                                     +/-1 INR per fill (paper_broker.py:156-161)
  OVERNIGHT       position persists (no EOD close)                                          should_force_close=False (market_status.py:136)
```

## Live engine call graph used by the parity replay

```
ReplayDataAdapter (boundary: serves the 5-day 5m CSV window — the ONLY substitute vs production)
   └─> TradingEngine._on_bar_closed(15m bar)               trading_engine.py:683
         ├─ indicators["INSTR:15m"].update(...)            (fast 15m line)
         ├─ htf_engine.on_htf_bar_closed(bar)  (1h & 15m)  backtest_style_htf.py:48
         ├─ map_to_fast_bar -> 1h DEMA-ATR                 backtest_style_htf.py:68
         ├─ map_mid_to_fast_bar -> 15m DEMA-ATR            backtest_style_htf.py:76
         ├─ _process_deferred_exit(strat, bar)  (reversal @open)  trading_engine.py:738
         ├─ strat.on_bar(bar, htf, fast, mid)  -> Signal   base_dema_strategy.py:96
         └─ _process_signal(signal) -> Order -> Fill       trading_engine.py:863
               └─ _on_fill -> PositionManager/TradeClose/PNL/Persist  :1042
```

## Where the two DIFFER (each audited; see FINAL_PARITY_GAP_REPORT.md)

| # | Difference | Class | Impact |
|---|---|---|---|
| D1 | Entry fill price: trigger level (live) vs crossing-bar OPEN (backtest) | INTENTIONAL (audit brief #15) | trade entry prices differ |
| D2 | Partial 1h bucket (23:00) kept by backtest, dropped live | Unintended dataset rule | 1h line drift from day 2; may move crossovers |
| D3 | Warmup depth: 5-day-only (backtest) vs 7-day (live) | Unintended parameter range | 1h/15m line level differs; may move crossovers |
| D4 | Slippage +/-1 INR per fill (live paper only) | Execution realism (config) | +/-1 per side on net P&L |
| D5 | STT side handling on SHORT trades (backtest charges exit leg) | Backtest fee quirk | tiny STT delta on SHORT trades |
| D6 | Pending entry 50-bar timeout (live only) | Live robustness guard | none if no pending exceeds 50 bars (verified) |
| D7 | Broker latency 100ms + order objects (live) vs instantaneous (backtest) | no-op for bar model | none |
| D8 | Tick-level SL (live) can fire intra-bar; bar-model uses bar low/high | mode-dependent | none in bar-model replay |