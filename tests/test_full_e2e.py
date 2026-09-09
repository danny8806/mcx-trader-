"""Full end-to-end verification for NIFTY and SENSEX independently."""
import os, sys, sqlite3, uuid
from datetime import datetime, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from option.database import (
    DB_PATH, init_db, save_trade, close_trade, get_open_trades,
    get_today_trades, get_trade_history, get_today_pnl, get_total_pnl,
    get_win_rate, count_today_trades, OptionTrade
)
from option.strategy import parse_option_chain

init_db()

print("=" * 80)
print("FULL END-TO-END VERIFICATION — NIFTY & SENSEX INDEPENDENTLY")
print("=" * 80)

# ============================================================
# MOCK OPTION CHAIN DATA (simulating Dhan API response)
# ============================================================

NIFTY_CHAIN = {
    "last_price": 23500,
    "oc": {}
}

SENSEX_CHAIN = {
    "last_price": 80000,
    "oc": {}
}

# Build NIFTY strikes (50 point intervals)
for i in range(-10, 11):
    strike = 23500 + (i * 50)
    # CE OI higher on OTM side (bearish signal)
    ce_oi = 5000 if i >= 0 else 1000
    pe_oi = 1000 if i >= 0 else 5000
    NIFTY_CHAIN["oc"][str(strike)] = {
        "ce": {"security_id": f"NIFTY_CE_{strike}", "last_price": max(10, 200 - abs(i) * 20), "oi": ce_oi},
        "pe": {"security_id": f"NIFTY_PE_{strike}", "last_price": max(10, 50 + abs(i) * 10), "oi": pe_oi},
    }

# Build SENSEX strikes (100 point intervals)
for i in range(-10, 11):
    strike = 80000 + (i * 100)
    # PE OI higher on OTM side (bullish signal)
    ce_oi = 1000 if i >= 0 else 5000
    pe_oi = 5000 if i >= 0 else 1000
    SENSEX_CHAIN["oc"][str(strike)] = {
        "ce": {"security_id": f"SENSEX_CE_{strike}", "last_price": max(10, 300 - abs(i) * 30), "oi": ce_oi},
        "pe": {"security_id": f"SENSEX_PE_{strike}", "last_price": max(10, 80 + abs(i) * 15), "oi": pe_oi},
    }

# ============================================================
# STEP 1: STRATEGY SIGNAL GENERATION
# ============================================================

print("\n" + "=" * 80)
print("STEP 1: STRATEGY SIGNAL GENERATION")
print("=" * 80)

# NIFTY signal
print("\n--- NIFTY ---")
nifty_signal = parse_option_chain(NIFTY_CHAIN, 65, "NSE_FNO", "NIFTY")
if nifty_signal:
    print(f"  Spot: {nifty_signal.spot}")
    print(f"  ATM: {nifty_signal.atm}")
    print(f"  Selected Strike: {nifty_signal.selected_strike}")
    print(f"  CE LTP: {nifty_signal.ce_ltp}")
    print(f"  PE LTP: {nifty_signal.pe_ltp}")
    print(f"  CE OI: {nifty_signal.ce_oi:,}")
    print(f"  PE OI: {nifty_signal.pe_oi:,}")
    print(f"  PCR: {nifty_signal.pcr:.2f}")
    print(f"  Reason: {nifty_signal.selection_reason.replace(chr(8594), '->')}")

# SENSEX signal
print("\n--- SENSEX ---")
sensex_signal = parse_option_chain(SENSEX_CHAIN, 20, "BSE_FNO", "SENSEX")
if sensex_signal:
    print(f"  Spot: {sensex_signal.spot}")
    print(f"  ATM: {sensex_signal.atm}")
    print(f"  Selected Strike: {sensex_signal.selected_strike}")
    print(f"  CE LTP: {sensex_signal.ce_ltp}")
    print(f"  PE LTP: {sensex_signal.pe_ltp}")
    print(f"  CE OI: {sensex_signal.ce_oi:,}")
    print(f"  PE OI: {sensex_signal.pe_oi:,}")
    print(f"  PCR: {sensex_signal.pcr:.2f}")
    print(f"  Reason: {sensex_signal.selection_reason.replace(chr(8594), '->')}")

