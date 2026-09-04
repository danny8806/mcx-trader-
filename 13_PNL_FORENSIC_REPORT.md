# 13 - P&L Forensic Report

**Audit Date:** 2026-09-04
**System:** MCX-TRADER

---

## 1. P&L Calculation Chain

### Realized P&L (per trade)

**File:** `portfolio/pnl.py:52-78`

```python
def calculate_realized_pnl(self, entry_fill, exit_fill, multiplier=1.0):
    # LONG:
    gross = (exit_fill.price - entry_fill.price) * entry_fill.quantity * multiplier
    # SHORT:
    gross = (entry_fill.price - exit_fill.price) * entry_fill.quantity * multiplier
    
    # Charges (MCXFeeModel)
    fees = self.fee_model.calculate(entry_price, exit_price, quantity, multiplier, side)
    net = gross - fees.total
    return gross, fees.total, net
```

### Fee Calculation (MCXFeeModel)

**File:** `execution/fee_model.py:49-91`

```python
def calculate(self, entry_price, exit_price, quantity, multiplier, side):
    buy_turnover = entry_price * quantity * multiplier
    sell_turnover = exit_price * quantity * multiplier
    if side == "SHORT":
        buy_turnover, sell_turnover = sell_turnover, buy_turnover
    
    brokerage = self.brokerage_per_side * 2        # ₹20 × 2 = ₹40
    stt = sell_turnover * self.stt_sell_pct         # 0.01% of sell
    exchange = (buy + sell) * self.exchange_pct      # 0.0026%
    sebi = (buy + sell) * self.sebi_pct             # 0.0001%
    stamp = buy_turnover * self.stamp_duty_pct      # 0.005% of buy
    gst = (brokerage + exchange + sebi) * self.gst_pct  # 18% of fees
    
    total = brokerage + stt + exchange + sebi + gst + stamp
    return FeeBreakdown(total=total, ...)
```

### Fee Components Breakdown

| Component | Rate | Applied To | Example (GOLDM, qty=1, entry=12000, exit=12100) |
|-----------|------|-----------|--------------------------------------------------|
| Brokerage | ₹20/side flat | Both legs | ₹40.00 |
| STT | 0.01% | Sell turnover only | ₹1.21 |
| Exchange | 0.0026% | Buy + Sell turnover | ₹0.63 |
| SEBI | 0.0001% | Buy + Sell turnover | ₹0.02 |
| GST | 18% | Brokerage + Exchange + SEBI | ₹7.32 |
| Stamp Duty | 0.005% | Buy turnover | ₹0.60 |
| **Total** | | | **₹49.78** |

**Note:** Multiplier is 1 for GOLDM/SILVER (lot size handled by quantity, not multiplier).

---

## 2. Unrealized P&L

**File:** `portfolio/position_manager.py:57-63`

```python
def update_mark(self, price: float) -> None:
    self.current_mark = price
    if self.is_long:
        self.unrealized_pnl = (price - self.average_entry) * self.quantity * self.multiplier
    else:
        self.unrealized_pnl = (self.average_entry - price) * self.quantity * self.multiplier
```

**Updated on:** Every tick (`trading_engine.py:631-634`)

---

## 3. P&L Flow Through Components

### Trade Close P&L Flow

```
TradeCloseManager.close_position()
  │
  ├── Step 1: PNLEngine.calculate_realized_pnl()
  │   └── Returns: (gross_pnl, charges, net_pnl)
  │
  ├── Step 2-3: Persist to DB (trade_record includes gross_pnl, charges, net_pnl)
  │
  ├── After persist:
  │   ├── PNLEngine.record_trade(gross, charges, net)     ← Running totals
  │   ├── AccountEngine.update_realized_pnl(net_pnl, charges)
  │   │   └── realized_pnl += net_pnl
  │   │   └── charges += charges
  │   │   └── cash += net_pnl (already net of charges)
  │   ├── AccountEngine.release_margin(margin)
  │   ├── RiskEngine.update_daily_pnl(net_pnl)
  │   └── TradeLedger.close_trade(gross_pnl, net_pnl, fees)
  │
  └── Frontend sees updated values via snapshot() → WebSocket push
```

### Tick P&L Flow

```
_tick() arrives
  ├── Position.update_mark(ltp)                          ← Update unrealized
  ├── AccountEngine.update_unrealized_pnl(total)         ← Sum of all positions
  ├── PNLEngine.update_unrealized_pnl(total)             ← Sum of all positions
  └── RiskEngine.update_peak_equity(current_equity)      ← Drawdown tracking
```

---

## 4. Account Equity Formula

**File:** `portfolio/account.py:56-59`

```python
@property
def equity(self) -> float:
    return self.starting_capital + self.realized_pnl + self.unrealized_pnl
```

**Components:**
- `starting_capital`: From config (NEVER restored from saved state)
- `realized_pnl`: Sum of all closed trade net P&L
- `unrealized_pnl`: Sum of all open position unrealized P&L

---

## 5. P&L Discrepancy Analysis

### A. PNLEngine vs DB Trade P&L

| Source | How Calculated | When Updated |
|--------|---------------|-------------|
| PNLEngine | `record_trade(gross, charges, net)` | After trade close (memory) |
| DB trades | Written by TradeCloseManager | After trade close (persist) |

**Consistency:** Both get the same values from the same `calculate_realized_pnl()` call. The only difference is timing — if the engine crashes between persist and record_trade, PNLEngine will be 0 but DB will have the trade.

### B. AccountEngine vs PNLEngine

| Metric | AccountEngine | PNLEngine |
|--------|--------------|-----------|
| realized_pnl | Sum of net P&L per trade | Sum of net P&L per trade |
| charges | Sum of charges per trade | Sum of charges per trade |
| unrealized_pnl | Sum of position marks | Sum of position marks |

**Consistency:** Should be identical. Both updated on the same tick/close events.

### C. Reconciliation Check

**File:** `reconciliation/engine.py:330-360`

```python
def _check_trades_vs_pnl(self, db_trades, result):
    for strat_id, engine in self.pnl_engines.items():
        db_pnl = sum(t.net_pnl for t in trades if t.strategy_id == strat_id)
        mem_pnl = engine.realized_net
        if abs(db_pnl - mem_pnl) > TOLERANCE:
            result.add_error(f"P&L mismatch for {strat_id}")
```

---

## 6. P&L Reporting Locations

| Component | Metric | Access |
|-----------|--------|--------|
| PNLEngine | realized_net, trade_count, win_rate | `snapshot()` → WebSocket |
| AccountEngine | equity, realized_pnl, charges | `snapshot()` → WebSocket |
| PositionManager | unrealized_pnl (per position) | `snapshot()` → WebSocket |
| RiskEngine | daily_pnl, peak_equity | `snapshot()` → WebSocket |
| DB trades table | net_pnl per trade | SQL query |
| TradeLedger | net_pnl, gross_pnl, fees per trade | SQL query |

---

## 7. Charge Accuracy Notes

- Charges are **estimated** (MCXFeeModel), not actual broker charges
- No STT on buy side (correct for MCX futures)
- Stamp duty on buy side only (correct for MCX)
- No SEBI turnover fee minimum (correct — SEBI charges are percentage-based)
- No transaction charges from exchange (simplified)
- No DP charges (not applicable for futures)
- GST on (brokerage + exchange + SEBI) — simplified
