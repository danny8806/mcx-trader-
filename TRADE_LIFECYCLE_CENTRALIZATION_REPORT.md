# TRADE LIFECYCLE CENTRALIZATION REPORT

**Date**: 2026-09-04
**Status**: COMPLETE — All phases deployed to VPS

---

## Architecture Summary

ONE TRADE = ONE `trade_id`. Every lifecycle object references this single identity.

```
Signal (signal_id) ──→ TradeContext (trade_id) ←── PendingOrder (pending_order_id)
                        │                         ←── Order (order_id)
                        │                         ←── Fill (fill_id)
                        │                         ←── Position (position_id)
                        │                         ←── Exit Fill (fill_id)
                        │
                        ├── entry_signal_id (COMPULSORY — every trade has this)
                        ├── entry_fill_id
                        ├── entry_order_id
                        ├── position_id (= trade_id, 1:1)
                        ├── exit_signal_id (OPTIONAL — NULL for SL)
                        ├── exit_fill_id
                        └── exit_order_id
```

## Components Delivered

### 1. `core/lifecycle.py` — TradeLifecycleManager
- **Single authority** for trade identity (955 lines)
- **TradeContext dataclass**: 40+ fields covering full lifecycle
- **5 identity maps**: signal→trade, order→trade, fill→trade, position→trade, pending→trade
- **5 resolve methods**: `resolve_trade_from_signal/order/fill/position/pending`
- **Lifecycle operations**: create, register pending, activate pending, register order, register entry fill, register exit fill, register position, close, reverse, apply stop loss
- **Orphan scan**: detects fills/orders/positions not linked to any trade
- **Reconciliation**: checks entry_signal_id not empty, open trades have positions, closed trades have exit info
- **Snapshot/restore**: full state serialization for DB persistence and API responses

### 2. `trading_engine.py` — Lifecycle Integration
- `_lifecycle` initialized in `__init__` (bare) and `start()` (wired)
- `_process_signal()`: creates lifecycle trade, registers order
- `_on_fill()` entry path: registers entry fill and position
- `_on_fill()` exit path: registers exit fill and closes trade
- `restore_from_db()` called on startup
- Snapshot includes lifecycle data

### 3. `dashboard/routes/trades.py` — Canonical API
- `GET /api/trades` serves lifecycle data via `get_trades_for_api()`
- `GET /api/trades/{trade_id}` single trade detail
- `GET /api/trades/orphan-scan` orphan detection
- `GET /api/trades/lifecycle-reconcile` lifecycle + legacy reconciliation

### 4. `dashboard/routes/reconciliation.py` — 3-Check Reconciliation
1. Legacy engine reconciliation (existing)
2. Lifecycle orphan scan (new)
3. Lifecycle identity consistency check (new)
Returns unified report with `is_consistent`, `errors`, `warnings`, `summary`

### 5. `dashboard-ui/src/pages/Trades.tsx` — Full Lineage Display
- Expandable trade rows showing: Entry Signal → Position → Exit details
- P&L calculation and display
- Signal metadata (candle data, HTF values, reason)

### 6. `tests/fresh_audit/test_lifecycle.py` — 52 Tests
| Test Class | Tests | Coverage |
|---|---|---|
| TestCreateTradeFromSignal | 6 | Trade creation, unique IDs, signal resolution, events, side derivation |
| TestPendingOrder | 4 | Register/activate, resolution, error cases |
| TestOrderRegistration | 4 | Entry/exit orders, resolution, error cases |
| TestEntryFill | 6 | Fill registration, position registration, auto position_id, resolution |
| TestExitClose | 5 | Exit fill, close trade, exit signal, already-closed, error cases |
| TestStopLoss | 3 | Apply SL, SL doesn't close trade, error cases |
| TestReversal | 3 | Atomic close+new open, same signal both uses, error case |
| TestIdentityResolution | 8 | All 5 resolve methods + negative cases |
| TestOrphanScan | 4 | Clean state, orphan fill/order/position detection |
| TestReconciliation | 2 | Clean state, closed trade |
| TestSnapshotRestore | 3 | Roundtrip, identity maps, closed trade |
| TestEdgeCases | 4 | Multiple trades, events accumulation, API output, full E2E lifecycle |

## Identity Invariants (Enforced)

1. **entry_signal_id is NEVER NULL** — every trade MUST have an entry signal
2. **exit_signal_id is NULL for SL** — stop loss doesn't create/need a signal
3. **position_id = trade_id** — 1:1 mapping (auto-set on entry fill)
4. **Reversal: same signal, both uses** — old trade's exit_signal_id = new trade's entry_signal_id
5. **SL never creates new trade** — only closes existing one
6. **One trade_id, immutable** — created once, never changed

## Files Modified

| File | Change |
|---|---|
| `core/lifecycle.py` | **NEW** — Central TradeLifecycleManager |
| `trading_engine.py` | Lifecycle integration in _process_signal, _on_fill, start(), snapshot() |
| `dashboard/routes/trades.py` | **REWRITTEN** — Serves canonical lifecycle data |
| `dashboard/routes/reconciliation.py` | **REWRITTEN** — 3-check reconciliation |
| `dashboard-ui/src/pages/Trades.tsx` | **REWRITTEN** — Full lineage display |
| `dashboard-ui/src/lib/api.ts` | Added orphan-scan and lifecycle-reconcile endpoints |
| `tests/fresh_audit/test_lifecycle.py` | **NEW** — 52 lifecycle tests |

## Test Results

```
991 passed, 43 skipped, 4 warnings in 179.18s
```

## VPS Deployment

- Container: `mcx-trader` running on `200.234.44.93:8000`
- Lifecycle module active in production
- WS connected, in trading mode
- `force_state_override: None` (safe mode fixed)
