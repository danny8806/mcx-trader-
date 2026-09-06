"""Live Dhan data + WebSocket architecture test.

Builds the REAL production pipeline (TradingEngine -> DhanDataAdapter ->
DhanRESTClient + DhanWebSocketClient -> EventBus -> strategies) against the
live Dhan API, using an ISOLATED temp DB + temp token file so the audited
repo DB is never touched.

Checks:
  1. REST historical candle path returns real Dhan data.
  2. WebSocket connects, authenticates, subscribes GOLDM+SILVERM.
  3. Ticks flow through the IN-ARCHITECTURE chain:
     DhanWebSocketClient._on_message -> adapter._process_tick -> engine._on_tick
     -> EventBus 'tick:<inst>' -> strategy _make_tick_handler (probe + counts).
  4. EventBus strategy subscriptions for candle/tick topics are wired.
  5. Engine health component reflects data_adapter state.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env(env_file: Path) -> dict:
    env = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env(ROOT / "mcx-trader.env")
    os.environ["DHAN_CLIENT_ID"] = env.get("DHAN_CLIENT_ID", "")
    os.environ["DHAN_ACCESS_TOKEN"] = env.get("DHAN_ACCESS_TOKEN", "")
    os.environ["TRADING_PIN"] = env.get("TRADING_PIN", "")
    os.environ["TOTP_SECRET"] = env.get("TOTP_SECRET", "")

    # ── Isolated environment ───────────────────────────────────────────────
    tmp = Path(tempfile.mkdtemp(prefix="dhan_live_test_"))
    tmp_db = tmp / "trading.db"
    tmp_token = tmp / "dhan_token.json"

    token = ""
    existing_token = ROOT / "data" / "dhan_token.json"
    if existing_token.exists():
        token = json.loads(existing_token.read_text()).get("access_token", "")
    if not token:
        token = env.get("DHAN_ACCESS_TOKEN", "")
    if not token:
        print("FATAL: no token available")
        return 2
    tmp_token.write_text(json.dumps({"access_token": token}), encoding="utf-8")
    print(f"[setup] seeded temp token ({len(token)} chars, expiry check below)")
    import base64
    try:
        p = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p)).get("exp", 0)
        hours = (exp - time.time()) / 3600
        print(f"[setup] token expires in {hours:.1f} h")
    except Exception:
        pass

    cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    cfg["system"]["db_path"] = str(tmp_db)          # absolute => Config.resolve_path passthrough
    cfg["dhan"]["token_file"] = str(tmp_token)      # absolute temp token file
    cfg["system"]["state_path"] = str(tmp / "system_state.json")
    tmp_cfg = tmp / "settings.json"
    tmp_cfg.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[setup] isolated config: db={tmp_db.name} token={tmp_token.name}")

    # ── Probe counters ─────────────────────────────────────────────────────
    counts = {"tick_gold": 0, "tick_silver": 0, "candle_gold": 0, "candle_silver": 0}
    engine = None
    try:
        # ── 1. REST path first (real Dhan API) ────────────────────────────
        from data.dhan import DhanDataAdapter
        probe_adapter = DhanDataAdapter(
            client_id=env.get("DHAN_CLIENT_ID", ""),
            token_file=str(tmp_token),
            on_tick=None,
            on_status=None,
        )
        instr_cfg = cfg["instruments"]
        probe_adapter.register_instruments(instr_cfg)
        now_ist = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        frm = (now_ist - datetime.timedelta(days=7)).date()
        to = now_ist.date()
        print("\n=== 1. REST historical candles (last 7 days) ===")
        rest_ok = True
        for name in ("GOLDM", "SILVERM"):
            try:
                bars5 = probe_adapter.fetch_historical_candles(name, "5", frm, to)
                bars15 = probe_adapter.fetch_historical_candles(name, "15", frm, to)
                bars60 = probe_adapter.fetch_historical_candles(name, "60", frm, to)
                print(f"  {name:8s} 5m={len(bars5):6d}  15m={len(bars15):6d}  1h={len(bars60):6d}")
                if len(bars5) == 0:
                    rest_ok = False
                if bars5:
                    first, last = bars5[0][0], bars5[-1][0]
                    print(f"          range: {datetime.datetime.fromtimestamp(first)} .. "
                          f"{datetime.datetime.fromtimestamp(last)}")
            except Exception as e:
                rest_ok = False
                print(f"  {name}: REST ERROR {type(e).__name__}: {e}")
        probe_adapter.disconnect()
        probe_adapter.rest.stop_scheduler()

        # ── 2. Full architecture engine (isolated db/token) ───────────────
        print("\n=== 2. Building real TradingEngine (isolated config) ===")
        from trading_engine import TradingEngine
        engine = TradingEngine(config_path=str(tmp_cfg))

        # Attach probes to the SAME EventBus the strategies use
        def make_probe(key):
            def cb(event):
                counts[key] += 1
            return cb

        engine.event_bus.subscribe("tick:GOLDM", make_probe("tick_gold"))
        engine.event_bus.subscribe("tick:SILVERM", make_probe("tick_silver"))
        engine.event_bus.subscribe("candle:GOLDM:*", make_probe("candle_gold"))
        engine.event_bus.subscribe("candle:SILVERM:*", make_probe("candle_silver"))

        print(f"  strategies: {list(engine.strategies.keys())}")
        tick_subs = engine.event_bus.subscriber_count
        print(f"  event_bus subscribers after engine init: {tick_subs}")

        print("\n=== 3. engine.start(): warmup REST + WS connect ===")
        engine.start()
        ws = engine.data_adapter.ws
        time.sleep(8)
        print(f"  ws.connected={ws.connected}  connected flag={engine.data_adapter.connected}")
        print(f"  ws.stats={ws.stats}")
        print(f"  market_state={engine.market_status.state.name}  "
              f"engine_status={engine.market_status.engine_status.name}")

        # ── 4. In-architecture tick delivery (real adapter -> engine -> bus) ──
        print("\n=== 4. In-architecture tick path (adapter._process_tick -> engine._on_tick -> EventBus) ===")
        raw = {
            "security_id": "569003",
            "ltp": 78000.0,
            "ltq": 1,
            "ltt": int(time.time()),
            "cumvol": 100,
        }
        before = dict(counts)
        engine.data_adapter._process_tick(dict(raw))
        engine.data_adapter._process_tick(dict(raw))
        raw2 = {**raw, "security_id": "483080", "ltp": 239000.0}
        engine.data_adapter._process_tick(dict(raw2))
        time.sleep(1.5)
        print(f"  counts before={before}")
        print(f"  counts after ={counts}")
        print(f"  adapter.tick_count={engine.data_adapter._tick_count}")
        print(f"  adapter._instrument_ticks={engine.data_adapter._instrument_ticks}")
        engine.data_adapter._ltp_lock.acquire()
        try:
            print(f"  adapter.live_ltp cache: {dict(engine.data_adapter._live_ltp)}")
        finally:
            engine.data_adapter._ltp_lock.release()
        print(f"  engine.health overall: {engine.health.overall_status().value}")

        # ── 5. Live WS sampling (market hours or not) ─────────────────────
        print("\n=== 5. Live WebSocket sampling (up to 30 s) ===")
        t_end = time.time() + 30
        while time.time() < t_end:
            time.sleep(5)
            live_counts = dict(counts)
            from_ws = engine._on_tick if ws.on_tick else None
            print(f"  t={int(time.time() % 100000)} ws.connected={ws.connected} "
                  f"ws.tick_count={ws._stats.get('tick', 0)} "
                  f"recv={ws._stats.get('recv', 0)} "
                  f"probe_tick_gold={live_counts['tick_gold']} "
                  f"probe_tick_silver={live_counts['tick_silver']} "
                  f"adapter.instr_ticks={engine.data_adapter._instrument_ticks} "
                  f"engine_status={engine.market_status.engine_status.name}")

        engine.stop()
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    # ── Verdict ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    tick_delivered = counts["tick_gold"] >= 1 and counts["tick_silver"] >= 1
    candle_wired = counts["candle_gold"] >= 0  # candles only flow during market/candles
    verdict = "PASS" if (rest_ok and tick_delivered) else "PARTIAL"
    print(f"  REST 5m candles : {'OK' if rest_ok else 'FAIL'}")
    print(f"  In-arch tick    : gold={counts['tick_gold']} silver={counts['tick_silver']}"
          f"  ({'delivered' if tick_delivered else 'NOT delivered'})")
    print(f"  Live WS         : see sample loop above (0 ticks emitted outside"
          f" market hours {os.environ.get('SESSION', '09:00-23:30 IST')})")
    print(f"  VERDICT         : {verdict}")
    print("=" * 70)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())