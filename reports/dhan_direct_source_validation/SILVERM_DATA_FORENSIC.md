# SILVERM Data Forensics — SILVERM_DATA_FORENSIC.md

## Question
Is the `open < low` bar (2026-09-03 09:00 IST SILVERM) a real Dhan server artifact or an APP parsing bug?

## Method (user-mandated: independent request, docs-first, then code)
1. Read Dhan API docs → `POST /v2/charts/intraday` (fields + response arrays confirmed).
2. Sent a **fully independent raw request** (httpx/requests direct to `https://api.dhan.co/v2/charts/intraday`, fresh token, no app code) for intervals 5/15/60 over 08:55–09:20 IST.
3. Compared against the app's own fetch output for the identical window.

## Result table (0900 IST bar, all live HTTP 200)

| interval | RAW independent (o,h,l,c) | APP output (o,h,l,c) | o<l? |
|---|---|---|---|
| 5  | 238988, 240699, 239812, 240370 | 238988, 240699, 239812, 240370 | **yes** |
| 15 | 238988, 240699, 239812, 240157 | 238988, 240699, 239812, 240157 | **yes** |
| 60 | 238988, 241188, 239812, 241188 | 238988, 241188, 239812, 241188 | **yes** |

## Conclusion
- RAW Dhan server response itself violates OHLC (o=238988 < l=239812) on all three timeframes. **The anomaly is server-side.** The app output is byte-identical to the raw response on every frame → the app's `_to_candles` (`rest_client.py:385-402`) is a faithful, spec-correct proxy. **No code error found.**
- GOLDM at the same timestamp is clean (o=h=l=152850 at 09:00) → instrument-scoped data-quality glitch in Dhan's ingested SILVERM series, not systemic.
- Samples BELOW the anomaly bar are normal (09:05 5m: o=240301 h=240450 l=240022 c=240450, ohlc_ok=True).

## Treatment
Recorded in `runtime_v3/rest_probe.json` → `instruments.SILVERM.{5,15,60}.ohlc_bad_rows` (full o/h/l/c evidence). Classified as a documented known data anomaly — logged as a DATA-NOTE, NOT a verdict failure (user directive: reconciliation is trade-focused; indicator/warmup history re-warms every restart). No data fabricated, no bar injected, no validator weakened for correctness — the anomaly is simply separated from the trade/connectivity verdict.