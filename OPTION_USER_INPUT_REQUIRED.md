# OPTION SELLING — USER INPUT REQUIRED

**Date:** 2026-09-09

Only values that CANNOT be determined from the source code are listed below.

---

## 1. STRATEGY PARAMETERS (from source code — NO input needed)

| Parameter | Value | Source |
|-----------|-------|--------|
| Entry time | 9:30 AM IST | `paper_trading.py` line 341 |
| Recheck time | 10:00 AM IST | `paper_trading.py` line 342 |
| Exit time | 3:15 PM IST | `paper_trading.py` line 343 |
| OI threshold | 50 Lakhs | `paper_trading.py` line 13 |
| Stop loss | 1% of margin | `paper_trading.py` line 12 |
| Max trades/day | 2 (1 NIFTY + 1 SENSEX) | `paper_trading.py` line 209 |
| Expiry days | Wednesday, Thursday | `paper_trading.py` line 61 |
| Strike offset | ATM-2 or ATM+2 | `option_selling_strategy.py` line 104-105 |
| Lot size NIFTY | 65 | `paper_trading.py` line 216 |
| Lot size SENSEX | 20 | `paper_trading.py` line 233 |
| Dhan client ID | 1102461741 | Hardcoded in all files |

---

## 2. REQUIRED DECISIONS

### 2.1 Stop Loss Implementation

**CURRENT VALUE:** Defined as 1% of margin but **NOT IMPLEMENTED**

**REQUIRED DECISION:**

Should the paper trading system implement stop loss monitoring?

- Option A: **NO SL** — Exit only at EOD (matches current behavior)
- Option B: **ADD SL** — Monitor premium and exit if loss > 1% of margin

If Option B: How should SL be checked?
- Check every N minutes?
- Check on each new candle?
- Use option LTP from Dhan API?

---

### 2.2 Exit Price Source

**CURRENT VALUE:** `exit_trade()` uses `get_margin()` (margin requirement) as exit price — **INCORRECT**

**REQUIRED DECISION:**

What should be used as the exit price for P&L calculation?

- Option A: **Option LTP** from Dhan option chain API at 3:15 PM
- Option B: **Last traded price** from Dhan API at exit time

The correct approach is Option A (fetch option chain at exit and use LTP).

---

### 2.3 Expiry Selection

**CURRENT VALUE:** Auto-selects nearest expiry (`expiries[0]`)

**REQUIRED DECISION:**

Should expiry selection remain automatic, or should it be configurable?

- Option A: **AUTO** — Always use nearest expiry (current behavior)
- Option B: **CONFIGURABLE** — Allow selecting specific expiry from dashboard settings

---

### 2.4 Trading Days

**CURRENT VALUE:** Wednesday and Thursday only

**REQUIRED DECISION:**

Should the system trade on other days?

- Option A: **Wed/Thu only** (current behavior — these are weekly expiry days)
- Option B: **All trading days** (Mon-Fri)
- Option C: **Configurable** — Allow selecting which days to trade from settings

---

### 2.5 Underlying Instruments

**CURRENT VALUE:** NIFTY and SENSEX only

**REQUIRED DECISION:**

Should the system support additional underlyings?

- Option A: **NIFTY + SENSEX only** (current behavior)
- Option B: **Add BANKNIFTY** — scrip=?, lot_size=?
- Option C: **Configurable** — Allow adding underlyings from settings

---

### 2.6 Number of Lots

**CURRENT VALUE:** Always 1 lot per trade

**REQUIRED DECISION:**

Should lot sizing be configurable?

- Option A: **1 lot** (current behavior)
- Option B: **Configurable lots** — Allow user to set number of lots from settings

---

### 2.7 Real-Time Premium Monitoring

**CURRENT VALUE:** No real-time monitoring. Only check at entry and EOD.

**REQUIRED DECISION:**

Should the dashboard show live option premiums for open trades?

- Option A: **NO** — Only show entry and exit prices
- Option B: **YES** — Poll Dhan API every N minutes and show current premium + live P&L

If Option B: What polling interval?
- Every 1 minute?
- Every 5 minutes?
- Every 15 minutes?

---

### 2.8 Trade Log Import

**CURRENT VALUE:** `trade_log.csv` has 1 open trade (NIFTY 23400, 2026-09-09)

**REQUIRED DECISION:**

Should the existing CSV trade be imported into the new SQLite database?

- Option A: **IMPORT** — Import the 1 open trade into the new system
- Option B: **IGNORE** — Start fresh, ignore CSV history

---

### 2.9 Dhan Token Handling

**CURRENT VALUE:** Token stored in `dhan_token.json`, auto-renewed via PIN+TOTP

**REQUIRED DECISION:**

Where should the Dhan token be managed for the option module?

- Option A: **REUSE MCX token** — Share the same Dhan token from MCX-TRADER
- Option B: **SEPARATE token** — Maintain separate `dhan_token.json` for options
- Option C: **ENV variable** — Read token from environment variable

---

### 2.10 Dashboard Settings Exposure

**CURRENT VALUE:** All parameters hardcoded in source

**REQUIRED DECISION:**

Which settings should be exposed in the dashboard?

Minimum recommended:
- [ ] Underlying (NIFTY/SENSEX)
- [ ] Number of lots
- [ ] Entry time
- [ ] Exit time
- [ ] OI threshold
- [ ] Stop loss percentage
- [ ] Trading days
- [ ] Enabled/disabled

Additional (optional):
- [ ] Strike offset (ATM-2 / ATM+2)
- [ ] Max trades per day
- [ ] Expiry selection (auto/manual)

---

## 3. NO INPUT NEEDED (determined from source)

| Question | Answer | Source |
|----------|--------|--------|
| What is the strategy? | OI-based strangle selling | `option_selling_strategy.py` |
| What data source? | Dhan REST API v2 | All files |
| What is the trade type? | Sell CE + PE at same strike | `paper_trading.py` |
| How are strikes selected? | ATM-2 if CE OI > PE OI, ATM+2 if PE OI > CE OI | `option_selling_strategy.py` |
| How is OI used? | Sum CE OI (ATM to ATM+3), Sum PE OI (ATM to ATM-3) | `option_selling_strategy.py` |
| How is margin calculated? | Dhan margin calculator API | `paper_trading.py` |
| What is the entry condition? | OI diff >= 50L on expiry day at 9:30 AM | `paper_trading.py` |
| What is the exit condition? | EOD 3:15 PM | `paper_trading.py` |
| What is the P&L formula? | (entry_premium - exit_premium) × quantity | Should be (currently broken) |

---

## 4. DEFAULT VALUES (if no input provided)

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Stop loss | DISABLED | Matches current behavior |
| Exit price | Option LTP from API | Correct approach |
| Expiry | Auto (nearest) | Current behavior |
| Trading days | Wed/Thu | Current behavior |
| Underlyings | NIFTY + SENSEX | Current behavior |
| Lots | 1 | Current behavior |
| Premium monitoring | YES, every 5 min | Useful for dashboard |
| CSV import | IGNORE | Start fresh |
| Token | Reuse MCX token | Simpler |
| Settings | All configurable | Maximum flexibility |

---

## 5. CRITICAL: STOP LOSS QUESTION

The source code defines `SL_PERCENT = 0.01` but **never implements it**.

**This is the most important decision:**

If you want stop loss:
1. How often should it be checked?
2. What should trigger the exit?
3. Should it be real-time or on-candle-close?

If you don't want stop loss:
1. Confirm EOD-only exit is acceptable
2. The 1% SL code will be removed

**Please answer this before implementation begins.**
