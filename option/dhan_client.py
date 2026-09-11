"""Dhan API client for option chain data. Shares token with MCX engine."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import requests

BASE = "https://api.dhan.co/v2"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "dhan_token.json")
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")

_token_cache = None
_token_ts = 0.0
_token_lock = threading.Lock()
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})


def _load_token() -> str:
    global _token_cache, _token_ts
    now = time.monotonic()
    with _token_lock:
        if _token_cache and (now - _token_ts) < 30:
            return _token_cache
    try:
        with open(TOKEN_FILE) as f:
            token = json.load(f).get("access_token", "")
        with _token_lock:
            _token_cache = token
            _token_ts = now
        return token
    except Exception:
        with _token_lock:
            _token_cache = ""
            _token_ts = now
        return ""


def is_token_valid() -> tuple[bool, str]:
    """Check if Dhan token is loaded and not expired. Returns (valid, reason)."""
    token = _load_token()
    if not token:
        return False, "No token file or empty token"
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        exp = decoded.get("exp", 0)
        now = int(time.time())
        if exp < now:
            return False, f"Token expired {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}"
        remaining_h = (exp - now) / 3600
        return True, f"Valid, expires in {remaining_h:.1f}h"
    except Exception as e:
        return False, f"Cannot decode token: {e}"


def _headers() -> dict:
    t = _load_token()
    if t:
        return {"access-token": t, "Content-Type": "application/json", "client-id": CLIENT_ID}
    raise RuntimeError("No Dhan access token - MCX engine handles renewal")


def _post(path: str, payload: dict, label: str = "") -> dict:
    tag = f"[dhan:{label}]" if label else "[dhan]"
    for attempt in range(3):
        try:
            r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get("errorType") == "Authentication_Failed":
                    print(f"{tag} Auth failed: {j.get('errorMessage', '')}")
                    return {"error": "Authentication_Failed", "data": None}
                if j.get("status") == "error":
                    msg = j.get("message", "")
                    print(f"{tag} API error: {msg}")
                    return {"error": msg, "data": None}
                return j
            if r.status_code == 429:
                print(f"{tag} Rate limited, waiting 3s...")
                time.sleep(3.0)
                continue
            if r.status_code == 401:
                print(f"{tag} 401 Unauthorized — token may be expired")
                return {"error": "Unauthorized", "data": None}
            print(f"{tag} HTTP {r.status_code}: {r.text[:100]}")
        except requests.RequestException as e:
            if attempt == 2:
                print(f"{tag} Request failed: {e}")
                return {"error": str(e), "data": None}
            time.sleep(1.0)
    return {"error": "Dhan API failed after 3 attempts", "data": None}


def get_expiry_list(underlying_scrip: int) -> list[str]:
    j = _post("/optionchain/expirylist", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I"
    }, label="expiry")
    data = j.get("data") or []
    if not data:
        print(f"[dhan:expiry] No expiry found for scrip {underlying_scrip}")
    return data


def get_option_chain(underlying_scrip: int, expiry: str) -> dict | None:
    j = _post("/optionchain", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I",
        "Expiry": expiry,
        "ltpModified": True,
    }, label="chain")
    data = j.get("data")
    if not data:
        print(f"[dhan:chain] No chain data for scrip={underlying_scrip} expiry={expiry}")
        return None
    return data


def get_margin(security_id: str, quantity: int, exchange: str = "NSE_FNO") -> float:
    j = _post("/margincalculator", {
        "dhanClientId": CLIENT_ID,
        "securityId": str(security_id),
        "exchangeSegment": exchange,
        "transactionType": "SELL",
        "quantity": quantity,
        "productType": "MARGIN",
        "price": 0,
        "triggerPrice": 0
    }, label="margin")
    margin = j.get("totalMargin", 0)
    if margin == 0:
        print(f"[dhan:margin] Zero margin for sec={security_id} qty={quantity}")
    return margin
