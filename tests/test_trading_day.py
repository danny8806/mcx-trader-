"""Deep check of full trading day for NIFTY and SENSEX."""
import os, sys, sqlite3, time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Clean database before test
db_path = "data/db/option_paper_trading.db"
if os.path.exists(db_path):
    os.remove(db_path)

from option.database import init_db, get_open_trades, get_today_trades, get_trade_history
from option.database import get_today_pnl, get_total_pnl, get_win_rate, count_today_trades
from option.database import save_trade, close_trade
from option.trader import OptionTrade, is_expiry_day
from option.strategy import parse_option_chain
from option.dhan_client import get_option_chain, get_expiry_list, get_margin
from option.config import INSTRUMENTS, EXPIRY_DAYS

init_db()

print("=" * 80)
print("DEEP CHECK: FULL TRADING DAY — NIFTY + SENSEX")
print("=" * 80)

# ============================================================
# MOCK DHAN API RESPONSES
# ============================================================

MOCK_NIFTY_CHAIN = {
    "last_price": 24500.0,
    "oc": {
        "24500": {
            "ce": {"security_id": "NIFTY_CE_24500", "last_price": 180.5, "oi": 8500000},
            "pe": {"security_id": "NIFTY_PE_24500", "last_price": 95.25, "oi": 6800000},
        },
        "24600": {
            "ce": {"security_id": "NIFTY_CE_24600", "last_price": 140.75, "oi": 7900000},
            "pe": {"security_id": "NIFTY_PE_24600", "last_price": 120.30, "oi": 5500000},
        },
        "24700": {
            "ce": {"security_id": "NIFTY_CE_24700", "last_price": 105.40, "oi": 6600000},
            "pe": {"security_id": "NIFTY_PE_24700", "last_price": 155.80, "oi": 4300000},
        },
        "24800": {
            "ce": {"security_id": "NIFTY_CE_24800", "last_price": 75.00, "oi": 5200000},
            "pe": {"security_id": "NIFTY_PE_24800", "last_price": 195.50, "oi": 3100000},
        },
        "24300": {
            "ce": {"security_id": "NIFTY_CE_24300", "last_price": 250.00, "oi": 9800000},
            "pe": {"security_id": "NIFTY_PE_24300", "last_price": 55.00, "oi": 7200000},
        },
        "24200": {
            "ce": {"security_id": "NIFTY_CE_24200", "last_price": 310.00, "oi": 10000000},
            "pe": {"security_id": "NIFTY_PE_24200", "last_price": 35.00, "oi": 7500000},
        },
        "24100": {
            "ce": {"security_id": "NIFTY_CE_24100", "last_price": 375.00, "oi": 10200000},
            "pe": {"security_id": "NIFTY_PE_24100", "last_price": 22.00, "oi": 7800000},
        },
        "24000": {
            "ce": {"security_id": "NIFTY_CE_24000", "last_price": 440.00, "oi": 10500000},
            "pe": {"security_id": "NIFTY_PE_24000", "last_price": 12.00, "oi": 8000000},
        },
    }
}

MOCK_SENSEX_CHAIN = {
    "last_price": 80500.0,
    "oc": {
        "80500": {
            "ce": {"security_id": "SENSEX_CE_80500", "last_price": 290.00, "oi": 3800000},
            "pe": {"security_id": "SENSEX_PE_80500", "last_price": 150.00, "oi": 6200000},
        },
        "80600": {
            "ce": {"security_id": "SENSEX_CE_80600", "last_price": 230.00, "oi": 3600000},
            "pe": {"security_id": "SENSEX_PE_80600", "last_price": 190.00, "oi": 4900000},
        },
        "80700": {
            "ce": {"security_id": "SENSEX_CE_80700", "last_price": 180.00, "oi": 3500000},
            "pe": {"security_id": "SENSEX_PE_80700", "last_price": 240.00, "oi": 3750000},
        },
        "80800": {
            "ce": {"security_id": "SENSEX_CE_80800", "last_price": 140.00, "oi": 2400000},
            "pe": {"security_id": "SENSEX_PE_80800", "last_price": 300.00, "oi": 2600000},
        },
        "80300": {
            "ce": {"security_id": "SENSEX_CE_80300", "last_price": 400.00, "oi": 4900000},
            "pe": {"security_id": "SENSEX_PE_80300", "last_price": 80.00, "oi": 5000000},
        },
        "80200": {
            "ce": {"security_id": "SENSEX_CE_80200", "last_price": 480.00, "oi": 4950000},
            "pe": {"security_id": "SENSEX_PE_80200", "last_price": 50.00, "oi": 5100000},
        },
        "80100": {
            "ce": {"security_id": "SENSEX_CE_80100", "last_price": 560.00, "oi": 5000000},
            "pe": {"security_id": "SENSEX_PE_80100", "last_price": 30.00, "oi": 5200000},
        },
        "80000": {
            "ce": {"security_id": "SENSEX_CE_80000", "last_price": 650.00, "oi": 5050000},
            "pe": {"security_id": "SENSEX_PE_80000", "last_price": 15.00, "oi": 5300000},
        },
    }
}

