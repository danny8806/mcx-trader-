"""Dhan API client for option chain data. Reused from source, simplified."""
from __future__ import annotations

import json
import os
import time
import threading

import requests

BASE = "https://api.dhan.co/v2"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "dhan_token.json")
CLIENT_ID = "1102461741"

_token_cache = None
_token_ts = 0.0
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})


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


def _headers() -> dict:
    t = _load_token()
    if not t:
        raise RuntimeError("No Dhan access token found")
    return {"access-token": t, "Content-Type": "application/json"}


def _post(path: str, payload: dict) -> dict:
    for attempt in range(3):
        try:
            r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get("errorType") == "Authentication_Failed":
                    raise RuntimeError(f"Auth failed: {path}")
                return j
            if r.status_code == 429:
                time.sleep(2.0)
                continue
            if r.status_code == 401:
                raise RuntimeError(f"Unauthorized: {path}")
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(1.0)
    raise RuntimeError(f"Dhan API failed: {path}")


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
