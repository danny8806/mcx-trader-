# OPTION SELLING — SIMPLE FORENSIC AUDIT

**Date:** 2026-09-09
**Source:** `C:\Users\pc\Desktop\FYERS APIS\option paper trading\`

---

## 1. FILES INVENTORY

| File | Lines | Status | Purpose |
|------|-------|--------|---------|
| `option_selling_strategy.py` | 166 | **PRIMARY SOURCE** | OI-based analysis + strike selection |
| `paper_trading.py` | 385 | **PRIMARY SOURCE** | Complete paper trading system |
| `smart_option_selling.py` | 182 | DUPLICATE of option_selling_strategy.py | Same OI logic, extra comparison |
| `auto_option_analysis.py` | 205 | DUPLICATE | OI + margin for ATM-2 and ATM+2 |
| `auto_straddle_margin.py` | 169 | STANDALONE | Margin comparison: OTM2 vs ITM2 |
| `auto_strangle_margin.py` | 168 | DUPLICATE of auto_straddle_margin.py | Same margin comparison |
| `nifty_sensex_oi.py` | 154 | STANDALONE | Pure OI analysis display |
| `dhan_broker.py` | 329 | **MCX FUTURES** | Dhan API client for MCX (NOT options) |
| `config.py` | 34 | FYERS CONFIG | FYERS stock data config (NOT options) |
| `fyers_data.py` | 354 | FYERS DATA | FYERS stock data SQLite (NOT options) |
| `dhan_token.json` | 3 | CREDENTIAL | Dhan API access token |
| `trade_log.csv` | 2 | DATA | 1 open trade (NIFTY 23400) |
| `fyers_historical.db` | - | DATABASE | FYERS stock data (NOT options) |

---

## 2. WHAT DOES `option_selling_strategy.py` ACTUALLY DO?

**Type:** Analysis tool (not a trader)

**Exact Logic:**
1. Fetch expiry list from Dhan API → use first (nearest) expiry
2. Fetch option chain for that expiry
3. Find ATM strike (closest to spot)
4. Sum CE OI from ATM to ATM+3 OTM (4 strikes)
5. Sum PE OI from ATM to ATM-3 OTM (4 strikes)
6. If CE OI sum > PE OI sum → select strike at **ATM-2**
7. If PE OI sum > CE OI sum → select strike at **ATM+2**
8. Calculate margin for selling CE + PE at selected strike
9. Print results

**No entry/exit logic. No trade execution. Pure analysis.**

---

## 3. EXACT STRATEGY

**Strategy Name:** OI-Based Strangle Selling

**Instruments:**
- NIFTY: scrip=13, lot_size=65, exchange=NSE_FNO
- SENSEX: scrip=51, lot_size=20, exchange=BSE_FNO

**Entry Condition:**
- Time: 9:30 AM IST
- Expiry day: Wednesday or Thursday only
- OI difference >= 50 Lakhs (OI_THRESHOLD = 5,000,000)
- If OI diff < 50L at 9:30 → recheck at 10:00 AM
- Max 2 trades per day (1 NIFTY + 1 SENSEX)

**Strike Selection:**
- CE OI (ATM to ATM+3 OTM) > PE OI (ATM to ATM-3 OTM) → Sell at ATM-2
- PE OI > CE OI → Sell at ATM+2
- Trade type: **Strangle** (sell CE + PE at SAME strike)

**Exit Condition:**
- EOD only: 3:15 PM IST
- Close all open trades at market

**Stop Loss:**
- Defined: 1% of total margin (SL_PERCENT = 0.01)
- **NOT IMPLEMENTED** in exit logic — `exit_trade()` only does EOD exit

**Profit Target:**
- None defined. EOD exit only.

---

## 4. ENTRY CONDITION (DETAILED)

```python
# From paper_trading.py → morning_check()
1. Check is_expiry_day() → Wednesday or Thursday
2. Check today_trades count < 2
3. For NIFTY (if not already traded):
   a. get_analysis(13, 65, "NSE_FNO")
   b. If oi_diff >= OI_THRESHOLD (50L) → place_trade()
   c. Else → will recheck at 10:00 AM
4. Same for SENSEX (51, 20, "BSE_FNO")
```

---

## 5. EXIT CONDITION (DETAILED)

```python
# From paper_trading.py → eod_exit()
1. Get all today's trades
2. Filter status == "OPEN"
3. For each open trade:
   a. Get current margin (used as exit price proxy — BUG)
   b. Calculate P&L: (ce_ltp - ce_exit + pe_ltp - pe_exit) * lot_size
   c. Mark CLOSED
