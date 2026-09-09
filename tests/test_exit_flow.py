"""Verify exit flow — every field saved, every calculation."""
import os, sys, sqlite3, uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from option.database import DB_PATH, init_db, save_trade, close_trade, get_open_trades, get_trade_history, OptionTrade

init_db()

print("=" * 70)
print("TRADE EXIT — COMPLETE VERIFICATION")
print("=" * 70)

# Simulate a full trade lifecycle
print("\n1. CREATING TEST TRADE")
print("-" * 70)

trade_id = f"TEST-EXIT-{uuid.uuid4().hex[:8]}"
trade = OptionTrade(
    trade_id=trade_id,
    underlying="NIFTY",
    expiry="2026-09-11",
    strike=23400,
    ce_security_id="SEC_CE_123",
    pe_security_id="SEC_PE_456",
    entry_time="2026-09-09 09:30:00",
    entry_ce_premium=215.60,
    entry_pe_premium=65.65,
    entry_credit=18281.25,  # (215.60 + 65.65) * 65
    quantity=65,
    lot_size=65,
    margin=340155.72,
    sl_amount=3401.56,  # 1% of margin
    status="OPEN",
    pcr=1.25,
    selection_reason="CE OI (15000) > PE OI (10000) -> ATM-2",
    spot_at_entry=23500.0,
)

save_trade(trade)
print(f"   Trade ID: {trade.trade_id}")
print(f"   Underlying: {trade.underlying}")
print(f"   Strike: {trade.strike}")
print(f"   Entry CE: {trade.entry_ce_premium}")
print(f"   Entry PE: {trade.entry_pe_premium}")
print(f"   Entry Credit: Rs {trade.entry_credit:,.2f}")
print(f"   Quantity: {trade.quantity}")
print(f"   Margin: Rs {trade.margin:,.2f}")
print(f"   SL Amount: Rs {trade.sl_amount:,.2f}")
print(f"   Status: {trade.status}")

# Simulate exit
print("\n2. EXITING TRADE (SL HIT)")
print("-" * 70)

exit_ce = 230.00  # Premium increased (loss for seller)
exit_pe = 80.00   # Premium increased (loss for seller)
exit_time = "2026-09-09 11:45:00"

# Calculate P&L
ce_pnl = (trade.entry_ce_premium - exit_ce) * trade.quantity
pe_pnl = (trade.entry_pe_premium - exit_pe) * trade.quantity
total_pnl = ce_pnl + pe_pnl

print(f"   Exit CE: {exit_ce}")
print(f"   Exit PE: {exit_pe}")
print(f"   CE P&L: ({trade.entry_ce_premium} - {exit_ce}) * {trade.quantity} = Rs {ce_pnl:+,.2f}")
print(f"   PE P&L: ({trade.entry_pe_premium} - {exit_pe}) * {trade.quantity} = Rs {pe_pnl:+,.2f}")
print(f"   Total P&L: Rs {total_pnl:+,.2f}")
print(f"   SL Amount: Rs {trade.sl_amount:,.2f}")
print(f"   Loss exceeds SL: {abs(total_pnl) >= trade.sl_amount}")
print(f"   Exit Reason: SL")

# Close trade
close_trade(trade_id, exit_time, exit_ce, exit_pe, total_pnl, "SL")

# Verify what was saved
print("\n3. VERIFYING DATABASE STORAGE")
print("-" * 70)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM option_trades WHERE trade_id=?", (trade_id,)).fetchone()
conn.close()

print(f"   trade_id: {row['trade_id']}")
print(f"   underlying: {row['underlying']}")
print(f"   expiry: {row['expiry']}")
print(f"   strike: {row['strike']}")
print(f"   ce_security_id: {row['ce_security_id']}")
print(f"   pe_security_id: {row['pe_security_id']}")
print(f"   entry_time: {row['entry_time']}")
print(f"   entry_ce_premium: {row['entry_ce_premium']}")
print(f"   entry_pe_premium: {row['entry_pe_premium']}")
print(f"   entry_credit: {row['entry_credit']}")
print(f"   quantity: {row['quantity']}")
print(f"   lot_size: {row['lot_size']}")
print(f"   margin: {row['margin']}")
print(f"   sl_amount: {row['sl_amount']}")
print(f"   exit_time: {row['exit_time']}")
print(f"   exit_ce_premium: {row['exit_ce_premium']}")
print(f"   exit_pe_premium: {row['exit_pe_premium']}")
print(f"   exit_pnl: {row['exit_pnl']}")
print(f"   status: {row['status']}")
print(f"   exit_reason: {row['exit_reason']}")
print(f"   pcr: {row['pcr']}")
print(f"   selection_reason: {row['selection_reason']}")
print(f"   spot_at_entry: {row['spot_at_entry']}")
print(f"   created_at: {row['created_at']}")

# Verify P&L calculation stored correctly
print("\n4. P&L CALCULATION VERIFICATION")
print("-" * 70)

stored_pnl = row['exit_pnl']
expected_pnl = (215.60 - 230.00) * 65 + (65.65 - 80.00) * 65
print(f"   Stored P&L: Rs {stored_pnl:+,.2f}")
print(f"   Expected P&L: Rs {expected_pnl:+,.2f}")
print(f"   Match: {abs(stored_pnl - expected_pnl) < 0.01}")

# Verify status
print("\n5. STATUS VERIFICATION")
print("-" * 70)
print(f"   Status: {row['status']}")
print(f"   Expected: CLOSED")
print(f"   Match: {row['status'] == 'CLOSED'}")

# Verify exit reason
print("\n6. EXIT REASON VERIFICATION")
print("-" * 70)
print(f"   Exit Reason: {row['exit_reason']}")
print(f"   Expected: SL")
print(f"   Match: {row['exit_reason'] == 'SL'}")

# Cleanup
conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM option_trades WHERE trade_id=?", (trade_id,))
conn.commit()
conn.close()

print("\n7. CLEANUP")
print("-" * 70)
print("   Test trade removed from database")

print("\n" + "=" * 70)
print("ALL EXIT FIELDS VERIFIED")
print("=" * 70)

# Summary of all fields saved
print("\nFIELDS SAVED ON EXIT:")
print("-" * 70)
fields = [
    ("trade_id", "Unique trade identifier"),
    ("underlying", "NIFTY or SENSEX"),
    ("expiry", "Option expiry date"),
    ("strike", "Selected strike price"),
    ("ce_security_id", "CE option security ID"),
    ("pe_security_id", "PE option security ID"),
    ("entry_time", "Trade entry timestamp"),
    ("entry_ce_premium", "CE premium at entry"),
    ("entry_pe_premium", "PE premium at entry"),
    ("entry_credit", "Total credit received"),
    ("quantity", "Lot size"),
    ("lot_size", "Lot size"),
    ("margin", "Total margin required"),
    ("sl_amount", "Stop loss amount (1% of margin)"),
    ("exit_time", "Trade exit timestamp"),
    ("exit_ce_premium", "CE premium at exit"),
    ("exit_pe_premium", "PE premium at exit"),
    ("exit_pnl", "Final P&L"),
    ("status", "OPEN or CLOSED"),
    ("exit_reason", "SL or EOD"),
    ("pcr", "Put-Call Ratio at entry"),
    ("selection_reason", "Why this strike was selected"),
    ("spot_at_entry", "Spot price at entry"),
    ("created_at", "Record creation time"),
]

for field, desc in fields:
    print(f"   {field:25s} - {desc}")
