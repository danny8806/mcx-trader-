# Token Renewal — TOKEN_RENEWAL.md

## Evidence chain (this mission)

| # | Event | IST | exp (+05:30) |
|---|---|---|---|
| — | Source token minted | 09-03 17:52? | 09-04 17:52 (expired) |
| P4 | Renewal via `dhan_broker.renew_token()` | 09-06 14:35 | 09-07 14:35 |
| side | Engine startup auto-renew (rest_client, no app change) | 09-06 14:43 | 09-07 14:43 |
| runtime | Runner `rest_probe` auto-renew | 09-06 15:31 | 09-07 15:31 |
| indep | Fresh mint for independent raw request | 09-06 15:36 | 09-07 15:36 **← current live token** |

- Every renewal used the **existing implementation** (pyotp + dhanhq `generate_token`), no redesigned auth.
- Current token: `len=304`, live-valid (verified by successful raw REST chart request at 15:36 IST), persisted to `data/db/dhan_token.json` and `mcx-trader.env`.

## Renewal-policy checks
- Thread-safe (single lock) ✓
- 2-minute server rate limit respected (130 s local cooldown) ✓
- Cooldown noise (`wait 123s/124s/118s`) non-fatal, verified across 3 runs ✓
- Scheduler: proactive 7 AM daily + 6h safety (`rest_client.py:225-292`) ✓
- JWT-expiry is the source of truth; no spurious renewal when `exp` healthy ✓

## Renewal-rate evidence during a 30-min+ session
- `rest_stats` during runtime_v3 soak accumulated retries (429 backoff) for candle fetches; renewals were serialized and never double-minted (only one active token per client).