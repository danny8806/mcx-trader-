import json
with open("config/settings.json") as f:
    s = json.load(f)
strats = s.get("strategies", {})
print("silver_01 fast_timeframe:", strats.get("silver_01", {}).get("fast_timeframe", "MISSING"))
print("gold_01 fast_timeframe:", strats.get("gold_01", {}).get("fast_timeframe", "MISSING"))
print("silver_01 instrument:", strats.get("silver_01", {}).get("instrument", "MISSING"))
print("gold_01 instrument:", strats.get("gold_01", {}).get("instrument", "MISSING"))
print("silver_01 mid_timeframe:", strats.get("silver_01", {}).get("mid_timeframe", "MISSING"))
print("silver_01 htf_timeframe:", strats.get("silver_01", {}).get("htf_timeframe", "MISSING"))
