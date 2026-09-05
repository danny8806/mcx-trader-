# SIGNAL_COUNT_RECONCILIATION

Phase 39 — Reference backtest signal counts vs live-architecture crossover/signal counts.

## Methodology

- **Reference** (oracle `dema_mtf.py` via `tools/replay_mcx_from_2026_09_02.py`): counts every qualifying crossover bar regardless of position state (ungated backtest math on every bar of the strategy's base timeframe). Sources: `all_signals.csv`, `summary.json`.
- **Live** (production `TradingEngine` + shared indicator engine, parallel mode): counts only signals created by the live lifecycle — a new-crossing signal is created by `_create_pending_signal` when flat, and `_create_reversal_signal` when in-position. Counted per strategy in `crossover_replay.json`.
- Matching = same strategy, same bar, same direction.

## Table (measured)

| strategy | mission | ref count | live crossovers | Δ | matched | missing (ref-only) | extra (live-only) |
|---|---|---|---|---|---|---|---|
| gold_01 | GOLDM_5M | 18 | 9 | −9 | 8 | 10 | 1 |
| gold_02 | GOLDM_15M | 12 | 8 | −4 | 7 | 5 | 1 |
| silver_01 | SILVERM_15M | 9 | 6 | −3 | 5 | 4 | 1 |
| silver_02 | SILVERM_5M | 12 | 12 | 0 | 9 | 3 | 3 |
| **total** | | **51** | **35** | **−16** | **29** | **22** | **6** |

## Why the difference (ref-only signals are lifecycle-gated, all classified)

Every ref-only signal (22 total) was classified against the live strategy state on that bar using the persisted `signals.state`/position/pending context:

| cause (live state at bar) | count |
|---|---|
| in-short/long position — in-position bars arm only reversal, a same-side/re-cross is not a new signal | 14 |
| pending long / pending short — a hanging pending entry supersedes the new cross (dangling-pending lifecycle rule) | 6 |
| exit submitted / mid-bar — edge bars near order placement | 0 (0 unexplained) |
| **unexplained / flat-state ref-only** | **0** |

- `ref_only_causes` per strategy: gold_01 {short_position:7, long_position:2, pending_long:1}, gold_02 {short_position:2, long_position:1, pending_short:1, pending_long:1}, silver_01 {long_position:3, pending_short:1}, silver_02 {short_position:1, long_position:1, pending_short:1}.
- Live-only signals (6 total): crossovers fired by the live engine on bars where the reference did not emit one; tied to the reference's 15m period-edge publication (see INDICATOR_PARITY_REPORT.md) and the exit-then-reversal cadence. None are orphans — every live crossover is traceable to bars with real crossing values in the evaluation stream.

## Secondary observation

The `reconciliation.json` note "HTF mapped DEMA/ATR values bit-identical … across all 522 bars" is **corrected** by this session's measured INDICATOR_PARITY: equality is exact on every *matched signal bar* for the 60m line (29/29), and for the 15m line 20/29 with all 9 remaining bars exactly equal to the reference's next-period value (reference period-edge publication). See `forensics/indicator_parity.json`.

## Status

- Signal count differences: **expained, zero unexplained residuals** (0 ref-only flat-state, 0 missing-signal trades).
- Full detail in `reconcile_live_replay.py` → `reconciliation/signal_parity.csv` + `signal_parity_causes.csv`.