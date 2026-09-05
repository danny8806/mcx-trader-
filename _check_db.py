import sqlite3
import json

db = sqlite3.connect(r"C:\Users\pc\Desktop\MCX-TRADER\trading.db")
db.row_factory = sqlite3.Row

print("=== DATABASE TABLES ===")
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for t in tables:
    print(f"  {t[0]}")

print()
print("=== DATA COUNTS ===")
for t in tables:
    try:
        count = db.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"  {t[0]}: {count} rows")
    except:
        print(f"  {t[0]}: error")

print()
print("=== TRADES ===")
trades = db.execute("SELECT trade_id, strategy_id, instrument, side, status, entry_price, exit_price, net_pnl FROM trades LIMIT 5").fetchall()
for t in trades:
    print(f"  {dict(t)}")

print()
print("=== FILLS ===")
fills = db.execute("SELECT fill_id, trade_id, order_id, instrument, side, quantity, price FROM fills LIMIT 5").fetchall()
for f in fills:
    print(f"  {dict(f)}")

print()
print("=== POSITIONS ===")
positions = db.execute("SELECT position_id, trade_id, instrument, side, quantity, average_entry, status FROM positions LIMIT 5").fetchall()
for p in positions:
    print(f"  {dict(p)}")

print()
print("=== ORDERS ===")
orders = db.execute("SELECT order_id, trade_id, instrument, side, quantity, state FROM orders LIMIT 5").fetchall()
for o in orders:
    print(f"  {dict(o)}")

print()
print("=== SIGNALS ===")
signals = db.execute("SELECT signal_id, strategy_id, instrument, signal_type FROM signals LIMIT 5").fetchall()
for s in signals:
    print(f"  {dict(s)}")

db.close()
