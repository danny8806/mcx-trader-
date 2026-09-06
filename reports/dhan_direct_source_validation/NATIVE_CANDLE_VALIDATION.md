# Native Candle Validation — NATIVE_CANDLE_VALIDATION.md

## Path traced (P13)
Dhan REST `POST /v2/charts/intraday` → `rest_client._to_candles` (`[ts,o,h,l,c,v]`) → adapter `fetch_historical_candles` → native `Bar`s (engine warmup `trading_engine._warmup_from_rest`) → shared indicator streams → strategy inputs / DB.

## Integrity (verified over 7-day window, runtime_v3 rest_probe)
- Loaded candles per (instrument × TF): GOLDM 871/291/75, SILVERM 871/291/75.
- `dup=0` (no duplicate candle identities), `monotonic=True` for all streams.
- GOLDM `ohlc_bad=0` across all TF; SILVERM = exactly 1 bar per TF (09:00 IST 2026-09-03), attributed to Dhan server data — see SILVERM_DATA_FORENSIC.

## NativeRouter guard (§7)
`data/native_router.py` enforces: identity `(security_id, timeframe, candle_end_ts)`; duplicates dropped once; out-of-order dropped; incomplete candles never published as completed. Stats exposed (`published/deduplicated/out_of_order/incomplete_rejected`).

## Candle → indicator step
`SharedIndicatorStream.feed()` (`indicators/shared.py:115-193`): dedup by end_ts; one DEMA/ATR advance per unique candle identity; `n_mismatch=0` for all 12 stream checks in runtime_v3 (see INDICATOR_FORENSIC).