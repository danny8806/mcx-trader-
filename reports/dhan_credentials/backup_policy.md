# Dhan Credential Backup & Rotation Policy (2026-09-06 audit)

Scope: `C:\Users\pc\Desktop\MCX-TRADER` (app) + `C:\Users\pc\Desktop\FYERS APIS\dhan` (archive).

## Single source of truth
- Exactly ONE TOTP base32 seed + ONE PIN pair exist, inside `renew_token.py`
  (verified by a full 198-file inventory scan; no duplicates anywhere).
  This file is the sole recovery source. Never duplicate it as plaintext.

## Rotation triggers (refresh `DHAN_ACCESS_TOKEN`)
1. exp <= 2h away (`data/dhan/rest_client.py` grace window, daily 7AM + 6h safety).
2. DH-901 / DH-906 "Invalid Token" rejections observed.
3. Any successful re-login on the same Dhan account elsewhere: a new
   `generate_token()` REVOKES the previous JWT server-side. Its `exp` stays in
   the future but the token is dead — time-based expiry checks CANNOT detect this.

## Single active session rule
- After every successful mint, update BOTH:
  - `data/db/dhan_token.json` (gitignored, deleted by runner teardown)
  - `DHAN_ACCESS_TOKEN=` in `mcx-trader.env` (gitignored `*.env`)
- Leaving the env token stale after an engine auto-renew produces silent
  DH-901/DH-906 until refreshed (observed today).

## Dhan hard limits
- Mint rate: once per **2 minutes** server-side; app enforces a local 130s cooldown.
  Do NOT retry sooner (rejected with `Token can be generated once every 2 minutes`).
- TOTP must be read in a fresh ~30s window; an edge call returns `Invalid TOTP` —
  retry in the NEXT window (the app's retry loop handles this).

## Backups
- Expired JWTs: `dhan\expired\` (ACL'd pc-only, deleted after audit).
- PIN/TOTP: no plaintext copies; only `renew_token.py`.
- Off-machine transfer of credential material is forbidden (no other host,
  container, or cloud object store).

## Recovery drill
1. Verify seed + PIN present in `renew_token.py`.
2. Mint via the app's own `DhanRESTClient.renew_token()` (never ad-hoc auth).
3. Confirm `fundlimit` HTTP 200.
4. Seed env + token file.
5. Re-validate with the closed-market `rest_probe` (AUTH ok).

## Audit evidence
- reports/dhan_credentials/credential_inventory.json, credential_classification.json,
  credential_matching.json, dhan_rest_validation.json, dhan_ws_validation.json,
  token_lifecycle.json, runtime_config.json, FINAL_CREDENTIAL_REPORT.md
- reports/dhan_auth_contract.json, reports/dhan_credential_inventory.json