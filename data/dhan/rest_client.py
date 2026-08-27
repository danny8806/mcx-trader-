"""Dhan REST client for historical data and reconciliation."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import requests


class TokenBucket:
    """Rate limiter using token bucket algorithm."""
    
    __slots__ = ("rate", "capacity", "tokens", "ts", "lock")

    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.ts = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """Wait until a token is available."""
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self.ts) * self.rate
                )
                self.ts = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                delay = (1.0 - self.tokens) / self.rate
            time.sleep(delay)


class DhanAuthError(Exception):
    """Authentication error from Dhan API."""
    pass


class DhanRateLimit(Exception):
    """Rate limit exceeded."""
    pass


class DhanRESTClient:
    """REST client for Dhan API.
    
    Handles:
    - Historical candle fetching
    - Intraday data
    - Rate limiting
    - Retry logic
    - Token management with auto-renewal via PIN+TOTP
    """

    def __init__(
        self,
        base_url: str = "https://api.dhan.co/v2",
        token_file: Optional[str] = None,
        client_id: str = "1102461741",
        pin: str = "",
        totp_secret: str = "",
        rate_per_sec: float = 3.5,
        burst: int = 3,
        max_retries: int = 3,
    ):
        self.base_url = base_url
        self.token_file = token_file
        self.client_id = client_id
        self.pin = pin
        self.totp_secret = totp_secret
        self.max_retries = max_retries
        self.limiter = TokenBucket(rate_per_sec, burst)
        
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        
        self._token_cache: Optional[str] = None
        self._token_ts: float = 0.0
        self._headers_cache: Optional[dict] = None
        self._headers_ts: float = 0.0
        
        self._stats = {"ok": 0, "empty": 0, "retry": 0}
        self._stats_lock = threading.Lock()

        # Proactive token renewal scheduler
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()
        self._renew_hour = 7  # 7 AM daily renewal
        self._renew_minute = 0
        self._safety_check_hours = 6  # Safety check every 6 hours
        self._last_safety_check: float = 0.0

    @property
    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def load_token(self) -> str:
        """Load access token from file with caching."""
        now = time.monotonic()
        if self._token_cache is not None and (now - self._token_ts) < 30:
            return self._token_cache
        
        if self.token_file and os.path.exists(self.token_file):
            try:
                with open(self.token_file) as f:
                    self._token_cache = json.load(f).get("access_token", "")
                    self._token_ts = now
                    return self._token_cache
            except Exception:
                pass
        return ""

    def token_expires_soon(self, grace_hours: float = 2.0) -> bool:
        """Check if token expires within grace period."""
        import base64
        t = self.load_token()
        if not t or "." not in t:
            return True
        try:
            p = t.split(".")[1]
            p += "=" * (-len(p) % 4)
            exp = json.loads(base64.urlsafe_b64decode(p)).get("exp", 0)
            return (exp - time.time()) <= grace_hours * 3600
        except Exception:
            return True

    def renew_token(self) -> str:
        """Auto-renew Dhan token using PIN + TOTP (no browser needed)."""
        if not self.pin or not self.totp_secret:
            print("[auth] no PIN/TOTP configured, cannot auto-renew", flush=True)
            return ""
        try:
            import pyotp
            from dhanhq import DhanLogin
            remaining = 30 - (int(time.time()) % 30)
            if remaining < 7:
                time.sleep(remaining + 1)
            totp = pyotp.TOTP(self.totp_secret).now()
            dhan_login = DhanLogin(self.client_id)
            result = dhan_login.generate_token(self.pin, totp)
            new_tok = result.get("accessToken", "")
            if new_tok:
                if self.token_file:
                    with open(self.token_file, "w") as f:
                        json.dump({"access_token": new_tok}, f, indent=2)
                self._token_cache = new_tok
                self._token_ts = time.monotonic()
                self._headers_cache = None
                print("[auth] token renewed via TOTP, expires %s" % result.get("expiryTime", "?"), flush=True)
                return new_tok
            print("[auth] renew failed: %s" % result, flush=True)
        except Exception as e:
            print("[auth] renew error: %s" % e, flush=True)
        return ""

    def ensure_token(self) -> str:
        """Get valid token, auto-renew if missing/expiring. Validates with API call."""
        t = self.load_token()
        if not t:
            print("[auth] no token found, auto-renewing...", flush=True)
            return self.renew_token()
        if self.token_expires_soon(grace_hours=1.0):
            print("[auth] token expiring soon, auto-renewing...", flush=True)
            return self.renew_token()
        # Validate existing token with a lightweight API call
        try:
            self._post("/charts/intraday", {
                "securityId": "563946",
                "exchangeSegment": "MCX_COMM",
                "instrument": "FUTCOM",
                "interval": "60",
                "oi": False,
                "fromDate": "2026-08-26 09:00:00",
                "toDate": "2026-08-27 18:00:00",
            })
            print("[auth] token validated OK", flush=True)
        except DhanAuthError:
            print("[auth] token validation failed, renewing...", flush=True)
            return self.renew_token()
        except Exception:
            pass
        return t

    # ── Proactive Token Renewal Scheduler ──────────────────────────────────

    def start_scheduler(self) -> None:
        """Start background scheduler for proactive token renewal.
        
        Schedule:
          - On startup: immediate ensure_token()
          - Every day at 7 AM: proactive renewal (before 24h expiry)
          - Every 6 hours: safety check (renew if expiring within 2h)
        """
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True, name="token-scheduler"
        )
        self._scheduler_thread.start()
        print("[auth] token scheduler started (daily 7 AM + 6h safety)", flush=True)

    def stop_scheduler(self) -> None:
        """Stop the token scheduler."""
        self._scheduler_stop.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)
        print("[auth] token scheduler stopped", flush=True)

    def _scheduler_loop(self) -> None:
        """Background loop: renew at 7 AM daily + safety check every 6 hours."""
        # Immediate renewal on startup
        print("[auth] scheduler: startup renewal...", flush=True)
        self.ensure_token()
        self._last_safety_check = time.monotonic()

        while not self._scheduler_stop.is_set():
            try:
                now = datetime.now()
                hour = now.hour
                minute = now.minute

                # Daily 7 AM renewal: renew at 7:00:30 (30s delay for TOTP window)
                if hour == self._renew_hour and minute == self._renew_minute:
                    print("[auth] scheduler: 7 AM daily renewal...", flush=True)
                    self.renew_token()
                    # Sleep until 7:01 to avoid double-fire
                    time.sleep(60)
                    self._last_safety_check = time.monotonic()
                    continue

                # Safety check every 6 hours
                elapsed = time.monotonic() - self._last_safety_check
                if elapsed >= self._safety_check_hours * 3600:
                    self._last_safety_check = time.monotonic()
                    if self.token_expires_soon(grace_hours=2.0):
                        print("[auth] scheduler: safety renewal (token expiring)...", flush=True)
                        self.renew_token()
                    else:
                        token = self.load_token()
                        if token:
                            print("[auth] scheduler: safety check OK (token valid)", flush=True)
                        else:
                            print("[auth] scheduler: no token found, renewing...", flush=True)
                            self.renew_token()

                # Sleep 30 seconds between checks
                self._scheduler_stop.wait(30)

            except Exception as e:
                print(f"[auth] scheduler error: {e}", flush=True)
                self._scheduler_stop.wait(60)

    @property
    def scheduler_running(self) -> bool:
        return self._scheduler_thread is not None and self._scheduler_thread.is_alive()

    # ── End Scheduler ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Get request headers with cached token. Auto-renews on expiry."""
        now = time.monotonic()
        if self._headers_cache is not None and (now - self._headers_ts) < 30:
            return self._headers_cache
        
        t = self.load_token()
        if not t or self.token_expires_soon(grace_hours=1.0):
            t = self.renew_token()
        if not t:
            raise DhanAuthError("no access token (auto-renew failed)")
        
        self._headers_cache = {
            "access-token": t,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._headers_ts = now
        return self._headers_cache

    def _post(self, path: str, payload: dict) -> dict:
        """Make rate-limited POST request with retry and auto-renew on auth failure."""
        self.limiter.acquire()
        last = None
        auth_retried = False
        
        for attempt in range(self.max_retries):
            try:
                r = self._session.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=self._headers(),
                    timeout=30,
                )
            except requests.RequestException as e:
                last = e
                time.sleep(1.0)
                continue
            
            if r.status_code == 200:
                j = r.json()
                if j.get("errorType") == "Authentication_Failed":
                    if not auth_retried:
                        auth_retried = True
                        print("[auth] Authentication_Failed, renewing token...", flush=True)
                        self._headers_cache = None
                        self.renew_token()
                        continue
                    raise DhanAuthError(f"{path}: {j}")
                with self._stats_lock:
                    self._stats["ok"] += 1
                return j
            
            if r.status_code == 429:
                with self._stats_lock:
                    self._stats["retry"] += 1
                time.sleep(2.0)
                continue
            
            # Auto-renew on any auth-related error (401, 400 DH-906, etc.)
            is_auth_error = (
                r.status_code == 401
                or (r.status_code == 400 and "DH-906" in r.text)
                or (r.status_code == 400 and "Invalid Token" in r.text)
            )
            if is_auth_error:
                if not auth_retried:
                    auth_retried = True
                    print(f"[auth] {r.status_code} auth error, renewing token...", flush=True)
                    self._headers_cache = None
                    self.renew_token()
                    continue
                raise DhanAuthError(f"{path}: {r.text[:200]}")
            
            last = RuntimeError(f"{r.status_code} {r.text[:200]}")
            time.sleep(1.0)
        
        raise last or RuntimeError(f"{path} failed")

    def _to_candles(self, j: dict) -> list[list]:
        """Convert API response to candle list format."""
        ts = j.get("timestamp") or []
        if not ts:
            with self._stats_lock:
                self._stats["empty"] += 1
            return []
        return [
            [t, o, h, l, c, v]
            for t, o, h, l, c, v in zip(
                j["timestamp"],
                j["open"],
                j["high"],
                j["low"],
                j["close"],
                j.get("volume") or [0] * len(ts),
            )
        ]

    def fetch_daily(
        self,
        security_id: str,
        from_date: datetime.date,
        to_date: datetime.date,
        exchange_segment: str = "MCX_COMM",
        instrument: str = "FUTCOM",
    ) -> list[list]:
        """Fetch daily OHLC candles."""
        j = self._post("/charts/historical", {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "expiryCode": 0,
            "oi": False,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        })
        return self._to_candles(j)

    def fetch_intraday(
        self,
        security_id: str,
        interval: str,
        from_dt: datetime.datetime,
        to_dt: datetime.datetime,
        exchange_segment: str = "MCX_COMM",
        instrument: str = "FUTCOM",
    ) -> list[list]:
        """Fetch intraday candles (max 90 days per call)."""
        j = self._post("/charts/intraday", {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": interval,
            "oi": False,
            "fromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return self._to_candles(j)

    def backfill_intraday(
        self,
        security_id: str,
        interval: str,
        exchange_segment: str = "MCX_COMM",
        instrument: str = "FUTCOM",
    ) -> list[list]:
        """Backfill intraday data from contract listing to today."""
        today = datetime.datetime.now()
        total_candles = []
        
        probe = today - timedelta(days=90)
        first = None
        
        # Find first available candle
        for _ in range(60):  # up to ~15 years
            candles = self.fetch_intraday(
                security_id, interval, probe,
                probe + timedelta(days=90),
                exchange_segment, instrument,
            )
            if candles:
                first = candles[0][0]
                break
            if (today - probe).days > 365 * 5:
                break
            probe -= timedelta(days=90)
        
        if first is None:
            return []
        
        # Sweep forward from first known candle
        cursor = datetime.datetime.fromtimestamp(first)
        while cursor < today:
            to_dt = min(cursor + timedelta(days=90), today)
            candles = self.fetch_intraday(
                security_id, interval, cursor, to_dt,
                exchange_segment, instrument,
            )
            if candles:
                total_candles.extend(candles)
            cursor = to_dt
        
        return total_candles

    def calculate_margin(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        product_type: str,
        price: float,
        security_type: str = "FUTCOM",
    ) -> dict:
        """Calculate actual margin required via Dhan margin calculator API.
        
        Returns dict with totalMargin, spanMargin, exposureMargin, brokerage, leverage.
        Returns empty dict on failure (caller should fallback to estimated margin).
        """
        payload = {
            "dhanClientId": self.client_id,
            "exchangeSegment": exchange_segment,
            "transactionType": transaction_type,
            "quantity": quantity,
            "productType": product_type,
            "securityId": str(security_id),
            "price": float(price),
        }
        try:
            result = self._post("/margincalculator", payload)
            if "totalMargin" in result:
                return {
                    "totalMargin": float(result.get("totalMargin", 0)),
                    "spanMargin": float(result.get("spanMargin", 0)),
                    "exposureMargin": float(result.get("exposureMargin", 0)),
                    "brokerage": float(result.get("brokerage", 0)),
                    "leverage": result.get("leverage", "1"),
                }
            return {}
        except Exception as e:
            print(f"[Margin] API call failed: {e}", flush=True)
            return {}
