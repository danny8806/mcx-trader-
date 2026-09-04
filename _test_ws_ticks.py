"""Standalone WebSocket test - auto-renews token then tests tick delivery.

Run:  python _test_ws_ticks.py

This connects DIRECTLY to Dhan (no trading system code) and subscribes
to GOLDM (569003) and SILVERM (483080) for 60 seconds to verify which
instrument is receiving ticks.
"""
from __future__ import annotations

import json
import struct
import time
import threading
import sys
import os

try:
    import websocket
except ImportError:
    sys.exit("pip install websocket-client")

# ── Load credentials from env file ──────────────────────────────────
_env = {}
_env_file = os.path.join(os.path.dirname(__file__), "mcx-trader.env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _env[k.strip()] = v.strip()

CLIENT_ID  = os.environ.get("DHAN_CLIENT_ID") or _env.get("DHAN_CLIENT_ID", "")
PIN        = os.environ.get("TRADING_PIN") or _env.get("TRADING_PIN", "")
TOTP_SEC   = os.environ.get("TOTP_SECRET") or _env.get("TOTP_SECRET", "")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "db", "dhan_token.json")

WS_URL = "wss://api-feed.dhan.co"

# ── Try to get a fresh token ─────────────────────────────────────────
def get_fresh_token() -> str:
    """Try PIN+TOTP renewal, fall back to token file, then env."""
    # 1. Try PIN+TOTP renewal
    if PIN and TOTP_SEC:
        try:
            import pyotp
            from dhanhq import DhanLogin
            remaining = 30 - (int(time.time()) % 30)
            if remaining < 7:
                print(f"[AUTH] Waiting {remaining+1}s for fresh TOTP window...")
                time.sleep(remaining + 1)
            totp = pyotp.TOTP(TOTP_SEC).now()
            login = DhanLogin(CLIENT_ID)
            result = login.generate_token(PIN, totp)
            tok = result.get("accessToken", "")
            if tok:
                print(f"[AUTH] Fresh token via PIN+TOTP, expires {result.get('expiryTime','?')}")
                # Save it
                os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
                with open(TOKEN_FILE, "w") as f:
                    json.dump({"access_token": tok}, f)
                return tok
            print(f"[AUTH] PIN+TOTP failed: {result}")
        except Exception as e:
            print(f"[AUTH] PIN+TOTP error: {e}")

    # 2. Try token file
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                tok = json.load(f).get("access_token", "")
            if tok:
                print(f"[AUTH] Using token from {TOKEN_FILE}")
                return tok
        except Exception:
            pass

    # 3. From env
    tok = os.environ.get("DHAN_ACCESS_TOKEN") or _env.get("DHAN_ACCESS_TOKEN", "")
    if tok:
        print("[AUTH] Using token from env")
    return tok


TOKEN = get_fresh_token()

# Validate token
import base64
if TOKEN and "." in TOKEN:
    try:
        payload = TOKEN.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp", 0)
        now = time.time()
        remaining_h = (exp - now) / 3600
        if remaining_h <= 0:
            print(f"[TOKEN] *** EXPIRED {abs(remaining_h):.1f}h ago ***")
            print("[TOKEN] Need valid token! Run main system first or set PIN/TOTP in env.")
        else:
            print(f"[TOKEN] Valid for {remaining_h:.1f} more hours")
    except Exception:
        print("[TOKEN] Could not decode, proceeding anyway...")

if not CLIENT_ID or not TOKEN:
    sys.exit("No credentials! Set DHAN_CLIENT_ID in mcx-trader.env")

# ── Instruments ───────────────────────────────────────────────────────
INSTRUMENTS = [
    {"ExchangeSegment": "MCX_COMM", "SecurityId": "569003", "Name": "GOLDM"},
    {"ExchangeSegment": "MCX_COMM", "SecurityId": "483080", "Name": "SILVERM"},
]

# ── State ─────────────────────────────────────────────────────────────
stats = {"recv": 0, "code2": 0, "code4": 0, "unknown": 0,
         "per_sid": {}, "per_ltp": {}, "per_ltt": {}, "dedup": 0,
         "parse_err": 0}
seen: set[str] = set()
conn_log: list[str] = []


def parse_packet(data: bytes) -> dict | None:
    if len(data) < 4:
        return None
    code = data[0]
    if code == 4:
        sid  = struct.unpack_from("<i", data, 4)[0]
        ltp  = struct.unpack_from("<f", data, 8)[0]
        ltq  = struct.unpack_from("<h", data, 12)[0]
        ltt  = struct.unpack_from("<i", data, 14)[0]
        cumv = struct.unpack_from("<i", data, 22)[0]
        return {"code": 4, "sid": sid, "ltp": ltp, "ltq": ltq, "ltt": ltt, "cumvol": cumv}
    elif code == 2:
        sid = struct.unpack_from("<i", data, 4)[0]
        ltp = struct.unpack_from("<f", data, 8)[0]
        ltt = struct.unpack_from("<i", data, 12)[0]
        return {"code": 2, "sid": sid, "ltp": ltp, "ltq": 0, "ltt": ltt, "cumvol": None}
    return None


