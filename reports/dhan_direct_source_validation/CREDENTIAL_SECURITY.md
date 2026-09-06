# Credential Security — CREDENTIAL_SECURITY.md

## Rules enforced throughout the mission
- Tokens, PIN, and TOTP seeds were NEVER printed, logged, screenshotted, or committed. Reports/artifacts only carry: token length (304), expiry timestamps, first/last 4 hash-prefix chars.
- All `reports/dhan_direct_source_validation/*` artifacts are secret-free.
- Workspace `.gitignore` covers `*.env`, `data/db/*.json`, TF token backups. No secret committed in this mission.

## Secret scan (P23)
Fixture-paired scan of `data/dhan/rest_client.py`, `data/dhan/websocket_client.py`, `data/dhan/adapter.py`, `reports/**`, and runner sources for the app's JWT + DHAN_* placeholder strings (pairing fixture with expected-match to auto-pass legitimately matching library versions). Result: no changed files introduced unpaired secrets during v2/v3 work (clean scan at each report step). Final scan re-run is the last P23 step.

## Credential homes
- Live: `data/db/dhan_token.json` + `mcx-trader.env` (gitignored, machine-local).
- Source/backup: `C:\Users\pc\Desktop\FYERS APIS\dhan_token.json` + `dhan/backup/dhan_token_20260906_143508.json`.
- Rotation: token superseded lifecycle documented (TOKEN_VALIDATION); current live token minted 15:36 IST 2026-09-06, exp 2026-09-07T15:36+05:30.

## Runner behavior
No credentials logged by the runner at any verbosity; token file deleted at teardown; runners re-seed from env only when the file is absent.