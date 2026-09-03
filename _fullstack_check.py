"""FULL-STACK DEEP CHECK — the entire working system, input -> output.

Boots the REAL dashboard server (uvicorn + dashboard.server:app) with a seeded
REAL engine (all four strategies through the actual signal/fill pipeline, temp
data dirs), then drives EVERY endpoint over real HTTP + WebSocket and feeds the
built frontend (dashboard-ui/dist) — verifying each layer's output end to end:

  REST:   health, overview, strategies + control, positions, orders, fills,
          trades, pnl, equity-curve, market-data, risk, indicators, htf,
          alerts, reconciliation, settings, audit, replay
  Analytics REST: strategies, performance, equity, drawdown, daily, monthly,
          time-of-day, day-of-week, mae-mfe, rolling, execution, parameters,
          correlation, portfolio, trade detail, open-trades, events,
          reconciliation, status
  WS:     connect -> engine_state + events pushes, ping/pong, subscribe,
          get_snapshot / get_trades / pause_strategy commands
  UI:     GET / (index.html), hashed JS/CSS assets, SPA fallback,
          bundle references API paths consumed by the React app

Usage:  $env:PYTHONIOENCODING="utf-8"; python _fullstack_check.py
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
IST = timezone(timedelta(hours=5, minutes=30))

RUN_BASE = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\fullstack")
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

CHECKS = []
STATS = {"http": 0, "ws": 0, "ui": 0}


def ok(name, cond, detail="", layer="http"):
    CHECKS.append((name, bool(cond), detail))
    STATS[layer if layer in STATS else "http"] += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")


def ist(epoch):
    return datetime.fromtimestamp(epoch, IST)


# ════════════════════════════════════════════════════════════════════
# Part A — seed a REAL engine with a deterministic multi-strategy state
# ════════════════════════════════════════════════════════════════════
import full_simulator as sim
from full_simulator import LIVE_INSTRUMENTS, write_config, build_engine, teardown
from core.market_status import EngineStatus, MarketState
from core.trade_close import TradeCloseManager
from strategies.types import Signal, SignalType, StrategyState
from core.timeframe_engine import Bar, BarState


def make_root(tag):
    p = RUN_BASE / tag
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def arm(engine, sid, side, price, stop, state):
    s = engine.strategies[sid]
    s.state = state
    s.position_side = side
    s.stop_price = stop
    s.pending_entry = None
    s.same_bar_stop = None
    s.pending_exit_at_open = False
    return s


def sig(engine, sid, price, stop, inst, kind, side, meta, qty=1):
    return Signal(kind, inst, sid, time.time(), price, stop, qty, side=side, metadata=meta)


def open_long(engine, sid, inst, price, stop):
    s = arm(engine, sid, "LONG", price, stop, StrategyState.LONG_POSITION)
    engine.execution_engine.update_price(inst, price)
    engine._process_signal(sig(engine, sid, price, stop, inst, SignalType.LONG, "LONG",
                               {"entry_price": price, "executed": True, "market": True}))
    return s


def close_exit(engine, sid, inst, price, reason):
    s = engine.strategies[sid]
    s.last_exit_reason = reason
    engine.execution_engine.update_price(inst, price)
    side = "SHORT" if s.position_side == "LONG" else "LONG"
    engine._process_signal(sig(engine, sid, price, 0.0, inst,
                               SignalType.SHORT if side == "SHORT" else SignalType.LONG, side,
                               {"exit": True, "exit_reason": reason, "fill_price": price}))


def mfe_tick(engine, sid, inst, price):
    """Emulate the engine's intrabar MFE/MAE tracking (set by real bar processing)."""
    try:
        for t in engine.trade_ledger.get_open_trades(strategy_id=sid):
            engine.trade_ledger.update_mfe_mae(t.trade_id, price)
    except Exception:
        pass