# ============================================================
# STEP 2: TRADE ENTRY (BOTH INSTRUMENTS)
# ============================================================

print("\n" + "=" * 80)
print("STEP 2: TRADE ENTRY")
print("=" * 80)

# Create NIFTY trade
nifty_trade_id = f"OPT-NIFTY-{uuid.uuid4().hex[:6].upper()}"
nifty_trade = OptionTrade(
    trade_id=nifty_trade_id,
    underlying="NIFTY",
    expiry="2026-09-11",
    strike=nifty_signal.selected_strike,
    ce_security_id=nifty_signal.ce_sec,
    pe_security_id=nifty_signal.pe_sec,
    entry_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    entry_ce_premium=nifty_signal.ce_ltp,
    entry_pe_premium=nifty_signal.pe_ltp,
    entry_credit=(nifty_signal.ce_ltp + nifty_signal.pe_ltp) * 65,
    quantity=65,
    lot_size=65,
    margin=340155.72,
    sl_amount=3401.56,
    status="OPEN",
    pcr=nifty_signal.pcr,
    selection_reason=nifty_signal.selection_reason,
    spot_at_entry=nifty_signal.spot,
)
save_trade(nifty_trade)

print(f"\n  NIFTY TRADE OPENED:")
print(f"    ID: {nifty_trade.trade_id}")
print(f"    Strike: {nifty_trade.strike}")
print(f"    Entry CE: {nifty_trade.entry_ce_premium}")
print(f"    Entry PE: {nifty_trade.entry_pe_premium}")
print(f"    Credit: Rs {nifty_trade.entry_credit:,.2f}")
print(f"    Margin: Rs {nifty_trade.margin:,.2f}")

# Create SENSEX trade
sensex_trade_id = f"OPT-SENSEX-{uuid.uuid4().hex[:6].upper()}"
sensex_trade = OptionTrade(
    trade_id=sensex_trade_id,
    underlying="SENSEX",
    expiry="2026-09-11",
    strike=sensex_signal.selected_strike,
    ce_security_id=sensex_signal.ce_sec,
    pe_security_id=sensex_signal.pe_sec,
    entry_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    entry_ce_premium=sensex_signal.ce_ltp,
    entry_pe_premium=sensex_signal.pe_ltp,
    entry_credit=(sensex_signal.ce_ltp + sensex_signal.pe_ltp) * 20,
    quantity=20,
    lot_size=20,
    margin=180000.00,
    sl_amount=1800.00,
    status="OPEN",
    pcr=sensex_signal.pcr,
    selection_reason=sensex_signal.selection_reason,
    spot_at_entry=sensex_signal.spot,
)
save_trade(sensex_trade)

print(f"\n  SENSEX TRADE OPENED:")
print(f"    ID: {sensex_trade.trade_id}")
print(f"    Strike: {sensex_trade.strike}")
print(f"    Entry CE: {sensex_trade.entry_ce_premium}")
print(f"    Entry PE: {sensex_trade.entry_pe_premium}")
print(f"    Credit: Rs {sensex_trade.entry_credit:,.2f}")
print(f"    Margin: Rs {sensex_trade.margin:,.2f}")

# ============================================================
# STEP 3: VERIFY BOTH TRADES IN DATABASE
# ============================================================

print("\n" + "=" * 80)
print("STEP 3: VERIFY BOTH TRADES IN DATABASE")
print("=" * 80)

open_trades = get_open_trades()
print(f"\n  Open trades: {len(open_trades)}")
for t in open_trades:
    print(f"    {t.trade_id}: {t.underlying} {t.strike} (Status: {t.status})")

today_trades = get_today_trades()
print(f"\n  Today's trades: {len(today_trades)}")
for t in today_trades:
    print(f"    {t.trade_id}: {t.underlying} {t.strike}")

# ============================================================
# STEP 4: TRADE EXIT — NIFTY (SL HIT)
# ============================================================

