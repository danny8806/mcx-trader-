"""Complete system audit of option selling."""
import os, sys, sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 80)
print("OPTION SELLING — COMPLETE SYSTEM AUDIT")
print("=" * 80)

# 1. File inventory
print("\n1. OPTION FILES:")
print("-" * 80)
for root, dirs, files in os.walk("option"):
    for f in sorted(files):
        if f.endswith(".py"):
            path = os.path.join(root, f)
            lines = sum(1 for _ in open(path))
            print(f"  {path:45s} {lines:5d} lines")

print("\n2. API ROUTES:")
print("-" * 80)
lines = sum(1 for _ in open("dashboard/routes/option.py"))
print(f"  {'dashboard/routes/option.py':45s} {lines:5d} lines")

print("\n3. DASHBOARD UI:")
print("-" * 80)
path = "dashboard-ui/src/pages/OptionSelling.tsx"
if os.path.exists(path):
    lines = sum(1 for _ in open(path))
    print(f"  {path:45s} {lines:5d} lines")

print("\n4. TESTS:")
print("-" * 80)
for f in sorted(os.listdir("tests")):
    if f.startswith("test_option") or f.startswith("test_full") or f.startswith("test_exit"):
        path = os.path.join("tests", f)
        lines = sum(1 for _ in open(path))
        print(f"  {path:45s} {lines:5d} lines")

print("\n5. DATABASE:")
print("-" * 80)
db_path = "data/db/option_paper_trading.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:35s} {count:5d} rows")
    conn.close()
else:
    print("  Database not yet created")

print("\n6. IMPORTS:")
print("-" * 80)
try:
    from option.config import SL_PERCENT, OI_THRESHOLD, MAX_TRADES_PER_DAY
    from option.dhan_client import get_expiry_list, get_option_chain, get_margin
    from option.strategy import parse_option_chain
    from option.trader import is_expiry_day, run_morning_check, check_and_exit
    from option.database import init_db, save_trade, close_trade, get_open_trades
    from option.scheduler import start_scheduler
    print("  All imports OK")
except Exception as e:
    print(f"  Import error: {e}")

print("\n7. CONFIG VALUES:")
print("-" * 80)
from option.config import SL_PERCENT, OI_THRESHOLD, MAX_TRADES_PER_DAY, INSTRUMENTS
print(f"  SL_PERCENT:         {SL_PERCENT}")
print(f"  OI_THRESHOLD:       {OI_THRESHOLD:,}")
print(f"  MAX_TRADES_PER_DAY: {MAX_TRADES_PER_DAY}")
print(f"  INSTRUMENTS:")
for name, cfg in INSTRUMENTS.items():
    print(f"    {name}: scrip={cfg['scrip']}, lot={cfg['lot_size']}, exchange={cfg['exchange']}")

print("\n8. SCHEDULE:")
print("-" * 80)
from option.config import ENTRY_TIME, RECHECK_TIME, EOD_EXIT_TIME, EXPIRY_DAYS
print(f"  ENTRY_TIME:    {ENTRY_TIME}")
print(f"  RECHECK_TIME:  {RECHECK_TIME}")
print(f"  EOD_EXIT_TIME: {EOD_EXIT_TIME}")
print(f"  EXPIRY_DAYS:   {EXPIRY_DAYS} (Wed=2, Thu=3)")

print("\n9. API ENDPOINTS:")
print("-" * 80)
from dashboard.routes.option import router
for route in router.routes:
    methods = getattr(route, "methods", set())
    path = getattr(route, "path", "?")
    print(f"  {list(methods)[0]:6s} {path}")

print("\n10. DHAN CLIENT:")
print("-" * 80)
from option.dhan_client import BASE, TOKEN_FILE, CLIENT_ID
print(f"  BASE:        {BASE}")
print(f"  TOKEN_FILE:  {TOKEN_FILE}")
print(f"  CLIENT_ID:   {CLIENT_ID}")

print("\n" + "=" * 80)
print("AUDIT COMPLETE")
print("=" * 80)
