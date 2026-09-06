# WebSocket Validation — WEBSOCKET_VALIDATION.md

## P7 — real Dhan WS lifecycle (market CLOSED)

- Adapter: `data/dhan/websocket_client.py` via `data/dhan/adapter.py`.
- Instruments: `[('569003','MCX:GOLDM202610'), ('483080','MCX:SILVERM202611')]` — subscribed 2/2.

### runtime_v3 IDLE_STATE + sampler (authenticated, valid token)

| field | value |
|---|---|
| ws_connected | true |
| adapter_connected | true |
| ws_stats | recv=4, parse_err=0, sub=1, tick=2 |
| market_state | OVERNIGHT |
| engine_status | READY |
| market_closed_idle_safe | true |

- On subscribe, Dhan pushes **one real snapshot tick per symbol even when the market is closed** (real prior-session data, not fabricated): GOLDM ltp=152950.0, SILVERM ltp=239495.0.
- The 2 `UNKNOWN code=5 len=12` packets are feed ACK frames (identical bytes each run); benign.

## Reconnect (P15/RECONNECT.md)
- `closed → disconnected → connected`; stats recv 4→8, sub 1→2, tick 2→4, parse_err 0 (bounded backoff 10s→30s cap, watchdog 60s).

## Control protocol
- 2 WS command round-trips (get_snapshot) via dashboard during soak; parse_err=0 (see DASHBOARD_RECONCILIATION).

## Availability note
- Closed market → no streaming page ticks (expected per mission: "lack of normal market ticks is acceptable"). Feed liveness is proven by subscribe-ACK frames + snapshot ticks + reconnect counters.