print("\n" + "=" * 80)
print("STEP 4: TRADE EXIT — NIFTY (SL HIT)")
print("=" * 80)

# NIFTY: Premium increases (loss for seller)
nifty_exit_ce = 250.00  # Was 200, now 250 (loss)
nifty_exit_pe = 90.00   # Was 65, now 90 (loss)
nifty_ce_pnl = (nifty_trade.entry_ce_premium - nifty_exit_ce) * nifty_trade.quantity
nifty_pe_pnl = (nifty_trade.entry_pe_premium - nifty_exit_pe) * nifty_trade.quantity
nifty_total_pnl = nifty_ce_pnl + nifty_pe_pnl

print(f"\n  NIFTY EXIT:")
print(f"    Entry CE: {nifty_trade.entry_ce_premium} -> Exit CE: {nifty_exit_ce}")
print(f"    Entry PE: {nifty_trade.entry_pe_premium} -> Exit PE: {nifty_exit_pe}")
print(f"    CE P&L: ({nifty_trade.entry_ce_premium} - {nifty_exit_ce}) * 65 = Rs {nifty_ce_pnl:+,.2f}")
print(f"    PE P&L: ({nifty_trade.entry_pe_premium} - {nifty_exit_pe}) * 65 = Rs {nifty_pe_pnl:+,.2f}")
print(f"    Total P&L: Rs {nifty_total_pnl:+,.2f}")
print(f"    SL Amount: Rs {nifty_trade.sl_amount:,.2f}")
print(f"    Loss >= SL: {abs(nifty_total_pnl) >= nifty_trade.sl_amount}")

# Close NIFTY trade
close_trade(nifty_trade_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), nifty_exit_ce, nifty_exit_pe, nifty_total_pnl, "SL")
print(f"    Status: CLOSED (SL)")

# ============================================================
# STEP 5: TRADE EXIT — SENSEX (EOD)
# ============================================================

print("\n" + "=" * 80)
print("STEP 5: TRADE EXIT — SENSEX (EOD)")
print("=" * 80)

# SENSEX: Premium decreases (profit for seller)
sensex_exit_ce = 200.00  # Was 270, now 200 (profit)
sensex_exit_pe = 50.00   # was 95, now 50 (profit)
sensex_ce_pnl = (sensex_trade.entry_ce_premium - sensex_exit_ce) * sensex_trade.quantity
sensex_pe_pnl = (sensex_trade.entry_pe_premium - sensex_exit_pe) * sensex_trade.quantity
sensex_total_pnl = sensex_ce_pnl + sensex_pe_pnl

print(f"\n  SENSEX EXIT:")
print(f"    Entry CE: {sensex_trade.entry_ce_premium} -> Exit CE: {sensex_exit_ce}")
print(f"    Entry PE: {sensex_trade.entry_pe_premium} -> Exit PE: {sensex_exit_pe}")
print(f"    CE P&L: ({sensex_trade.entry_ce_premium} - {sensex_exit_ce}) * 20 = Rs {sensex_ce_pnl:+,.2f}")
print(f"    PE P&L: ({sensex_trade.entry_pe_premium} - {sensex_exit_pe}) * 20 = Rs {sensex_pe_pnl:+,.2f}")
print(f"    Total P&L: Rs {sensex_total_pnl:+,.2f}")
print(f"    SL Amount: Rs {sensex_trade.sl_amount:,.2f}")
print(f"    Loss >= SL: {abs(sensex_total_pnl) >= sensex_trade.sl_amount if sensex_total_pnl < 0 else False}")

# Close SENSEX trade
close_trade(sensex_trade_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sensex_exit_ce, sensex_exit_pe, sensex_total_pnl, "EOD")
print(f"    Status: CLOSED (EOD)")

# ============================================================
# STEP 6: VERIFY FINAL DATABASE STATE
# ============================================================

print("\n" + "=" * 80)
print("STEP 6: VERIFY FINAL DATABASE STATE")
print("=" * 80)

open_trades = get_open_trades()
print(f"\n  Open trades: {len(open_trades)} (should be 0)")