def seed_engine():
    root = make_root("engine")
    cfg = write_config(root)
    engine, persistence = build_engine(cfg)
    engine._running = True
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine.market_status.update_data_status(True, time.time())
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager, pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines, global_account=engine.account_engine,
        risk_engine=engine.risk_engine, persistence=engine._persistence,
        event_store=engine.event_store, telegram=engine.telegram,
        event_callback=engine._event_callback, trade_ledger=engine.trade_ledger,
    )

    # -- gold_01: LONG -> stop-loss exit (closed trade). Deliberately NO
    #    intrabar MFE/MAE tick: mae-mfe report must survive NULL mfe (regression
    #    guard for analytics/routes.py get_strategy_mae_mfe). --
    open_long(engine, "gold_01", "GOLDM", 162_000.0, 161_800.0)
    close_exit(engine, "gold_01", "GOLDM", 161_500.0, "stop_loss_hit")

    # -- silver_02: LONG -> real stop-loss exit (closed trade). Do this BEFORE
    #    opening gold_02/silver_01 so the close only affects silver_02. --
    open_long(engine, "silver_02", "SILVERM", 251_650.0, 251_200.0)
    mfe_tick(engine, "silver_02", "SILVERM", 251_900.0)
    engine.execution_engine.update_price("SILVERM", 251_600.0)
    close_exit(engine, "silver_02", "SILVERM", 251_100.0, "stop_loss_hit")

    # -- gold_02: LONG -> opposite (reversal) SHORT, left OPEN --
    s = open_long(engine, "gold_02", "GOLDM", 158_253.0, 158_100.0)
    mfe_tick(engine, "gold_02", "GOLDM", 158_350.0)
    r = s._create_reversal_signal("SHORT", close=158_253.0, high=158_400.0, low=158_014.0,
                                  timestamp=time.time(), prev_high=158_250.0, prev_low=157_900.0)
    assert r is None and s.pending_exit_at_open
    next_bar = Bar("GOLDM", "5m", int(time.time()), int(time.time()) + 300,
                   158_200.0, 158_250.0, 158_100.0, 158_180.0, 10, BarState.CLOSED)
    engine.execution_engine.update_price("GOLDM", next_bar.open)
    engine._process_deferred_exit(s, next_bar)
    trig_bar = Bar("GOLDM", "5m", int(time.time()) + 301, int(time.time()) + 601,
                   158_050.0, 158_060.0, 158_010.0, 158_020.0, 10, BarState.CLOSED)
    engine.execution_engine.update_price("GOLDM", trig_bar.low)
    en = s._check_pending_entry(trig_bar)
    assert en is not None and en.signal_type == SignalType.SHORT
    engine._process_signal(en)

    # -- silver_01: LONG left OPEN (for positions / pause-guard checks) --
    open_long(engine, "silver_01", "SILVERM", 250_000.0, 249_500.0)

    # final live prices for mark-to-market
    engine.execution_engine.update_price("GOLDM", 158_300.0)
    engine.execution_engine.update_price("SILVERM", 250_000.0)

    # risk daily pnl = realized net of today's closed trades
    realized = sum(p.snapshot().get("realized_net", 0) for p in engine.pnl_engines.values())
    engine.risk_engine.update_daily_pnl(realized)

    data_adapter = engine.data_adapter
    data_adapter.connected = True
    data_adapter.stats = {"connected": True, "ticks": 12345, "reconnects": 0}

    engine.start = lambda: setattr(engine, "_running", True)  # prod network disabled
    return engine, persistence, root


def ref_closed(engine):
    """Self-consistent reference of what the engine DB holds (source of truth)."""
    trades = engine._persistence.get_trades()
    nets = {t["trade_id"]: t.get("net_pnl") for t in trades}
    realized = sum(v for v in nets.values() if v is not None)
    return {"count": len(trades), "nets": nets, "realized": realized,
            "gold01": {t["trade_id"]: {k: t.get(k) for k in ("entry_price", "exit_price", "exit_reason", "net_pnl")}
                       for t in trades if t.get("strategy_id") == "gold_01"}}


