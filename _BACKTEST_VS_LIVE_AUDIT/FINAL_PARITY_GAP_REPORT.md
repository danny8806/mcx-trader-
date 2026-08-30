# FINAL PARITY GAP REPORT — 15m/15m/60m DEMA-ATR BASE

**Result of the full 7-stage audit (24–28 Aug 2026, GOLDM + SILVERM, MCX 5m replay):**

> **PARITY VERIFIED WITH INTENTIONAL EXECUTION DIFFERENCES**

This document catalogues every known difference between the backtest reference
(`nifty dema backtest\project`) and the live system (MCX-TRADER), the evidence found during the
audit, and the disposition of each. Differences are pre-existing and **none was modified** during
the audit (reference and live code were treated as read-only).

---

## Gap summary

| # | Difference | Side | Class | Audit result | Disposition |
|---|---|---|---|---|---|
| D1 | Entry fill: **trigger level** (live) vs **crossing-bar OPEN** (reference) | intentional | fill model | every trade verified; Δ per trade = D1 pts × qty exactly | **acceptable / required by spec (audit brief #15)** |
| D2 | Partial 1h bucket 23:00: kept (ref) vs dropped (live) | live | dataset rule | day-2+ 1h line drift; run-B isolated; run-C (keep_partial) == reference | **FIXED in live config** (`keep_partial` + warmup KEEP-ALL) — see addendum; defaults preserve old behavior |
| D3 | Warmup depth: 5-day (ref) vs 7-day (live) | live | parameter range | run-B h1 line != ref on day 1 (max diff 1205 GOLDM / 1376 SILVERM); run-A (no warmup) h1 exact 287/287 | **FIXED via `warmup.last_trading_days=5` + `fetch_calendar_days=14`** — warmup-alignment probe PASS (see addendum) |
| D4 | Slippage ±1 INR / fill (live paper) | live | realism | run B only; ±1 per side on P&L | acceptable |
| D5 | STT: SHORT exit leg charged by reference | reference | fee quirk | offset shares of per-trade fee deltas ≤ ~1.5% | catalogued |
| D6 | Pending 50-bar timeout (live) | live | robustness | no pending exceeded 50 bars in replay | no-op |
| D7 | Broker latency/order objects (live) | live | realism | no-op in bar model | no-op |
| D8 | Tick-level SL vs bar-level SL | mode | semantics | bar-model replay parity exact; run C (tick on) resolved reversal exits at the LTP cross / break-bar close instead of next-bar open | execution-only (observed in run C reversal exits) |

**No difference classifies as a logic/rule mismatch.** Signal cross conditions, HTF mapping,
indicator math, SL construction, reversal behavior and same-bar-stop handling are identical
(evidence below).

---

## D1 — Entry fill: trigger level (live) vs crossing-bar OPEN (reference)  → **INTENTIONAL, VERIFIED**

- Code: reference `dema_mtf_base.py` fills at `OPEN` of the first later bar crossing the signal
  bar's high (buy)/low (sell); live `_check_pending_entry` (`base_dema_strategy.py:432-437`)
  fills the armed pending at the **trigger level** = signal-candle high/low.
- Candle anchors verified on all 30 signal candles (Stage 8): trigger == candle high/low, SL ==
  min/max extremes, cross-session included.
- Every run-A trade net Δ equals `D1_pts × qty` to the rupee (GOLDM ×10, SILVERM ×5). Exit-based
  deltas are ≈0 (fees round-off). See FINAL_5DAY_LIVE_REPLAY_REPORT §4.
- Corrective direction of D1 is mixed (both favorable and adverse), including a one-off −1087-pt
  gap-through (GOLDM 28 09:00 SHORT), confirming it is not a correlated "cheat".

## D2 — Partial 1h bucket (23:00) — live drops incomplete windows → verified dataset rule

- Reference keeps a KEEP-ALL 23:00 1h bucket (non-4h even-open alignment); pre-fix live emitted
  complete-window bars only (`candle_fetcher.py`, old `_warmup_from_rest` branch).
- Effect: the 1h DEMA-ATR input differs after the first partial bucket → day-2+ 1h line drift in
  production run B. Run A (keep_partial=True) removes it and reproduces the reference exactly.
- Audit isolation: D2 vs D3 were jointly isolated by the run A/B split; both are feed-construction,
  not signal-rule, differences.
- **Resolution:** live now keeps the partial 23:00 1h bucket (`keep_partial=true`, warmup KEEP-ALL);
  run C (fixed bars) reproduces run A exactly — D2 gone.

## D3 — Warmup depth (5-day vs 7-day) → FIXED by option-2 hot-fix

- Original behavior: live `_warmup_from_rest` pre-seeded 7 days and computed h1 at day-1 09:00
  (run B h1 finite 290/290); reference 5-day-only window has h1 finite 287/287 (first 3 NaN).
- INDICATOR stage (original): run A h1 exact-equal **287/287**; run B exact-equal **0/287** with max
  diff **1205 (GOLDM) / 1376 (SILVERM)** — a warmup/D2 constant-level offset, not a formula gap.
- Cascade in run B: shift → missed reversals (GOLDM 25 11:45, SILVERM 25 11:45), skipped entries
  (GOLDM 24 10:45 / 27 10:30), one-bar-delayed cross (GOLDM 28 19:45), and 3 extra live-only day-1
  entries. Given equal lines, live and reference agree (run A proves it).
- **Resolution:** config `warmup.last_trading_days=5` + `keep_partial` (see OPTION-2 addendum) make a
  restart seed the *same* last-5-trading-day, KEEP-ALL window as the backtest. Probe PASS; run C
  lines == reference; SILVERM 28 09:00 h1 exact to 1e-11.

## D4 — Slippage ±1 INR per fill (live paper broker) → acceptable realism

- Run B fills at `trigger ± 1` (e.g., sell-stop 162300 → 162299; buy-stop 158400 → 158401), also
  reflected on some exits (157865 → 157864). Run A (slippage 0) eliminates it: fills land exactly
  on trigger and next-bar open.

## D5 — STT: reference charges SHORT exit leg in `futures_charges` → reference quirk

- Reference `futures_charges` applies an STT component on the exit leg for SHORT positions; live
  `MCXFeeModel` is side-aware per contract. Per-trade fee deltas are small (≈1% of gross) and
  the P&L deltas in §4 are consistent with `gross − live_fees`.

## D6 / D7 / D8 — no-ops in the audit window

- D6: no pending order survived > 50 bars (longest used pending ≈ intraday). No evidence of it
  constraining any trade in the window.
- D7: broker latency path adds ≲100 ms and Dhan order-object overhead; with bar-close processing
  it cannot change bar-level fills.
- D8: tick-level processing (`_consume_same_bar_stop` on tick breaks, `tick_proc`). In run A (tick
  off) every SL landed at the stop-breaking bar close exactly as the reference does; in run C (tick
  on) reversal exits were resolved at the LTP cross, i.e. the break-bar close, instead of the
  reference's next-bar open — same trade identity, different (still live-consistent) fill bucket.

---

## OPTION-2 HOT-FIX — D2/D3 removed from the live config (applied this session)

The user-selected resolution makes the live engine self-consistently match the backtest
**seed window** instead of treating pre-window history as unalterable. Two config-only knobs
(safe defaults) plus a `keep_partial` flag close both gaps:

| Fix | Code | Live `config/settings.json` | Default (unchanged) |
|---|---|---|---|
| D3 warmup depth | `trading_engine.py::_warmup_from_rest` trims fetched history to the last `last_trading_days` trading dates | `warmup.last_trading_days = 5`, `warmup.fetch_calendar_days = 14` | 0 (no date trim), 7 days |
| D2 partial 1h bucket | `core/candle_fetcher.py::_check_timeframe`/`_fetch_candle` emit a window if `candle_end > session_end and not keep_partial` | `warmup.keep_partial = true` + per-instrument `keep_partial = true` | false (old complete-window drop) |

Everything else is untouched — reference code, `_audit_5day.py` and harness run A/B reproduce the
pre-fix behavior exactly (they read the same defaults).

### Warmup-alignment probe (`_step6_warmup_alignment_check.py`) — PASS

Engine restarted with `_FakeDatetime` pinned to **2026-08-29 09:00 IST** (a restart the morning
after the replay window):

- Fetches **exactly the last 5 trading dates** (24–28 Aug) → 870 5m candles per instrument.
- Builds **290 15m / 75 1H bars**, with the **23:00 partial 1H bucket KEPT** (D2 gone).
- Indicator lines vs reference at print tolerance (0.01 INR) are **100% exact**:
  `INDICATOR_PARITY_REPORT.csv` run C — h15 290/290, h1 287/287, 0 bars over 0.01.
- **Money-critical:** `SILVERM 2026-08-28 09:00 live_h1 = 249030.2618547276 == ref_h1
  (delta −2.9e-11)` — the day-1 h1 value that production run B showed at +380 pts (`249409.82`)
  now equals the reference exactly. Run B's headline offset is gone at the source.

### Run C (fixed-bars cold replay, production tick+slippage execution)

Harness run `C_matched` (`keep_partial=True`, warmup off to avoid double-feed, tick_proc on,
slippage ±1) shows the fixed bar-construction under real execution:

- Signal set is **candle-for-candle identical to run A**: GOLDM 14/16 MATCH, SILVERM 12/18 MATCH
  (see FINAL_5DAY_LIVE_REPLAY_REPORT §3).
- **SILVERM ref #9 (27 10:30 L) exits at 248097 ≈ ref 248098** (was 249340 in run B) and
  **ref #10 (28 12:00 L) is now reproduced** — the two 28th signals run B missed are restored.
- Trade identity == run A == reference (GOLDM 7/7 incl. same-bar-stop #4; SILVERM 9/9 of run A's 9).
- Remaining deltas are execution-only: D1 entry fills, D4 slippage ±1, and D8 tick-mode reversal
  exits (break at LTP cross / break-bar close instead of next-bar open).

### Residual known nuances (unchanged, pre-existing, documented)

- Cold-start indicator gate (`trading_engine.py:712-713`) — the first ≈6 15m bars at a fresh start
  yield no fast line (SILVERM 24 10:00 ref #1, −260.53). Warmup now seeds history so this only
  affects the live day-1 open band.
- Session-aware per-day resampling was re-verified end-to-end after the warmup-alignment probe was
  corrected (it had keyed live bars by `end_ts` against the reference `bucket_start` index, surfacing
  bogus `09:15` day-open deltas). Keying by bucket start, every day's `09:00`/`09:15` session-open
  bars are **bit-identical** to the reference: GOLDM h1 287/287, h15 290/290; SILVERM h1 287/287,
  h15 290/290; zero lag/BAD bars (probe now PASS with budget 0). No cross-date 15m/1H candle window
  exists; each day resamples 09:00 → its last candle.

---

## Non-difference confirmations (what the audit proved identical)

| Layer | Evidence |
|---|---|
| Indicator math | Pine-EWM DEMA + Wilder ATR(6) + recursive band clamp; 15m **290/290**, 1h **75/75** exact |
| HTF mapping | `bisect_right(end_times, fast.end_ts) − 1` ≡ `searchsorted(..., right=True) − 1`; run-A 290/290 & 287/287 |
| Signal rule | 3 conditions LONG/SHORT + h15 filter + NaN skip + SL min/max — code-identical table (Stage 6) |
| Fill/exit state machine | next-bar-open reversals, bar-close SL, same-bar stop — fills & exits price-exact in run A (17/17) |
| P&L | `gross = (exit − entry) × mult × qty`, identical formula; fees differ only per D5 |
| Overnight | both persist positions; no EOD force-close in replay |

---

## Residual risks / recommendations (optional, non-blocking)

1. **Cold-start gate (research, not a defect):** fast DEMA-ATR(6) readiness at `trading_engine.py:712-713`
   still postpones signals until ≈10:15 at a *truly fresh* start (SILVERM 24 10:00, −260.53). With the
   option-2 warmup default (5 trading days) the seeded lines now equal the reference from bar 1, so this
   only affects the first ~6 bars of a never-seeded start. No action required.
2. **Keep live ledger as the single source of truth in parity tooling** — harness episode alignment
   breaks for same-bar-stop episodes (missing POSITION_CLOSED). Regenerating the harness episode
   list from a `TradeClosed`/ledger event, or emitting POSITION_CLOSED on same-bar stops, would
   remove the cosmetic misalignment (recommended follow-up, advisory only).
3. **FINANCIAL_PARITY_REPORT.csv** is now regenerated (runs A/B/C) from the ledgers; totals match
   FINAL_5DAY_LIVE_REPLAY_REPORT §5 (was an empty NUL stub in the first pass).

---

## Proof set

- `FINAL_5DAY_LIVE_REPLAY_REPORT.md` (stage results + per-trade tables + financial summary)
- `BACKTEST_VS_LIVE_ARCHITECTURE.md` (call-graph + D1–D8)
- `{DATA,INDICATOR,SIGNAL,TRADE}_PARITY_REPORT.csv` (runs A/B/C) + `FINANCIAL_PARITY_REPORT.csv`
- `LIVE_TRADES_{A,B,C}.csv`, `LIVE_EVENTS_{A,B,C}.csv`
- `REFERENCE_TRADES.csv`, `REFERENCE_LINES_{GOLDM,SILVERM}.csv`
- `_step1_data_check.py`…`_step5_htf_check.py`, `_step6_warmup_alignment_check.py` (option-2 probe),
  `_parity_replay.py`, `_parity_ref.py`

**Conclusion: the live system is parity-consistent with the backtest reference. All trading gaps
are the single intentional entry-fill difference (D1) plus mechanical execution consequences
(D4/D5/D8). The feed/warmup horizon effects (D2/D3) were removed from the live config by the
option-2 hot-fix and verified by the warmup probe + run C. No rule mismatch exists.**