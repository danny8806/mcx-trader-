# BACKTEST REFERENCE SPEC — DEMA-ATR Multi-TF 15m/15m/60m (BASE)

> Forensic extraction from the reference backtest source. Sole purpose: document, with
> exact file:line provenance, what the `show_15_15_60.py` run computes so it can be
> compared line-by-line against the LIVE system (`LIVE_REFERENCE_SPEC.md`).
>
> Combo audited: **15m base / 15m mid / 60m HTF, D=3 A=6 F=1.0, BASE strategy**.
> Instruments: **GOLDM-Oct26 (569003) mult 10.0**, **SILVERM-Nov26 (483080) mult 5.0**.
> Window: **2026-08-24..2026-08-28** (LAST5). Capital **300000**, qty **1**.

## 1. Entry-point / runner

| Item | Value | Source |
|---|---|---|
| Runner | `show_15_15_60.py` | `project\show_15_15_60.py` |
| Params | `BASE,MID,HTF = 15,15,60`; `CAPITAL = 300_000`; `LAST5 = 24..28 Aug 2026` | `show_15_15_60.py:25-27` |
| Instruments | `GOLDM-Oct26 (569003)` → `data_mcx/gold/GOLDM_04Sep2026_5m.csv`, mult `10.0`; `SILVERM-Nov26 (483080)` → `data_mcx/silver/SILVERM_30Nov2026_5m.csv`, mult `5.0` | `show_15_15_60.py:29-32` |
| Strategy engine | `goldm_dema_mtf_futures.GoldFuturesDemaStrategy` (Backtrader) | `show_15_15_60.py:49-52`, `goldm_dema_mtf_futures.py:115` |
| Resample override | `DM.resample_ohlcv = base_mod.resample_ohlcv` — **KEEP ALL BUCKETS** (partial end-of-session windows are INCLUDED) | `show_15_15_60.py:13-17`; `%TEMP%\opencode\dema_mtf_base.py` |
| Signals/indicators | `core.dema_mtf.htf_dema_line` (`DM.htf_dema_line`) | `show_15_15_60.py:38-41` |

## 2. Data ingestion

### 2.1 `load_5m` (validation)
- Required cols: `datetime, open, high, low, close, volume` (`goldm_dema_mtf_futures.py:92`).
- `datetime` → naive IST (`tz_localize(None)`), all OHLCV numeric, invalid-OHLC rows rejected,
  `volume` fillna 0 int, **sort + dedupe keep last** (`:94-108`).

### 2.2 Window filter
- `df = df[df["datetime"].dt.strftime("%Y-%m-%d").isin(LAST5)]` — **ONLY the 5 days are fed**;
  warm-up (EMA/ATR/DEMA/band history) is computed from those 5 days only (`show_15_15_60.py:60`,
  executed before signal/indicator computation in `run_combo`).

## 3. Bar construction (15m base)

- 15m base = `DM.resample_ohlcv(df, "15min", session_open="09:00")` (`show_15_15_60.py:37`).
- Bucket anchor: `session_start = date+" 09:00"`; `_bucket = session_start + floor(mins/15)*15m`
  (`dema_mtf_base.py` — keep-all; verified: the override does NOT drop `size != expected` rows).
- OHLC agg: `open=first, high=max, low=min, close=last, volume=sum` over each bucket.
- Because base_min for the resampled 15m series = 15: **a bucket exists at 23:00-23:30 on each day**
  (6 five-minute bars) and is KEPT by the reference.
- Note: `core.dema_mtf.resample_ohlcv` itself drops partial windows (`core/dema_mtf.py:76-78`),
  but the reference **overrides it** with `base_mod` (keep-all). This is the authoritative
  reference behaviour for this run.

## 4. Indicators (exact formulas)

### 4.1 EMA
`pine_ema = source.ewm(alpha=2/(length+1), adjust=False, min_periods=1).mean()` — `build_15min_enriched.py:84-85`.

### 4.2 Wilder ATR(6)
- `TR = max(H−L, |H−prevC|, |L−prevC|)` (`:88-94`).
- `ATR[0..4]=NaN; ATR[5]=mean(TR[0:6])`; then `ATR[i]=0.1667*TR[i]+0.8333*ATR[i−1]` (`:96-102`).

### 4.3 DEMA-ATR period 3, ATR period 6, ATR factor 1.0 (recursive band clamp)
- `ema1 = pine_ema(close,3); dema = 2*ema1 − pine_ema(ema1,3)` (`:106-107`).
- `band = wilder_atr*1.0; upper = dema+band; lower = dema−band` (`:108-110`).
- Clamp loop (`:116-123`): `cur = prev_output (or dema if first); if lower>cur: cur=lower; if upper<cur: cur=upper; out[i]=cur`.
- `dema_atr` imported into `core.dema_mtf` (`core/dema_mtf.py:25`).

