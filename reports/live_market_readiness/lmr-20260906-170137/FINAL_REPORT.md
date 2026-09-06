# LIVE MARKET READINESS FINAL REPORT — lmr-20260906-170137

Verdict: **NOT_VERIFIED_LIVE_MARKET_READINESS**

## Acceptance checklist

- [x] 1. Real Dhan REST auth + read-only historical acquisition  (ENV/REAL_SOURCE)
- [ ] 2. Real Dhan WebSocket connectivity (live instrument feed)  (REAL_LIVE_BOOT)
- [x] 3. Verified completion classifier applied to real Dhan candles (no source modification)  (REAL_SOURCE)
- [x] 4. Warmup backfill feeds shared indicator streams from real completed candles only  (REAL_LIVE_BOOT)
- [x] 5. Four strategies constructed on the shared indicator infra with own decision state  (INDEPENDENCE)
- [ ] 6. Indicator parity: engine arrays == independent re-derivation over same real candles  (WARMUP/INDEPENDENCE)
- [x] 7. Dhan timestamp == strategy input timestamp (candle start)  (REHEARSAL)
- [x] 8. Signals arise only from genuine crossover decisions on real candles (no synthetic candles)  (REHEARSAL)
- [x] 9. Signal candle timestamp == Dhan row open timestamp (exact)  (SIGNAL_EVIDENCE)
- [x] 10. Signal candle OHLC == Dhan row OHLC (exact)  (SIGNAL_EVIDENCE)
- [x] 11. LONG trigger == signal candle HIGH; SHORT trigger == signal candle LOW  (SIGNAL_EVIDENCE)
- [ ] 12. signal_id unique and disjoint from trade/order/fill/position  (LINEAGE)
- [ ] 13. Each entry trade references an existing signal (entry_signal_id)  (LINEAGE)
- [ ] 14. trade_id identical across trade/pending_order/order/fill/position (lineage carried)  (LINEAGE)
- [ ] 15. trade_id never equals position/order/fill/signal id  (LINEAGE)
- [ ] 16. Entry only after breakout trigger (pending distinct from execution; no execution-only second signal)  (LINEAGE/REHEARSAL)
- [x] 17. No duplicate signals/trades/orders/fills/positions  (RESTART)
- [ ] 18. SL closes same trade_id; exit_signal_id NULL; exit_reason STOP_LOSS  (LINEAGE)
- [x] 19. Reversal shares SIG-NEW: old exit_signal_id == new entry_signal_id; exit_reason *_reversal; opposite side  (LINEAGE)
- [ ] 20. DB canonical: memory vs trading.db vs analytics vs API vs WS reconciliation  (DASHBOARD)
- [ ] 21. No cross-strategy contamination on order/fill/position edges  (LINEAGE)
- [x] 22. Restart reconstruction preserves DB record sets exactly (no duplicated lifecycle rows)  (RESTART)
- [ ] 23. Dashboard/API/WS serve the canonical data with traceable trade->signal->candle evidence  (DASHBOARD)
- [x] 24. Four strategies keep disjoint pending/position/trade/SL/decision state even for the same instrument  (INDEPENDENCE)
- [x] 25. No infinite loop: bounded wall-clock, REST fetches, reconnect/restart attempts, stage deadlines  (meta)
- [x] 26. Strict verdict produced; any defect raises an exact DEFECT_REPORT with repro/fix/tests  (VERDICT)

## Failures

- real live boot: Dhan WS not connected (no live ticks)
- real Dhan WS not connected
- reversal exit_signal_id == next trade entry_signal_id in real data (SIG-NEW shared)
- lineage/DB gates failed
- dashboard API + WS serve the same canonical data (positions/trades/fills reconcile; WS snapshot lists all 4)
- dashboard/API/WS reconciliation failed
