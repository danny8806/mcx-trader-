# FINAL REPORT — Dhan Direct-Source Completion-Candle Fix (MCX warmup/backfill)

## Verdict
**VERIFIED**

Scope: the Dhan-direct-source authoritative-completed-candle contract for
`TradingEngine._warmup_from_rest` (MCX, native 5m/15m/1H). Verified by a
finite focused suite (`tests/test_warmup_latest_available.py`, 14/14), the
full regression (`pytest -q`: **1240 passed, 44 skipped, 0 failed** — baseline
was 1226 passed / 44 skipped), and one live closed-market runtime run
(`test_id clm-20260906-162834`: **VERIFIED_CLOSED_MARKET_LIVE_RUNTIME**,
failures=[], errors=[]).

## Sections
- `WARMUP_COMPLETED_CANDLE_REPORT.md` — exact root cause, mechanism, before/after
  counts, acceptance-matrix mapping, forensics/watermark contract, live evidence,
  known limitations, and the strict acceptance criteria.
- `indicator_audit.json` in `runtime_v5_warmup_fix/` — 12/12 stream checks,
  `n_mismatch=0`, `max_dev=0.0`, engine arrays 870 / 290 / 75.
- `WARMUP_FORENSICS.json` in `runtime_v5_warmup_fix/` — per-(symbol, interval)
  real-Dhan forensics + watermarks (source `DHAN_REST`).
- `rest_probe.json` in `runtime_v5_warmup_fix/` — real fetch 1741/581/150 with
  echo rejection and SILVERM anomaly recorded.
- `SUMMARY.json` / `ERRORS.json` / `restart_test.json` in `runtime_v5_warmup_fix/`.

## Highlights
1. **Defect (proven on live Dhan)**: warmup fed every returned bar — the
   2026-09-03 post-close flat echo (23:30 start, o=h=l=c=155930) inflated fed
   counts to 871/291; a mid-session forming candle would be dedup-frozen
   (identity `(security_id, timeframe, end_ts)`), poisoning DEMA/ATR and
   blocking the final candle; and the native 23:00 1H was misjudged as
   forever-forming by its grid end (00:00).
2. **Fix**: shared classifier `data/dhan/candle_validation.py` (completion,
   start-based session window 09:00 ≤ open < 23:30, OHLC validity;
   `effective_end_ts` caps grid end at the session-close instant so the
   23:00 1H is preserved as the latest available completed candle);
   `_warmup_from_rest` rewritten to fetch→classify→feed only completed bars,
   with per-key forensics + latest-completed watermark; the audit truth uses
   the same completed-only predicate.
3. **Before/after fed counts (real Dhan, 5 trading days)**: 5m 871→870,
   15m 291→290, 1H 75→75 (unaltered — 23:00 preserved, 0 rejections).
4. **Weekends/Sunday/Monday-pre-open**: latest available trading candle wins
   (unit matrix + semantic proof); restart is re-backfilled every boot.
5. **SILVERM `o < l` bar**: recorded (rest_probe `ohlc_bad_count=1`), never
   repaired — real Dhan OHLC preserved unchanged.
6. **No unrelated changes**: no strategy math, DEMA/ATR formulas, crossover,
   SL/reversal, backtest, or resampling changes. Native 5m/15m/1H preserved.

## Boundary respected
Paper/read-only only; no synthetic candles; no fabrication; finite verification
(no uncontrolled replay loop); no live order endpoints.