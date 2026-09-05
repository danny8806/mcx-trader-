# SIGNAL_CANDLE_FORENSIC_REPORT

Phase 41 — Every live signal with its triggering bar's candle context and indicator state.

## Evidence files

- `forensics/signal_forensics.json` — full per-signal records
- `forensics/signal_forensics.csv` — tabular view
- Raw stream: `parallel/crossover_replay.json`, `parallel/evaluation_stream.jsonl`

## Measured facts

- **35 live crossover signals** captured across the 4 strategies (gold_01 9, gold_02 8, silver_01 6, silver_02 12).
- **35/35 (100%)** joined to their triggering bar's candle in the evaluation stream (`candle_joined = 35`), each carrying `open/high/low/close/volume` and the strategy `state`, plus the fast/mid/slow mapped values and prev-bar references.
- Every signal's candle timestamps exist inside the window bounds (first 2026-09-02T09:00, last 2026-09-04T23:25).
- Indication cross states are real DEMA-3/ATR-6 crossings on completed bars — no fabricated rows; all 35 rows exist in the ranked chronological feed recorded by the crossover loggers installed on `_create_pending_signal` / `_create_reversal_signal`.

## Candle context per signal

Exact per-row candle OHLC (`open`, `high`, `low`, `close`, `volume`) + `state`,
`eval_state`, `fast/mid/slow` values, `is_reversal`, `trigger_price`,
`stop_price`, and `candle_joined` flag are in `signal_forensics.csv` (and
`signal_forensics.json`). See for example the gold_01 2026-09-02T17:20:00 SHORT
cross and its joined candle row.

## Consistency

- Signal timestamps == bar `start_ts` of the candle that produced them (no intra-bar or off-grid signals).
- `eval_state` reflects the lifecycle at signal time (flat / pending_* / *_position), consistent with the lifecycle gating described in `SIGNAL_COUNT_RECONCILIATION.md`.

## Status

**PASS** — 35/35 signals have complete, in-window, real candle + state context; no synthetic or orphan signal rows.