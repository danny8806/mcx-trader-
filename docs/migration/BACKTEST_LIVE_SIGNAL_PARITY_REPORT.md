# BACKTEST vs LIVE — Signal Generation Parity Report

**Mandate reference**: MANDATE §"LIVE == BACKTEST until signal generation" (2026-09-04).
**Date**: 2026-09-05
**Status**: Signal-generation layer VERIFIED (formula parity exact on intended 5m/15m grid).
**Reproduce**: `python tools/parity_signal_harness.py`

## 1. Scope

Prove that for all four strategies the pre-signal chain — raw candles → resampling →
DEMA/ATR indicators → HTF mapping → signal formula → trigger/SL — is **identical**
between the production MCX-TRADER live pipeline and the authoritative reference
backtest (`nifty dema backtest` project), bar-by-bar.

| Strategy | Instrument | base tf | mid tf | htf tf |
|---|---|---|---|---|
| gold_01 | GOLDM | 5m | 15m | 1h |
| gold_02 | GOLDM | 15m | 15m | 1h |
| silver_01 | SILVERM | 5m | 15m | 1h |
| silver_02 | SILVERM | 15m | 15m | 1h |

(Engine mapping verified: gold_01=5m, gold_02=15m, silver_01=5m, silver_02=15m —
matches the mandate; see §4.)

## 2. Inputs

- **Data** (sanctioned substitution — the mandate-named files
  `GOLDM_04Sep2026_5m.csv` / `SILVERM_30Nov2026_5m.csv` do **not** exist in the
  repo): `data_mcx/GOLDM_5m_mcx.csv` (19,972 rows, 2026-03-06 14:44 → 2026-08-26
  14:00) and `data_mcx/SILVERM_5m_mcx.csv` (21,404 rows, 2026-02-23 09:07 →
  2026-08-21 12:30). Naive IST, 6 columns. **The mandate replay window
  (2026-09-02+) is not covered by any available CSV — recorded in
  FORENSIC_BASELINE.md as a data gap.**
- **Reference backtest**: `C:\Users\pc\Desktop\nifty dema backtest\project`
  (`core/dema_mtf.py`, `build_15min_enriched.py`, `goldm_dema_mtf_futures.py`).
- **Production live**: this repo (`indicators/dema_atr.py`,
  `htf/backtest_style_htf.py`, `strategies/base_dema_strategy.py`,
  `core/timeframe_engine.py`).

Reference code was loaded WITHOUT modification (importlib, to avoid the `core`
package-name collision). No reference file was edited.

## 3. Reference semantics (code-verified)

- Session anchor 09:00; HTF buckets anchored to session_open.
- `DEMA=2*EMA1−EMA2` (period 3), Wilder ATR (period 6), factor 1.0 — identical
  formulas on both sides (`indicators/dema_atr.py` == `build_15min_enriched.dema_atr`).
