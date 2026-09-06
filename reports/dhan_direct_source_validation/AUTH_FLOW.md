# Auth Flow — AUTH_FLOW.md

## How Dhan authentication actually works (verified in this mission)

1. **PIN + TOTP override** — `pyotp.TOTP(<seed>).now()` + `dhanhq.DhanLogin(client_id).generate_token(pin, totp)` → `{"accessToken": "<JWT 304 chars>", "expiryTime": "<ISO>"}`.
2. **Token file** — app persists `{"access_token": "<JWT>"}` to `data/db/dhan_token.json` (gitignored).
3. **JWT is the source of truth** — `rest_client.ensure_token()` renews only when token file is empty or `exp` `< 1h` grace (`data/dhan/rest_client.py:134-222`).
4. **Scheduler** — proactive daily 7 AM renewal + 6h safety check (`rest_client.py:225-292`).
5. **Rate limit** — Dhan allows token generation **once every 2 minutes**. App enforces a 130 s local cooldown (`rest_client.py:164-176`), logs `[auth] rate-limited, wait Ns`.
6. **Auth-failure auto-renew** — on HTTP 401 / `DH-906 Invalid Token` / `Authentication_Failed`, `_post()` renews once then retries (`rest_client.py:345-378`).

## Finding: token supersession (new)
Every successful `generate_token` **invalidates the previous token server-side**. During this session the P4-mint (14:35 IST, exp 09-07T09:05Z) was superseded by later mints (15:31, 15:36 IST); an independent raw request with the superseded token returned `400 DH-906 Invalid Token`. This is normal Dhan lifecycle (one active token per client). Implication: any token file/env copy must be refreshed after each renewal. Current live token: minted 15:36:41 IST, exp `2026-09-07T15:36:41+05:30`, persisted in `data/db/dhan_token.json` and `mcx-trader.env`.

## Security invariants (never relaxed)
- Tokens/PIN/TOTP seeds NEVER printed, logged, or committed. All artifacts use lengths/expiry/prefixes only.
- `mcx-trader.env`, `data/db/dhan_token.json` are machine-local and gitignored.
- No order endpoints reachable from this repo's Python (paper broker only).