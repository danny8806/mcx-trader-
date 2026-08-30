# FINAL_VERIFIED_DATA_FLOW — MCX-TRADER

Generated 2026-08-30 09:34 UTC — PASS 1 + PASS 2 agreement on the current tree.

## Data path (verified end-to-end)
1. Dhan REST candles -> fetch_real_candles (client_id from Config/env, no hardcode)
2. _warmup_from_rest (IST-aware `datetime.now(timezone(+05:30))`) -> per-TF indicators
3. WS ticks -> _on_tick: market status update + order fill engine + P&L marking
   (+ transition EngineStatus.READY->TRADING when CONNECTED+LIVE)
4. _on_bar_closed -> strategy signal -> _process_signal -> execution -> order
5. Order fill -> _on_fill (authoritative path):
   is_duplicate? -> get_fill DB guard (skip+mark if already durable)
   -> note_processed (mem) -> ... -> mark_processed at end AND before both early
   returns (global-margin rollback, position-open failure); the classic
   crash/reconnect double-fill window is CLOSED between save_fill/save_trade_and_fill
   and the durable mark.
6. ReconcileEngine -> ReconciliationReport (IST-aware) — Consistent=True daily.

## Money / margin / equity identity (verified)
- equity = per-strategy account_equity sum; margin = positions lock vs account used_margin;
  verified 5 days: gbl==strat, equity==1.2M baseline, used_margin==positions.

## UI contract (fixed)
- positions: entry_price = average_entry alias; indicators: dema/atr flattened
  (dema_value = 2*ema1 - ema2 additive, raw keys preserved);
  detail configuration.starting_capital surfaced; StrategyDetail equity baseline
  uses cfg.starting_capital || 1000000; analytics events -> {id,type,data,timestamp}.