print()
print("=" * 80)
print("PHASE 1: MARKET OPEN (09:30) — MORNING CHECK")
print("=" * 80)

# Parse NIFTY signal
nifty = INSTRUMENTS["NIFTY"]
nifty_signal = parse_option_chain(MOCK_NIFTY_CHAIN, nifty["lot_size"], nifty["exchange"], "NIFTY")

print(f"\n  NIFTY:")
print(f"    Spot:           {nifty_signal.spot}")
print(f"    ATM:            {nifty_signal.atm}")
print(f"    Selected:       {nifty_signal.selected_strike}")
print(f"    CE LTP:         {nifty_signal.ce_ltp}")
print(f"    PE LTP:         {nifty_signal.pe_ltp}")
print(f"    CE OI (ATM to ATM+3): {nifty_signal.ce_oi:,}")
print(f"    PE OI (ATM to ATM-3): {nifty_signal.pe_oi:,}")
print(f"    OI Diff:        {abs(nifty_signal.ce_oi - nifty_signal.pe_oi):,}")
print(f"    PCR:            {nifty_signal.pcr:.2f}")
print(f"    Reason:         {nifty_signal.selection_reason.replace(chr(8594), '->')}")

# Parse SENSEX signal
sensex = INSTRUMENTS["SENSEX"]
sensex_signal = parse_option_chain(MOCK_SENSEX_CHAIN, sensex["lot_size"], sensex["exchange"], "SENSEX")

print(f"\n  SENSEX:")
print(f"    Spot:           {sensex_signal.spot}")
print(f"    ATM:            {sensex_signal.atm}")
print(f"    Selected:       {sensex_signal.selected_strike}")
print(f"    CE LTP:         {sensex_signal.ce_ltp}")
print(f"    PE LTP:         {sensex_signal.pe_ltp}")
print(f"    CE OI (ATM to ATM+3): {sensex_signal.ce_oi:,}")
print(f"    PE OI (ATM to ATM-3): {sensex_signal.pe_oi:,}")
print(f"    OI Diff:        {abs(sensex_signal.ce_oi - sensex_signal.pe_oi):,}")
print(f"    PCR:            {sensex_signal.pcr:.2f}")
print(f"    Reason:         {sensex_signal.selection_reason.replace(chr(8594), '->')}")

# Check OI threshold
oi_diff_nifty = abs(nifty_signal.ce_oi - nifty_signal.pe_oi)
oi_diff_sensex = abs(sensex_signal.ce_oi - sensex_signal.pe_oi)
print(f"\n  OI THRESHOLD CHECK:")
print(f"    NIFTY OI diff:  {oi_diff_nifty:,} vs threshold 5,000,000 -> {'PASS' if oi_diff_nifty >= 5000000 else 'FAIL'}")
print(f"    SENSEX OI diff: {oi_diff_sensex:,} vs threshold 5,000,000 -> {'PASS' if oi_diff_sensex >= 5000000 else 'FAIL'}")

print()
print("=" * 80)
print("PHASE 2: OPEN TRADES — ENTRY PREMIUMS RECORDED")
print("=" * 80)

