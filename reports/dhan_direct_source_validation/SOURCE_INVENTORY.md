# Dhan Token Direct-Source Validation — SOURCE_INVENTORY.md

## Scope
Mission P1/P2: inventory the three named source files WITHOUT printing their contents, mask everything, and trace the real import/credential authority.

## Source files (masked inventory)

| File | Exists | Size | Kind |
|---|---|---|---|
| `C:\Users\pc\Desktop\FYERS APIS\dhan_token.json` | yes | JWT token file | **authoritative token source** |
| `C:\Users\pc\Desktop\FYERS APIS\config.py` | yes | small | FYERS-only config |
| `C:\Users\pc\Desktop\FYERS APIS\dhan_broker.py` | yes | 329 lines | Dhan broker adapter + renewal tool |

## Authority trace (actual imports, not assumptions)

- `dhan_token.json` structure: `{"access_token": "<JWT>"}`. The JWT payload (decoded locally, never printed) carried `iat` / `exp` claims.
- At start of this mission the source token was **expired**: `exp` = 2026-09-04T12:22:35Z (minted 2026-09-03T12:22Z). Live validation returned HTTP 401 `DH-901` at 137 ms.
- `config.py` is **FYERS-only**: APP_ID ends `-100`, base `https://api-t1.fyers.in/`, `TOKEN_FILE = access_token.json`. It does **NOT** reference Dhan at all. → NOT part of the Dhan chain.
- `dhan_broker.py` imports `fyers_data`, which imports `config` + `fyers_auth`; import verified OK. Contains `renew_token()` using PIN + TOTP seed (local hash prefixes verified identical to `renew_token.py`: PIN `9e2945b2f0…`, seed `b3ffadb0c0…` — one authoritative pair). No repository `.py` imports `dhan_broker.py` → it is an **archive/backfill tool**, not part of the live MCX chain.
- Live MCX credential chain remains: `data/dhan/rest_client.py` → `data/db/dhan_token.json` + env `DHAN_*` → adapter. Verified by import graph.

## Verdict (P2)
- `dhan_token.json` = authoritative Dhan token source (was expired → renewed, see TOKEN_RENEWAL).
- `config.py` = FYERS-only, out of scope.
- `dhan_broker.py` = valid archived renewal/backfill tool; PIN/TOTP pair consistent with `renew_token.py`.