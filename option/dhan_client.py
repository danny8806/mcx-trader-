"""Dhan API client for option chain data. Shares token with MCX engine."""
from __future__ import annotations

import json
import os
import time
import requests

BASE = "https://api.dhan.co/v2"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "dhan_token.json")
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "1102461741")

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
    if t:
        return {"access-token": t, "Content-Type": "application/json", "client-id": CLIENT_ID}
    raise RuntimeError("No Dhan access token - MCX engine handles renewal")


def _post(path: str, payload: dict) -> dict:
    for attempt in range(3):
        try:
            r = _session.post(BASE + path, json=payload, headers=_headers(), timeout=15)
            if r.status_code == 200:
                j = r.json()
                if j.get("errorType") == "Authentication_Failed":
                    return {"error": "Authentication_Failed", "data": None}
                return j
            if r.status_code == 429:
                time.sleep(3.0)
                continue
            if r.status_code == 401:
                return {"error": "Unauthorized", "data": None}
        except requests.RequestException as e:
            if attempt == 2:
                return {"error": str(e), "data": None}
            time.sleep(1.0)
    return {"error": "Dhan API failed", "data": None}


def get_expiry_list(underlying_scrip: int) -> list[str]:
    j = _post("/optionchain/expirylist", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I"
    })
    return j.get("data") or []


def get_option_chain(underlying_scrip: int, expiry: str) -> dict | None:
    j = _post("/optionchain", {
        "UnderlyingScrip": underlying_scrip,
        "UnderlyingSeg": "IDX_I",
        "Expiry": expiry
    })
    return j.get("data") if j.get("data") else None


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
    })
    return j.get("totalMargin", 0)


def get_spot_price(underlying_scrip: int, expiry: str) -> float:
    data = get_option_chain(underlying_scrip, expiry)
    if data:
        return data.get("last_price", 0)
    return 0
