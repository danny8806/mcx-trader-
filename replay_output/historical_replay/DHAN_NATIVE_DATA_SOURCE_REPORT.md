# DHAN NATIVE DATA SOURCE REPORT

## Purpose
Prove that the historical replay of this project (and the live engine) consumes
**native Dhan REST candles** fetched from the live Dhan charts endpoint —
**never resampled, never aggregated from 5m** — for 5m, 15m and 1h timeframes.

## Source & Authentication
- **Endpoint (live):** `POST https://api.dhan.co/v2/charts/intraday` (max 90 calendar
  days per call), `X-Access-Token` header, Dhan REST v2.3.
- **Client id:** `1102461741` (Dhan partner account).
- **Auth:** PIN + TOTP `renew_token` flow (renew_token.py) minting a fresh
  access token saved to `data/dhan_token.json` (current token expiry
  2026-09-07T00:29 IST).
- **Network check to api.dhan.co:** verified HTTP 200.
- **Instruments / security ids:**
  - `GOLDM  -> 569003` (MCX GOLDM Oct 2026, multiplier 10)
  - `SILVERM -> 483080` (MCX SILVERM Nov 2026, multiplier 5)

## Capture Tooling
- `tools/dhan_historical_download.py` — bounded 25-day chunked downloader
  (rate-limit TokenBucket 3.5/sec; 429/auto-renew retry), emits per
  (instrument, interval) **raw rows** as `*.json` plus `*.IST.csv`, records
  SHA-256 in `replay_output/checksums.json`, and builds
  `replay_output/replay_manifest.json`.
- **Window:** 2026-08-03 09:00 IST → 2026-09-04 23:25 IST (last closed 5m bucket
  of the last closed session; 09-05 is Saturday / 09-06 Sunday — no data).

## Native Intervals — Zero Resampling Proof
Every candle is a **native** exchange bar fetched directly at its own native
interval via Dhan REST (`interval = "5" | "15" | "60"`). No 5m→15m / 5m→1h
aggregation was performed anywhere in the download or replay path. The replay
adapter (`tools/dhan_historical_replay.py`) and the live engine
(`core/candle_fetcher.py:_fetch_candle`) both read native candles:
- 5m  → fetch interval `"5"`  (native 5-minute bars)
- 15m → fetch interval `"15"` (native 15-minute bars)
- 1h  → fetch interval `"60"` (native 60-minute bars)

The manifest asserts `"resampling_used": false` and `"intervals_native": [...]`.

## Dataset Counts / Ranges / Hashes
| Instrument | Native Interval | Candles | First bar (IST)        | Last bar (IST)         | SHA-256 (12) |
|-----------|-----------------|---------|------------------------|------------------------|--------------|
| GOLDM     | 5               | 4352    | 2026-08-03 09:05       | 2026-09-04 23:25       | `de3aac412dcb` |
| GOLDM     | 15              | 1452    | 2026-08-03 09:15       | 2026-09-04 23:15       | `88c18b2b0779` |
| GOLDM     | 60              | 373     | 2026-08-03 10:00       | 2026-09-04 23:00       | `a78c850abf53` |
| SILVERM   | 5               | 4352    | 2026-08-03 09:05       | 2026-09-04 23:25       | `06ad0f3d4a52` |
| SILVERM   | 15              | 1452    | 2026-08-03 09:15       | 2026-09-04 23:15       | `1c328418c61e` |
| SILVERM   | 60              | 373     | 2026-08-03 10:00       | 2026-09-04 23:00       | `e0f861be0068` |

Total native bars: **12,354** (6,177 GOLDM + 6,177 SILVERM). Trading days: 25.

## Determinism / Immutability
- Raw rows are stored as immutable JSON; SHA-256 is recorded at ingest time in
  `replay_output/checksums.json` and any tamper is detectable by re-hash.
- `HistoricalDhanReplaySource` re-serves rows **read-only** and in fixed order;
  the engine consumes them through the **exact production fetch contract**
  (`fetch_historical_candles(symbol, interval, from_date, to_date)` returning
  `[epoch_open, o, h, l, c, v]` rows).

## Lookahead Guarantee
The replay adapter gates every served row by `end_ts <= cutoff_ts`
(`cutoff_ts = replay start = 2026-09-02 09:00 IST`). Warmup therefore sees
**only bars already closed at that instant** — identical to the live engine at
the same start instant. No future/incomplete bar is ever fed. See
`tools/dhan_historical_replay.py:HistoricalDhanReplaySource.fetch_historical_candles`.

## Replay Surface (from this source)
Warmup fetch windows: **12** (per-strategy fast + mid + slow HTF fetch).
Full finite replay (09-02 09:00 → 09-04 23:25): **1,486 native bars** in the
replay window → **40 signals, 23 trades, 44 orders, 44 fills, 23 positions**.

## Cross-check
- `data/dhan/rest_client.py` `fetch_intraday` (L424) — native interval fetch.
- `data/dhan/adapter.py` `fetch_historical_candles` (L203-226) — live adapter.
- `core/candle_fetcher.py:_fetch_candle` (L206-271) — live native-HTF fetch.
