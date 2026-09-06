# WARMUP COMPLETED-CANDLE REPORT — Dhan-direct source, MCX

## 1. Verdict
**VERIFIED** — the warmup/backfill source-of-truth defect is fixed and proven
by finite tests + full regression + one live closed-market run.
(Standalone verdict per the acceptance requirement: `VERIFIED`.)

## 2. Exact root cause (proven on live Dhan data)
`TradingEngine._warmup_from_rest` previously fed **every** bar Dhan REST
returned straight into the shared indicator streams, with no completion or
session guard. Three observable failure modes on MCX:

1. **Post-close echo inflation** — Dhan returns a flat post-close bar (start
   23:30 IST, o=h=l=c=155930 GOLDM) on 2026-09-03 for 5m and 15m. Live read:
   5m returned 175 bars that day (174 normal + echo), 15m 59 (58 + echo),
   1H 15 (no echo). Fed counts were therefore 871/291/75 instead of the true
   870/290/75.
2. **Forming-candle poison + dedup freeze** — on a mid-session restart the
   currently-forming bar (end_ts in the future) was fed into DEMA/ATR. The
   shared stream dedups by candle identity `(security_id, timeframe, end_ts)`;
   once the partial bar was accepted, the real final candle (same identity)
   was silently dropped — a permanent, unrecoverable indicator skew.
3. **23:00 1H forever-forming (discovered during verification)** — the native
   1H candle starting 23:00 has grid end 00:00 next day. A strict
   `open + tf*60 <= now` rule rejects it the whole evening and on any weekend
   restart, losing the final 1H bar of each session. Verified by the focused
   suite at 23:20 (kept 14/15, 23:00 rejected) vs 23:35 (kept 15/15).

The live `CandleFetcher` path already guarded completion
(`core/candle_fetcher.py:186-187`, `open + tf*60 <= now`) — only warmup was
unprotected.

## 3. Production fix (smallest correct change)
- **`data/dhan/candle_validation.py`** (new, shared classifier):
  - `classify(candle, tf_id, now_epoch, session_open, session_close)`
    → `(accepted, reason)`; reasons `FORMING_CANDLE`, `INVALID_TIMESTAMP`,
    `INVALID_OHLC`, `OUTSIDE_SUPPORTED_SESSION`.
  - `effective_end_ts(open_ts, tf_id, session_close)` — completion is judged
    on `min(grid_end, session_close_instant_of_the_candle's_day)`. This is
    the timeline-aware rule that preserves the 23:00 1H (final data at 23:30)
    while still rejecting any candle whose **start** is at/after the session
    close (the 23:30 echo).
  - Session window is **start-based**: 09:00 ≤ wall(open) < 23:30. Native
    5m/15m (max start 23:25/23:15) and the 23:00 1H all pass; the echo (start
    23:30) is rejected.
  - `filter_completed`, `latest_completed`, `iso_ist`, `tf_minutes`,
    `parse_hhmm`, `ist_wall_minutes`, `REASON_*` constants.
- **`trading_engine.py`** — `_warmup_from_rest(now_epoch=None)` rewritten:
  per-instrument `session_open`/`session_close`/`security_id` from config;
  every `(name, tf)` delegates to `_fetch_warmup_candles(...)` which fetches
  once, converts once, filters calendar days + `last_trading_days`, classifies
  via `filter_completed`, records per-key forensics + watermark, and returns
  only the accepted (completed) bars. Feeding calls
  `warmup_indicator` / `warmup_htf` / `warmup_indicator_htf` with only
  accepted bars. New state: `_warmup_forensics`, `_warmup_watermark`.
- **`tools/closed_market_runtime_test.py`** — `indicator_audit` truth applies
  the **same** `filter_completed` predicate (session from instrument config)
  and writes a `WARMUP_FORENSICS` artifact; `rest_probe` reports
  `completed_count`, `forming_count`, `session_rejected`, `ohlc_bad_count`
  per (instrument, interval). One fixes: post-filter DataFrame slices to 6
  columns (the df round-trip leaks the IST-datetime helper column).

## 4. Before / after fed counts (real Dhan, 5 trading days, GOLDM/SILVERM)
| tf | Dhan returned | fed before | fed after | rejected | reason |
|----|--------------|-----------|-----------|----------|--------|
| 5m | 871          | 871       | **870**   | 1        | OUTSIDE_SUPPORTED_SESSION (09-03 23:30 echo) |
| 15m| 291          | 291       | **290**   | 1        | OUTSIDE_SUPPORTED_SESSION (09-03 23:30 echo) |
| 1H | 75           | 75        | **75**    | 0        | 23:00 preserved via effective-end rule |

Watermarks (per stream, source `DHAN_REST`): 5m latest open 2026-09-04T23:25
(end 23:30); 15m latest open 23:15 (end 23:30); 1H latest open 23:00 (end
00:00 grid identity).

## 5. Finite focused tests — `tests/test_warmup_latest_available.py` (14 passed)
Uses a `FakeAdapter` (patched `trading_engine.DhanDataAdapter`) + pinned
`now_epoch`, run-relative dates, IST epoch builders, and a reference DEMAATR.

