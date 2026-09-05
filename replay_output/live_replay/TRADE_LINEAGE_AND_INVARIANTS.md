# TRADE_LINEAGE_AND_INVARIANTS

Phase 42 — Every trade with its full entry/exit signal lineage plus the lifecycle invariants, measured.

## Evidence files

- `forensics/trade_forensics.json` / `.csv`
- `forensics/reversal_invariant.json`
- `parallel/trade_replay.json`, `parallel/persisted_signal_replay.json`, `parallel/db_integrity.json`
- `reconciliation/live_vs_reference_trades.csv`, `trades_detail_parallel/sequential/reference.csv`

## Measured facts

- **31 live trades** (gold_01 9, gold_02 7, silver_01 6, silver_02 9); 28 closed, 3 open.
- Exit breakdown: **STOP_LOSS 14**, **long_reversal 8**, **short_reversal 6**, open 3.
- 31/31 trades carry a valid `entry_signal_id` that exists in the signals store (`trades_without_entry_signal = []`, `entry_sig_persisted True` in `trade_forensics`).
- 14/14 SL trades have **empty `exit_signal_id`** — the stop-loss is not a new signal (invariant verified in DB: `sl_invariant_violations = []`).

## Reversal signal reuse (measured)

Of 14 reversal exits:

- **11** `same_signal_follow_through` — the same signal object closes the old trade and opens the next opposite trade (exit_signal_id == next trade's entry_signal_id). See `reversal_invariant.json` cases.
- **3** `exit_only` — the reversal crossover closed the position but its armed breakout entry never re-filled, so the next trade came from a later signal:
  - gold_02 292dd011… (short_reversal, exit_sig 257cc5a2…)
  - silver_02 4238a504… (long_reversal, exit_sig 7f1f722b…)
  - silver_02 3c6875b2… (long_reversal, exit_sig 48d29846…)
  This is the engine's documented deferred-reversal semantics (`_create_reversal_signal` returns None; re-entry is a pending breakout that fires only on a later bar crossing the trigger). It is not a missing trade and not an unknown exit.

## Lineage/dedup invariants (measured in the DB forensic)

- `foreign_keys = ON` in the replay DB.
- duplicate trades/orders/fills/positions/pending_orders/broker_mappings: **0**.
- orphan rows (pending_orders/orders/fills/positions/trade_legs/trade_signal_link/trade_events/trade_snapshots pointing at missing trades): **0**. Fills without an order: **0**.
- `position_id == trade_id` collisions: **0** (position and trade IDs distinct).
- unknown broker orders / quarantine records: **0**.

## Status

**PASS** — every trade is signal-linked, dedup clean, orphan clean, SL-not-a-signal holds, reversal reuse is 11 same-signal + 3 explained exit-only.