# Create NIFTY trade
nifty_trade = OptionTrade(
    trade_id="OPT-NIFTY-DEEP-TEST",
    underlying="NIFTY",
    expiry="2026-09-11",
    strike=nifty_signal.selected_strike,
    entry_time="2026-09-09 09:30:00",
    entry_ce_premium=nifty_signal.ce_ltp,
    entry_pe_premium=nifty_signal.pe_ltp,
    entry_credit=(nifty_signal.ce_ltp + nifty_signal.pe_ltp) * nifty["lot_size"],
    quantity=nifty["lot_size"],
    lot_size=nifty["lot_size"],
    margin=0,
    sl_amount=0,
    status="OPEN",
    pcr=nifty_signal.pcr,
    selection_reason=nifty_signal.selection_reason,
    spot_at_entry=nifty_signal.spot,
    ce_security_id=nifty_signal.ce_sec,
    pe_security_id=nifty_signal.pe_sec,
)
nifty_trade.margin = 350000.0
nifty_trade.sl_amount = nifty_trade.margin * 0.01
save_trade(nifty_trade)

# Create SENSEX trade
sensex_trade = OptionTrade(
    trade_id="OPT-SENSEX-DEEP-TEST",
    underlying="SENSEX",
    expiry="2026-09-11",
    strike=sensex_signal.selected_strike,
    entry_time="2026-09-09 09:30:00",
    entry_ce_premium=sensex_signal.ce_ltp,
    entry_pe_premium=sensex_signal.pe_ltp,
    entry_credit=(sensex_signal.ce_ltp + sensex_signal.pe_ltp) * sensex["lot_size"],
    quantity=sensex["lot_size"],
    lot_size=sensex["lot_size"],
    margin=0,
    sl_amount=0,
    status="OPEN",
    pcr=sensex_signal.pcr,
    selection_reason=sensex_signal.selection_reason,
    spot_at_entry=sensex_signal.spot,
    ce_security_id=sensex_signal.ce_sec,
    pe_security_id=sensex_signal.pe_sec,
)
sensex_trade.margin = 200000.0
sensex_trade.sl_amount = sensex_trade.margin * 0.01
save_trade(sensex_trade)

print(f"\n  NIFTY TRADE OPENED:")
print(f"    Trade ID:       {nifty_trade.trade_id}")
print(f"    Strike:         {nifty_trade.strike}")
print(f"    Entry CE:       {nifty_trade.entry_ce_premium}")
print(f"    Entry PE:       {nifty_trade.entry_pe_premium}")
print(f"    Entry Credit:   Rs {nifty_trade.entry_credit:,.2f}")
print(f"    Margin:         Rs {nifty_trade.margin:,.2f}")
print(f"    SL Amount:      Rs {nifty_trade.sl_amount:,.2f}")
print(f"    Lot Size:       {nifty_trade.lot_size}")
print(f"    Quantity:       {nifty_trade.quantity}")

print(f"\n  SENSEX TRADE OPENED:")
print(f"    Trade ID:       {sensex_trade.trade_id}")
print(f"    Strike:         {sensex_trade.strike}")
print(f"    Entry CE:       {sensex_trade.entry_ce_premium}")
print(f"    Entry PE:       {sensex_trade.entry_pe_premium}")
print(f"    Entry Credit:   Rs {sensex_trade.entry_credit:,.2f}")
print(f"    Margin:         Rs {sensex_trade.margin:,.2f}")
print(f"    SL Amount:      Rs {sensex_trade.sl_amount:,.2f}")
print(f"    Lot Size:       {sensex_trade.lot_size}")
print(f"    Quantity:       {sensex_trade.quantity}")

# Verify both open
open_trades = get_open_trades()
print(f"\n  DATABASE STATE:")
print(f"    Open trades:    {len(open_trades)}")
print(f"    Expected:       2")
for t in open_trades:
    print(f"      {t.trade_id}: {t.underlying} {t.strike}")

print()
print("=" * 80)
print("PHASE 3: INTRADAY — PREMIUM MONITORING (10:00 RECHECK)")
print("=" * 80)

