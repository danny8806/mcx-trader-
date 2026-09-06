# REST Validation — REST_VALIDATION.md

## Independent raw request (user-mandated, docs-first)

Per Dhan V2 API docs (dhanhq.co/docs/v2/historical-data):
`POST https://api.dhan.co/v2/charts/intraday` — body `securityId, exchangeSegment, instrument, interval, oi, fromDate, toDate ("YYYY-MM-DD HH:MM:SS")`, header `access-token`. Response = parallel arrays `open/high/low/close/volume/timestamp` (epoch = bar START; IST).

Independent raw call (no app code), SILVERM `securityId=483080, MCX_COMM/FUTCOM`, window 08:55–09:20 IST 2026-09-03:

| Interval | bar 09:00 IST | HTTP |
|---|---|---|
| 5 | o=238988 h=240699 **l=239812** c=240370 vol=1422 | 200 |
| 15 | o=238988 h=240699 **l=239812** c=240157 vol=2233 | 200 |
| 60 | o=238988 h=241188 **l=239812** c=241188 vol=4770 | 200 |

All three frames return **o<l** in Dhan's own server data (physically impossible; any OHLC convention broken). APP output is byte-identical on all three (see SILVERM_DATA_FORENSIC) → the app faithfully returns server data; **no code error**.

## App endpoint parity check
App `rest_client._post("/charts/intraday", ...)` (`rest_client.py:424-443`) matches the docs exactly: method, path, params, response parse (`_to_candles` zips `open,high,low,close,volume,timestamp` in documented order, lines 385-402). GOLDM `security_id=569003`, SILVERM `483080`, `MCX_COMM`/`FUTCOM`.

## runtime_v3 rest_probe (authenticated, live token)

| Instrument | TF | count | dup | monotonic | ohlc_bad | min_low | max_high | lat |
|---|---|---|---|---|---|---|---|---|
| GOLDM | 5 | 871 | 0 | yes | 0 | 149900 | 156381 | 0.165s |
| GOLDM | 15 | 291 | 0 | yes | 0 | 149900 | 156381 | 0.063s |
| GOLDM | 60 | 75 | 0 | yes | 0 | 149900 | 156381 | 0.052s |
| SILVERM | 5 | 871 | 0 | yes | **1** | 234024 | 244640 | 0.079s |
| SILVERM | 15 | 291 | 0 | yes | **1** | 234024 | 244640 | 0.265s |
| SILVERM | 60 | 75 | 0 | yes | **1** | 234024 | 244640 | 2.346s (1×429 retry) |

- The 3 SILVERM anomalies are the SAME bar captured from a 7-day window (09:00 IST 2026-09-03) — classified, not gated (see ISSUES_AND_ROOT_CAUSE.md).
- `rest_stats`: ok=6, empty=0, retry=1 (the 60m call), 429 backoff non-fatal.

## Fund/account read endpoint
App `DhanRESTClient` exposes no `get_fund_limit` method (AttributeError). Read-only reconciliation uses candle/position services only in this repo. (Not blocking: mission fallback "account/fund read-only endpoint if available" → margin/account reconciliation evidence is in DATABASE_RECONCILIATION + API_RECONCILIATION via canonical DB.)