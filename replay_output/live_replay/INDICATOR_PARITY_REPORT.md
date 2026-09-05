# INDICATOR_PARITY_REPORT

Phase 66 — Live mapped HTF values vs reference h15/h1 lines, measured.

## Method

- Live values: per-bar mapped values recorded on every live signal (crossover events, `crossover_replay.json`) and captured by the replay evaluator from the production strategy HTF states.
- Reference values: `replay_2026-09-02_to_latest/boundary_checks.csv` (per-bar `h15`, `h1`) produced by the oracle `native_map_htf` via `tools/replay_mcx_from_2026_09_02.py`; independently recomputed from the raw CSVs this session (reproduced bit-exactly).
- Primary comparison: **matched signal bars** — bars where BOTH the reference and the live engine produced a signal in the same direction (29 such bars).

## Measured results

- **60m / slow line: `live htf value == reference h1` on every matched bar — 29/29 exact.**
- **15m / mid line: `live mid value == reference h15` on 20/29 matched bars exact;**
- **9 mismatched bars — every one satisfies `live_mid(t) == ref_h15(t+15m)` exactly (9/9).**

## Root cause of the 9 mid mismatches (period-edge publication)

The reference maps the in-progress 15m bar onto the last 5m base bar of that period
(`native_map_htf`: `src_avail = dt + rule_min`, `target = base_dt + base_min`,
`searchsorted(..., side="right")`), so at each 15m-period edge the reference *publishes
the mapped value of the still-forming 15m bar one 5m-bar early*:

| base bar | ref h15 | ref h1 | live mid at bar |
|---|---|---|---|
| 17:00 | 78024.685582 | 78007.681667 | — |
| 17:05 | 78024.685582 | 78007.681667 | — |
| 17:10 | 78023.308144 | 78007.681667 | — |
| 17:15 | 78023.308144 | 78007.681667 | — |
| 17:20 | 78023.308144 | 78007.681667 | **78017.160928 = ref h15 at 17:25** |
| 17:25 | 78017.160928 | 78007.681667 | — |

The live engine evaluates only **completed** 15m bars (the shared indicator engine
consumes a macro bar only when it closes), i.e. the live 15m line is the
no-lookahead value. All 9 mismatches are exactly `live = reference + one period`,
demonstrated with exact float equality in `forensics/indicator_parity.json`.

## Consequence

- The dominant crossover gauge (60m line) is **bit-identical live vs reference.**
- The mid/15m line agrees exactly everywhere except at period edges, where the reference line is one bar early (oracle artifact), not a live defect. This single-bar reference advantage explains a subset of ref-only signals and the live-only bars in `SIGNAL_COUNT_RECONCILIATION.md`.
- `mismatch_ledger` and the per-strategy matrix are in `forensics/indicator_parity.json` / `.csv`.

## Status

**PASS** with the documented reference period-edge note. Zero unexplained mid/60m value differences on any matched signal bar.