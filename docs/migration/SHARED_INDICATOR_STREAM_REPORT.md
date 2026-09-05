# SHARED_INDICATOR_STREAM_REPORT

Mission §7–§12. Status: **VERIFIED** — evidence-backed, full suite green.

## Objective

One incremental DEMA/ATR/DEMA-ATR stream per unique `(security_id, timeframe,
indicator configuration)`, calculated exactly once per candle, consumed
immutably by every strategy that subscribes. No per-strategy DEMAATR clones,
no global dataframe HTF mapping engine in the hot path.

## Previous architecture (the gap)

Each of the four `StrategyInstance`s owned **five** DEMAATR objects:

| Strategy | fast_indicator | mid_indicator | slow_indicator | mid_htf_state |
|---|---|---|---|---|
| gold_01 GOLDM_5M | GOLDM 5m | GOLDM 15m | GOLDM 1H | GOLDM 15m |
| gold_02 GOLDM_15M | GOLDM 15m | GOLDM 15m | GOLDM 1H | GOLDM 15m |
| silver_01 SILVERM_15M | SILVERM 15m | SILVERM 15m | SILVERM 1H | SILVERM 15m |
| silver_02 SILVERM_5M | SILVERM 5m | SILVERM 15m | SILVERM 1H | SILVERM 15m |

GOLDM 15m DEMA-ATR was therefore computed **three times** (gold_01
`mid_indicator.`+`mid_htf_state.indicator` and gold_02 `fast_indicator`),
GOLDM 1H up to four times, and the `mid_indicator`/`slow_indicator` values were
**never read** (pure waste). Each HTFState kept its own `_end_times`/`_values`
and its own DEMAATR, re-implementing the identical BacktestStyleHTFEngine
bisect mapping per strategy.

## New architecture (implemented)

```
NativeCandleRouter/CandleFetcher (native, closed candles only)
        |
        v
SharedNativeIndicatorEngine.indicator_engine   (TradingEngine)
        |
        +-- IndicatorStream (569003, 5m )      <- one DEMAATR each
        +-- IndicatorStream (569003, 15m)      <- shared by gold_01.mid +
        |                                          gold_02.fast + gold_02.mid
        +-- IndicatorStream (569003, 1h )      <- shared by all GOLDM
        +-- IndicatorStream (483080, 5m )
        +-- IndicatorStream (483080, 15m)
        +-- IndicatorStream (483080, 1h )
        |
        v
StrategyIndicatorView / StreamHTFStateView  (read-only views on StrategyInstance)
```

### `indicators/shared.py`
- `IndicatorSnapshot` — immutable frozen dataclass with every §10 field:
  security_id, timeframe, candle_start_ts, candle_end_ts, open/high/low/close/
  volume, dema, atr, dema_atr, previous_dema, previous_atr, previous_dema_atr,
  is_complete. Mutable access raises `FrozenInstanceError`.
- `IndicatorStream` — one shared `DEMAATR` per (security_id, timeframe, config).
  - `feed(open, high, low, close, end_ts=None, ...)` advances the single
    DEMAATR, maintains `_end_times`/`_values` (mapping arrays identical to
    HTFState), and publishes an immutable snapshot.
  - **Dedup by candle_end_ts** (§7): the same `end_ts` delivered twice is a
    no-op returning the same snapshot object (`_dedup_count`). This is what
    makes cross-strategy sharing safe — whichever subscriber handler runs
    first advances the stream, the second is deduplicated.
  - **Out-of-order detection** (§7): an `end_ts` older than the last accepted
    increments `_out_of_order_count`.
  - `get_mapped_value(fast_bar) -> HTFMappedValue` reproduces the exact
    `bisect_right(end_times, fast_bar.end_ts) - 1` algorithm of both
    HTFState and BacktestStyleHTFEngine. Cold streams return an
    unconfirmed HTFMappedValue (htf_value=None) so on_bar skips — only warm
    snapshots flow into signal logic.
