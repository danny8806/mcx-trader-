# HISTORICAL REPLAY REPORT — REAL Dhan Native Data

## Purpose
Report the execution of the **finite deterministic historical replay** driven by
**real Dhan REST native candles** through the **unchanged production pipeline**
(NativeCandleRouter → shared indicator engine → 4 strategies → HTF → signals →
triggers → trade lifecycle → fills → positions → SL/reversal → exits → trading.db).

## Environment
- Replay driver: `tools/dhan_historical_replay.py` (adapter = production-engine
  drop-in `HistoricalDhanReplaySource`).
- Data source: `replay_output/dhan_snapshot/*.json` — REAL Dhan `/charts/intraday`
  native 5/15/60 candles, SHA-256 checksummed (manifest).
- Warmup: **production `engine._warmup_from_rest()`** with the period clock frozen
  at `2026-09-02 09:00 IST` (deterministic; identical history the live engine
  would consume at that start instant). ZERO lookahead (adapter serves only bars
  whose `end_ts <= replay_start`).
- Replay window: 2026-09-02 09:00 IST → 2026-09-04 23:25 IST (last closed 5m
  bucket of the dataset; 09-05 Sat / 09-06 Sun have no market data).
- Bounded: the entire replay is driven by the immutable snapshot; no wall-clock,
  no network, no randomness (paper execution latency/slippage/partials = 0).

## Execution Summary
| Entity | Count |
|---|---|
| Bars in snapshot (native 5/15/60 × 2 instruments) | 12,354 |
| Bars in replay window (native) | 1,486 |
| Warmup REST fetch windows (production `_warmup_from_rest`) | 12 |
| Strategies enabled | gold_01, gold_02, silver_01, silver_02 |
| Signals generated | **40** |
| Trades | **23** (21 closed + 2 open at window end) |
| Orders | 44 (entry+exit legs) |
| Fills | 44 |
| Positions | 23 |
| Reversal exits | 7 (5 short_reversal + 2 long_reversal) |
| DP-consistent DB rows | trades=23, orders=44, fills=44, positions=23 |
| Net P&L (all strategies) | **+20,921.06 INR** |

## Per-Strategy Outcome
| Strategy | Instrument/FTF | Bars processed | Signals | Trades | Net P&L |
|---|---|---|---|---|---|
| gold_01 | GOLDM / 5m | 523 | 14 | 8 | +25,207.43 |
| gold_02 | GOLDM / 15m | 175 | 6 | 5 | +20,696.08 |
| silver_01 | SILVERM / 15m | 175 | 6 | 3 | -209.37 |
| silver_02 | SILVERM / 5m | 523 | 14 | 7 | -24,773.08 |

(Bars processed = fast-TF bars in the window: 3 trading days × ~174 (5m) / ~58 (15m).)

## Determinism (restart/checkpoint)
`tools/replay_determinism_test.py` — two fully isolated engine builds (separate
workdirs/DBs/object graphs), same frozen warmup + same real replay window →
**bit-identical** normalized output:
- signals 40/40, trades 23/23, orders 44/44, fills 44/44, positions 23/23 — all
  `equal: true`
- evaluation_stream 4,184/4,184 identical
- indicator_streams, warmup fetch calls, crossover logs — equal
- **Verdict: VERIFIED** (only UUIDs differ, by design).

## Warmup Surface (real data, production path)
- `_warmup_from_rest()` per strategy fetched its fast id (5m → "5", 15m → "15")
  plus mid ("15") and HTF ("60") native bars over `fetch_calendar_days=14` and
  kept the configured `last_trading_days=5` (2026-08-26 … 09-01) for the fast leg.
- 12 fetch windows logged in `warmup_report.json`.
- Shared-stream counts after warmup (bar_count): GOLDM 5m=1,392 / 15m=755 / 1h=194
  (dedup suppressed duplicates from the warmup+replay boundary on the HTF legs;
  5m dedup=0).

## Exit-reason distribution (trade lifecycle)
| Exit reason | Trades |
|---|---|
| STOP_LOSS (SL1 / SL2 style) | 14 |
| short_reversal | 5 |
| long_reversal | 2 |
| open at window end | 2 |

## Reconciliation & DB integrity
- DB forensics (`db_integrity.json`): foreign_keys=ON, no duplicate signal/trade/
  event keys, no orphans anywhere, no trades-without-entry-signal,
  position_id ≠ trade_id for all rows, no SL invariant violations.
- analytics vs DB: **0 diff** in trade count and P&L per strategy.

## Artifacts
`replay_output/historical_replay/single_run/`: full_replay, signal_replay,
trade_replay, order_replay, fill_replay, position_replay, pnl_replay,
strategy_replay, crossover_replay, evaluation_stream.jsonl, persisted_signal_replay,
db_integrity, checksums.json.

## Notes
- 2 trades remain open at the window end (the dataset ends 09-04 23:25 IST); their
  P&L is unrealized and excluded from the net above.
- The SILVERM 09-03 09:00 corrupted bar (OHLC violation) was consumed as-is
  (pass-through, live-identical) — see DHAN_HISTORICAL_DATA_QUALITY_REPORT.md.