4. Print total P&L
```

**BUG:** `exit_trade()` calls `get_margin()` to determine exit price. This returns the margin requirement, not the option LTP. The P&L calculation is incorrect.

---

## 6. STOP LOSS

**Defined:** `SL_PERCENT = 0.01` (1% of margin)
**Calculated:** `sl_amount = total_margin * SL_PERCENT`
**Example:** NIFTY margin=340155 → SL = Rs 3,401

**STATUS: NOT IMPLEMENTED**
- No monitoring loop checks current premium against SL
- No exit triggered when loss exceeds SL amount
- Only EOD exit exists

---

## 7. PROFIT TARGET

**Not defined.** No profit-taking logic exists. All trades exit at EOD.

---

## 8. STRADDLE / STRANGLE

**Trade type: STRANGLE** (same strike for CE and PE = technically a straddle)

Despite the name "strangle" in the analysis logic, the trade sells CE + PE at the **same strike** (ATM-2 or ATM+2). This is actually a **straddle** at an OTM strike.

True strangle would sell CE and PE at different strikes.

---

## 9. UNDERLYING

- NIFTY (Index): Dhan scrip=13, IDX_I segment
- SENSEX (Index): Dhan scrip=51, IDX_I segment

---

## 10. STRIKE SELECTION

```python
# Find ATM
atm = min(strikes, key=lambda x: abs(x["strike"] - spot))
atm_idx = strikes.index(atm)

# OI-based selection
ce_oi_sum = sum(strikes[atm_idx + i]["ce_oi"] for i in range(4))  # ATM to ATM+3
pe_oi_sum = sum(strikes[atm_idx - i]["pe_oi"] for i in range(4))  # ATM to ATM-3

if ce_oi_sum > pe_oi_sum:
    selected = strikes[atm_idx - 2]  # ATM - 2
else:
    selected = strikes[atm_idx + 2]  # ATM + 2
