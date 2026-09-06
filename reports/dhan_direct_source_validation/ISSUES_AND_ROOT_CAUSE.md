# ISSUES & ROOT CAUSE — where / why each check failed

Every failing check from the mission, WHERE in the code it comes from, WHY it failed, and what to do about it.

---

## 1. Indicator / warmup arrays too large + silver out-of-order (ALL the 13 v2 failures)

- **WHERE**: `trading_engine.py:1091-1103` — the HTF warmup loop fed candles for `fetch_days` (14 days → 10 trading days) **untruncated** and in Dhan's **raw API order**, while the fast path (`trading_engine.py:1061-1090`) truncates to the last `last_days` (5) trading days and sorts ascending.
- **WHY**: gold 15m = 290 older-day bars FIRST then 291 recent bars → audit compared the first 291 slots → mismatch 291. Silver 15m = 291 recent bars first then 290 older bars appended → `_end_times` became non-monotonic → `out_of_order=1` and the `bisect` lookup in `get_mapped_value` could read a stale older value for silver until a newer candle closes live.
- **FIX (APPLIED)**: HTF loop now truncates to last `last_days` **and** sorts ascending. Verified: gold/ silver 15m = 291/291, 1h = 75/75, `n_mismatch=0`, `oo0=0`, `maxdev=0.0`; audit 12/12 PASS.
- **Your call**: NONE — fixed and verified.

## 2. SILVERM `open < low` (09:00 IST 2026-09-03, all timeframes)

- **WHERE**: Not in the code — proven server-side. Independent raw `POST https://api.dhan.co/v2/charts/intraday` (docs-spec, fresh token, no app code) returns o=238988 < l=239812 on 5m/15m/60m. App output byte-identical.
- **WHY**: Physical impossibility in Dhan's own ingested SILVERM series (GOLDM at the same bar is clean). Likely a bad low print from the tick-sourced OHLC aggregation.
- **TREATMENT (APPLIED, per your trade-focused directive)**: runner `rest_probe` no longer `self.fail()`s on it; it records full evidence (`ohlc_bad_rows`, `min_low`, `max_high`, classification) in `runtime_v3/rest_probe.json` and logs a `DATA-NOTE`. Verdict unaffected.
- **Your call**: How to treat this 1 bad bar going forward: (a) leave as-is + documented, (b) drop that bar during warmup (a data-cleanup shim), or (c) raise it with Dhan support. It cannot affect trades on 5m bars reliably (only 1 bar in 7 days), but a bar with `low > open` inflates the low side of the range.

## 3. Restart test `bars_processed > 0` gate unsatisfiable (CHECK FAILED/UNSURE, not counted)

- **WHERE**: `closed_market_runtime_test.py:1181-1183` (restart_test gate).
- **WHY**: Requires ≥1 live candle after a warm restart; in a closed market there are ZERO live candles — structurally unsatisfiable, not a code bug. IDs matched, engine READY, positions restored, WS re-subscribed, snapshot ticks received (GOLDM 152950.0 / SILVERM 239495.0).
- **FIX**: none required — restart_test doesn't call `fail()`, verdict stays VERIFIED.
- **Your call**: On an OPEN-market day, re-run the full suite so this gate actually executes; or accept it as closed-market-only-unsatisfiable.

## 4. Token expired / invalid (P3/P4) and later supersession

- **WHERE**: initial `dhan_token.json` had `exp 2026-09-04T12:22Z` (401 DH-901 at 137ms). P4 renewal fixed it (mint 14:35). During the session Dhan invalidated that P4 token server-side once a new `generate_token` was issued (new finding: **supersession** — one active token per client).
- **WHY**: Dhan's token model (newest mint wins); not an app bug. The app's 401 → auto-renew → retry handles it (`rest_client.py:345-378`).
- **TREATMENT**: fresh token minted 15:36 IST (exp 09-07T15:36+05:30) live-valid, in `data/db/dhan_token.json` + env.
- **Your call**: NONE needed; note FYERS source file now holds a superseded token (harmless; that beat is archive-only).
- **IMPORTANT**: `dhan_token.json`/env must be refreshed after **any** renewal — a stale copy may DH-906 (observed), and the app auto-recovers with a 1-retry renewal.

## 5. Startup token-renew cooldown noise at bp01/06 (benign)

- **WHERE**: `rest_client.py:225-292` scheduler + boot path.
- **WHY**: every engine start attempts renewal even with a valid token file; Dhan enforces one mint per 2 min → 401/rate-limit logs (`wait 123s` etc.). Non-fatal; old token stays in use until renewal lands.
- **Your call**: optional cleanup — only attempt renewal when the token file is missing/expired (skip 'renew if boot-time' noise). Not required.

## 6. No `get_fund_limit` (fund/live balance read) on DhanRESTClient

- **WHERE**: `data/dhan/rest_client.py` — endpoint method absent (AttributeError when probed).
- **WHY**: repo never needed live fund reads (paper/trade-ledger reconciliation via canonical DB).
- **Your call**: add `get_fund_limit()` (read-only `GET /v2/fundlimit`) if you want live margin/fund balance in reconciliation; optional.

---

**Disposition summary**: 1 code bug → FIXED+verified. 1 raw-data artifact → evidence-classified. 1 unsatisfiable gate → documented, not a failure. Token lifecycle → renewed + characterized. Startup noise + missing fund-reader → optional decisions above.