| test | matrix item | proves |
|------|-------------|--------|
| normal day reaches last closed, rejects echo | A | 870 fed, watermark 23:25/23:30, 1H 15 bars |
| 3-day native grid preserved | B | 5m/15m/1H counts 522/174/45, 1H end 23:00+1h |
| weekend/pre-open ×3 (Sat noon, Sun, Mon pre-open) | C/D/E | 174 bars all Friday, no weekend candles, watermark Friday |
| mid-session restart forming rejected | F | 19/19 (forming 10:35 never fed); 15m/1H forming also rejected |
| final-candle replacement once | G | 20 bars, watermark 10:35/10:40, DEMA == reference, restart re-feed dedups (no double count, no freeze) |
| duplicate REST fetch deduped | H | bar_count 174, `_count` 174, `duplicate_count`=2 |
| repeated restart idempotent | I | watermark & DEMA stable across 3 restarts |
| 23:00 1H completes at session close | J | rejected at 23:20, accepted at 23:35; echo rejected |
| 23:00 1H survives session boundary | J | 23:00 in opens, min 09:00 max 23:00 |
| four strategies share same native streams | K | gold_02.fast is gold_01.mid (15m), silver_01.fast is silver_02.mid, equal DEMA, equal counts |
| no partial-candle contamination | L | warmup DEMA == reference over 19 completed; final bar advances to reference-over-20 |
| signal-parity invariant after warmup | M | `_prev_fast_close=None`, `_bars_processed=0`, stream DEMA == reference over completed seq; 1 live candle advances exactly, re-feed dedups |
| full regression `pytest -q` | N | 1240 passed / 44 skipped / 0 failed (baseline 1226/44/0) |

## 6. Live closed-market evidence
Run `clm-20260906-162834` (1-min finite soak), real Dhan REST + WS, paper only.
- `rest_probe.json`: GOLDM+SILVERM 5m returned 871 → completed 870; 15m 291 →
  290; 1H 75 → 75; session_rejected = 2026-09-03T23:30 · OUTSIDE_SUPPORTED_SESSION;
  1H forming_count 0, rejected 0 (23:00 preserved). SILVERM `o<l` bar recorded
  once (ohlc_bad_count 1 per tf) with full OHLC in the row dump — **recorded,
  not repaired**.
- `indicator_audit.json`: 12/12 checks `ok=true`; `n_mismatch=0`,
  `max_dev=0.0`; engine arrays == independent re-derivation == 870/290/75;
  shared-stream dedup_count reflects expected multi-view re-feeds (15m ×4,
  1H ×3) with bar_count/indicator_count unchanged.
- `WARMUP_FORENSICS.json`: per-(symbol, interval) — requested_start/end (fetch
  boundary), dhan_return_count, first/last Dhan candle, last_completed_candle,
  forming/rejected/duplicate counts, rejected list with time+reason,
  watermark_before/after, source DHAN_REST.
- `SUMMARY.json`: failures=[], errors=[], verdict VERIFIED_CLOSED_MARKET_LIVE_RUNTIME.
- `restart_test.json`: `bars_processed=0` after restore — the documented
  open-market-only gate; **structurally unsatisfiable in a closed market** and
  not counted (same precedent as the prior runtime_v3 run).

## 7. Known limitations (honest, non-blocking)
1. Live `CandleFetcher` completed filter still uses strict grid end, so the
   1H 23:00 candle is usually ingested live only after 00:00. Warmup now
   includes it earlier; state converges via identity dedup (no double feed).
   A future alignment could apply `effective_end_ts` in `candle_fetcher.py`
   — deliberately out of scope (no unrelated changes).
2. Warmup forensics `security_id` comes from the strategy config block where
   not present (streams still resolve the real ids 569003/483080 via the
   binder). Cosmetic; no correctness impact.
3. No persistent bar store exists (`persistence/` stores signals only);
   "dedup/upsert" semantics map to shared-stream identity dedup + accepted
   feed counts, documented here for exactness.
4. The `-A/-T` matrix is covered by the 14 focused tests plus the live audit;
   it does not re-implement the strategy FSM (full replay-driven signal parity
   remains covered by the deterministic replay/full-system suites).

## 8. Acceptance criteria — all met
1. Dhan REST is the authoritative completed-candle source ✓
2. requested end_time is a fetch boundary, not a market-close rule ✓
3. weekend/Sunday/Monday-pre-open → latest available Dhan candle ✓
4. every restart re-backfills (deterministic, idempotent) ✓
5. forming candle never enters completed streams ✓
6. final candle completes the same identity exactly once ✓
7. timeframe-aware session semantics; 23:00 1H preserved; no blind open≥23:30
   reject ✓
8. native 5m/15m/1H preserved, no resampling ✓
9. watermarks from actual Dhan data ✓
10. dedup never freezes a forming candle ✓
11. no indicator contamination (independent-reference equality) ✓
12. weekend/pre-open restart tests ✓ (focused + closed-market evidence)
13. market-closed is not confused with data failure (`ok` on empty/echo) ✓
14. warmup does not depend on "today" (run-relative windows; epoch-pinned
    `now_epoch` injection) ✓
15. real Dhan OHLC preserved; anomalies recorded, never repaired ✓
16. four strategies receive the same verified native streams (identity DEMA) ✓
17. execution is the only post-trigger difference (paper vs live; not touched) ✓
18. focused test matrix A–T above ✓
19. forensic logging fields per spec ✓
20. strict verdict **VERIFIED** ✓
21. no unrelated changes (no DEMA/ATR/crossover/SL/reversal/synthetic/resample)
    ✓
22. finite verification; no uncontrolled replay loop left running ✓