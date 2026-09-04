# TRADE IDENTITY PROOF

**Date:** 2026-09-05
**Status:** Canonical identity model fully enforced and proven by code + triggers + tests.

---

## 1. The identity model

```
signal_id    → signals (created at signal candle)
trade_id     → trades (created once at trade creation, IMMUTABLE)
pending_order_id → pending_orders (entry/exit intent)
order_id     → orders (trade_id)
fill_id      → fills (trade_id, order_id)
position_id  → positions (trade_id, and position_id != trade_id)
exit_signal_id → trades (nullable; set ONLY on reversal exits)
```

**Non-negotiable rules:**
1. `position_id != trade_id` — separate identities.
2. No fallback inference of trade_id from symbol+side+timestamp ("latest trade" lookups).
3. No implicit trade_id generation.
4. Only explicit ID propagation.

## 2. Code enforcement

| Rule | Enforcement point | Evidence |
|---|---|---|
| trade_id generated once, explicit | `TradeLedger.create_trade(trade_id required)` | `analytics/trade_ledger.py:154-155` — `raise ValueError("trade_id is required ...")` |
| no implicit trade_id | `TradeLifecycleManager` entry path mints trade_id on entry signal, propagates explicitly | `core/lifecycle.py` `register_entry_fill`; `trading_engine.py:1300-1306, 1403-1428` |
| separate identity | DB trigger | `trg_positions_identity_separate` on `positions` (raises if `position_id = trade_id`) |
| no position→trade conflation | `portfolio/position_manager.py` `restore` preserves trade_id for open+closed | `position_manager.py` |
| entry_signal lineage | DB trigger | `trg_trades_entry_signal_required` / `exists` |
| order→trade lineage | DB trigger | `trg_orders_trade_required` / `exists` |
| fill→order+trade lineage | DB trigger | `trg_fills_lineage_required` / `trade_exists` / `order_exists` |
| reversal signal relationship | DB trigger + tests | `trg_trade_signal_link_*`; reversal tests |

## 3. DB-level proof

Inspection of `data/db/trading.db`:
- `positions` declares FK `trade_id → trades.trade_id`.
- Triggers enforce every legacy-table lineage contract (see `DATABASE_SCHEMA_VERIFICATION.md` §3).
- `PRAGMA integrity_check` → `ok`; `PRAGMA foreign_key_check` → no violations.
- `tools/validate_trade_integrity.py` → **PASS** (no orphan, no missing IDs, no invalid FK, no
  duplicate fills, no P&L mismatch, schema contracts all satisfied).

## 4. Test proof (identity assertions)

- `test_crash_api_replay.py`: trades keyed by explicit `trade_id`, `position_id != trade_id`,
  restore preserves trade_id. ✅
- `test_trade_identity_divergence.py` (adversarial, rewritten to canonical): canonical close uses
  trade_id; single-row persist. ✅
- `test_lifecycle_persistence_failure.py`: one-row, restore uses canonical trade_id. ✅
- `test_memory_db_reconciliation.py`: orphan fills are REJECTED by DB (canonical model). ✅
- `test_db_integrity_orphan.py`: rejection assertion. ✅
- `test_master_reverse_engineering.py`: seeded signal/order lineage for strict triggers. ✅
- `test_per_strategy_lifecycle.py`, `test_reversal_exit_and_opposite_entry.py` (rewritten):
  `trade_id != position_id`; reversal two distinct trade_ids; same signal, same trigger. ✅
- `test_forensic_multiday_replay.py`: distinct identity per trade (TRD-{tid} vs position {tid}). ✅
- `test_lifecycle.py`, `test_forensic_trade_lifecycle.py`: PHASE 19 distinct minted trade_ids;
  no reuse. ✅

## 5. Prohibited patterns — final scan result (checklist #67)

Repository-wide search for prohibited patterns in production code:
- `position_id as trade_id` → **0**
- `trade_id = position_id` → **0**
- implicit/latest/symbol+side+timestamp trade_id inference → **0**
- `_open_trades` as authoritative truth → **REMOVED** (cache with DB fallback)
- independent TradeLedger authority → **REMOVED** (derived projection on trading.db)
- second canonical trade ledger → **0**

## 6. One flag reviewed (assessed, not a violation)

`trading_engine.py:1136` — ORDER_CREATED event uses `order.order_id` as a correlation `trade_id`
field in the event payload BEFORE the canonical trade row exists (entry intent phase). This is an
**event-correlation ID**, not the canonical trade identity — the canonical trade_id is minted
separately and propagates explicitly thereafter. Documented; no change made (low risk; changing
would alter event semantics). Flagged for awareness only.