# ════════════════════════════════════════════════════════════════════
# Part B — boot the real server (uvicorn) against the seeded engine
# ════════════════════════════════════════════════════════════════════
def boot_server(engine, persistence):
    import dashboard.server as ds
    from analytics import routes as aroutes

    ana = Path(engine.trade_ledger._db_path)
    aroutes.init(str(ana), strategy_ids=list(engine.strategies.keys()))

    ds.set_engine(engine)
    ds._persistence = persistence
    ds.ws_manager = ds.ws_manager  # shared ConnectionManager

    # seed live events for alerts/audit/WS
    bus = ds.event_bus
    bus.publish("risk_alert", {"message": "margin pressure", "margin": 0.85})
    bus.publish("order_rejected", {"strategy_id": "gold_01", "reason": "insufficient_margin"})
    bus.publish("position_opened", {"strategy_id": "silver_01"})
    bus.publish("position_closed", {"strategy_id": "gold_01", "reason": "stop_loss_hit"})
    bus.publish("trade_closed", {"strategy_id": "gold_01", "net_pnl": -5311.77})
    bus.publish("signal", {"strategy_id": "gold_02", "signal_type": "SHORT"})

    import uvicorn
    cfg = uvicorn.Config(ds.app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            import urllib.request
            urllib.request.urlopen(f"{BASE}/api/health", timeout=0.5)
            return server
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not become ready")


# ════════════════════════════════════════════════════════════════════
# Part C — client battery (HTTP + WS + UI)
# ════════════════════════════════════════════════════════════════════
async def main_checks(engine, ref):
    import httpx
    import websockets

    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:

        # ── health ──
        r = (await c.get("/api/health")).json()
        ok("health.status", r.get("status") == "ok" and r.get("engine") is True and r.get("persistence") is True,
           f"engine={r.get('engine')} persistence={r.get('persistence')} ws={r.get('ws_connections')}")

        r = (await c.get("/api/health/system")).json()
        comps = {d.get("name"): d for d in r.get("components", [])}
        ok("health.system", "dashboard_api" in comps and comps.get("dashboard_api", {}).get("status") == "healthy",
           f"components={list(comps)} overall={r.get('overall')}")
        ok("health.market_state", r.get("market_status") == "live_trading" and r.get("engine_status") == "trading"
           and r.get("data_status") == "connected",
           f"market={r.get('market_status')} engine={r.get('engine_status')} data={r.get('data_status')}")
        comp = next((d for d in r.get("components", []) if d.get("name") == "data_adapter"), {})
        ok("health.uptime_present", comp.get("uptime", 0) > 0,
           f"data_adapter uptime={comp.get('uptime')}")

        # ── overview ──
        r = (await c.get("/api/overview")).json()
        ok("overview.counts", r.get("open_positions_count", {}).get("value") == 2
           and r.get("active_strategies_count", {}).get("value") == 4,
           f"open={r.get('open_positions_count',{}).get('value')} strats={r.get('active_strategies_count',{}).get('value')}")
        ok("overview.equity", r.get("total_equity", {}).get("value", 0) > 0 and r.get("kill_switch", {}).get("value") is False,
           f"equity={r.get('total_equity',{}).get('value'):.2f}")
        ov_pos = list(r.get("positions", {}).keys())
        ok("overview.positions", set(ov_pos) and any(ov_pos), f"positions={ov_pos}")
        ok("overview.realized_matches_db", abs(r.get("realized_pnl", {}).get("value", 0) - ref["realized"]) < 2.0,
           f"api={r.get('realized_pnl',{}).get('value'):.2f} db={ref['realized']:.2f}")

        r = (await c.get("/api/overview/GOLDM")).json()
        ok("overview.instr", r.get("instrument") == "GOLDM" and r.get("ltp") == 158_300.0
           and len(r.get("strategies", [])) == 2,
           f"ltp={r.get('ltp')} strats={len(r.get('strategies', []))}")

        # ── strategies ──
        r = (await c.get("/api/strategies")).json()
        ok("strategies.list", r.get("count") == 4, f"count={r.get('count')}")
        r = (await c.get("/api/strategies", params={"instrument": "GOLDM"})).json()
        ok("strategies.filter.instrument", r.get("count") == 2, f"count={r.get('count')}")
        r = (await c.get("/api/strategies", params={"status": "short_position"})).json()
        ok("strategies.filter.status", r.get("count") == 1 and r.get("strategies", [{}])[0].get("strategy_id") == "gold_02",
           f"count={r.get('count')} {[s.get('strategy_id') for s in r.get('strategies',[])]}")

        r = (await c.get("/api/strategies/gold_01")).json()
        ok("strategies.gold01", r.get("configuration", {}).get("instrument") == "GOLDM"
           and r.get("current_state", {}).get("state") == "flat"
           and len(r.get("positions", [])) == 0,
           f"state={r.get('current_state',{}).get('state')}")
        ok("strategies.gold01.pnl", abs(r.get("performance", {}).get("realized_net", 0) - ref["nets"][list(ref["gold01"])[0]]) < 0.01,
           f"api={r.get('performance',{}).get('realized_net')}")
        htf_ind = (r.get("htf") or {}).get("indicator") or {}
        ok("strategies.gold01.htf_flattened", "dema_value" in htf_ind and "atr_value" in htf_ind,
           f"htf_indicator_keys={sorted(htf_ind)}")
        r = (await c.get("/api/strategies/silver_01")).json()
        ok("strategies.silver01.open", r.get("current_state", {}).get("state") == "long_position"
           and len(r.get("positions", [])) == 1,
           f"state={r.get('current_state',{}).get('state')} pos={len(r.get('positions',[]))}")

        r = (await c.get("/api/strategies/gold_01/parameters")).json()
        ok("strategies.params", r.get("strategy", {}).get("enabled") is True and r.get("source") == "settings.json",
           f"source={r.get('source')}")

        # control: pause / resume / guards
        r = (await c.post("/api/strategies/gold_01/control", json={"action": "pause"})).json()
        ok("control.pause", r.get("success") is True, f"{r}")
        r = (await c.get("/api/strategies")).json()
        gold01 = next((s for s in r.get("strategies", []) if s.get("strategy_id") == "gold_01"), {})
        ok("control.pause.applied", gold01.get("enabled") is False, f"enabled={gold01.get('enabled')}")
        r = (await c.post("/api/strategies/silver_01/control", json={"action": "pause"})).json()
        ok("control.pause.guard_open_position", r.get("success") is False
           and "open position" in r.get("error", ""), f"{r}")
        r = (await c.post("/api/strategies/gold_01/control", json={"action": "resume"})).json()
        ok("control.resume", r.get("success") is True, f"{r}")
        r = (await c.post("/api/strategies/gold_01/control", json={"action": "bogus"})).json()
        ok("control.unknown_action", r.get("success") is False, f"{r}")
        r = (await c.post("/api/strategies/does_not_exist/control", json={"action": "pause"})).json()
        ok("control.missing_strategy", "not found" in r.get("error", ""), f"{r}")

        # ── positions ──
        r = (await c.get("/api/positions")).json()
        pl = r.get("positions", [])
        sides = {p.get("strategy_id"): p.get("side") for p in pl}
        ok("positions.count", r.get("count") == 2, f"count={r.get('count')}")
        ok("positions.sides", sides.get("gold_02") == "SHORT" and sides.get("silver_01") == "LONG", f"{sides}")
        rc = (await c.get("/api/positions", params={"status": "closed"})).json()
        ok("positions.closed", rc.get("count") == ref["count"],
           f"closed={rc.get('count')} trades={ref['count']}")
        ra = (await c.get("/api/positions", params={"status": "all"})).json()
        ok("positions.all", ra.get("count") == 2 + ref["count"],
           f"all={ra.get('count')} open=2 closed={ref['count']}")
        pid = pl[0].get("position_id") if pl else None
        r = (await c.get(f"/api/positions/{pid}")).json()
        ok("positions.detail", r.get("position_id") == pid and r.get("strategy_id") in ("gold_02", "silver_01"), f"{r.get('strategy_id')}")
        r = (await c.get(f"/api/positions/{pid}/pnl")).json()
        ok("positions.pnl", "position" in r and r.get("quantity", 0) == 1, f"entry={r.get('entry_price')}")
        r = (await c.get("/api/positions/ZZZ")).json()
        ok("positions.missing", "not found" in r.get("error", ""), f"{r}")

        # ── orders / fills ──
        r = (await c.get("/api/orders")).json()
        ostate = {o.get("state") for o in r.get("orders", [])}
        ok("orders.filled", r.get("count") >= 5 and ostate == {"filled"}, f"count={r.get('count')} states={ostate}")
        oid = r.get("orders", [{}])[0].get("order_id")
        r = (await c.get(f"/api/orders/{oid}")).json()
        ok("orders.detail", r.get("order_id") == oid and r.get("average_fill_price", 0) > 0, f"avg={r.get('average_fill_price')}")
        r = (await c.get("/api/fills")).json()
        ok("fills", r.get("count") >= 7 and all(f.get("price", 0) > 0 for f in r.get("fills", [])),
           f"count={r.get('count')}")

        # ── trades ──
        r = (await c.get("/api/trades")).json()
        tr = r.get("trades", [])
        ok("trades.closed_count", r.get("count") == ref["count"] == 3, f"api={r.get('count')} db={ref['count']}")
        ok("trades.reasons", sorted(t.get("exit_reason") for t in tr) == sorted(["stop_loss_hit", "stop_loss_hit", "short_reversal"]),
           f"reasons={sorted(t.get('exit_reason') for t in tr)}")
        trid = next(t.get("trade_id") for t in tr if t.get("strategy_id") == "gold_01")
        r = (await c.get(f"/api/trades/{trid}")).json()
        ok("trades.detail", r.get("trade_id") == trid
           and abs(r.get("net_pnl", 0) - ref["nets"][trid]) < 0.01
           and r.get("exit_reason") == "stop_loss_hit",
           f"net={r.get('net_pnl')} ref={ref['nets'][trid]} reason={r.get('exit_reason')}")
        r = (await c.get(f"/api/positions/{trid}")).json()
        ok("trades.closed_position", r.get("status") == "closed" and r.get("position_id") == trid,
           f"status={r.get('status')} pid={r.get('position_id')}")

        # ── pnl / equity ──
        r = (await c.get("/api/pnl")).json()
        ok("pnl.portfolio_realized", abs(r.get("portfolio", {}).get("realized_pnl", 0) - ref["realized"]) < 2.0,
           f"api={r.get('portfolio',{}).get('realized_pnl'):.2f} ref={ref['realized']:.2f}")
        ok("pnl.by_instrument", set(r.get("by_instrument", {})) == {"GOLDM", "SILVERM"},
           f"insts={set(r.get('by_instrument', {}))}")
        r = (await c.get("/api/pnl/GOLDM")).json()
        ok("pnl.instrument", r.get("instrument") == "GOLDM" and r.get("realized", {}).get("trade_count", 0) == 2,
           f"trades={r.get('realized',{}).get('trade_count')}")
        r = (await c.get("/api/pnl/GOLDM/strategy/gold_01")).json()
        ok("pnl.strategy", r.get("strategy_id") == "gold_01"
           and abs(r.get("realized", {}).get("realized_net", 0) - ref["nets"][list(ref["gold01"])[0]]) < 0.01,
           f"realized={r.get('realized',{}).get('realized_net')}")
        r = (await c.get("/api/equity-curve")).json()
        pts = r.get("equity_curve", [])
        acct_eq = engine.account_engine.equity if hasattr(engine, "account_engine") else 0.0
        ok("pnl.equity_curve", "equity_curve" in r and isinstance(pts, list) and len(pts) >= 1,
           f"points={len(pts)}")
        ok("pnl.equity_curve_value", len(pts) >= 1 and abs(pts[0].get("equity", 0) - acct_eq) < 0.01,
           f"snap={pts[0].get('equity') if pts else None} acct={acct_eq:.2f}")

        # ── market data ──
        r = (await c.get("/api/market-data")).json()
        md = r.get("instruments", {})
        ok("market_data", r.get("ws_connected") is True and md.get("GOLDM", {}).get("ltp", ) == 158_300.0
           and md.get("SILVERM", {}).get("ltp") == 250_000.0,
           f"G={md.get('GOLDM',{}).get('ltp')} S={md.get('SILVERM',{}).get('ltp')} ws={r.get('ws_connected')}")
        r = (await c.get("/api/market-data/GOLDM")).json()
        ok("market_data.instrument", r.get("ltp") == 158_300.0 and r.get("config"), f"ltp={r.get('ltp')}")

        # ── risk ──
        r = (await c.get("/api/risk")).json()
        ok("risk", r.get("open_positions") == 2 and r.get("kill_switch_active") is False
           and r.get("used_margin", 0) > 0 and r.get("available_margin", 0) > 0,
           f"open={r.get('open_positions')} margin_used={r.get('used_margin'):.0f}")

        # ── indicators / htf ──
        r = (await c.get("/api/indicators")).json()
        ok("indicators", "indicators" in r and r.get("count", 0) >= 4, f"count={r.get('count')}")
        r = (await c.get("/api/indicators/GOLDM")).json()
        ok("indicators.instrument", r.get("instrument") == "GOLDM", f"{r.get('instrument')}")
        r = (await c.get("/api/htf")).json()
        ok("htf", "htf" in r, f"keys={list(r)}")
        r = (await c.get("/api/htf/GOLDM")).json()
        ok("htf.instrument", r.get("instrument") == "GOLDM", f"{r.get('instrument')}")

        # ── alerts / audit ──
        r = (await c.get("/api/alerts")).json()
        sev = {a.get("type"): a.get("severity") for a in r.get("alerts", [])}
        ok("alerts.severity", sev.get("risk_alert") == "critical" and sev.get("order_rejected") == "warning",
           f"sev={sev}")
        r = (await c.get("/api/alerts", params={"event_type": "risk_alert"})).json()
        ok("alerts.filter", all(a.get("type") == "risk_alert" for a in r.get("alerts", [])) and r.get("count", 0) >= 1,
           f"count={r.get('count')}")
        r = (await c.get("/api/audit")).json()
        ok("audit", r.get("count", 0) >= 6, f"count={r.get('count')}")

        # ── reconciliation ──
        r = (await c.get("/api/reconciliation")).json()
        ok("reconciliation", r.get("is_consistent") is True, f"errors={len(r.get('errors', []))}")

        # ── settings ──
        r = (await c.get("/api/settings")).json()
        ok("settings", len(r.get("strategies", {})) == 4 and r.get("timestamp"), f"strats={len(r.get('strategies', {}))}")
        r = (await c.post("/api/settings/refresh")).json()
        ok("settings.refresh", r.get("status") == "refreshed", f"{r.get('status')}")

        # ── replay ──
        r = (await c.get("/api/replay/status")).json()
        ok("replay.status", r.get("status") == "idle", f"{r}")
        r = (await c.post("/api/replay/start", json={"file": "x.csv"})).json()
        ok("replay.start", "not_implemented" in r.get("status", ""), f"{r}")
        r = (await c.post("/api/replay/stop")).json()
        ok("replay.stop", r.get("status") == "stopped", f"{r}")

        # ── analytics (REST) ──
        r = (await c.get("/api/analytics/strategies")).json()
        ast = r if isinstance(r, list) else r.get("strategies", [])
        ok("an.strategies", isinstance(ast, list) and len(ast) == len(engine.strategies),
           f"count={len(ast)} engine={len(engine.strategies)}")
        r = (await c.get("/api/analytics/strategies/gold_01")).json()
        ok("an.strategy", isinstance(r, dict) and ("strategy_id" in r or "trade_count" in r),
           f"keys={list(r)[:6]}")
        r = (await c.get("/api/analytics/strategies/gold_01/trades", params={"limit": 5})).json()
        rl = r if isinstance(r, list) else r.get("trades", [])
        ok("an.trades", len(rl) == 1 and rl[0].get("trade_id") and rl[0].get("instrument") == "GOLDM",
           f"trades={len(rl)}")
        for ep in ("equity", "drawdown", "daily", "monthly", "time-of-day", "day-of-week", "mae-mfe", "rolling", "execution", "parameters"):
            try:
                rr = (await c.get(f"/api/analytics/strategies/gold_01/{ep}")).json()
                ok(f"an.{ep}", not isinstance(rr, dict) or "error" not in rr, f"{str(rr)[:60]}")
            except Exception as e:
                ok(f"an.{ep}", False, f"http/json: {str(e)[:60]}")
        r = (await c.get("/api/analytics/correlation")).json()
        ok("an.correlation", isinstance(r, (dict, list)), f"{str(r)[:60]}")
        r = (await c.get("/api/analytics/portfolio")).json()
        ok("an.portfolio", isinstance(r, (dict, list)), f"{str(r)[:60]}")
        r = (await c.get(f"/api/analytics/trades/{trid}")).json()
        ok("an.trade_detail", r.get("trade", {}).get("strategy_id") == "gold_01", f"{str(r)[:80]}")
        r = (await c.get("/api/analytics/open-trades")).json()
        ro = r if isinstance(r, list) else r.get("open_trades", r.get("trades", []))
        ok("an.open_trades", len(ro) == 2, f"open={len(ro)}")
        r = (await c.get("/api/analytics/events", params={"strategy_id": "gold_01", "limit": 20})).json()
        re_ = r if isinstance(r, list) else r.get("events", [])
        ok("an.events", len(re_) >= 2, f"events={len(re_)}")
        r = (await c.get("/api/analytics/reconciliation")).json()
        ok("an.reconciliation", isinstance(r, dict) and "issues" in r, f"keys={list(r)[:6]}")
        r = (await c.get("/api/analytics/status")).json()
        ok("an.status", isinstance(r, dict) and len(r) > 0, f"{str(r)[:80]}")

        # ── frontend (built dist) ──
        r = await c.get("/")
        html = r.text
        m = re.search(r'src="(/assets/[^"]+\.js)"', html) or re.search(r'<script[^>]+src="([^"]+\.js)"', html)
        ok("ui.index", r.status_code == 200 and "<div id=" in html and m is not None,
           f"status={r.status_code} js={m.group(1) if m else None}")
        js_path = m.group(1) if m else "/assets/missing.js"
        r = await c.get(js_path)
        ok("ui.js_bundle", r.status_code == 200 and "javascript" in r.headers.get("content-type", "")
           and "/api/overview" in r.text and "/api/analytics" in r.text,
           f"status={r.status_code} size={len(r.content)} types={r.headers.get('content-type')}")
        css = re.search(r'href="([^"]+\.css)"', html)
        if css:
            r = await c.get(css.group(1))
            ok("ui.css_bundle", r.status_code == 200, f"status={r.status_code} size={len(r.content)}")
        r = await c.get("/strategies/gold_01")
        ok("ui.spa_fallback", r.status_code == 200 and "<div id=" in r.text, f"status={r.status_code}")
        ok("ui.api_404_isolated", (await c.get("/api/overview")).status_code == 200
           and (await c.get("/not-an-api")).status_code == 200, "api vs spa routing")

    # ── WebSocket layer ──
    import dashboard.server as ds
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
        inbox: list[dict] = []
        stop = threading.Event()

        async def reader():
            while not stop.is_set():
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                    inbox.append(msg)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

        read_task = asyncio.create_task(reader())

        def push_types(mtype):
            return [m for m in inbox if m.get("type") == mtype]

        async def wait_type(mtype, timeout=5.0):
            dl = time.time() + timeout
            while time.time() < dl:
                if push_types(mtype):
                    return push_types(mtype)[-1]
                await asyncio.sleep(0.1)
            return None

        # deterministic live events push: publish a fresh event now that a
        # client is connected (the _push_events broadcaster will relay it)
        ds.event_bus.publish("fullstack_probe", {"source": "fullstack_check", "at": time.time()})

        cmd_seen = 0

        async def wait_cmd(cmd, timeout=5.0):
            nonlocal cmd_seen
            dl = time.time() + timeout
            while time.time() < dl:
                for idx, m in enumerate(inbox[cmd_seen:], start=cmd_seen):
                    if m.get("type") == "command_result" and m.get("data", {}).get("command") == cmd:
                        cmd_seen = idx + 1
                        return m.get("data", {})
                await asyncio.sleep(0.1)
            return None

        es_msg = await wait_type("engine_state")
        es = es_msg.get("data", {}) if es_msg else {}
        ok("ws.engine_state_push", "strategies" in es and "positions" in es,
           f"keys={list(es)[:6]}")

        await ws.send(json.dumps({"action": "ping"}))
        r = await wait_type("pong", 3)
        ok("ws.ping_pong", r is not None and r.get("type") == "pong", f"{r}")

        await ws.send(json.dumps({"action": "subscribe", "channels": ["risk", "trades"]}))
        await ws.send(json.dumps({"action": "command", "command": "get_snapshot", "params": {}}))
        cr = await wait_cmd("get_snapshot")
        snap = cr.get("data", {}) if cr else {}
        ok("ws.get_snapshot", bool(cr) and cr.get("success") is True and "strategies" in snap,
           f"success={cr.get('success') if cr else None} keys={list(snap)[:6]}")

        await ws.send(json.dumps({"action": "command", "command": "get_trades", "params": {}}))
        cr = await wait_cmd("get_trades")
        ok("ws.get_trades", bool(cr) and cr.get("success") is True and isinstance(cr.get("data"), list)
           and len(cr.get("data", [])) == 3,
           f"trades={len(cr.get('data', [])) if cr else None}")

        await ws.send(json.dumps({"action": "command", "command": "pause_strategy", "params": {"strategy_id": "gold_01"}}))
        cr = await wait_cmd("pause_strategy")
        ok("ws.pause_strategy", bool(cr) and cr.get("success") is True, f"{str(cr)[:80]}")
        await ws.send(json.dumps({"action": "command", "command": "pause_strategy", "params": {"strategy_id": "silver_01"}}))
        cr = await wait_cmd("pause_strategy")
        ok("ws.pause_guard_open_pos", bool(cr) and cr.get("success") is False
           and "open position" in str(cr.get("data", "")), f"{str(cr)[:100]}")

        evt = await wait_type("events", 3)
        ok("ws.events_push", evt is not None and len(evt.get("data", [])) >= 1,
           f"events_seen={len(push_types('events'))}")
        stop.set()
        read_task.cancel()
        try:
            await read_task
        except asyncio.CancelledError:
            pass


def main():
    engine, persistence, root = seed_engine()
    ref = ref_closed(engine)
    print(f"[Seeded] open=2 closed={ref['count']} realized={ref['realized']:.2f}  dir={root}")
    print(f"[Ref] gold_01 trade: {ref['gold01']}")

    server = boot_server(engine, persistence)
    print(f"[Server] ready on {BASE}")

    try:
        asyncio.run(main_checks(engine, ref))
    finally:
        server.should_exit = True
        time.sleep(1)

    failed = [c for c in CHECKS if not c[1]]
    print()
    print("=" * 90)
    print("FULL-STACK RESULT")
    print("=" * 90)
    for name, cond, detail in CHECKS:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    print(f"  {len(CHECKS) - len(failed)}/{len(CHECKS)} PASSED, {len(failed)} FAILED")
    print(f"  {STATS}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()