```

---

## 11. OI LOGIC

- CE OI: Sum of open interest from ATM to ATM+3 OTM (4 strikes)
- PE OI: Sum of open interest from ATM to ATM-3 OTM (4 strikes)
- PCR: PE OI / CE OI
- Sentiment: PCR > 1.2 = BULLISH, PCR < 0.8 = BEARISH, else NEUTRAL
- Entry threshold: |CE OI - PE OI| >= 50 Lakhs

---

## 12. MARGIN LOGIC

```python
# From Dhan API /v2/margincalculator
payload = {
    "dhanClientId": "1102461741",
    "securityId": ce_sec_id,
    "exchangeSegment": "NSE_FNO",
    "transactionType": "SELL",
    "quantity": lot_size,
    "productType": "MARGIN",
    "price": 0,
    "triggerPrice": 0
}
total_margin = ce_margin + pe_margin
```

---

## 13. QUANTITY / LOT SIZE

- NIFTY: 65 (lot_size)
- SENSEX: 20 (lot_size)
- Always 1 lot per trade

---

## 14. EXPIRY SELECTION

```python
expiries = get_expiry_list(scrip)
expiry = expiries[0]  # First (nearest) expiry
```

Auto-selects nearest expiry. No manual override.

---

## 15. DATA SOURCE

**Dhan REST API v2:**
- `POST /v2/optionchain/expirylist` — get expiry dates
- `POST /v2/optionchain` — get option chain (strikes, LTP, OI)
- `POST /v2/margincalculator` — get margin requirement

**Authentication:** JWT token from `dhan_token.json` (auto-renewed via PIN+TOTP)

---

## 16. `paper_trading.py` — WHAT DOES IT DO?

**Complete paper trading system with CLI:**

| Command | Function | Time |
|---------|----------|------|
| `run` | Starts scheduler (auto trading) | Continuous |
| `check` | Morning check | Manual |
| `recheck` | 10:00 AM recheck | Manual |
| `exit` | EOD exit all open trades | Manual |
| `status` | Show current trades | Manual |

**Scheduler:**
- 09:30 AM → `morning_check()`
- 10:00 AM → `recheck_10am()`
- 03:15 PM → `eod_exit()`

**Trade Logging:** CSV file (`trade_log.csv`)

---

## 17. DATABASE

**Current:** CSV file only (`trade_log.csv`)

**CSV columns:**
```
Date, Name, Entry Time, Strike, CE LTP, PE LTP, CE Margin, PE Margin,
Total Margin, SL Amount, Exit Time, CE Exit, PE Exit, P&L, Status
```

**No SQLite database for options.** The `fyers_historical.db` is for FYERS stock data.

---

## 18. CURRENT TRADE IN CSV

```
Date: 2026-09-09
Name: NIFTY
Entry Time: 12:27:58
Strike: 23400
CE LTP: 215.60
PE LTP: 65.65
CE Margin: 187,763
PE Margin: 152,392
Total Margin: 340,155
SL Amount: 3,401
Status: OPEN
P&L: 0 (not closed)
```

---

## 19. SECURITY FINDINGS

| Finding | Severity | File |
|---------|----------|------|
| Dhan PIN in source code | **CRITICAL** | `dhan_broker.py:35` — `DHAN_PIN = "107602"` |
| Dhan TOTP secret in source | **CRITICAL** | `dhan_broker.py:36` — `DHAN_TOTP_SECRET = "VUQQFLIRDEJ46O2WPXDGNIBULKCJU7FO"` |
| Dhan client ID hardcoded | MEDIUM | Multiple files — `client-id: "1102461741"` |
| Access token in file | MEDIUM | `dhan_token.json` |

---

## 20. CODE DUPLICATION

| Original | Duplicate | Difference |
|----------|-----------|------------|
| `option_selling_strategy.py` | `smart_option_selling.py` | Extra NIFTY vs SENSEX comparison |
| `option_selling_strategy.py` | `auto_option_analysis.py` | Shows ATM-2 and ATM+2 margins side by side |
| `auto_straddle_margin.py` | `auto_strangle_margin.py` | Identical logic |
| All Dhan API calls | Duplicated in every file | Same `get_token()`, `get_option_chain()`, `get_margin()` |

---

## 21. DEAD CODE / UNUSED FILES

| File | Status | Reason |
|------|--------|--------|
| `config.py` | **UNUSED by options** | FYERS stock data config |
| `fyers_data.py` | **UNUSED by options** | FYERS stock data SQLite |
| `fyers_historical.db` | **UNUSED by options** | FYERS stock data |
| `auto_straddle_margin.py` | **STANDALONE** | Analysis only, not used by paper_trading.py |
| `auto_strangle_margin.py` | **STANDALONE** | Duplicate of above |
| `nifty_sensex_oi.py` | **STANDALONE** | Analysis only |

---

## 22. BROKEN / BUGS

| Bug | Location | Impact |
|-----|----------|--------|
| **SL not implemented** | `paper_trading.py` | 1% SL defined but never checked — trades run to EOD regardless |
| **Wrong exit price** | `exit_trade()` line 184 | Uses `get_margin()` (margin requirement) instead of option LTP |
| **Wrong P&L calc** | `exit_trade()` line 187 | P&L based on margin instead of premium change |
| **No expiry validation** | `is_expiry_day()` | Only checks Wed/Thu, doesn't verify actual expiry date |

---

## 23. WHAT CAN BE REUSED DIRECTLY

| Component | Reuse | Notes |
|-----------|-------|-------|
| Dhan option chain API calls | **YES** | `get_option_chain()`, `get_expiry_list()` |
| OI analysis logic | **YES** | CE/PE OI sum, PCR calculation |
| Strike selection logic | **YES** | ATM-2/ATM+2 based on OI |
| Margin calculation | **YES** | Dhan margin calculator API |
| Trade schedule | **YES** | 9:30, 10:00, 15:15 |

---

## 24. WHAT NEEDS MINIMAL MODIFICATION

| Component | Change |
|-----------|--------|
| Paper fill price | Use option LTP instead of margin |
| P&L calculation | (entry_premium - exit_premium) × quantity |
| Exit logic | Add SL monitoring (if implementing) |
| Storage | CSV → SQLite |
| API | Wrap in FastAPI endpoints |

---

## 25. SUMMARY

**The existing system is a simple OI-based option selling strategy that:**

1. Checks NIFTY/SENSEX option chains at 9:30 AM
2. If OI difference >= 50L → sells CE + PE at ATM-2 or ATM+2
3. Rechecks at 10:00 AM if no trade at 9:30
4. Exits all trades at 3:15 PM EOD
5. Logs trades to CSV

**Critical issues:**
- Stop loss defined but not implemented
- Exit price calculation is wrong (uses margin, not premium)
- No real-time monitoring
- CSV-only storage

**For integration into MCX-TRADER dashboard:**
- Reuse the Dhan API calls and OI logic
- Fix the P&L calculation
- Add SQLite storage
- Create FastAPI endpoints
- Add dashboard panels with MCX/OPTION partitions
