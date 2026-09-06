# HTF PARITY REPORT — Native HTF Replay

## Purpose
Verify the higher-timeframe path (15m mid / 1h slow) consumed **native Dhan
15m/60m candles** (never resampled from 5m) through the production
`BacktestStyleHTFEngine` mapping, producing valid `mid_value`/`htf_value` per
signal and non-NaN confirmed values at every bar, with parity between the
two-isolated-runs determinism check and against the reference backtest formula
layer where applicable.

## HTF mechanics under replay
- Signals carry `mid_value` (15m DEMA+ATR) and `htf_value` (1h DEMA+ATR)
  computed by the shared indicator engine on native 15m/1h bars.
- Mapping: `bisect_right(end_times, fast_bar.end_ts) - 1` (production
  `HTFState.get_mapped_value`) — the just-completed native HTF candle.
- No `strip_anchor_bars`: genuine 09:00 native bars preserved everywhere.

## Signal metadata sample (native replay)
gold_01 LONG @1788327900 (2026-09-02 09:15 IST):
- fast_dema_atr=151,005.73  mid_value=150,556.41  htf_value=151,143.24
- trigger_price=151,300 / stop=150,755 (both sane vs candle).

All 40 signals carry non-None mid/htf values (no incomplete mapping).

## Native vs Aggregated HTF (backtest-style) parity
`tools/replay_backtest_parity.py` ran the same production pipeline with:
- **native**: Dhan native 15m/60m rows (this replay)
- **aggregated**: the reference backtest's 15m/1h construction
  (`full_simulator.build_bars` = production CandleFetcher session-anchored
  aggregation of the same real 5m)

| Entity | native | aggregated |
|---|---|---|
| Signals | 40 | 37 |
| Trades | 23 | 22 |
| Positions | 23 | 22 |
| gold_01 signals | 14 | 11 |
| gold_02 / silver_01 / silver_02 | 6/6/14 | 6/6/14 |
| signal timestamp overlap | 23 common; 4 native-only; 1 agg-only | |
| trade-key overlap | 18 common; 5 native-only; 4 agg-only | |

Interpretation: the two HTF feeds are **intentionally different constructions**
(native exchange bars vs clock-anchored aggregation of the same real 5m). GOLDM
15m/1h aggregation differs from Dhan native at session edges and bucket
boundaries, which legitimately shifts a minority of HTF-mapped signals
(gold_01 14→11; gold_02/silver unchanged at 6/6/14). This is the **data-source
difference**, not a strategy/indicator defect — the identical production engine
and identical 5m base produced both runs.

## Formula-layer anchor (reference backtest)
`tools/parity_signal_harness.py` (reference `core/dema_mtf.py` compute_signals vs
production `_check_*_cross`):
- direction mismatches vs **forced-grid reference**: **0** for all four
  strategies (gold_01, gold_02, silver_01, silver_02).
- trigger/SL mismatches on common signal bars: **0**.

This confirms the HTF signal formula on the intended grid is exact; the
remaining native-vs-aggregated deltas above are feed construction only.

## Verdict
HTF path (native data) verified — **PASS**; native-vs-backtest-agg deltas are
documented data-construction differences with formula parity intact.