# Simulate premium changes - NIFTY goes against us
print(f"\n  SIMULATING 10:00 PREMIUM CHECK...")
print(f"  NIFTY premiums rising (market moving against us):")
nifty_exit_ce = nifty_signal.ce_ltp + 75  # CE premium up
nifty_exit_pe = nifty_signal.pe_ltp + 45   # PE premium up
nifty_ce_pnl = (nifty_signal.ce_ltp - nifty_exit_ce) * nifty_trade.quantity
nifty_pe_pnl = (nifty_signal.pe_ltp - nifty_exit_pe) * nifty_trade.quantity
nifty_total_pnl = nifty_ce_pnl + nifty_pe_pnl
print(f"    Entry CE: {nifty_signal.ce_ltp} -> Current: {nifty_exit_ce} (up {nifty_exit_ce - nifty_signal.ce_ltp})")
print(f"    Entry PE: {nifty_signal.pe_ltp} -> Current: {nifty_exit_pe} (up {nifty_exit_pe - nifty_signal.pe_ltp})")
print(f"    CE P&L:   ({nifty_signal.ce_ltp} - {nifty_exit_ce}) * {nifty_trade.quantity} = Rs {nifty_ce_pnl:,.2f}")
print(f"    PE P&L:   ({nifty_signal.pe_ltp} - {nifty_exit_pe}) * {nifty_trade.quantity} = Rs {nifty_pe_pnl:,.2f}")
print(f"    Total:    Rs {nifty_total_pnl:,.2f}")
print(f"    SL:       Rs {nifty_trade.sl_amount:,.2f}")
print(f"    SL Hit:   {'YES' if abs(nifty_total_pnl) >= nifty_trade.sl_amount else 'NO'}")

# SENSEX goes in our favor
print(f"\n  SENSEX premiums falling (market moving in our favor):")
sensex_exit_ce = sensex_signal.ce_ltp - 60  # CE premium down
sensex_exit_pe = sensex_signal.pe_ltp - 40  # PE premium down
sensex_ce_pnl = (sensex_signal.ce_ltp - sensex_exit_ce) * sensex_trade.quantity
sensex_pe_pnl = (sensex_signal.pe_ltp - sensex_exit_pe) * sensex_trade.quantity
sensex_total_pnl = sensex_ce_pnl + sensex_pe_pnl
print(f"    Entry CE: {sensex_signal.ce_ltp} -> Current: {sensex_exit_ce} (down {sensex_signal.ce_ltp - sensex_exit_ce})")
print(f"    Entry PE: {sensex_signal.pe_ltp} -> Current: {sensex_exit_pe} (down {sensex_signal.pe_ltp - sensex_exit_pe})")
print(f"    CE P&L:   ({sensex_signal.ce_ltp} - {sensex_exit_ce}) * {sensex_trade.quantity} = Rs {sensex_ce_pnl:,.2f}")
print(f"    PE P&L:   ({sensex_signal.pe_ltp} - {sensex_exit_pe}) * {sensex_trade.quantity} = Rs {sensex_pe_pnl:,.2f}")
print(f"    Total:    Rs {sensex_total_pnl:,.2f}")
print(f"    SL:       Rs {sensex_trade.sl_amount:,.2f}")
print(f"    SL Hit:   {'YES' if abs(sensex_total_pnl) >= sensex_trade.sl_amount else 'NO'}")

print()
print("=" * 80)
print("PHASE 4: EXIT — NIFTY SL HIT")
print("=" * 80)

# NIFTY SL exit
if abs(nifty_total_pnl) >= nifty_trade.sl_amount:
    close_trade(
        nifty_trade.trade_id,
        "2026-09-09 11:30:00",
        nifty_exit_ce,
        nifty_exit_pe,
        nifty_total_pnl,
        "SL"
    )
    print(f"\n  NIFTY EXITED (SL):")
    print(f"    Exit Time:      2026-09-09 11:30:00")
    print(f"    Exit CE:        {nifty_exit_ce}")
    print(f"    Exit PE:        {nifty_exit_pe}")
    print(f"    Exit P&L:       Rs {nifty_total_pnl:,.2f}")
    print(f"    Exit Reason:    SL")
    print(f"    Status:         CLOSED")

print()
print("=" * 80)
print("PHASE 5: EXIT — SENSEX EOD")
print("=" * 80)

# SENSEX EOD exit (premiums dropped)
sensex_exit_ce = sensex_signal.ce_ltp - 100
sensex_exit_pe = sensex_signal.pe_ltp - 70
sensex_ce_pnl = (sensex_signal.ce_ltp - sensex_exit_ce) * sensex_trade.quantity
sensex_pe_pnl = (sensex_signal.pe_ltp - sensex_exit_pe) * sensex_trade.quantity
sensex_total_pnl = sensex_ce_pnl + sensex_pe_pnl