## 5. HTF line mapping (lookahead_off / gaps_off)

`htf_dema_line(df_base, rule, …)` — `core/dema_mtf.py:92-122`:
1. `htf = resample_ohlcv(df_base, rule, session_open="09:00")` (15m rule for MID line, 60m for 1H line; the 15m MID line is the DEMA-ATR of the 15m base itself, i.e. equal-time 1:1).
2. `vals = dema_atr(htf, ...)`.
3. `src_avail = htf["datetime"] + rule_min minutes` (each HTF bucket becomes "available" at its END timestamp).
4. `base_min` = smallest positive intra-session spacing of the base rows (**15 min** here since base is 15m; code derives it dynamically, `:109-113`).
5. `target_close = base_dt + base_min` (base bar END).
6. `idx = searchsorted(src_avail, target_close, side="right") − 1` (`:117`) — a base bar uses the latest HTF bar that has already closed.
7. NaN while `idx < 0` (incomplete mapping history).

## 6. Signal rules (exact boolean)

From `goldm_dema_mtf_futures._signal_at` (`:260-277`), evaluated on closed base (15m) bar `i`:
- `BUY  = close[i] > h1[i]  AND close[i−1] <= h1[i−1]  AND h15[i] < h1[i]`   (`:271`)
- `SELL = close[i] < h1[i]  AND close[i−1] >= h1[i−1]  AND h15[i] > h1[i]`   (`:274`)
- SKIP if any of `h1[i], h1[i−1], h15[i]` is NaN (`:264`).
- Signal-bar SL: `long SL = min(low[i], low[i−1])`; `short SL = max(high[i], high[i−1])` (`:273-276`).
- Trigger barrier = signal bar's own `high` (long) / `low` (short) (`:287-288`).

## 7. State machine (`next()`, always-in-market)

Order per bar (`:139-148`):
1. `_execute_pending()` (`:144, :150-173`) — handles BOTH:
   - **Reversal exit**: if this bar is `signal_idx+1` and a position is open → `_finalize('open', reason)` (exit AT THIS BAR'S OPEN) (`:161-163`).
   - **Breakout entry**: if `side=LONG and high[0] > trig_high` (or SHORT `low[0] < trig_low`) → fill at **this bar's OPEN** (`:167-173`).
2. `_check_sl_hit()` (`:146, :249-258`) — if `long low[0] <= sl` or `short high[0] >= sl` → `_finalize('close', 'stop_loss_hit')` (exit at bar CLOSE; can be SAME bar as entry → same-bar round-trip).
3. `_check_signals_for_next_bar()` (`:148, :279-302`):
   - Flat: `buy → pending_exec=('LONG', ...)`; `sell → pending_exec=('SHORT', ...)`. **Replaces** any prior unfilled pending (`:290-298`).
   - Position SHORT + buy-cross → `pending=('LONG', ..., 'bull_reversal')` (`:299-300`); Position LONG + sell-cross → `pending=('SHORT', ..., 'bear_reversal')` (`:301-302`).
   - No pending expiry; a pending lasts until it crosses, is replaced by a newer signal, or the feed ends.

Exit-reason strings: `bull_reversal`, `bear_reversal` (reversal exits), `stop_loss_hit` (SL), plus `eod_close` (live-only, DISABLED — see LIVE spec §8).

## 8. Execution & P&L

- Entry fill = OPEN of the bar that crosses the trigger (`:177`).
- Reversal exit = OPEN of the bar right after the opposite signal (`:163, :217`).
- SL exit = CLOSE of the bar breaking the stop (`:217`).
- `gross = (exit−entry)*MULTIPLIER*QTY` (long) / `(entry−exit)*…` (short) (`:221-224`).
- Charges (`futures_charges`, `:68-79`):
  - `buy_turn = entry*mult*qty`; `sell_turn = exit*mult*qty` (NOT side-aware).
  - `stt = sell_turn*0.01%` — for a SHORT this is entry leg; for LONG it is the exit leg (see GAP report: live model is side-aware).
  - `exchange = (buy+sell)*0.0026%`; `sebi = (buy+sell)*0.0001%`; `brokerage = 20*2`; `gst = (brokerage+exchange+sebi)*18%`.
- `net = gross − charges['total']` (`:226`); equity `cash += net` (`:227`).
- No slippage, no latency, no market-state gating, no margin-block effect at 300k capital (worst margin GOLDM ≈ 6.5%×1.6M ≈ 105k < 300k).

## 9. Reference outputs (expected = the values the parity run must reproduce)

- GOLDM: **7 trades**, WR **14.3%**, Net **₹−28,717** (see `%TEMP%\opencode\ref_show_15_15_60.txt`).
- SILVERM: **10 trades**, Net **₹−9,276**.
- Trade detail schema: `side, entry_time, entry_price, sl_price, exit_time, exit_price, exit_reason, holding_minutes, gross_pnl, charges, pnl`.