import urllib.request
import json

BASE = "http://127.0.0.1:8791"

def get(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

print("=== API ENDPOINT TEST ===")
print()

# Health
print("1. /api/health")
r = get("/api/health")
print(f"   Status: {r.get('status', 'unknown')}")
print()

# Trades
print("2. /api/trades")
r = get("/api/trades")
print(f"   Source: {r.get('source', 'unknown')}")
print(f"   Count: {r.get('count', 0)}")
if r.get("trades"):
    t = r["trades"][0]
    print(f"   First trade: {t.get('trade_id', 'N/A')} {t.get('side', 'N/A')} {t.get('instrument', 'N/A')} status={t.get('status', 'N/A')}")
    print(f"   Entry: {t.get('entry_price', 'N/A')}, Exit: {t.get('exit_price', 'N/A')}, PnL: {t.get('net_pnl', 'N/A')}")
print()

# Positions
print("3. /api/positions")
r = get("/api/positions")
print(f"   Count: {r.get('count', 0)}")
if r.get("positions"):
    p = r["positions"][0]
    print(f"   First position: {p.get('position_id', 'N/A')} {p.get('side', 'N/A')} {p.get('instrument', 'N/A')} status={p.get('status', 'N/A')}")
    print(f"   Entry: {p.get('average_entry_price', 'N/A')}, Qty: {p.get('quantity', 'N/A')}")
print()

# Orders
print("4. /api/orders")
r = get("/api/orders")
print(f"   Count: {r.get('count', 0)}")
if r.get("orders"):
    o = r["orders"][0]
    print(f"   First order: {o.get('order_id', 'N/A')} {o.get('side', 'N/A')} {o.get('instrument', 'N/A')} state={o.get('state', 'N/A')}")
print()

# Fills
print("5. /api/fills")
r = get("/api/fills")
print(f"   Count: {r.get('count', 0)}")
if r.get("fills"):
    f = r["fills"][0]
    print(f"   First fill: {f.get('fill_id', 'N/A')} {f.get('side', 'N/A')} {f.get('instrument', 'N/A')} price={f.get('price', 'N/A')}")
print()

# Analytics
print("6. /api/analytics/strategies")
r = get("/api/analytics/strategies")
if isinstance(r, list) and len(r) > 0:
    print(f"   Count: {len(r)}")
    s = r[0]
    print(f"   First strategy: {s.get('strategy_id', 'N/A')} trade_count={s.get('trade_count', 'N/A')} net_pnl={s.get('net_pnl', 'N/A')}")
else:
    print(f"   Response: {r}")
print()

print("=== API TEST COMPLETE ===")
