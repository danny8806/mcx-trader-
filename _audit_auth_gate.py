"""PASS 1 — optional DASHBOARD_API_KEY gate smoke test (off by default).

Boots the real dashboard server with DASHBOARD_API_KEY set and verifies:
  - /api/health stays open (exempt)
  - /api/* rejects without key / with wrong key (403, constant-time compare)
  - /api/* accepts with correct x-api-key
  - /ws rejects (close 1008) without key, accepts and pushes with ?key=
"""
import os
os.environ["DASHBOARD_API_KEY"] = "audit-test-key-123"

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from _fullstack_check import boot_server, BASE, PORT  # noqa: E402
from _frontend_deep_check import seed_deep  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def main():
    engine, persistence, root = seed_deep()
    server = boot_server(engine, persistence)
    time.sleep(1.0)

    try:
        K = "audit-test-key-123"
        r = httpx.get(f"{BASE}/api/health", timeout=5)
        check("health.allowed_without_key", r.status_code == 200, str(r.status_code))

        r = httpx.get(f"{BASE}/api/overview", timeout=5)
        check("overview.blocked_without_key", r.status_code == 403, str(r.status_code))

        r = httpx.get(f"{BASE}/api/overview", headers={"x-api-key": "wrong"}, timeout=5)
        check("overview.blocked_wrong_key", r.status_code == 403, str(r.status_code))

        r = httpx.get(f"{BASE}/api/overview", headers={"x-api-key": K}, timeout=5)
        check("overview.allowed_with_key", r.status_code == 200, str(r.status_code))

        r = httpx.get(f"{BASE}/api/analytics/events", headers={"x-api-key": K}, timeout=5)
        check("analytics.allowed_with_key", r.status_code == 200, str(r.status_code))

        async def ws_no_key():
            import websockets
            try:
                async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
                    await ws.send(json.dumps({"action": "subscribe", "channels": ["all"]}))
                    await asyncio.wait_for(ws.recv(), timeout=2)
                    return "OPEN"
            except Exception as e:
                return "CLOSED"

        no_key = asyncio.run(ws_no_key())
        check("ws.blocked_without_key", no_key == "CLOSED", no_key)

        async def ws_with_key():
            import websockets
            try:
                async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?key={K}") as ws:
                    await ws.send(json.dumps({"action": "subscribe", "channels": ["all"]}))
                    deadline = time.time() + 6
                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        msg = json.loads(raw)
                        if msg.get("type") == "engine_state":
                            return "RECEIVED"
                    return "TIMEOUT"
            except Exception as e:
                return f"ERR {e}"

        with_key = asyncio.run(ws_with_key())
        check("ws.allowed_with_key", with_key == "RECEIVED", with_key)

        r = httpx.get(f"{BASE}/api/strategies", headers={"x-api-key": K}, timeout=5)
        check("strategies.allowed_with_key", r.status_code == 200, str(r.status_code))
    finally:
        server.should_exit = True
        time.sleep(1)

    passed = sum(1 for _, ok in results if ok)
    print(f"\nAUTH GATE: {passed}/{len(results)} PASSED")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()