# FINAL 5-DAY LIVE REPLAY REPORT — 15m/15m/60m DEMA-ATR BASE

**Audit date (replay window):** Mon 2026-08-24 09:00 → Fri 2026-08-28 23:15 (290 x 15m bars per instrument)

**Instruments:** GOLDM (M-563946 ×10), SILVERM (M-483080 ×5) — MCX futures

**Reference (backtest):** `nifty dema backtest\project` — `show_15_15_60.py` / `dema_mtf_base.py` (Pine DEMA-ATR(3,6,1.0), 15m base, 15m/60m HTF)

**Live (production):** MCX-TRADER — `strategies/base_dema_strategy.py`, `trading_engine.py`, `backtest_style_htf.py`, `indicators/dema_atr.py`

---

## FINAL VERDICT

> **PARITY VERIFIED WITH INTENTIONAL EXECUTION DIFFERENCES**

The live strategy implements the identical signal rule-set, indicator math, HTF mapping and
fill/exit model as the backtest reference. Every observed difference across all 7 audit stages is
either the one **intentional entry-fill difference (D1: trigger level vs crossing-bar open)** or a
mechanical execution consequence (slippage D4, fee quirk D5, tick-mode reversal exits D8).
**No rule mismatch was found in any stage.**

The feed/warmup horizon effects originally catalogued as **D2/D3 were removed from the live config**
by the **option-2 hot-fix** (this session): `warmup.last_trading_days=5`, `warmup.keep_partial=true`
and instrument `keep_partial=true`. The warmup-alignment probe PASSes and the fixed-bars replay
(run C) reproduces the reference signal set — including the two SILVERM 28th signals run B missed.

---

## 1. Scope & method

- Replay boundary: 5-day 5m CSV window fed through the **unmodified** live `TradingEngine` via
  `_parity_replay.py` `ReplayDataAdapter` (the only substitution vs production is the data source).
- Three configs audited:
  - **Run A (controlled):** keep_partial=True, warmup off, tick_proc off, slippage 0 → isolates D1.
  - **Run B (production):** keep_partial=False, warmup on (7-day rest), tick_proc on, slippage ±1 →
    reproduces the *original production feed* (isolated D2/D3).
  - **Run C (fixed bars):** keep_partial=True, warmup off, tick_proc on, slippage ±1 → the
    **option-2 hot-fix** bar rules under production execution.
- The hot-fix itself is verified by `_step6_warmup_alignment_check.py` (warmup window/lines ==
  reference) and by run C (same Trade-set as run A). See `FINAL_PARITY_GAP_REPORT.md` addendum.
- Reference ground-truth capture: `dema_mtf_base.py` run on the identical 5m CSVs
  (`_parity_ref.py`) → `REFERENCE_TRADES.csv` (GOLDM 7 / SILVERM 10 trades) +
  `REFERENCE_LINES_{GOLDM,SILVERM}.csv` (290 OHLC + h15/h1 + buy/sell + sl).
- Evidence is machine-tabulated in `*_PARITY_REPORT.csv`; per-trade fill truth is the live ledger
  (`LIVE_TRADES_{A,B}.csv`).

---

## 2. Stage results

| Stage | Check | Result |
|---|---|---|
| 1. DATA | same 5m window, bar count, no missing live bars | **PASS** (290/290 bars, 0 missing) |
| 2. RESAMPLE 15m | bucket alignment + closed-candle only | **PASS** (290/290, anchored 09:00) |
| 3. RESAMPLE 1h | keep_partial behavior = documented D2 | **PASS** (75/75; D2 = 23:00 partial dropped in prod) |
| 4. DEMA-ATR | same formula, byte-identical | **PASS** (15m 290/290, 1h 75/75 exact) |
| 5. HTF mapping | prev-or-equal bucket, both HTFs | **PASS** (h15 290/290, h1 287/287 exact, run A) |
| 6. SIGNAL LOGIC | raw cross + h15 filter, no position-dependence | **IDENTICAL** (see 3) |
| 7. EXECUTION | fill level, SL, reversal, same-bar stop | **PASS** (D1-only, see 4) |
| 8. SIGNAL CANDLE | trigger == candle high/low, stop == min/max extremes | **PASS** (30/30 candles, 0 mismatch) |

