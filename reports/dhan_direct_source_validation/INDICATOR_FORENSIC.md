# Indicator Forensics — INDICATOR_FORENSIC.md

## Symptom (persistent across v1/v2, now root-caused and fixed)
Shared 15m/1h streams appeared inflated: engine arrays 581/150 vs independent truth 291/75; gold 15m `n_mismatch=291`, silver 15m matched within window but `out_of_order=1`; `dedup_count` 2034/450.

## Root cause (proven empirically, before/after)
TradingEngine warmup fed HTF candles differently from fast candles:
- **Fast path** (`trading_engine.py:1061-1090`): fetch → convert to IST → **sort ascending** → **truncate to last `last_days` (5) trading dates**.
- **HTF path** (pre-fix, `trading_engine.py:1091-1103`): fetch → feed **RAW API order, untruncated** (all `fetch_days` = 14 days → 10 trading days → 581/150 bars).

Dump of Dhan's raw order (stream dump `P13_STREAM_DUMP.json`, pre-fix):
- gold 15m = 581 entries: 290 older-day bars first, then 291 (5-day) bars → audit compares first 291 slots → mismatch 291.
- silver 15m = 581 entries: 291 recent bars first (exactly matching truth: n_mismatch=0), then 290 older bars APPENDED → `out_of_order=1` triggered; **`_end_times` non-monotonic** → `bisect` mapping in `get_mapped_value` could resolve a stale older value for silver until a newer bar closes live. This is the one genuine latent correctness risk; fixed by ordering.
- `dedup_count` 2034/450 = shared-stream dedup correctly swallowing re-feeds from multiple strategy slots (gold_01 mid + gold_02 fast + gold_02 mid, etc.). No exact same-candle duplication existed.

## Fix applied (mission boundary: no strategy math / signal / SL / backtest semantics changed)
`trading_engine._warmup_from_rest` HTF loop now applies the SAME window truncation (last `last_days`) **and** ascending sort before feeding. Verified post-fix via stream dump:

| stream | engine | truth | n_extra | n_mismatch | out_of_order |
|---|---|---|---|---|---|
| gold 15m | 291 | 291 | 0 | 0 | 0 |
| gold 1h | 75 | 75 | 0 | 0 | 0 |
| silver 15m | 291 | 291 | 0 | 0 | 0 |
| silver 1h | 75 | 75 | 0 | 0 | 0 |

runtime_v3 indicator_audit: **all 12 checks ok=True**, `maxdev=0.0`, `n_mismatch=0`.

## Conclusion
Indicator math (DEMA/ATR) was always correct; the failures were (a) an audit-window mismatch (14d-fed vs 5d-truth) and (b) a feed-ordering defect (silver). Both resolved by the warmup-window/order fix. `dedup 2034/450` confirmed the shared-stream dedup is working exactly as designed (§11).