close_trade(
    sensex_trade.trade_id,
    "2026-09-09 15:15:00",
    sensex_exit_ce,
    sensex_exit_pe,
    sensex_total_pnl,
    "EOD"
)
print(f"\n  SENSEX EXITED (EOD):")
print(f"    Exit Time:      2026-09-09 15:15:00")
print(f"    Exit CE:        {sensex_exit_ce}")
print(f"    Exit PE:        {sensex_exit_pe}")
print(f"    Exit P&L:       Rs {sensex_total_pnl:,.2f}")
print(f"    Exit Reason:    EOD")
print(f"    Status:         CLOSED")

print()
print("=" * 80)
print("PHASE 6: FINAL DATABASE STATE")
print("=" * 80)

open_trades = get_open_trades()
today_trades = get_today_trades()
history = get_trade_history(10)

print(f"\n  OPEN TRADES:     {len(open_trades)} (should be 0)")
print(f"  TODAY'S TRADES:  {len(today_trades)} (should be 2)")
print(f"  TRADE HISTORY:   {len(history)}")

for t in history:
    pnl_str = f"Rs {t.exit_pnl:+,.2f}" if t.exit_pnl is not None else "N/A"
    print(f"    {t.trade_id}: {t.underlying} {t.strike} | {t.status} | {t.exit_reason} | P&L: {pnl_str}")

print()
print("=" * 80)
print("PHASE 7: P&L SUMMARY")
print("=" * 80)

today_pnl = get_today_pnl()
total_pnl = get_total_pnl()
win_rate = get_win_rate()
count = count_today_trades()

print(f"\n  Today P&L:       Rs {today_pnl:+,.2f}")
print(f"  Total P&L:       Rs {total_pnl:+,.2f}")
print(f"  Win Rate:        {win_rate:.1f}%")
print(f"  Trade Count:     {count}")

# Manual calculation
expected = nifty_total_pnl + sensex_total_pnl
print(f"\n  NIFTY P&L:       Rs {nifty_total_pnl:+,.2f}")
print(f"  SENSEX P&L:      Rs {sensex_total_pnl:+,.2f}")
print(f"  Expected Total:  Rs {expected:+,.2f}")
print(f"  DB Total:        Rs {total_pnl:+,.2f}")
print(f"  Match:           {'YES' if abs(expected - total_pnl) < 0.01 else 'NO'}")

print()
print("=" * 80)
print("PHASE 8: INDEPENDENT VERIFICATION")
print("=" * 80)

for t in history:
    print(f"\n  {t.trade_id}:")
    print(f"    underlying:       {t.underlying}")
    print(f"    strike:           {t.strike}")
    print(f"    entry_ce:         {t.entry_ce_premium}")
    print(f"    entry_pe:         {t.entry_pe_premium}")
    print(f"    entry_credit:     Rs {t.entry_credit:,.2f}")
    print(f"    exit_ce:          {t.exit_ce_premium}")
    print(f"    exit_pe:          {t.exit_pe_premium}")
    print(f"    exit_pnl:         Rs {t.exit_pnl:+,.2f}")
    print(f"    status:           {t.status}")
    print(f"    exit_reason:      {t.exit_reason}")
    print(f"    quantity:         {t.quantity}")
    print(f"    lot_size:         {t.lot_size}")
    print(f"    margin:           Rs {t.margin:,.2f}")
    print(f"    sl_amount:        Rs {t.sl_amount:,.2f}")
    print(f"    pcr:              {t.pcr:.2f}")
    print(f"    selection_reason: {t.selection_reason}")
    print(f"    spot_at_entry:    {t.spot_at_entry}")

    # Verify P&L
    expected_pnl = (t.entry_ce_premium - t.exit_ce_premium) * t.quantity + \
                   (t.entry_pe_premium - t.exit_pe_premium) * t.quantity
    pnl_ok = abs(expected_pnl - t.exit_pnl) < 0.01
    print(f"    P&L CHECK:        {'PASS' if pnl_ok else 'FAIL'} (expected {expected_pnl:+,.2f})")

print()
print("=" * 80)
print("ALL PHASES COMPLETE")
print("=" * 80)