def on_message(ws, message):
    now = time.strftime("%H:%M:%S")
    if isinstance(message, str):
        conn_log.append(f"[{now}] TEXT: {message[:200]}")
        return
    stats["recv"] += 1
    try:
        tick = parse_packet(message)
        if tick is None:
            stats["unknown"] += 1
            if stats["unknown"] <= 5:
                print(f"[{now}] UNKNOWN_PACKET len={len(message)} hex={message[:20].hex()}", flush=True)
            return
        sid = str(tick["sid"])
        ltp = tick["ltp"]
        ltt = tick["ltt"]
        stats["code2" if tick["code"] == 2 else "code4"] += 1
        name = next((i["Name"] for i in INSTRUMENTS if i["SecurityId"] == sid), f"UNK({sid})")
        dedup_key = f"{sid}|{ltt}|{ltp:.2f}"
        if ltt and dedup_key in seen:
            stats["dedup"] += 1
            return
        if ltt:
            seen.add(dedup_key)
        stats["per_sid"][sid] = stats["per_sid"].get(sid, 0) + 1
        stats["per_ltp"][sid] = ltp
        stats["per_ltt"][sid] = ltt
        cnt = stats["per_sid"][sid]
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        ts = datetime.fromtimestamp(ltt, tz=ist).strftime("%H:%M:%S") if ltt > 0 else "?"
        print(f"[{now}] TICK {name:8s} sid={sid} code={tick['code']} ltp={ltp:>12.2f} ltt={ts} count={cnt}", flush=True)
    except Exception as e:
        stats["parse_err"] += 1
        print(f"[{now}] PARSE_ERR len={len(message)} err={e}", flush=True)


def on_open(ws):
    now = time.strftime("%H:%M:%S")
    sub_msg = {"RequestCode": 17, "InstrumentCount": len(INSTRUMENTS), "InstrumentList": INSTRUMENTS}
    ws.send(json.dumps(sub_msg))
    names = [f"{i['Name']}({i['SecurityId']})" for i in INSTRUMENTS]
    msg = f"[{now}] CONNECTED + subscribed: {', '.join(names)}"
    print(f"\n{msg}", flush=True)
    conn_log.append(msg)


def on_error(ws, error):
    now = time.strftime("%H:%M:%S")
    msg = f"[{now}] WS_ERROR: {error}"
    print(f"\n{msg}\n", flush=True)
    conn_log.append(msg)


def on_close(ws, code, msg):
    now = time.strftime("%H:%M:%S")
    msg_s = f"[{now}] CLOSED code={code} msg={msg}"
    print(f"\n{msg_s}\n", flush=True)
    conn_log.append(msg_s)


# ── Main ─────────────────────────────────────────────────────────────
url = f"{WS_URL}?version=2&token={TOKEN}&clientId={CLIENT_ID}&authType=2"
print(f"[START] Client={CLIENT_ID}  WS={WS_URL}")

ws = websocket.WebSocketApp(
    url,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)

t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 10, "ping_timeout": 5}, daemon=True)
t.start()
t.join(timeout=65)
ws.close()

# ── Report ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  TICK DELIVERY TEST RESULTS")
print("=" * 60)
gold = stats["per_sid"].get("569003", 0)
silv = stats["per_sid"].get("483080", 0)
print(f"  Total received  : {stats['recv']}")
print(f"  Code-4 (quote)  : {stats['code4']}")
print(f"  Code-2 (price)  : {stats['code2']}")
print(f"  Unknown packets : {stats['unknown']}")
print(f"  Parse errors    : {stats['parse_err']}")
print(f"  Dedup drops     : {stats['dedup']}")
print(f"")
print(f"  GOLDM  (569003) : {gold:>5d} ticks  LTP={stats['per_ltp'].get('569003',0):.2f}")
print(f"  SILVERM (483080) : {silv:>5d} ticks  LTP={stats['per_ltp'].get('483080',0):.2f}")
print(f"")
if gold == 0 and silv == 0:
    print("  RESULT: NO ticks for ANY instrument")
    if conn_log:
        for m in conn_log[-5:]:
            print(f"    {m}")
    print("  -> Check: token validity, market hours (9AM-11:30PM IST),")
    print("     network/firewall, Dhan account status")
elif gold == 0 and silv > 0:
    print("  RESULT: GOLDM=0 ticks while SILVERM has ticks")
    print("  -> Dhan is NOT sending GOLDM data for security_id 569003")
    print("  -> Verify correct security_id on Dhan platform")
    print("  -> GOLDM202610 may be expiring soon; try next month contract")
elif gold < silv * 0.1:
    print(f"  RESULT: GOLDM({gold}) << SILVERM({silv}) - much lower activity")
    print("  -> Dhan IS sending gold ticks, just fewer (low liquidity)")
else:
    print(f"  RESULT: Both receiving ticks ({gold}:{silv}) - feed is OK")
    print("  -> The issue is in the main system's processing")
print("=" * 60)
