# FOUR-STRATEGY CROSSING-SIGNAL-CANDLE PARITY (fresh re-verification)

Cross-referenced three ways on the current code: LIVE engine (real strategy objects), independent backtest tracker (incremental DEMAATR + BacktestStyleHTFEngine), and the `_p1_lib` batch reference.

| check | result |
|-------|--------|
| S_GOLDM_gold_01(fast=5m)_candles | **PASS** |
| S_GOLDM_gold_02(fast=15m)_candles | **PASS** |
| S_SILVERM_silver_01(fast=15m)_candles | **PASS** |
| S_SILVERM_silver_02(fast=5m)_candles | **PASS** |

**VERDICT: ALL PASSED**
crossing candles emitted to CSV: 79; value mismatches: 0.
