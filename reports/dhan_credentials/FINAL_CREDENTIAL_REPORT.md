# FINAL CREDENTIAL REPORT — Dhan Secure Validation

Date 2026-09-06  |  Machine: local Windows (user pc only)  |  Repo: `MCX-TRADER`

**No secret material appears anywhere in this report.** Client id shown masked
(`1102…1741`); PIN, TOTP seed, and JWTs are not reproducible from this document.

---

## 1. Purpose
Recover real Dhan credentials from the developer machine, configure the app
safely, prove read-only connectivity (REST + WS) against the live Dhan feed, run
the UNMODIFIED closed-market runtime gate in PAPER mode with the REAL feed, and
record an honest, evidence-backed verdict — all without printing, committing, or
sending secrets, placing orders, or weakening the existing verification gate.

## 2. Result summary

| Item | Outcome |
|---|---|
| Credential inventory | 198 files scanned; 73 Dhan-referencing, 66 FYERS-only, 85 credential-bearing |
| Renewal material | FOUND (one PIN + one TOTP seed pair, in `FYERS APIS\renew_token.py`) |
| Dhan access token | EXPIRED at audit start → RENEWABLE → **renewed and validated** |
| App configuration | `mcx-trader.env` now carries DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN / TRADING_PIN / TOTP_SECRET (all gitignored, ACL pc-only) |
| REST (read-only) | PASS — fundlimit HTTP 200 (171–174 ms), GOLDM/SILVERM 5/15/60m candles (871/291/75 bars) |
| WS (read-only) | PASS — connected=True, sub=1 (569003 GOLDM, 483080 SILVERM), 2 feed frames, parse_err=0 |
| Phase-0 gate | `tests/full_system_verification.py` **106/106 VERIFIED** (baseline x2 + final regression) |
| PAPER config | `config/settings.json` environment = `paper`; safety gate PASS both runs |
| Closed-market runtime gate | **NOT_VERIFIED_CLOSED_MARKET_LIVE_RUNTIME** — 13 precisely root-caused failures (see §5). Not weakened. |

## 3. Phase evidence map (1–19)

