# FOUR-STRATEGY HTF DEMA-ATR MAPPING PARITY (fresh re-verification)

Re-verified from a cold CURRENT-code engine fed the real 870-bar LAST5 stream; reference math in `_p1_lib` (independent).

| check | result |
|-------|--------|
| M1_GOLDM_15m_bucket_ohlcv | **PASS** |
| M1_GOLDM_1h_bucket_ohlcv_keepall | **PASS** |
| M2_GOLDM_5m_raw_ohlcv_identity | **PASS** |
| M3_GOLDM_dema_atr_line_values_5m15m1h | **PASS** |
| M3_GOLDM_engine_indicator_final_values | **PASS** |
| M4_GOLDM_gold_01(fast=5m)_mapped_values | **PASS** |
| M4_GOLDM_gold_02(fast=15m)_mapped_values | **PASS** |
| M1_SILVERM_15m_bucket_ohlcv | **PASS** |
| M1_SILVERM_1h_bucket_ohlcv_keepall | **PASS** |
| M2_SILVERM_5m_raw_ohlcv_identity | **PASS** |
| M3_SILVERM_dema_atr_line_values_5m15m1h | **PASS** |
| M3_SILVERM_engine_indicator_final_values | **PASS** |
| M4_SILVERM_silver_01(fast=15m)_mapped_values | **PASS** |
| M4_SILVERM_silver_02(fast=5m)_mapped_values | **PASS** |

**VERDICT: ALL PASSED**
raw 5m bars consumed: 1740; 15m buckets: 580; 1h buckets (keep-all incl. 23:00 partial): 150; strategy fast-bar mappings checked: 2300; signals generated during capture: 363.