- `StrategyIndicatorView` — minimal fast/mid/slow indicator surface
  (`.update(o,h,l,c,end_ts)`, `.value`, `._count`, `.reset`) forwarding to the
  shared stream.
- `StreamHTFStateView` — minimal HTFState surface (`.update(bar)`,
  `.get_mapped_value(bar)`, `.bar_count()`, `.last_value`, `.prev_value`)
  forwarding to the shared stream.
- `SharedNativeIndicatorEngine` — owns `{ (security_id, timeframe): stream }`.
  `bind_shared_indicators()` on each strategy replaces the five per-strategy
  slots with views; **on_bar/evaluation logic is untouched**.

### Engine wiring
- `TradingEngine.__init__` / `_init_strategies`: creates `indicator_engine`
  (guarded with `hasattr` so `__new__`-constructed test engines keep working)
  and binds each created strategy after construction.
- `_build_runtimes`' compat `engine.indicators` view now reads through the
  shared views (same public surface).

## Evidence

### New acceptance suite — `tests/fresh_audit/test_shared_indicator_engine.py`
10 tests, all passing:

| Test | § | Proves |
|---|---|---|
| `test_snapshot_is_immutable` | §10 | snapshot frozen; mutation raises |
| `test_stream_dedups_duplicate_candle_end_ts` | §7 | re-feed same end_ts → same snapshot, `_count`/bar_count unchanged, `_dedup_count==1` |
| `test_stream_detects_out_of_order` | §7 | older end_ts after newer → flag set |
| `test_snapshot_has_all_fields` | §10 | all documented fields populated post-warmup |
| `test_shared_matches_standalone_dema_atr` | §8 | stream value == standalone DEMAATR for 40 bars |
| `test_shared_mapping_matches_htf_state` | §11/12 | stream mapping == the replaced HTFState (htf_value & prev) |
| `test_shared_engine_stream_count_and_identity` | §8 | exactly 6 streams; get_or_create returns same object |
| `test_views_share_stream_object_and_feed_deduplicates` | §11 | two subscribers → same indicator object; value parity |
| `test_mapped_value_share_across_subscribers` | §12 | different views over one stream see identical data |
| `test_four_strategies_bind_to_six_shared_streams` | §8/§11/§12 | 4 real strategies → 6 streams; gold_02.fast is gold_01.mid (15m); shared 1H; dedup across strategies |

### Regression evidence
- Full suite: **1100 passed, 43 skipped, 3 warnings, 0 failed** (was 1090
  passed before this work; +10 = the new acceptance tests).
- `tests/fresh_audit`: 853 passed, 54 skipped, 0 failed.
- `tests/live_runtime_v2`: 167 passed (boot + runtime unchanged).

## Parity argument (why signals are unchanged)

1. The shared stream's one DEMAATR is fed the **same native candle sequence**
   each strategy previously fed its own DEMAATR/HTFState (identical bars,
   identical order — live, bar-closed events, and warmup replay).
2. EventBus dispatch order is strategy-subscription order
   (gold_01 → gold_02 → silver_01 → silver_02); for a shared 15m candle the
   first finshing handler advances the stream, later ones dedup. The advance
   count per bar is exactly one either way.
3. `get_mapped_value` math is byte-for-byte the same bisect both HTFState and
   BacktestStyleHTFEngine used.
4. Cold (unwarmed) streams return unconfirmed mapped values → `on_bar` skips,
   exactly as before.
5. No ship of strategy evaluation, crossover, pending/trigger, SL, reversal,
   or signal-creation code was modified.

## Remaining gaps (out of scope this step)

- §7 "NativeCandleRouter" as a first-class class: dedup is implemented at the
  stream feed boundary (idempotent by candle_end_ts) rather than as a separate
  router object. Out-of-order/incomplete detection lives on the stream.
- §39–40 BrokerEventRouter mapping evidence: deferred to the next step.
- §69 global-state code scan: not yet run for `current_*` identifiers.

## Verification commands

```
python -m pytest tests/fresh_audit/test_shared_indicator_engine.py -q      # 10 passed
python -m pytest tests -q                                                  # 1100 passed
```