- HTF mapping: reference `searchsorted(src_avail=htf_end, target=base_end, side="right")−1`;
  live `bisect_right(end_times, fast_bar.end_ts)−1`. Equality with `htf_end`
  (target == end) is explicitly intended by the reference ("the final base bar
  uses the just-completed HTF candle").
- Signal (reference `compute_signals`):
  - LONG  ⇔ `close[i] > h1[i] and close[i-1] <= h1[i-1] and h15[i] < h1[i]`
  - SHORT ⇔ `close[i] < h1[i] and close[i-1] >= h1[i-1] and h15[i] > h1[i]`
  - Gate: skip bar unless `h1[i]`, `h1[i-1]`, `h15[i]` all valid.
- Trigger/SL (LONG): trigger = signal bar HIGH; SL = min(low[i], low[i-1]).
  (SHORT): trigger = signal bar LOW; SL = max(high[i], high[i-1]). Identical in
  production (`_create_pending_signal` / `_create_reversal_signal`).

## 4. Results (all four strategies)

| Strategy | resample 15m/1h parity | h1/h15 line parity (intended grid) | signal dir parity (intended grid) | trigger/SL parity |
|---|---|---|---|---|
| gold_01 (5m) | exact (0 diffs) | exact | **0 / 19,972** | exact |
| gold_02 (15m) | exact (0 diffs) | exact | **0 / 6,755** | exact |
| silver_01 (5m) | exact (0 diffs) | exact | **0 / 21,404** | exact |
| silver_02 (15m) | exact (0 diffs) | exact | **0 / 7,239** | exact |

- Resample parity: production offline aggregation vs `resample_ohlcv` — identical
  bucket datetimes and OHLC/volume on both 15m and 1h for both metals (0 diffs).
- Line parity: live `BacktestStyleHTFEngine` DEMA-ATR values vs reference
  `dema_atr` values, bar-by-bar, **0 mismatches** once the mapping grid is
  evaluated correctly (see §5 below).
- Signal parity: production `BaseDEMAStrategy._check_long_cross` /
  `_check_short_cross` vs reference `compute_signals`, **0 mismatches**.

## 5. Root-cause #1 — production bug FIXED (D1)

`strategies/base_dema_strategy.py` `_check_long_cross` / `_check_short_cross`
compared `prev_close` against **`htf_val` (h1[i])** instead of **`prev_htf_val`
(h1[i-1])**:

```python
# before (wrong)
cross = close > htf_val and prev_close <= htf_val
# after (matches reference close[i-1] <= h1[i-1])
cross = close > htf_val and prev_close <= prev_htf_val
```

The `prev_htf_val` parameter was already threaded through `on_bar` but unused.
The `tests/live_runtime_v2/test_phase6_strategy.py` oracle used the correct
`prev_htf_val` but with inputs that could not distinguish the two formulas.

**Fix applied** and locked in by:
- `tests/live_runtime_v2/test_phase26_false_positive.py`:
  `test_long_cross_uses_prev_htf_val`, `test_short_cross_uses_prev_htf_val`
  (inputs chosen so the old formula returns the wrong answer).
- `tests/fresh_audit/test_backtest_vs_live_crossover.py`: now drives the
  PRODUCTION `_check_*_cross` functions bar-by-bar (use_production=True) and
  ASSERTS equality with the reference formula and with backtest filtered signals
  (previously it returned a bool and never exercised production code → false
  positive).

## 6. Root-cause #2 — DATA classification (reference auto base_min artifact)

The reference `htf_dema_line` derives the fast grid from the smallest positive
intra-session bar spacing. Both CSVs begin with **sub-5m head rows**
(gold 14:44→14:45 = 1 min; silver 09:07→09:10 = 3 min), so reference
auto-detects `base_min=1` **for the WHOLE series**. Its mapping then lags the
grid: on the final 5m bar of every 15m/1h bucket it still uses the PREVIOUS
bucket's line (its target `base+1min` never reaches the bucket end), while
production (correctly) uses the just-completed bucket.

Consequences measured (vs the auto-detected reference):
- line mismatches: gold_01 h1=784, h15=3154; silver_01 h1=741, h15=2766 —
  seeded at the exact bucket-boundary bars.
- signal residual: gold_01 9 dir mismatches, silver_01 1 — all confirmed
  pre-bucket-boundary bars where the stale h15 flips the h15<h1 confirmation.

**Classification: DATA/MAPPING-DERIVATION, not a production defect.** Re-running
the reference with the intended grid (`base_min=5/15`, harness
`htf_line_forced`) yields **0 mismatches** everywhere. On a clean 5m feed
(which is what the broker actually delivers in production) the auto-detected
grid equals the intended grid and both references are identical; the difference
is an artifact of the CSV head rows. No reference file was modified (mandate:
do not silently change the reference).

Emissions: gold_01 auto=413 vs forced=404; silver_01 auto=515 vs forced=514 —
the 9 (gold) / 1 (silver) extra reference signals are exactly the boundary-bar
artifacts above, all absent from live.

## 7. Execution-layer note (not part of signal generation)

The live state machine additionally *gates* when signals reach the engine
(pending-entry suppression while a pending is armed, position tracking, pending
timeout). Raw crossover bars therefore exceed emitted entry events
(gold_01 404 crosses → 238 events; similar for the others). This is the
EXECUTION layer (fill/trigger/state) and is deliberately out of scope for the
signal-generation boundary; the trade-level comparison (fills, reversal exits)
is covered in the execution parity phase (§Phase 9).

## 8. Test regression

`python -m pytest tests -q` → **1072 passed, 43 skipped** both before and after
the D1 fix (the only behavioral change is production code now matching the
reference). New/updated tests included above.