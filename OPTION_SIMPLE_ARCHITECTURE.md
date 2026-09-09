# OPTION SIMPLE ARCHITECTURE

**Date:** 2026-09-09

## System Overview

Simple option selling paper trading integrated into MCX-TRADER dashboard.

```
Market Data (Dhan API)
        ↓
Option Strategy (OI Analysis)
        ↓
Signal (Sell CE + PE at ATM-2 or ATM+2)
        ↓
Paper Trader (Open Trade)
        ↓
Option Database (SQLite)
        ↓
Dashboard API (FastAPI)
        ↓
Dashboard UI (React)
```

## Trade Lifecycle

1. **Market Data** — Dhan API fetches option chain (strikes, LTP, OI)
2. **Strategy Check** — OI analysis: CE OI vs PE OI at ATM+3 OTM
3. **Signal** — If OI diff >= 50L → Sell CE + PE at ATM-2 or ATM+2
4. **Entry** — Record entry premiums, calculate margin, open trade
5. **Open Trade** — Store in SQLite, status=OPEN
6. **Monitor** — Poll premiums every 1 minute (dashboard)
7. **Exit** — SL hit (1% of margin) or EOD (3:15 PM)
8. **P&L** — (entry_premium - exit_premium) × quantity
9. **Database** — Save exit details, status=CLOSED
10. **Dashboard** — Read from API, display MCX | OPTION partitions

## File Structure

```
option/
├── __init__.py          # Package marker
├── config.py            # Strategy parameters
├── dhan_client.py       # Dhan API client (option chain, margin)
├── strategy.py          # OI-based strike selection
├── trader.py            # Paper trading logic
├── database.py          # SQLite storage
└── scheduler.py         # Auto-run at 09:30, 10:00, 15:15

dashboard/routes/
└── option.py            # API endpoints
```

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/options/overview` | GET | Dashboard overview |
| `/api/options/trades` | GET | Trade history |
| `/api/options/trades/open` | GET | Open trades with live P&L |
| `/api/options/pnl` | GET | P&L summary |
| `/api/options/status` | GET | System status |
| `/api/options/check` | POST | Manual morning check |
| `/api/options/recheck` | POST | Manual 10 AM recheck |
| `/api/options/exit` | POST | Manual EOD exit |

## Database

**File:** `data/db/option_paper_trading.db`

**Table:** `option_trades`

| Column | Type | Purpose |
|--------|------|---------|
| trade_id | TEXT PK | Unique ID |
| underlying | TEXT | NIFTY or SENSEX |
| expiry | TEXT | Expiry date |
| strike | REAL | Selected strike |
| ce_security_id | TEXT | CE option security ID |
| pe_security_id | TEXT | PE option security ID |
| entry_time | TEXT | Entry timestamp |
| entry_ce_premium | REAL | CE premium at entry |
| entry_pe_premium | REAL | PE premium at entry |
| entry_credit | REAL | Total credit received |
| quantity | INTEGER | Lot size |
| lot_size | INTEGER | Lot size |
| margin | REAL | Total margin |
| sl_amount | REAL | Stop loss amount (1% of margin) |
| exit_time | TEXT | Exit timestamp |
| exit_ce_premium | REAL | CE premium at exit |
| exit_pe_premium | REAL | PE premium at exit |
| exit_pnl | REAL | Final P&L |
| status | TEXT | OPEN or CLOSED |
| exit_reason | TEXT | SL or EOD |

## Strategy Rules (from source)

- **Entry:** 9:30 AM, recheck 10:00 AM
- **Exit:** EOD 3:15 PM
- **SL:** 1% of margin
- **OI threshold:** 50 Lakhs difference
- **Expiry days:** Wednesday, Thursday
- **Max trades:** 2 per day (1 NIFTY + 1 SENSEX)
- **Strike:** ATM-2 if CE OI > PE OI, ATM+2 if PE OI > CE OI

## MCX / OPTION Isolation

| Component | MCX | OPTION |
|-----------|-----|--------|
| Database | `trading.db` | `option_paper_trading.db` |
| API prefix | `/api/*` | `/api/options/*` |
| Dashboard | MCX section | OPTION section |
| Strategy | DEMA-ATR futures | OI-based option selling |
| Execution | Paper/Live futures | Paper only |

**Rule:** MCX trades never appear in option tables. Option trades never appear in MCX tables.

## Dashboard Layout

```
┌──────────────────────────────────────────────────────┐
│                    OVERVIEW                          │
├──────────────────────┬───────────────────────────────┤
│       MCX            │      OPTION SELLING           │
│                      │                               │
│ P&L                  │ P&L                           │
│ Open Positions       │ Open Trades                   │
│ Trades Today         │ Trades Today                  │
│ Status               │ Status                        │
└──────────────────────┴───────────────────────────────┘
```
