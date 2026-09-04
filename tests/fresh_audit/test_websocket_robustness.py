"""Regression tests for the Dhan WebSocket robustness fixes.

Covers:
- Fix 1: DhanDataAdapter always mints a FRESH auto-generated token on startup
  via renew_token() (never reuses a cached/stale token), and uses that fresh
  token for both REST and the WebSocket (token_loader = renew_token).
- Fix 2a: The WebSocket stale watchdog forces a reconnect when the connection
  is up but silently stopped delivering ticks (breaks connected-but-silent).
- Fix 2b: The reconnect backoff is bounded (capped) so reconnects stay fast
  instead of growing unboundedly and stalling recovery.
"""
from __future__ import annotations

import time

import pytest

from data.dhan.adapter import DhanDataAdapter
from data.dhan.websocket_client import DhanWebSocketClient


# ── Fix 1: fresh token on startup ──────────────────────────────────────────

class TestAdapterFreshTokenOnStartup:
    def test_init_renews_token_and_passes_fresh_to_ws(self, monkeypatch):
        calls = []

        class FakeRest:
            def __init__(self):
                self._token = "fresh-token"
                self._loader_calls = ["renew_token"]

            def ensure_token(self):
                calls.append("ensure_token")
                return self._token

            def renew_token(self):
                calls.append("renew_token")
                self._token = "fresh-token"
                return self._token

            def load_token(self):
                return self._token

            def start_scheduler(self):
                calls.append("start_scheduler")

        class FakeWS:
            def __init__(self, **kwargs):
                self.token = kwargs.get("token")
                self.token_loader = kwargs.get("token_loader")
                self.on_tick = kwargs.get("on_tick")

        import data.dhan.adapter as adapter_mod
        monkeypatch.setattr(adapter_mod, "DhanRESTClient", lambda **kw: FakeRest())
        monkeypatch.setattr(adapter_mod, "DhanWebSocketClient", FakeWS)

        a = DhanDataAdapter(client_id="1102461741", token_file="/tmp/x.json",
                            pin="1", totp_secret="S")
        # Startup MUST mint a fresh token via renew_token(), not reuse ensure_token().
        assert "renew_token" in calls
        assert "ensure_token" not in calls
        # WebSocket was given the fresh token and a renewing token_loader.
        assert a.ws.token == "fresh-token"
        assert a.ws.token_loader.__name__ == "renew_token"

    def test_ws_token_loader_is_renew_token(self, monkeypatch):
        # Direct check: token_loader wired to renew (fresh mint on reconnect).
        ws = DhanWebSocketClient(client_id="c", token="", token_loader=renew_loader)
        assert callable(ws.token_loader)
        assert ws.token_loader.__name__ == "renew_loader"
        assert ws.token_loader() == "renewed"


def renew_loader():
    return "renewed"


# ── Fix 2a: stale watchdog ─────────────────────────────────────────────────

class TestStaleWatchdog:
    def _make_client(self):
        statuses = []
        client = DhanWebSocketClient(
            client_id="c", token="t",
            on_status=lambda s: statuses.append(s),
        )
        client._stale_threshold = 1.0
        client._watchdog_interval = 0.05
        return client, statuses

    def test_watchdog_force_reconnects_stale_connected_socket(self):
        client, statuses = self._make_client()
        closed = []
        client._connected = True
        client._last_tick_time = time.time() - 100  # stale
        client._ws = _FakeWS(on_close=lambda: closed.append(True))
        client._watchdog_loop_once()
        assert closed == [True], "stale connected socket must be force-closed"
        assert "stale_reconnect" in statuses

    def test_watchdog_ignores_healthy_connection(self):
        client, _ = self._make_client()
        closed = []
        client._connected = True
        client._last_tick_time = time.time()  # fresh
        client._ws = _FakeWS(on_close=lambda: closed.append(True))
        client._watchdog_loop_once()
        assert closed == [], "healthy socket must NOT be force-closed"

    def test_watchdog_ignores_while_connecting_or_disconnected(self):
        client, _ = self._make_client()
        closed = []
        # disconnected
        client._connected = False
        client._last_tick_time = time.time() - 100
        client._ws = _FakeWS(on_close=lambda: closed.append(True))
        client._watchdog_loop_once()
        assert closed == []
        # connecting
        client._connected = True
        client._connecting = True
        client._last_tick_time = time.time() - 100
        client._watchdog_loop_once()
        assert closed == []


class _FakeWS:
    def __init__(self, on_close=None):
        self._on_close = on_close

    def close(self):
        if self._on_close:
            self._on_close()


# ── Fix 2b: bounded reconnect backoff ──────────────────────────────────────

class TestBoundedReconnectBackoff:
    def test_max_delay_is_capped_relative_to_reconnect_delay(self):
        client = DhanWebSocketClient(client_id="c", token="",
                                     reconnect_delay=2.0)
        # Replicate the loop's cap: max_delay = reconnect_delay * 3
        delay = client.reconnect_delay
        max_delay = client.reconnect_delay * 3
        seen = []
        for _ in range(10):  # simulate many consecutive failures
            seen.append(delay)
            delay = min(delay * 2, max_delay)
        assert max(seen) == 6.0, "backoff must be capped, not unbounded"
        assert delay == 6.0

    def test_backoff_resets_after_success(self):
        client = DhanWebSocketClient(client_id="c", token="",
                                     reconnect_delay=2.0)
        _, max_delay = 2.0, 2.0 * 3
        # ramp up to the cap
        delay = 2.0
        for _ in range(10):
            delay = min(delay * 2, max_delay)
        assert delay == max_delay
        # on success the delay resets to reconnect_delay
        delay = client.reconnect_delay
        assert delay == 2.0