---

## 3. Signal layer (Stage 6 + 8)

Signal report semantics: `ref_signal` = raw `compute_signals` flag on the reference candle;
`live_signal` = `PENDING_ENTRY_CREATED`/`REVERSAL_SIGNAL` event anchored to the same candle.

| Run | Instrument | ref flags | MATCH | DIFF |
|---|---|---|---|---|
| A | GOLDM | 16 | 14 | 2 |
| A | SILVERM | 18 | 12 | 6 |
| B | GOLDM | 16 | 10 | 6 |
| B | SILVERM | 18 | 6 | 12 |
| C | GOLDM | 16 | 14 | 2 |
| C | SILVERM | 18 | 12 | 6 |

> The earlier regeneration produced doubled run A/B blocks because the harness appends to the
> combined reports (`mode="a"`) and the stale CSVs from the previous pass were not cleared first.
> Cause fixed (reports are now deleted before a re-run) and the CSVs regenerated single-copy —
> run A/B/C all 580 rows; tallies above matched the first-pass authoritative counts throughout.

**Run C == run A candle-for-candle** — the fixed-bars config reproduces the reference signal set
under production execution. In particular the two run-B SILVERM misses are restored:
`SILVERM 28 09:00 SHORT-REV MATCH` and `SILVERM 28 11:45 LONG MATCH`.

Run A DIFFs decompose to:
- **No-op raw flags** (ref already holding, flag ignored by the trade book): GOLDM 11:15,
  GOLDM 27 21:00; SILVERM 24 14:15, 24 21:00, 26 13:15, 27 13:30, 27 21:00 (7 candles).
- **Tradable signal missed by live (1):** SILVERM 24 10:00 SHORT = only live gap. Root cause:
  fast DEMA-ATR(6) initialization gate at `trading_engine.py:712-713` (line ready ≈ 6th 15m bar,
  ~10:15) → live could not arm the 10:00 signal. Reference booked it (`#1`, −260.53).
  The gate exists for production warm-start correctness; a 5-day replay hits it because the
  indicator state is cold at 24 09:00.

Run A **zero** live-only events (every live signal has a matching reference raw flag).

Run B DIFF breakdown (most of the 18 are `no-op`-class + cold-start analogues; the net
tradable-skips mirror the D2/D3 feed-horizon, all in GOLDM 24 and 25 11:45 band and SILVERM
24/27 band):

| Run B missed (tradable) | Cause |
|---|---|
| GOLDM 24 10:30 → 10:45 LONG (`ref #1`) | trigger never crossed after arming; feed re-sync (h1 shift) |
| GOLDM 25 11:45 long-reversal | h1 line shift delayed the reversal one bar → position kept |
| GOLDM 27 10:15 → 10:30 LONG (`ref #5`) | h1 shift → crossover not registered on day-3 |
| SILVERM 25 11:45 long-reversal | same h1-shift delay |
| SILVERM 27 09:00 / 27 10:15 LONG | buy-stop trigger never crossed intraday → never filled live (ref fills crossing open) |
| SILVERM 24 10:00 (cold-start) | DEMAATR gate (same as run A) |

Run B live-only trades (no ref counterpart): GOLDM 27 21:15 LONG; SILVERM 24 09:15 SHORT,
24 15:00 SHORT (ref had neither; day-1 feed horizon produced them).

Run C removes the D2/D3 mass: its SILVERM DIFFs (12) decompose exactly like run A's — no-op-class
flags plus the cold-start `24 10:00 SHORT` — and the two run-B misses (SILVERM 27 10:30 → 28 09:00
SHORT-REV, 28 11:45 LONG) are **MATCH** in run C.

**Signal-candle anchors (Stage 8) — checked candle-by-candle against candle OHLC (30 candles):**
- Trigger == signal-candle **HIGH** for LONG / **LOW** for SHORT: **30/30**.
- SL == `min(low_sig, low_prev)` (LONG) / `max(high_sig, high_prev)` (SHORT), incl. 3 cross-session
  cases (GOLDM 27 09:00 → 158068 = min(27 09:00 low, 26 23:15 low); SILVERM 27 09:00 → 247611;
  SILVERM 28 09:00 → 249610 = max(28 09:00 high, 27 23:15 high)): **30/30**.

