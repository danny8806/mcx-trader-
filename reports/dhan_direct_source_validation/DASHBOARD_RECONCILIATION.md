# Dashboard Reconciliation — DASHBOARD_RECONCILIATION.md

## Frontend ↔ engine WS (runtime_v3)
- 2 WS command round-trips (`get_snapshot`) completed against the dashboard server; `ws_cmd_messages` captured.
- Dashboard lifespan = the *production start path*: it restores state, sets the engine `_running`, starts CandleFetcher + auth scheduler + routes + background tasks. Verified order:
  `Lifespan Starting → Engine ready → Persistence ready → State restored → initializing → reconciling → warming_up → READY → Engine started`.
- Sampler parity: dashboard-visible state (engine_status READY, market_state OVERNIGHT, ws_connected true) identical to engine-internal markers through the whole soak — no drift.

## No-fabrication rule
All dashboard-visible tick/candle counters were 0 for live market data during the closed session (only subscription-time snapshot ticks GOLDM=2, SILVERM=2). No synthetic candles were injected to make the dashboard "look alive".

## Conclusion
Frontend/API layer reconciles 1:1 with the engine; parse_err=0 across all WS command traffic.