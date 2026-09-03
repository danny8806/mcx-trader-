import urllib.request, json
BASE="http://200.234.44.93:8000"
def get(p):
    try:
        with urllib.request.urlopen(BASE+p, timeout=25) as r: return json.loads(r.read() or b"{}")
    except Exception as e: return {"err":str(e)}
print("== positions (side/entry/stop/is_open) ==")
for p in get("/api/positions").get("positions",[]):
    print(f"  {p["strategy_id"]:10} {p["side"]:5} entry={p["average_entry"]} stop={p.get("stop_price")} open={p["is_open"]}")
print("== strategies (state/position_side/stop_price) ==")
for s in get("/api/strategies").get("strategies",[]):
    print(f"  {s["strategy_id"]:10} {s["state"]:15} {s["position_side"]:5} stop={s.get("stop_price")}")