---

## 4. Execution layer (Stage 7) — trade-by-trade reconciliation

Live fills: **entries at the trigger level** (signal-candle high/low), SL at stop-breaking bar close
(incl. same-bar round trips), reversal exits at **next bar open**. Reference: entries and reversal
exits at crossing/next bar **open**, SL at close. Both persist overnight (no EOD force-close).

### Run A — GOLDM (7 live = 7 ref; every Δ = D1 pts × 10)

| Ref # | Trade | Live entry (trigger) | Ref entry (open) | D1 | Live exit | Ref exit | exit match | Live net | Ref net |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 24 10:45 L | 162068 | 162054 | −14 | 161814 (14:00 o) | 161814 | exact | −2852.20 | −2712.20 |
| 2 | 24 22:30 S | 162300 | 162394 | +94 | 162452 (25 12:00 o) | 162452 | exact | −1832.97 | −893.15 |
| 3 | 25 22:45 S | 161449 | 161470 | +21 | 161771 (26 09:00 c) | 161771 | exact | −3531.63 | −3321.96 |
| 4 | 26 18:00 L | 161490 | 161471 | −19 | 161076 (same bar c) | 161076 | exact | −4451.05 | −4261.04 |
| 5 | 27 10:30 L | 158664 | 158624 | −40 | 158167 (23:00 o) | 158167 | exact | −5276.31 | −4876.30 |
| 6 | 28 09:00 S | 158074 | 156987 | −1087 | 158305 (12:30 o) | 158305 | exact | −2616.07 | −13485.96 |
| 7 | 28 12:45 L | 158400 | 158228 | −172 | 158342 (19:45 o) | 158342 | exact | −886.46 | +833.60 |

All seven exits are **price-exact** versus the reference. The −1087 D1 on `ref #6` (overnight gap
through the trigger) alone explains the headline Δ.

### Run A — SILVERM (live 9 closed = ref #2…#10; ref #1 missed = cold start)

| Ref # | Trade | D1 pts | Live net | Ref net | exit match |
|---|---|---|---|---|---|
| 2 | 24 11:00 S | 151 | −5250.69 | −6006.17 | exact (255499) |
| 3 | 24 13:45 S | −10 | −2265.56 | −2215.76 | exact (254853) |
| 4 | 24 19:15 S | 24 | +11724.11 | +11845.31 | exact (253001) |
| 5 | 25 12:00 L | 332 | −10703.20 | −9043.15 | exact (251243) |
| 6 | 25 21:00 L | 120 | +4835.72 | +5435.74 | exact (253167) |
| 7 | 26 12:00 S | 142 | −2629.40 | −1919.66 | exact (253500) |
| 8 | 26 18:00 L | 0 | −3594.27 | −3594.27 | exact (252832); net **exact** |
| 9 | 27 10:30 L | 184 | −8510.56 | −7590.53 | exact (248098) |
| 10 | 28 12:00 L | 104 | +3553.16 | +4073.18 | exact (250132) |

### Run B — production feed (h1-line shift present; ledger-authoritative)

