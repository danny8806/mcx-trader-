# 14 - Strategy Matrix Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. Strategy Inventory

| Strategy | Instrument | Fast TF | HTF | Qty | Class |
|----------|-----------|---------|-----|-----|-------|
| gold_01 | GOLDM | 5m | 1h | 1 | GoldStrategy01 |
| gold_02 | GOLDM | 5m | 1h | 1 | GoldStrategy02 |
| gold_03 | GOLDM | 5m | 1h | 1 | GoldStrategy03 |
| gold_04 | GOLDM | 5m | 1h | 1 | GoldStrategy04 |
| silver_01 | SILVERM | 5m | 1h | 1 | SilverStrategy01 |
| silver_02 | SILVERM | 5m | 1h | 1 | SilverStrategy02 |
| silver_03 | SILVERM | 5m | 1h | 1 | SilverStrategy03 |
| silver_04 | SILVERM | 5m | 1h | 1 | SilverStrategy04 |

**Note:** All strategies use identical logic (BaseDEMAStrategy). Differentiation is by config (instrument, quantity, timeframe). The gold/__init__.py and silver/__init__.py create instances of the same base class.

---

## 2. Trade Counting Methodology

### Live Trading

**File:** `portfolio/pnl.py:80-90`

```python
def record_trade(self, gross, charges, net) -> None:
    with self._lock:
        self._trade_count += 1
        if net >= 0:
            self._wins += 1
        else:
            self._losses += 1
```

**Count per strategy:** Each PNLEngine maintains its own `_trade_count`, `_wins`, `_losses`.

### Analytics (TradeLedger)

**File:** `analytics/trade_ledger.py:435-447`

```python
def count_trades(self, strategy_id=None, status=None) -> int:
    query = "SELECT COUNT(*) FROM trades_analytics WHERE 1=1"
    if strategy_id: query += " AND strategy_id = ?"
    if status: query += " AND status = ?"
    return conn.execute(query, params).fetchone()[0]
```

### Trade Count Verification Points

| Source | Location | Method |
|--------|----------|--------|
| PNLEngine (memory) | `pnl.py:140-142` | `_trade_count` property |
| DB trades table | `trading.db:trades` | `COUNT(*)` |
| TradeLedger (analytics) | `analytics.db:trades_analytics` | `COUNT(*)` |

**Reconciliation check:** `reconciliation/engine.py:354-360`
```python
db_count = trade_count_by_strategy.get(strat_id, 0)
mem_count = engine.trade_count
if db_count != mem_count:
    result.add_error(f"Trade count mismatch")
```

---

## 3. Win/Loss Classification

**File:** `portfolio/pnl.py:87-90`

```python
if net >= 0:
    self._wins += 1
else:
    self._losses += 1
```

**Classification:** Net P&L >= 0 is a win, < 0 is a loss. Charges are included in the net.

**Win rate:** `self._wins / self._trade_count * 100`

---

## 4. Strategy State Machine

**File:** `strategies/types.py:21-33`

```
                    ┌──────────┐
                    │   FLAT   │
                    └────┬─────┘
                         │ signal
                    ┌────▼─────────┐
                    │ PENDING_LONG │  or  PENDING_SHORT
                    └────┬─────────┘
                         │ breakout trigger
                    ┌────▼─────────┐
                    │LONG_POSITION │  or  SHORT_POSITION
                    └────┬─────────┘
                         │ exit signal
                    ┌────▼──────────────┐
                    │EXIT_ORDER_SUBMITTED│
                    └────┬──────────────┘
                         │ fill processed
                    ┌────▼─────┐
                    │   FLAT   │
                    └──────────┘
```

**Reversal path:** While in LONG_POSITION/SHORT_POSITION:
```
LONG_POSITION → reversal signal → EXIT_ORDER_SUBMITTED (deferred)
  → fill at next bar open → FLAT (momentarily)
  → opposite pending_entry → PENDING_LONG/PENDING_SHORT
  → breakout trigger → new position
```

---

## 5. Per-Strategy Capital Isolation

**File:** `trading_engine.py:258-290`

```python
# Per-strategy capital
for strat_name, strat_config in strategies_config.items():
    per_strategy_capital = strat_config.get("capital", default_capital)
    self.account_engines[strat_name] = AccountEngine(
        starting_capital=per_strategy_capital,
        margin_per_trade_pct=margin_pct,
    )

# Global account (sum of all)
total_capital = sum(a.starting_capital for a in self.account_engines.values())
self.account_engine = AccountEngine(starting_capital=total_capital, ...)
```

**Default:** ₹300,000 per strategy (configurable)

---

## 6. Strategy Enable/Disable

**File:** `strategies/base_dema_strategy.py:47`

```python
self.enabled = True  # Default enabled
```

**Toggle:** Via dashboard command or config reload
```python
# server.py:349-351
strat = _engine.strategies[sid]
strat.pending_entry = None
strat.enabled = False
```

**Effect:** `on_bar()` returns `None` when disabled (line 122-123)
