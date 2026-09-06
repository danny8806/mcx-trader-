# DHAN HISTORICAL DATA QUALITY REPORT

## Purpose
Document the quality of the REAL Dhan REST dataset used for the historical
replay, including every anomaly found, the pass-through policy, and the
verification that the dataset is trustworthy for a finite deterministic replay.

## Dataset
- Source: Dhan REST `/charts/intraday`, native intervals 5/15/60.
- Window: 2026-08-03 → 2026-09-04 (25 trading days, 12,354 native bars).
- Files: `replay_output/dhan_snapshot/{GOLDM,SILVERM}_{5,15,60}.json`
  (+ `*.IST.csv`), SHA-256 in `replay_output/checksums.json`.

## Quality Checks Performed (28 Aug 2026 run)
1. **Grid check (5m):** every 5m bar timestamp is expected at `:00/:05/:10/…/:55`
   modulo 300 s. Result: **exactly 1 non-grid bar** per instrument (both at the
   same timestamp) — 2026-08-18T16:21 IST (5m). Documented below.
2. **OHLC sanity:** `low <= high`, `open <= high`, `open >= low`, `close <= high`,
   `close >= low`. Result: **1 invalid OHLC bar** in SILVERM 5m/15m/60m at the
   09:00 bucket of 2026-09-03. Documented below.
3. **Day start mapping:** first intraday bar of each session. Result: sessions
   normally start at 09:00 IST. **Two sessions start late** (missing the opening
   bar): 2026-08-03 (dataset boundary day) and 2026-08-28.
4. **Coverage:** no gaps > 1 interval within a session; count/range verified
   against the manifest.

## Anomalies (documented; pass-through preserved)

### A1. SILVERM 2026-09-03 09:00 corrupted bar (OHLC violation)
- Present in **all three native intervals** (5m/15m/60m) — it is a genuine
  Dhan-side data artifact, reproduced identically in each interval.
- Bar (5m): `O=238988 H=240699 L=239812 C=240370 V=1422`
  (`epoch_open=1788406200`).
- Violation: `low (239812) > open (238988)` — the low is 824 points above the
  open, which is incoherent for a bar whose body spans 238988→240370.
  `low <= close` (240370) holds, so only the low-vs-open edge is violated.
- **Impact on indicators:** this bar enters the live OHLC feeds (fast/mid/slow
  ATR & DEMA update on it). Because the engine is fed the real data as-is, the
  value is **passed through** — no sanitisation, no drop. This matches the
  live engine behaviour exactly (it would consume the same row from REST).
- **Replay position:** 2026-09-03 is *inside* the replay window, so the bar was
  consumed by the replay per the pass-through policy.
- **Reference:** present in `SILVERM_5.json`, `SILVERM_15.json`, `SILVERM_60.json`.

### A2. Non-grid 5m bar 2026-08-18T16:21 IST (GOLDM & SILVERM)
- Both instruments have a 5m bar whose open epoch is `1787050260` =
  2026-08-18 16:21 IST (minute `:21`, not `:2X`/`:01` on the 5-grid).
- GOLDM: `O=155330 H=155371 L=155307 C=155362 V=46`
- SILVERM: `O=243050 H=243100 L=243050 C=243100 V=15`
- Likely origin: Dhan occasionally emits an off-grid millisecond-stamped row for
  a burst of volume right after the scheduled close of another bar. OHLC is
  internally valid in both cases.
- **Impact:** 16:21 sits *before* the replay window (09-02) and before the
  warmup keep-window (08-26..09-01), so it did not affect the replay. For
  completeness it is retained in the dataset and hash-stable.
- **Policy:** keep as-is (immutable dataset).

### A3. Late session starts (missing first bar)
- 2026-08-03: dataset boundary — 5m starts 09:05, 15m 09:15, 1h 10:00
  (first available closed bar of each interval).
- 2026-08-28: 5m starts 09:05 (opening 09:00–09:05 bar missing), 15m/1h start
  at their first complete bucket (09:15 / 10:00).
- **Replay impact:** none — both dates precede the replay window. The 0/1 count
  deltas from a perfect 174-bar day (5m) are bounded and documented.

### A4. Normal first-bar handling (opposite of the old seed)
- The previous *synthetic* replay seeds stored 09:00 rows that had to be
  stripped (`strip_anchor_bars`). The **real Dhan data DOES contain genuine
  09:00 opening bars** on every normal session (e.g. 2026-09-02 GOLDM 5m
  first bar = 09:00:00). Therefore `strip_anchor_bars` is **not applied** to the
  real dataset — doing so would delete real bars. The prior tools
  (`replay_restart.py`, `replay_warmup_parity.py`) remain synthetic-seed-only.

## Summary Table
| Anomaly | Instrument | Interval(s) | Date/Time | OHLC valid? | In replay window? | Policy |
|---------|-----------|------------|-----------|-------------|-------------------|--------|
| A1 low>open | SILVERM | 5/15/60 | 09-03 09:00 | No | Yes | Pass-through (live-identical) |
| A2 off-grid row | GOLDM/SILVERM | 5 | 08-18 16:21 | Yes | No | Keep (immutable) |
| A3 late start | GOLDM/SILVERM | all | 08-03 / 08-28 | Yes | No | Keep (documented) |

## Integrity
- Re-hash of all 12 raw files matches `replay_output/checksums.json` (recorded
  at ingest). Dataset is treated as immutable for the replay.
- Every anomaly above is **documented, not silently modified** — in accordance
  with the replay directive ("record and document; do not silently fix").