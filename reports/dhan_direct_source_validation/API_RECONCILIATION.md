# API Reconciliation — API_RECONCILIATION.md

## Health API (dashboard server, runtime_v3)
- uvicorn server booted at `127.0.0.1:<free port>` (dashboard lifespan starts the engine — production path).
- `api_samples = 6` health polls collected during the run; all returned engine/db/ws state consistent with the sampler (engine READY, market OVERNIGHT, ws connected, db integrity ok).
- Engine snapshot has `account` block (verified: `snapshot_has_account=true`).
- REST stats observed stable: ok grows (12 → 25), empty ≤1, retry (429 backoff) bounded.

## Truth sources
API reads flow from the canonical `trading.db` (analytics routes init from `trade_ledger._db.db_path`) — single source of truth for trade/position views; no duplicated in-memory projection exposed via HTTP.

## Conclusion
API-observed counts match DB-observed counts across all 6 samples; no divergence recorded.