# Four-Strategy Validation — FOUR_STRATEGY_VALIDATION.md

## Engine topology (runtime_v3, authenticated, market CLOSED)

| strategy_id | instrument | fast | mid | slow | enabled | bars_processed* |
|---|---|---|---|---|---|---|
| gold_01 | GOLDM | 5m | 15m | 1h | true | 0 |
| gold_02 | GOLDM | 15m | 15m | 1h | true | 0 |
| silver_01 | SILVERM | 15m | 15m | 1h | true | 0 |
| silver_02 | SILVERM | 5m | 15m | 1h | true | 0 |

\* `bars_processed` stays 0 in a closed market (no live candle events) — restart gate uses it, see RESTART_RECOVERY.

## Shared streams (gold: fast_15 == mid_15 shared; silver likewise)
Warmup-dump evidence: identical stream objects shared across strategy slots:
- gold 15m stream serves gold_01.mid + gold_02.fast + gold_02.mid.
- silver 15m stream serves silver_01.fast + silver_01.mid + silver_02.mid.
- 5m streams serve gold_01.fast / silver_02.fast.

Each `(security_id, timeframe)` DEMA-ATR calculated ONCE (`indicators/shared.py`), `dedup_count` proving refeeds collapsed.

## Stream integrity (runtime_v3 indicator_audit — all PASS)

| security:tf | engine | truth | n_mismatch | out_of_order | maxdev |
|---|---|---|---|---|---|
| 569003:5m | 871 | 871 | 0 | 0 | 0.0 |
| 569003:15m | 291 | 291 | 0 | 0 | 0.0 |
| 569003:1h | 75 | 75 | 0 | 0 | 0.0 |
| 483080:5m | 871 | 871 | 0 | 0 | 0.0 |
| 483080:15m | 291 | 291 | 0 | 0 | 0.0 |
| 483080:1h | 75 | 75 | 0 | 0 | 0.0 |

## Identity mapping
`security_id ≠ strategy_id` honored: strategies keyed by ids; security binding via `bind_shared_indicators` → `SharedNativeIndicatorEngine.get_or_create(security_id, timeframe)` (`strategies/instance.py:136-169`).

Four strategies run on ONE engine, independent runtimes (`tests/new_architecture/test_strategy_runtime_isolation.py` covers parity).