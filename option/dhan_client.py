"""Dhan API client for option chain data. Auto-renews token via PIN+TOTP."""
from __future__ import annotations

import json
import os
import time
import threading

import requests

BASE = "https://api.dhan.co/v2"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "dhan_token.json")
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "1102461741")

_token_cache = None
_token_ts = 0.0
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})
_renew_lock = threading.Lock()
_last_renew = 0.0


def _load_token() -> str:
    global _token_cache, _token_ts
    now = time.monotonic()
    if _token_cache and (now - _token_ts) < 30:
        return _token_cache
    try:
        with open(TOKEN_FILE) as f:
            _token_cache = json.load(f).get("access_token", "")
            _token_ts = now
            return _token_cache
    except Exception:
        return ""


def _renew_token() -> str:
    """Auto-renew Dhan token using PIN + TOTP from environment."""
    global _token_cache, _token_ts, _last_renew

    pin = os.environ.get("DHAN_PIN", os.environ.get("TRADING_PIN", ""))
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", os.environ.get("TOTP_SECRET", ""))

    if not pin or not totp_secret:
        print("[Option-Auth] No PIN/TOTP configured, cannot auto-renew")
        return ""

    # Rate limit: max once per 120 seconds
    now = time.monotonic()
    if (now - _last_renew) < 120:
        return _token_cache or ""
    _last_renew = now

    with _renew_lock:
        try:
            import pyotp
            from dhanhq import DhanLogin

            # Wait for fresh TOTP window
            remaining = 30 - (int(time.time()) % 30)
            if remaining < 7:
                print(f"[Option-Auth] Waiting {remaining + 1}s for fresh TOTP window...")
                time.sleep(remaining + 1)

            totp = pyotp.TOTP(totp_secret).now()
            dhan_login = DhanLogin(CLIENT_ID)
            result = dhan_login.generate_token(pin, totp)
            new_tok = result.get("accessToken", "")

            if new_tok:
                # Save to file
                os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
                with open(TOKEN_FILE, "w") as f:
                    json.dump({"access_token": new_tok}, f, indent=2)
                _token_cache = new_tok
                _token_ts = time.monotonic()
                print(f"[Option-Auth] Token renewed, expires {result.get('expiryTime', '?')}")
                return new_tok
            else:
                print(f"[Option-Auth] Renew failed: {result}")
        except Exception as e:
            print(f"[Option-Auth] Renew error: {e}")
    return ""


def _headers() -> dict:
    t = _load_token()
    if t:
        return {"access-token": t, "Content-Type": "application/json"}
    # No cached token, try auto-renew
    t = _renew_token()
    if t:
        return {"access-token": t, "Content-Type": "application/json"}
    raise RuntimeError("No Dhan access token found and cannot auto-renew")


def _post(path: str, payload: dict) -> dict:
    for attempt in range(3):
        try:
            r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get("errorType") == "Authentication_Failed":
                    print(f"[Option-Auth] Auth failed on {path}, attempting token renewal...")
                    new_tok = _renew_token()
                    if new_tok:
                        r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
                        if r.status_code == 200:
                            return r.json()
                    return {"error": "Authentication_Failed", "data": None}
                return j
            if r.status_code == 429:
                time.sleep(2.0)
                continue
            if r.status_code == 401:
                print(f"[Option-Auth] 401 on {path}, attempting token renewal...")
                new_tok = _renew_token()
                if new_tok:
                    r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
                    if r.status_code == 200:
                        return r.json()
                return {"error": "Unauthorized", "data": None}
        except requests.RequestException as e:
            if attempt == 2:
                return {"error": str(e), "data": None}
            time.sleep(1.0)
    return {"error": "Dhan API failed", "data": None}


def get_expiry_list(underlying_scrip: int) -> list[str]:
    """Get list of expiry dates for an underlying."""
    j = _post("/optionchain/expirylist", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I"
    })
    return j.get("data", [])


def get_option_chain(underlying_scrip: int, expiry: str) -> dict | None:
    """Get full option chain for an underlying and expiry."""
    j = _post("/optionchain", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I",
        "Expiry": expiry
    })
    return j.get("data") if j.get("data") else None


def get_margin(security_id: str, quantity: int, exchange: str = "NSE_FNO") -> float:
    """Get margin requirement for selling an option."""
    j = _post("/margincalculator", {
        "dhanClientId": CLIENT_ID,
        "securityId": str(security_id),
        "exchangeSegment": exchange,
        "transactionType": "SELL",
        "quantity": quantity,
        "productType": "MARGIN",
        "price": 0,
        "triggerPrice": 0
    })
    return j.get("totalMargin", 0)


def get_spot_price(underlying_scrip: int, expiry: str) -> float:
    """Get spot/last price from option chain."""
    data = get_option_chain(underlying_scrip, expiry)
    if data:
        return data.get("last_price", 0)
    return 0