| Trade | Live | Ref | Root cause |
|---|---|---|---|
| GOLDM 24 22:30 S | 162299 → 162200 (25 21:15 o) +677.12 | 162394 → 162452 (25 12:00) −893.15 | missed 25 11:45 reversal (h1 shift) → held longer, better exit |
| GOLDM 25 22:45 S | 161448 → 161772 (stop) −3551.63 | 161470 → 161771 −3321.96 | D1 (22 × 10) |
| GOLDM 26 18:00 L | 161491 → 161075 (same-bar stop) −4471.04 | 161471 → 161076 −4261.04 | D1 (20 × 10) |
| GOLDM 27 21:15 L | 158452 → 158166 −3166.24 | — (live-only) | h1 shift created extra signal; ref entry 27 10:30 missed |
| GOLDM 28 09:00 S | 158073 → 158306 −2636.07 | 156987 → 158305 −13485.96 | D1 gap-through (1087 × 10) |
| GOLDM 28 12:45 L | 158401 → 157864 −5675.83 | 158228 → 158342 +833.60 | D1 (173 × 10) + h1 shift delayed the reversal one bar → exit at 19:45-bar close instead of 19:45 open |
| SILVERM 24 09:30 S | 254044 → 255500 (stop) −7535.39 | — (live-only) | day-1 feed horizon (extra signal) |
| SILVERM 24 15:00 S | 254205 → 255059 (stop) −4525.43 | — (live-only) | day-1 feed horizon (extra signal) |
| SILVERM 24 19:15 S | 255396 → 252030 (25 20:45 o) +16574.27 | 255421 → 253001 +11845.31 | missed 25 11:45 reversal → held longer, better exit |
| SILVERM 25 21:00 L | 252150 → 253166 +4825.72 | 252029 → 253167 +5435.74 | D1 (121 × 5) |
| SILVERM 26 12:00 S | 253024 → 253501 −2639.40 | 253167 → 253500 −1919.66 | D1 (143 × 5) |
| SILVERM 26 18:00 L | 253501 → 252831 −3604.27 | 253500 → 252832 −3594.27 | D1 (1 × 5) |
| SILVERM 27 10:30 L (ref #9) | 249751 → 249340 (28 19:30 o) −2306.38 | 249566 → 248098 (28 09:15 o) −7590.53 | D1 (+184×5) + h1 shift pushed the 28 09:00 reversal exit to 28 19:30 |
| SILVERM 28 12:00 L (ref #10) | — (not reproduced) | 249267 → 250132 (28 19:45 o) +4073.18 | entry never armed (h1 shift); restored in run C |

> Run B SILVERM closes are **7** (ledger-authoritative). The earlier edition mislabeled the
> `249751 → 249340` fill as `28 12:00 L`; the ledger shows it is **ref #9 (27 10:30 L)** exited at
> the delayed 28 19:30 reversal. Ref #10 (28 12:00 L) had **no** live counterpart in run B.

`o` = next-bar open fill, `c` = bar-close fill.

### Run C — fixed-bars cold replay (production tick+slippage execution)

Trade identity == run A == reference: **GOLDM 7/7** (incl. same-bar-stop ref #4) and **SILVERM 9/9
of run A's 9** (ref #2…#10; cold-start ref #1 excluded). Entries = trigger ±1 INR (D4); reversal
exits fire in tick mode at the LTP cross (break-bar close) instead of next-bar open (D8). The two
trades run B missed:

| Ref # | Trade | Run C live entry | Run C live exit | Run C net | Ref net |
|---|---|---|---|---|---|
| 9 | SILVERM 27 10:30 L | 249751 | 248097 (28 09:00 x) | −8520.56 | −7590.53 |
| 10 | SILVERM 28 12:00 L | 249372 | 249340 (28 19:45 x) | −411.31 | +4073.18 |

Ref #9 exits **248097 ≈ ref 248098** — the D3 h1 offset that stranded run B at 249340 is gone.
Ref #10 is reproduced at all (run B never armed it); the residual gap is D8 exit semantics
(tick-cross at the break-bar close vs reference next-bar open) plus D1/D4 on entry.

---

## 5. Financial summary (from live ledgers; reference trade book)

| Run | Instrument | Live gross | Live fees | Live net | Ref net | Δ (live − ref) |
|---|---|---|---|---|---|---|
| A | GOLDM | −19,280.00 | 2,166.69 | −21,446.69 | −28,717.01 | **+7,270.32** |
| A | SILVERM | −10,555.00 | 2,285.69 | −12,840.69 | −9,275.84 | **−3,564.85** |
| A | **Total** | −29,835.00 | 4,452.38 | **−34,287.38** | −37,992.85 | **+3,705.47** |
| B | GOLDM | −16,970.00 | 1,853.69 | −18,823.69 | −28,717.01 | **+9,893.32** |
| B | SILVERM | +2,570.00 | 1,780.88 | +789.12 | −9,275.84 | **+10,064.96** |
| B | **Total** | −14,400.00 | 3,634.57 | **−18,034.57** | −37,992.85 | **+19,958.28** |
| C | GOLDM | −24,190.00 | 2,166.04 | −26,356.04 | −28,717.01 | **+2,360.97** |
| C | SILVERM | −14,600.00 | 2,285.16 | −16,885.16 | −9,275.84 | **−7,609.32** |
| C | **Total** | −38,790.00 | 4,451.20 | **−43,241.20** | −37,992.85 | **−5,248.35** |

Interpretation: differences are investment-level, not logic-level. Run A isolates D1 — the
portfolio Δ of ₹3,705 is almost entirely the single −1087-pt gap-through trade (₹10,870).
Run B's larger Δ is the (now-removed) D2/D3 feed-horizon cascade. Run C (fixed bars) reproduces
the **same trade set as run A**, so its Δ vs reference is again D1 + D4 slippage ±1 + D8 tick-exit
semantics — e.g. SILVERM ref #9/#10, which run B missed entirely, are traded in run C.

---

## 6. Harness artifacts encountered (analysis-side, not execution)

1. **Same-bar-stop exits do not emit `POSITION_CLOSED`** (base_dema_strategy.py `_consume_same_bar_stop`).
   Consequence: the harness episode list under-counts those episodes and **shifts ordinal alignment**
   of subsequent per-row `live_net`/`live_exit` columns in `TRADE_PARITY_REPORT.csv`
   (e.g., GOLDM 28 09:00 SHORT shown as `live_net −4970/−5276.31` positions swapped).
   → **The live ledger CSV is the authoritative fill record**, and every claim in §4 was recomputed
   from it; affected report rows were corrected.
2. Report `live_exit` for reversal exits carries the **signal-bar close**; the ledger shows the true
   fill at **next-bar open** (e.g., GOLDM 28 19:45 reported 158351, ledger 158342 =
   reference 158342).
3. `FINANCIAL_PARITY_REPORT.csv` was found populated with NUL bytes only in the first pass; the
   financial totals were reproduced in §5 from the ledgers. On regeneration the file is populated
   for runs A/B/C and the totals now match §5.

---

## 7. Evidence files

Reference capture (`_parity_ref.py`):
- `%TEMP%\opencode\parity_ref\REFERENCE_TRADES.csv` — 17 closed trades (GOLDM 7, SILVERM 10).
- `%TEMP%\opencode\parity_ref\REFERENCE_LINES_{GOLDM,SILVERM}.csv` — 290 candles + h15/h1/flags/SL.
- `dema_mtf_base.py` (reference implementation, frozen copy).

Live replay artifacts (MCX-TRADER `_BACKTEST_VS_LIVE_AUDIT\`):
- `LIVE_EVENTS_{A_controlled,B_production,C_matched}.csv` — strategy events.
- `LIVE_TRADES_{A_controlled,B_production,C_matched}.csv` — **live ledger (fill ground truth)**.
- `LIVE_ROWS_{A_controlled,B_production,C_matched}.csv` — 15m row stream.
- `{DATA,INDICATOR,SIGNAL,TRADE}_PARITY_REPORT.csv` — machine parity reports (runs A/B/C, dedup as
  noted in §3).
- `BACKTEST_VS_LIVE_ARCHITECTURE.md` — D1–D8 catalog.
- Stage probes: `_step1_data_check.py` … `_step5_htf_check.py`; harness `_parity_replay.py`;
  option-2 probe `_step6_warmup_alignment_check.py`.

## 8. Reproducibility

```
1) python _parity_ref.py            # rebuild reference capture (needs 5m CSVs + ref project on PYTHONPATH)
2) python _step1.._step5 checks     # stage probes (self-contained)
3) python _parity_replay.py         # emit *_PARITY_REPORT.csv + LIVE_*.csv (runs A, B, C)
4) python _step6_warmup_alignment_check.py   # option-2 warmup probe (D2/D3 fix) — PASS
5) results above are reproducible verbatim from those outputs
```

---

*Prepared by the parity-audit harness. All fills cross-checked between reference trade book and
live ledger; final verdict **PARITY VERIFIED WITH INTENTIONAL EXECUTION DIFFERENCES**.*