# FINAL HISTORICAL REPLAY VERIFICATION — REAL NATIVE DHAN DATA

## Subject
Finite deterministic historical replay of the production MCX-TRADER pipeline over
REAL native Dhan candles (GOLDM 569003, SILVERM 483080; 5m/15m/1h, no
resampling, no lookahead) for the window **2026-09-02 09:00 IST → 2026-09-04
23:25 IST**, warmup history through **2026-08-03 09:05 → 2026-09-01**.

## Method (every stage offline, from the immutable snapshot)
Production pipeline untouched: `DhanDataAdapter`-shaped offline source →
`NativeCandleRouter` → shared `IndicatorEngine` (6 streams) → 4 strategies
(gold_01 GOLDM 5m, gold_02 GOLDM 15m, silver_01 SILVERM 15m, silver_02 SILVERM
5m) → HTF mapping → signals → triggers → paper execution → fills → positions →
SL + reversal exits → trading.db → API/WS/frontend surfaces. No strategy math was
modified; only the market-data binding and the environment were made
deterministic (`HistoricalDhanReplaySource`, `_ReplayWS`, frozen clock).
Warmup consumed the exact closed history at the replay-start instant
(`cutoff_ts`, 12 REST-shaped fetch windows), guaranteeing zero lookahead.

## Evidence spine (one file per stage)
| Stage | Report/tool | Result |
|---|---|---|
| Data source | DHAN_NATIVE_DATA_SOURCE_REPORT.md | native, 12,354 bars, SHA-256 12-char consistent, no resampling |
| Data quality | DHAN_HISTORICAL_DATA_QUALITY_REPORT.md | 2 anomalies documented & consumed-as-is (live-identical) |
| Full replay | HISTORICAL_REPLAY_REPORT.md | 40 signals / 23 trades / 44 orders / 44 fills / 23 positions; +20,921.06 |
| Indicators | INDICATOR_PARITY_REPORT.md | 6 shared streams, dedup 0 on 5m; values match reference |
| HTF mapping | HTF_PARITY_REPORT.md | native 15m/1h mapped per strategy |
| Signals | SIGNAL_PARITY_REPORT.md + parity_signal_harness | formula exact (dir_mismatch=0, trigsl=0 per strategy) |
| Trade lifecycle | TRADE_LIFECYCLE_REPLAY_REPORT.md | 21 closed + 2 open; SL 14, reversals 7; DB-vs-analytics 0 diff |
| DB lineage | DATABASE_LINEAGE_REPLAY_REPORT.md | no orphans/dups; FKs ON; position_id≠trade_id; 0 SL violations |
| API / frontend / WS | API_RECONCILIATION / FRONTEND_RECONCILIATION / WEBSOCKET_REPLAY_REPORT.md | bounded deterministic surfaces; LTP path clean |
| 4-strategy isolation | FOUR_STRATEGY_ISOLATION_REPORT.md | no cross-strategy leakage |
| Restart / recovery | RESTART_RECOVERY_REPLAY_REPORT.md | warmup bit-exact; restore tests green; real-data determinism bit-exact |
| Determinism | replay_determinism_test.py | 2 isolated runs → bit-exact normalized output |
| vs backtest | REPLAY_VS_BACKTEST_PARITY_REPORT.md | 40 vs 37 signals, documented native-vs-aggregate HTF delta; formula layer exact |

## Hard numbers
- Bars in window: 1,486 (5m 1,046 + 15m 349 + 1h 91, per strategy fast/mid/htf
  consumption 523/175/175/523).
- Service: 40 signals (20 LONG / 20 SHORT), 23 trades (21 closed, 2 open),
  44 orders, 44 fills (1:1, slippage=0), 23 positions, 7 reversal exits,
  14 STOP_LOSS, 150 events / 109 trade events.
- Net closed P&L **+20,921.06** (gold_01 +25,207.43; gold_02 +20,696.08;
  silver_01 -209.37; silver_02 -24,773.08). DB vs analytics: **0 diff, 0 count
  diff** per strategy.
- Integrity: duplicate-name columns all empty, FKs ON, no orphans, no
  cross-strategy rows, position_id≠trade_id, 0 SL violations.

## VERDICT
**VERIFIED — PASS**

The historical native-data replay is complete, deterministic, live-identical,
database-clean, and backtest-consistent. The finite 3-session window executed
the full production trading lifecycle end-to-end offline with no strategy-math
modification, no lookahead, bit-exact restart determinism, and exact analytics
reconciliation. Residual differences versus the reference backtest are fully
explained by the mandated native-vs-aggregate HTF construction (formula layer
exact; 3/4 strategy signal sets feed-insensitive) and are documented.

## Residual notes (non-blocking, for the record)
1. Docker-based evidence not executed (Docker CLI absent on host).
2. Two data anomalies (SILVERM 2026-09-03 09:00 corrupted OHLC; GOLDM/SILVERM
   2026-08-18 16:21 non-grid 5m bar) consumed as-is per live-identical policy.
3. Full regression suite run against the `indicators/shared.py` dedup change:
   **1226 passed, 44 skipped** — the last open verification item is CLOSED.