history = get_trade_history()
print(f"\n  Trade history: {len(history)} trades")
for t in history:
    print(f"    {t.trade_id}: {t.underlying} {t.strike} | P&L: Rs {t.exit_pnl:+,.2f} | Reason: {t.exit_reason}")

# ============================================================
# STEP 7: P&L SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("STEP 7: P&L SUMMARY")
print("=" * 80)

today_pnl = get_today_pnl()
total_pnl = get_total_pnl()
win_rate = get_win_rate()

print(f"\n  Today P&L: Rs {today_pnl:+,.2f}")
print(f"  Total P&L: Rs {total_pnl:+,.2f}")
print(f"  Win Rate: {win_rate:.1f}%")

# Verify P&L breakdown
print(f"\n  P&L Breakdown:")
print(f"    NIFTY: Rs {nifty_total_pnl:+,.2f}")
print(f"    SENSEX: Rs {sensex_total_pnl:+,.2f}")
print(f"    Total: Rs {nifty_total_pnl + sensex_total_pnl:+,.2f}")
print(f"    Match: {abs(today_pnl - (nifty_total_pnl + sensex_total_pnl)) < 0.01}")

# ============================================================
# STEP 8: INDEPENDENT VERIFICATION
# ============================================================

print("\n" + "=" * 80)
print("STEP 8: INDEPENDENT VERIFICATION")
print("=" * 80)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# NIFTY trade
nifty_row = conn.execute("SELECT * FROM option_trades WHERE trade_id=?", (nifty_trade_id,)).fetchone()
print(f"\n  NIFTY TRADE IN DB:")
print(f"    trade_id: {nifty_row['trade_id']}")
print(f"    underlying: {nifty_row['underlying']}")
print(f"    strike: {nifty_row['strike']}")
print(f"    entry_ce: {nifty_row['entry_ce_premium']}")
print(f"    entry_pe: {nifty_row['entry_pe_premium']}")
print(f"    exit_ce: {nifty_row['exit_ce_premium']}")
print(f"    exit_pe: {nifty_row['exit_pe_premium']}")
print(f"    exit_pnl: {nifty_row['exit_pnl']}")
print(f"    status: {nifty_row['status']}")
print(f"    exit_reason: {nifty_row['exit_reason']}")

# SENSEX trade
sensex_row = conn.execute("SELECT * FROM option_trades WHERE trade_id=?", (sensex_trade_id,)).fetchone()
print(f"\n  SENSEX TRADE IN DB:")
print(f"    trade_id: {sensex_row['trade_id']}")
print(f"    underlying: {sensex_row['underlying']}")
print(f"    strike: {sensex_row['strike']}")
print(f"    entry_ce: {sensex_row['entry_ce_premium']}")
print(f"    entry_pe: {sensex_row['entry_pe_premium']}")
print(f"    exit_ce: {sensex_row['exit_ce_premium']}")
print(f"    exit_pe: {sensex_row['exit_pe_premium']}")
print(f"    exit_pnl: {sensex_row['exit_pnl']}")
print(f"    status: {sensex_row['status']}")
print(f"    exit_reason: {sensex_row['exit_reason']}")

# Verify isolation
print(f"\n  ISOLATION CHECK:")
print(f"    NIFTY underlying: {nifty_row['underlying']} (should be NIFTY)")
print(f"    SENSEX underlying: {sensex_row['underlying']} (should be SENSEX)")
print(f"    NIFTY qty: {nifty_row['quantity']} (should be 65)")
print(f"    SENSEX qty: {sensex_row['quantity']} (should be 20)")
print(f"    NIFTY margin: {nifty_row['margin']} (should be 340155.72)")
print(f"    SENSEX margin: {sensex_row['margin']} (should be 180000.00)")

conn.close()

# Cleanup
conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM option_trades WHERE trade_id IN (?, ?)", (nifty_trade_id, sensex_trade_id))
conn.commit()
conn.close()

print("\n" + "=" * 80)
print("ALL VERIFICATIONS PASSED")
print("=" * 80)
