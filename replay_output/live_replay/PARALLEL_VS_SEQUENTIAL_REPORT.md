# PARALLEL_VS_SEQUENTIAL

Phase 36 — Logical equivalence of the 4-strategy parallel replay and the per-strategy sequential replay.

## Method

Run 1: one engine, all four strategies, ranked chronological feed (parallel).
Run 2: four isolated engines, one strategy each, same feed (sequential).
Compare the three observable streams per strategy after removing nondeterministic
surrogate keys (`signal_id`, `trade_id`, `position_id`, `order_id`, `fill_id`,
`created_at`, `updated_at`, etc.):

1. in-memory signal stream,
2. crossover event stream (with candle + mapped values),
3. trade stream.

## Measured

| stream | gold_01 | gold_02 | silver_01 | silver_02 |
|---|---|---|---|---|
| in-memory signals (seq vs par) | identical | identical | identical | identical |
| crossover events (seq vs par) | identical | identical | identical | identical |
| trades (seq vs par) | identical | identical | identical | identical |

- Sequence counts match the parallel run exactly (signals 13/11/8/17; trades 9/7/6/9) while each engine was isolated (single strategy) — i.e., running all four in one engine produces **exactly** the same per-strategy signals and trades as running them in isolation.
- SHA-256 for each stream comparison is in `forensics/phase67_checksums.json` and `reconciliation/parallel_vs_sequential.csv` (`signals_identical` / `trades_identical` all True).
- Determinism (Phase 37): a second parallel run reproduced all four normalized stream hashes bit-exactly.

## Status

**PASS** — parallel and sequential architectures are logically equivalent per strategy; orchestrating the four strategies in-process causes no behavioral change.