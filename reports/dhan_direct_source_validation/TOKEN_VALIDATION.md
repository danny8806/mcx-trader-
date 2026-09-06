# Token Validation — TOKEN_VALIDATION.md

## P3 — Live validation of the source token

| Check | Result |
|---|---|
| Source file | `C:\Users\pc\Desktop\FYERS APIS\dhan_token.json` (authoritative) |
| Initial state | EXPIRED — `iat` 2026-09-03T12:22Z, `exp` 2026-09-04T12:22Z |
| Live probe | HTTP 401 `DH-901 "Client ID or user generated access token is invalid or expired"` |
| Latency | 137 ms |
| Conclusion | Token genuinely expired; renewal required and impossible via API alone (PIN+TOTP needed) |

## P4 — Renewal via the EXISTING implementation

- Reused `dhan_broker.renew_token()` (same PIN/TOTP pair hash-verified against `renew_token.py`).
- Result: `len=304`, `exp = 2026-09-07T09:05:09+00:00` (minted 14:35 IST 2026-09-06).
- Persisted to all three stores: FYERS `dhan_token.json`, app `data/db/dhan_token.json` (`{"access_token": ...}`), `mcx-trader.env` `DHAN_ACCESS_TOKEN`.

## P5 — Backup before mutation

- `C:\Users\pc\Desktop\FYERS APIS\dhan\backup\dhan_token_20260906_143508.json` created + verified present.
- Backup permissions checked (user-readable).

## Token lifecycle observations

- Renewal respects Dhan 2-minute mint rule (local 130 s cooldown; `[auth] rate-limited` messages are benign backoff noise).
- Startup renewal attempts happen even when a valid token is loaded (transient 401 / empty-cache path); they are non-fatal — old token continues to be used until renewal lands.
- **Supersession (new finding):** a newer `generate_token` invalidates older tokens (see AUTH_FLOW). The FYERS source file now holds the P4 mint, which is superseded server-side; the live valid token is in `data/db/dhan_token.json` + env (mint 15:36 IST).