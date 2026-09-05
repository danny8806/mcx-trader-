# FOUR_STRATEGY_ISOLATION

Phase 47 — All four strategies run inside the single shared engine; cross-strategy contamination checks.

## Evidence files

- `parallel/db_integrity.json` (cross_strategy section)
- `parallel/full_replay.json` (all strategies, one engine)

## Measured facts

In the single-engine parallel replay, all four strategies ran simultaneously against the shared indicator engine and the shared paper-execution/persistence pipeline. Contamination checks across every lineage edge returned **zero rows**:

| edge | check | result |
|---|---|---|
| orders ↔ trades | `order.strategy_id != trade.strategy_id` | 0 |
| fills ↔ trades | `fill.strategy_id != trade.strategy_id` | 0 |
| fills ↔ orders | `fill.strategy_id != order.strategy_id` | 0 |
| positions ↔ trades | `position.strategy_id != trade.strategy_id` | 0 |

Per-strategy account: signals 13/11/8/17, trades 9/7/6/9 — each strategy's objects live in its own namespace; no order/fill/position leaks across strategies.

## Status

**PASS** — zero cross-strategy objects at any lineage edge.