| Phase | Task | Evidence file |
|---|---|---|
| P1 | Inventory scan (198 files, masked) | reports/dhan_credential_inventory.json, reports/dhan_credentials/credential_inventory.json |
| P2–P3 | Classification (73 dhan / 66 fyers / 85 credential-bearing) | credential_classification.json |
| P4 | Auth contract (fields, renewal triggers, cooldown 130s, WS authType=2) | reports/dhan_auth_contract.json |
| P5 | Matching: client FOUND, token EXPIRED_RENEWABLE, PIN+TOTP FOUND → injected; DASHBOARD_API_KEY MISSING (no impact) | credential_matching.json |
| P6 | Archive structure + security README | `FYERS APIS\dhan\` + README_SECURITY.txt |
| P7 | Env injection + ACL hardening (pc R/W only) | runtime_config.json |
| P8 | Git security audit — clean (no JWT/PIN/seed/password tracked) | runtime_config.json |
| P9–P10 | Mint + REST validation | dhan_rest_validation.json |
| P11 | WS lifecycle validation | dhan_ws_validation.json |
| P12 | RENEWABLE proven (actual mint via repo rest_client) | token_lifecycle.json |
| P13 | Gate 106/106; P18 paper env confirmed | runtime_config.json |
| P14 | Closed-market runtime w/ REAL Dhan + PAPER (2 runs) | reports/live_closed_market_verified/ (v1), reports/live_closed_market_verified_v2/ (v2) |
| P15 | Token lifecycle documented | token_lifecycle.json |
| P16 | Backup & rotation policy | backup_policy.md + README_SECURITY.txt |
| P17 | Runtime config verified | runtime_config.json |
| P19 | This report + final battery | FINAL_CREDENTIAL_REPORT.md |

## 4. Key authentication facts (masked)

- `renew_token.py`: `DhanLogin(client_id).generate_token(pin, pyotp.TOTP(seed).now())`.
- Token file `data/db/dhan_token.json` is gitignored; runner teardown deletes it
  (re-seeded from env each run).
- Observed Dhan behaviors:
  - New login **revokes** the previous JWT while its `exp` stays in the future
    (REST DH-906 / WS DH-901). Time-based expiry checks cannot detect this.
  - Mint rate limit = 1 per 2 min server-side; app enforces 130s cooldown.
  - TOTP read at the edge of a 30s window returns `Invalid TOTP`; retry next
    window succeeds (observed 214 ms mint).
- Final live token: VALID, exp 2026-09-07T08:52:03Z (env + token file).

## 5. P14 verdict analysis (the important part — honest, not waived)

Run v1 (`clm-20260906-132623`, 30 min): 17 failures. Run v2
(`clm-20260906-141200`, 2 min soak, probe fix): 13 failures. Both
`NOT_VERIFIED`. Root causes:

| Failure (v2 count) | Root cause | Resolution |
|---|---|---|
| GOLDM OHLC anomalies (811/285/73) | **Bug in the runner's OHLC invariant**: `close <= min(open, low)` typo tested `close ≤ low`; raw Dhan bars are valid | Fixed invariant to `high >= max(o,c)` and `low <= min(o,c)`; v2 shows GOLDM 0 anomalies. This CORRECTS a broken check; no acceptance rule was weakened. |
| SILVERM OHLC (1/1/1) | GENUINE server artifact: 1 session gap-open bar (2026-09-03 09:00 IST) where `open=238,988 < low=239,812` (last-print open below the day's traded low) | Kept as flagged; monitored. Probe now correctly surfaces real anomalies. |
| Indicator audit (10/12) | Reproducible (identical in both runs): engine **5m slot arrays correct (871), 15m/1h slot arrays ~2x truth (581/150)** with dedup 2034/450 and one out-of-order; gold 15m/1h values diverge from native REST truth while silver 15m matches exactly. 5m-stream feeds are exact; aggregate-timeframe warmup/feed path duplicates entries and mixes resampled vs native OHLC | Do NOT weaken gate. Flagged for an engine warmup/shared-stream audit outside this credential mission (protected trading code untouched). Indicator MATH itself verified by unit fixtures (13/13). |
| Restart test | With auth available, gate requires `_bars_processed > 0` after restore — **structurally unsatisfiable in a closed market** (no live candle events; strategy ids/engine READY/account present all PASS) | Kept failing, documented as environment-unsatisfiable, not a regression. |
| Reconnect (v1 only) | Emitted during v1 because the mid-run auto-renew revoked the seed token and WS kept resending it (DH-901 cycling). | v2 (no rotation) **reconnect PASS**: closed→disconnected→connected, recv 4→8, sub 1→2. WS reconnect machinery proven. |

This is the gate working as intended: with real Dhan connectivity, previously
dark paths (aggregate-warmup duplication, session-gap opens, token revocation
lifecycle) are now visible. The app is structurally sound (REST/WS connected,
PAPER only, DB/lineage/signal/fault/restart-state/soak/reconcile all PASS) but
the mission does not claim `VERIFIED` while any real-data divergence remains.

## 6. Security measures applied
- ACLs pc-only (R/W) on mcx-trader.env, renew_token.py, `dhan\` tree, dhan_token.json, access_token.json (restored on 2026-09-06 after an inheritance reset stripped subpath ACEs).
- `*.env` and `data/db/` gitignored; git audit clean.
- All report/tool/terminal output masked; temp helpers keep no secrets and live under the pre-approved temp dir.
- No orders; PAPER environment verified twice.

## 7. Recommendations (follow-up, outside this report's scope)
1. Engine warmup/shared-stream duplication for aggregated (15m/1h) timeframes — root-cause and fix in a dedicated audit (currently NOT_VERIFIED driver).
2. Make WS reconnect react to DH-901/908 by forcing a RE-MINT (renewal currently only keys off time-expiry, so a revoked-but-unexpired token loops).
3. Decide whether `restart_test`'s auth branch should count warmup REST-seeded bars as processed bars in closed-market conditions (gate semantics question, not a weakening).
4. On open-market DAYS, re-run the closed-market battery at a weekend/holiday to clear the unsat restart branch and confirm clean indicator arrays.
5. Keep `DHAN_ACCESS_TOKEN` in env in sync with every auto-renew (single-session revoke risk).

## 8. Artifact index
`reports/dhan_credentials/` (credential_inventory, credential_classification, credential_matching, dhan_rest_validation, dhan_ws_validation, token_lifecycle, runtime_config, backup_policy.md, FINAL_CREDENTIAL_REPORT.md);
`reports/dhan_auth_contract.json`, `reports/dhan_credential_inventory.json`;
`reports/live_closed_market_verified/` (v1) and `reports/live_closed_market_verified_v2/` (v2 finals).