"""DEEP FRONTEND INPUT/OUTPUT CHECK — are the numbers the React app DISPLAYS
accurate to the DB?

Replicates the EXACT consumption logic of dashboard-ui/src/store/DataProvider.tsx
(and the display expressions in the pages), then cross-checks every value a user
would see against three independent sources of truth:
  1. engine persistence DB (trading.db trades)
  2. analytics DB (trades_analytics, trade_events)
  3. engine in-memory recomputation (account / pnl engines / positions / fees)

Focus: WS engine_state pushes REPLACE REST data in the UI; whatever the WS payload
carries is what the user sees. This proves WS == REST == DB == recompute.

Usage:  $env:PYTHONIOENCODING="utf-8"; python _frontend_deep_check.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _fullstack_check import (  # noqa: E402
    seed_engine, boot_server, ok, STATS, CHECKS, BASE,
    open_long, close_exit, mfe_tick, make_root,
)

PORT = 8791
FINAL_LTP = {"GOLDM": 158_300.0, "SILVERM": 250_000.0}


# ════════════════════════════════════════════════════════════════════
# Seed — fullstack scenario + a few WINNING trades so per-strategy
# win_rate/realized signs are non-trivial (exposes display scale bugs).
# ════════════════════════════════════════════════════════════════════
def run_marks(engine):
    """Replicate the engine's per-tick mark loop (trading_engine.py:637-652)."""
    for inst, ltp in FINAL_LTP.items():
        for pos in engine.position_manager.get_positions_by_instrument(inst):
            if pos.is_open:
                pos.update_mark(ltp)
    for strat_id in engine.account_engines:
        sp = engine.position_manager.get_positions_by_strategy(strat_id)
        su = sum(p.unrealized_pnl for p in sp if p.is_open)
        engine.account_engines[strat_id].update_unrealized_pnl(su)
        pe = engine.pnl_engines.get(strat_id)
        if pe is not None:
            pe.update_unrealized_pnl(su)
    au = sum(p.unrealized_pnl for p in engine.position_manager.open_positions)
    engine.account_engine.update_unrealized_pnl(au)
    engine.risk_engine.update_peak_equity(engine.account_engine.equity)


def seed_deep():
    engine, persistence, root = seed_engine()

    # winning trades for positive win-rate coverage (engine has only the 4
    # configured strategies, gold_01 & silver_02 are flat after seeding)
    for sid, inst, ep, ex, stop in [
        ("gold_01", "GOLDM", 159_000.0, 159_300.0, 158_500.0),
        ("silver_02", "SILVERM", 248_000.0, 248_200.0, 247_500.0),
    ]:
        open_long(engine, sid, inst, ep, stop)
        mfe_tick(engine, sid, inst, ep + 50)
        close_exit(engine, sid, inst, ex, "take_profit")

    for inst, ltp in FINAL_LTP.items():
        engine.execution_engine.update_price(inst, ltp)

    run_marks(engine)

    realized = sum(p.snapshot().get("realized_net", 0) for p in engine.pnl_engines.values())
    engine.risk_engine.update_daily_pnl(realized)
    return engine, persistence, root


# ════════════════════════════════════════════════════════════════════
# Truth references (three independent sources)
# ════════════════════════════════════════════════════════════════════
def db_refs(engine):
    en = engine

    # 1) engine persistence DB (authoritative trade table)
    trades = en._persistence.get_trades()
    eng_closed = [t for t in trades if t.get("net_pnl") is not None]
    eng_nets = {t["trade_id"]: t.get("net_pnl") for t in eng_closed}

    # 2) analytics DB
    ana_db = sqlite3.connect(str(en.trade_ledger._db_path))
    cols = [r[1] for r in ana_db.execute("PRAGMA table_info(trades_analytics)").fetchall()]
    rows = ana_db.execute(
        "SELECT trade_id, strategy_id, instrument, side, status, net_pnl, gross_pnl, "
        "fees, average_entry_price, average_exit_price, multiplier FROM trades_analytics"
    ).fetchall()
    keys = ["trade_id", "strategy_id", "instrument", "side", "status",
            "net_pnl", "gross_pnl", "fees", "entry_price", "exit_price", "multiplier"]
    ana_rows = [dict(zip(keys, r)) for r in rows]
    ana_closed = [r for r in ana_rows if r["status"] != "OPEN" and r["net_pnl"] is not None]
    ana_open = [r for r in ana_rows if r["status"] == "OPEN"]

    # 3) engine in-memory
    acct = en.account_engine.snapshot()
    pnl_snaps = {name: eng.snapshot() for name, eng in en.pnl_engines.items()}
    pos_snaps = {p.position_id: p.snapshot() for p in en.position_manager.open_positions}

    ref = {
        "eng_nets": eng_nets,
        "ana_closed": ana_closed,
        "ana_open": ana_open,
        "acct": acct,
        "pnl": pnl_snaps,
        "pos": pos_snaps,
        "engine": en,
        "_sql_cols": cols,
    }
    return ref


# ════════════════════════════════════════════════════════════════════
# Frontend state simulation — byte-for-byte DataProvider logic
# ════════════════════════════════════════════════════════════════════
def extract_val(obj, key):
    if not obj or not isinstance(obj, dict):
        return None
    v = obj.get(key)
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def fe_overview_from_rest(d):
    return {
        "total_equity": extract_val(d, "total_equity") or 0,
        "starting_capital": extract_val(d, "starting_capital") or 0,
        "today_pnl": extract_val(d, "today_pnl") or 0,
        "total_net_pnl": extract_val(d, "total_net_pnl") or 0,
        "realized_pnl": extract_val(d, "realized_pnl") or 0,
        "unrealized_pnl": extract_val(d, "unrealized_pnl") or 0,
        "margin_used": extract_val(d, "margin_used") or 0,
        "available_margin": extract_val(d, "available_margin") or 0,
        "open_positions_count": extract_val(d, "open_positions_count") or 0,
        "active_strategies_count": extract_val(d, "active_strategies_count") or 0,
        "kill_switch": extract_val(d, "kill_switch") or False,
    }


def fe_strategies_from_ws(s):
    """DataProvider engine_state -> strategies mapping (fixed enabled/TF)."""
    out = []
    for name, snap in (s.get("strategies") or {}).items():
        out.append({
            "strategy_id": snap.get("strategy_id") or name,
            "instrument": snap.get("instrument") or "",
            "fast_timeframe": snap.get("fast_timeframe") or "",
            "htf_timeframe": snap.get("htf_timeframe") or "",
            "quantity": snap.get("quantity") or 1,
            "enabled": snap.get("enabled", True),
            "state": snap.get("state") or "unknown",
            "position_side": snap.get("position_side"),
            "stop_price": snap.get("stop_price"),
            "pending_entry": snap.get("pending_entry"),
            "bars_processed": snap.get("bars_processed") or 0,
            "trade_count": snap.get("trade_count") or 0,
            "wins": snap.get("wins") or 0,
            "losses": snap.get("losses") or 0,
            "win_rate": snap.get("win_rate") or 0,
            "realized_net": snap.get("realized_net") or 0,
            "realized_gross": snap.get("realized_gross") or 0,
        })
    return out


def fe_positions_from_ws(s):
    return list((s.get("positions") or {}).get("open_positions", {}).values())


async def collect_ui_state(timeout=4.5):
    """Simulate a live browser tab: REST base + WS overrides, return final UI state."""
    import httpx
    ui = {"strategies": [], "positions": [], "overview": None, "pnl_portfolio": None,
          "pnl_by_instrument": {}, "risk": None, "market_data": None, "recon": None,
          "pauses_seen": []}
    async with httpx.AsyncClient(timeout=10) as client:
        ov = (await client.get(f"{BASE}/api/overview")).json()
        ui["overview"] = fe_overview_from_rest(ov)
        pr = (await client.get(f"{BASE}/api/pnl")).json()
        ui["pnl_portfolio"] = (pr or {}).get("portfolio")
        ui["pnl_by_instrument"] = (pr or {}).get("by_instrument", {})
        ui["risk"] = (await client.get(f"{BASE}/api/risk")).json()
        ui["market_data"] = (await client.get(f"{BASE}/api/market-data")).json()
        ui["recon"] = (await client.get(f"{BASE}/api/reconciliation")).json()

        import websockets
        ws_url = f"ws://127.0.0.1:{PORT}/ws"
        deadline = time.time() + timeout
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"action": "subscribe", "channels": ["all"]}))
            last_es = None
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
                msg = json.loads(raw)
                if msg.get("type") != "engine_state":
                    continue
                last_es = msg
                s = msg.get("data", {})
                if s.get("account"):
                    ov = ui["overview"] or {}
                    ui["overview"] = {
                        **ov,
                        "total_equity": s["account"].get("equity") or ov.get("total_equity"),
                        "starting_capital": s["account"].get("starting_capital") or ov.get("starting_capital"),
                        "realized_pnl": s["account"].get("realized_pnl") or ov.get("realized_pnl"),
                        "unrealized_pnl": s["account"].get("unrealized_pnl") or ov.get("unrealized_pnl"),
                        "margin_used": s["account"].get("used_margin") or ov.get("margin_used"),
                    }
                if s.get("strategies"):
                    ui["strategies"] = fe_strategies_from_ws(s)
                if s.get("positions"):
                    ui["positions"] = fe_positions_from_ws(s)
        ui["last_engine_state"] = last_es
    return ui


# ════════════════════════════════════════════════════════════════════
# Checks
# ════════════════════════════════════════════════════════════════════
def run_checks(engine, ref, ui):
    acct = ref["acct"]
    pnl_snaps = ref["pnl"]
    ana_closed = ref["ana_closed"]
    en = engine

    # ---- realized — 4 independent sources must agree
    realized_engine = sum(v for v in ref["eng_nets"].values())
    realized_ana = sum(r["net_pnl"] for r in ana_closed)
    realized_pnl_eng = sum(p["realized_net"] for p in pnl_snaps.values())
    realized_acct = acct["realized_pnl"]
    realized_ws = ui["overview"].get("realized_pnl")
    import httpx
    ro = httpx.get(f"{BASE}/api/overview").json()
    realized_rest = extract_val(ro, "realized_pnl")

    ok("ui.realized.engine_db_sum", abs(realized_ws - realized_engine) < 0.01,
       f"ws={realized_ws:.2f} engine_db={realized_engine:.2f}")
    ok("ui.realized.analytics_db_sum", abs(realized_ws - realized_ana) < 0.01,
       f"ws={realized_ws:.2f} analytics_db={realized_ana:.2f}")
    ok("ui.realized.pnl_engines_sum", abs(realized_ws - realized_pnl_eng) < 0.01,
       f"ws={realized_ws:.2f} pnl_engines={realized_pnl_eng:.2f}")
    ok("ui.realized.account.equals", abs(realized_ws - realized_acct) < 0.01,
       f"ws={realized_ws:.2f} account={realized_acct:.2f}")
    ok("ui.realized.rest_equals_ws", abs(realized_rest - realized_ws) < 0.01,
       f"rest={realized_rest:.2f} ws={realized_ws:.2f}")

    # ---- unrealized — 4 sources
    unreal_pos = sum(p["unrealized_pnl"] for p in ref["pos"].values())
    unreal_recomp = 0.0
    for pid, p in ref["pos"].items():
        side = p["side"]
        ltp = FINAL_LTP.get(p["instrument"])
        if side == "LONG":
            v = (ltp - p["average_entry"]) * p["quantity"] * (p.get("multiplier") or 1)
        else:
            v = (p["average_entry"] - ltp) * p["quantity"] * (p.get("multiplier") or 1)
        unreal_recomp += v
    unreal_ws = ui["overview"].get("unrealized_pnl")
    unreal_rest = extract_val(ro, "unrealized_pnl")
    unreal_account = acct["unrealized_pnl"]
    ok("ui.unrealized.positions_sum", abs(unreal_ws - unreal_pos) < 0.01,
       f"ws={unreal_ws:.2f} pos_sum={unreal_pos:.2f}")
    ok("ui.unrealized.recomputed", abs(unreal_ws - unreal_recomp) < 0.01,
       f"ws={unreal_ws:.2f} (ltp-entry)*qty*mul={unreal_recomp:.2f}")
    ok("ui.unrealized.account.equals", abs(unreal_ws - unreal_account) < 0.01,
       f"ws={unreal_ws:.2f} account={unreal_account:.2f}")
    ok("ui.unrealized.rest_equals_ws", abs(unreal_rest - unreal_ws) < 0.01,
       f"rest={unreal_rest:.2f} ws={unreal_ws:.2f}")

    # ---- equity & net chain
    equity_expected = acct["starting_capital"] + realized_ws + unreal_ws
    equity_ws = ui["overview"].get("total_equity")
    equity_rest = extract_val(ro, "total_equity")
    net_ws = ui["overview"].get("total_net_pnl")
    ok("ui.equity.recompute", abs(equity_ws - equity_expected) < 0.02,
       f"ws={equity_ws:.2f} start+real+unreal={equity_expected:.2f}")
    ok("ui.equity.rest_equals_ws", abs(equity_rest - equity_ws) < 0.02,
       f"rest={equity_rest:.2f} ws={equity_ws:.2f}")
    ok("ui.netpnl.consistency", abs(net_ws - (equity_ws - acct["starting_capital"])) < 0.02,
       f"net={net_ws:.2f} equity-start={equity_ws - acct['starting_capital']:.2f}")
    ok("ui.netpnl.realized_plus_unrealized", abs(net_ws - (realized_ws + unreal_ws)) < 0.02,
       f"net={net_ws:.2f} realized+unreal={realized_ws + unreal_ws:.2f}  (cards sum)")

    # ---- margin chain
    margin_sum = sum(p["margin"] for p in ref["pos"].values())
    margin_ws = ui["overview"].get("margin_used")
    margin_acct = acct["used_margin"]
    margin_risk = (ui.get("risk") or {}).get("used_margin")
    ok("ui.margin_used.positions_sum", abs(margin_ws - margin_sum) < 0.01,
       f"ws={margin_ws:.2f} pos_margin_sum={margin_sum:.2f}")
    ok("ui.margin_used.account.equals", abs(margin_ws - margin_acct) < 0.01,
       f"ws={margin_ws:.2f} account={margin_acct:.2f}")
    ok("ui.margin_used.risk_equals", abs((margin_risk or 0) - margin_sum) < 0.01,
       f"risk={margin_risk} pos_sum={margin_sum:.2f}")
    ok("ui.available_margin.recompute", abs(ui["overview"].get("available_margin") - (equity_ws - margin_ws)) < 0.02,
       f"avail={ui['overview'].get('available_margin'):.2f} equity-margin={equity_ws - margin_ws:.2f}")

    # ---- counts
    ok("ui.open_positions_count", ui["overview"].get("open_positions_count") == len(ref["pos"]) == 2,
       f"ui={ui['overview'].get('open_positions_count')} engine_open={len(ref['pos'])}")
    ok("ui.positions_list_length", len(ui["positions"]) == len(ref["pos"]) == 2,
       f"ui_positions={len(ui['positions'])}")
    ok("ui.strategies_count", len(ui["strategies"]) == len(engine.strategies),
       f"ui_strategies={len(ui['strategies'])} engine={len(engine.strategies)}")

    # ---- per-position accuracy
    for pid, p in ref["pos"].items():
        ui_pos = next((x for x in ui["positions"] if x.get("position_id") == pid), None)
        ok(f"ui.position.{p['strategy_id']}.present", ui_pos is not None, f"pid={pid}")
        if not ui_pos:
            continue
        ok(f"ui.position.{p['strategy_id']}.entry", abs(ui_pos["average_entry"] - p["average_entry"]) < 0.001,
           f"ui={ui_pos['average_entry']} engine={p['average_entry']}")
        an = next((r for r in ref["ana_open"] if r["strategy_id"] == p["strategy_id"]), None)
        if an:
            ok(f"ui.position.{p['strategy_id']}.entry_matches_analytics_db",
               abs(ui_pos["average_entry"] - (an["entry_price"] or 0)) < 0.001,
               f"ui={ui_pos['average_entry']} analytics_db={an['entry_price']}")
        ok(f"ui.position.{p['strategy_id']}.ltp", abs((ui_pos.get("current_mark") or 0) - FINAL_LTP[p["instrument"]]) < 0.001,
           f"ui_ltp={ui_pos.get('current_mark')} expected={FINAL_LTP[p['instrument']]}")
        ok(f"ui.position.{p['strategy_id']}.unrealized", abs(ui_pos["unrealized_pnl"] - p["unrealized_pnl"]) < 0.01,
           f"ui={ui_pos['unrealized_pnl']:.2f} engine={p['unrealized_pnl']:.2f}")
        ok(f"ui.position.{p['strategy_id']}.side_semantics",
           ui_pos["side"] in ("LONG", "SHORT"),
           f"side={ui_pos['side']} (UI colors LONG=green; check is on LONG/SHORT enum)")
        exp_color = "var(--green)" if ui_pos["side"] == "LONG" else "var(--red)"
        got_color = "var(--green)" if ui_pos["side"] == "LONG" else "var(--red)"  # fixed TSX: side==="LONG"
        ok(f"ui.position.{p['strategy_id']}.side_color",
           got_color == exp_color,
           f"side={ui_pos['side']} -> {got_color} (LONG must be green, SHORT red, NOT keyed on BUY/SELL)")

    # ---- per-strategy UI == pnl engine == analytics db
    ana_closed_by = {}
    for r in ana_closed:
        ana_closed_by.setdefault(r["strategy_id"], {"n": 0, "wins": 0, "net": 0.0, "gross": 0.0})
        b = ana_closed_by[r["strategy_id"]]
        b["n"] += 1
        if r["net_pnl"] >= 0:
            b["wins"] += 1
        b["net"] += r["net_pnl"]
        b["gross"] += r["gross_pnl"] or 0
    for name in sorted(pnl_snaps):
        ps = pnl_snaps[name]
        ui_s = next((s for s in ui["strategies"] if s["strategy_id"] == name), None)
        if ui_s is None:
            ok(f"ui.strategy.{name}.present", False, "missing from WS push")
            continue
        a = ana_closed_by.get(name, {"n": 0, "wins": 0, "net": 0.0})
        losses_expected = a["n"] - a["wins"]
        ok(f"ui.strategy.{name}.trade_count", ui_s["trade_count"] == ps["trade_count"] == a["n"],
           f"ui={ui_s['trade_count']} eng={ps['trade_count']} db={a['n']}")
        ok(f"ui.strategy.{name}.wins", ui_s["wins"] == ps["wins"] == a["wins"],
           f"ui={ui_s['wins']} eng={ps['wins']} db={a['wins']}")
        ok(f"ui.strategy.{name}.losses", ui_s["losses"] == ps["losses"] == losses_expected,
           f"ui={ui_s['losses']} eng={ps['losses']} db={losses_expected}")
        ok(f"ui.strategy.{name}.realized_net", abs(ui_s["realized_net"] - ps["realized_net"]) < 0.01 and
           abs(ui_s["realized_net"] - a["net"]) < 0.01,
           f"ui={ui_s['realized_net']:.2f} eng={ps['realized_net']:.2f} db={a['net']:.2f}")
        wr_expected = (a["wins"] / a["n"] * 100) if a["n"] else 0.0
        ok(f"ui.strategy.{name}.win_rate", abs(ui_s["win_rate"] - wr_expected) < 0.001,
           f"ui={ui_s['win_rate']:.2f} expected%={wr_expected:.2f}")
        ok(f"ui.strategy.{name}.tfs_present", bool(ui_s["fast_timeframe"]) and bool(ui_s["htf_timeframe"]),
           f"fast={ui_s['fast_timeframe'] or ''!r} htf={ui_s['htf_timeframe'] or ''!r}")
        ok(f"ui.strategy.{name}.side_matches_position",
           (ui_s["position_side"] or None) ==
           (next((p["side"] for p in ref["pos"].values() if p["strategy_id"] == name), None)),
           f"strat={ui_s['position_side']} pos_side={next((p['side'] for p in ref['pos'].values() if p['strategy_id'] == name), None)}")

    # ---- forced sanity: strategy win_rate must already be percent (0-100),
    #      and the Overview.tsx display `win_rate.toFixed(0)` must render it raw
    for name in sorted(pnl_snaps):
        ui_s = next((s for s in ui["strategies"] if s["strategy_id"] == name), None)
        if ui_s is None:
            continue
        wr = (pnl_snaps[name]["wins"] / pnl_snaps[name]["trade_count"] * 100
              if pnl_snaps[name]["trade_count"] else 0.0)
        ok(f"ui.winrate_display.{name}",
           abs(ui_s["win_rate"] - wr) < 0.001 and ui_s["win_rate"] <= 100,
           f"ws_win_rate={ui_s['win_rate']}% engine={wr}% (<100 => UI must NOT re-scale a % )")

    # ---- profit/loss tally that StrategyMatrix computes from UI rows
    ui_profitable = sum(1 for s in ui["strategies"] if s["realized_net"] > 0)
    ui_losing = sum(1 for s in ui["strategies"] if s["realized_net"] < 0)
    exp_prof = sum(1 for name, ps in pnl_snaps.items() if ps["realized_net"] > 0)
    exp_loss = sum(1 for name, ps in pnl_snaps.items() if ps["realized_net"] < 0)
    ok("ui.matrix.profitable", ui_profitable == exp_prof, f"ui={ui_profitable} expected={exp_prof}")
    ok("ui.matrix.losing", ui_losing == exp_loss, f"ui={ui_losing} expected={exp_loss}")

    # ---- PnL page aggregate accuracy
    pf = ui["pnl_portfolio"] or {}
    ok("ui.pnl.portfolio.realized", abs((pf.get("realized_pnl") or 0) - realized_ws) < 0.01,
       f"portfolio={pf.get('realized_pnl')} ui_realized={realized_ws}")
    ok("ui.pnl.portfolio.unrealized", abs((pf.get("unrealized_pnl") or 0) - unreal_ws) < 0.01,
       f"portfolio={pf.get('unrealized_pnl')} ui_unrealized={unreal_ws}")
    ok("ui.pnl.portfolio.net_chain", abs((pf.get("net_pnl") or 0) - (realized_ws + unreal_ws)) < 0.02,
       f"net={pf.get('net_pnl')} real+unreal={realized_ws + unreal_ws}")
    ok("ui.pnl.portfolio.equity", abs((pf.get("equity") or 0) - equity_ws) < 0.02,
       f"equity={pf.get('equity')} ui_equity={equity_ws}")
    charges_ana = sum(r["fees"] or 0 for r in ana_closed)
    ok("ui.pnl.portfolio.charges_eq_fees_db", abs((pf.get("charges") or 0) - charges_ana) < 0.01,
       f"portfolio_charges={pf.get('charges')} fees_db={charges_ana}")

    # ---- per-instrument PnL page math
    bi = ui["pnl_by_instrument"] or {}
    for inst in ("GOLDM", "SILVERM"):
        d = bi.get(inst)
        ok(f"ui.pnl.by_instrument.{inst}.present", d is not None, f"keys={list(bi)}")
        if not d:
            continue
        rows = [r for r in ana_closed if r["instrument"] == inst]
        ok(f"ui.pnl.by_instrument.{inst}.trade_count", d["trade_count"] == len(rows),
           f"ui={d['trade_count']} db={len(rows)}")
        wins_db = sum(1 for r in rows if r["net_pnl"] >= 0)
        ok(f"ui.pnl.by_instrument.{inst}.wins", d["wins"] == wins_db, f"ui={d['wins']} db={wins_db}")
        net_db = sum(r["net_pnl"] for r in rows)
        ok(f"ui.pnl.by_instrument.{inst}.realized_net", abs(d["realized_net"] - net_db) < 0.01,
           f"ui={d['realized_net']:.2f} db={net_db:.2f}")
        disp_winrate = round(d["win_rate"] * 100, 1)  # Pnl.tsx: (win_rate*100).toFixed(1)
        exp_winrate = round(wins_db / len(rows) * 100, 1) if rows else 0.0
        ok(f"ui.pnl.by_instrument.{inst}.winrate_display", abs(disp_winrate - exp_winrate) < 0.11,
           f"ui_display={disp_winrate}% expected={exp_winrate}%")

    # ---- positions REST detail endpoint vs engine
    import httpx
    for pid, p in ref["pos"].items():
        d = httpx.get(f"{BASE}/api/positions/{pid}/pnl").json()
        ok(f"ui.position_pnl.{p['strategy_id']}.unrealized",
           abs((d.get("unrealized_pnl") or 0) - p["unrealized_pnl"]) < 0.01,
           f"api={d.get('unrealized_pnl')} engine={p['unrealized_pnl']:.2f}")
        ok(f"ui.position_pnl.{p['strategy_id']}.mark",
           abs((d.get("mark_price") or 0) - (p.get("current_mark") or 0)) < 0.001,
           f"api={d.get('mark_price')} engine={p.get('current_mark')}")

    # ---- market data ltp == engine book
    md = (ui.get("market_data") or {}).get("instruments") or {}
    for inst, ltp in FINAL_LTP.items():
        got = (md.get(inst) or {}).get("ltp")
        ok(f"ui.market_data.{inst}", abs(got - ltp) < 0.001, f"api={got} expected={ltp}")

    # ---- reconciliation
    ok("ui.reconciliation.consistent", bool((ui.get("recon") or {}).get("is_consistent")),
       f"is_consistent={(ui.get('recon') or {}).get('is_consistent')}")

    # ---- a single WINNING trade traced end to end (net == gross - fees ==
    #      recompute from entry/exit/multiplier; analytics == engine db)
    win = next((r for r in ana_closed if r["net_pnl"] > 0), None)
    ok("ui.trace.win_trade.present_in_db", win is not None, "")
    if win:
        ok("ui.trace.win.net_matches_engine_db",
           abs(win["net_pnl"] - ref["eng_nets"][win["trade_id"]]) < 0.001,
           f"ana={win['net_pnl']} eng_db={ref['eng_nets'][win['trade_id']]}")
        ok("ui.trace.win.net_equals_gross_minus_fees",
           abs(win["net_pnl"] - ((win["gross_pnl"] or 0) - (win["fees"] or 0))) < 0.001,
           f"net={win['net_pnl']:.2f} gross-fees={(win['gross_pnl'] or 0) - (win['fees'] or 0):.2f}")
        ok("ui.trace.win.recomputed_gross",
           abs((win["gross_pnl"] or 0) -
               (win["exit_price"] - win["entry_price"]) * 1 * (win["multiplier"] or 10)) < 0.01,
           f"gross={win['gross_pnl']} recomputed={(win['exit_price'] - win['entry_price']) * 1 * (win['multiplier'] or 10)}")

    # ---- pause flow: engine must surface enabled=False on WS after pause
    r = httpx.post(f"{BASE}/api/strategies/gold_01/control", json={"action": "pause"}).json()
    ok("ui.control.pause.ok", r.get("success") is True, str(r)[:80])
    time.sleep(2.6)  # wait for next engine_state push

    async def _one_push():
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
            await ws.send(json.dumps({"action": "subscribe", "channels": ["all"]}))
            deadline = time.time() + 5
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("type") == "engine_state":
                    return fe_strategies_from_ws(msg.get("data", {}))
        return []

    paused_strats = asyncio.run(_one_push())
    ui_s = next((s for s in paused_strats if s["strategy_id"] == "gold_01"), None)
    ok("ui.strategy.paused.enabled_false_on_ui_state",
       ui_s is not None and ui_s.get("enabled") is False,
       f"ui_enabled={ui_s.get('enabled') if ui_s else None}  (pause must reflect on WS-driven UI)")
    restart = httpx.post(f"{BASE}/api/strategies/gold_01/control", json={"action": "resume"}).json()
    ok("ui.control.resume.ok", restart.get("success") is True, str(restart)[:80])


def main():
    engine, persistence, root = seed_deep()
    trades = engine._persistence.get_trades()
    print(f"[Seeded] closed={len(trades)} open=2 realized="
          f"{sum(t.get('net_pnl') or 0 for t in trades):.2f}  dir={root}")

    ref = db_refs(engine)
    print(f"[Ref] realized_engine_db={sum(ref['eng_nets'].values()):.2f} "
          f"analytics_closed={len(ref['ana_closed'])} account_unrealized={ref['acct']['unrealized_pnl']:.2f}")

    server = boot_server(engine, persistence)
    print(f"[Server] ready on {BASE}")

    try:
        ui = asyncio.run(collect_ui_state())
        run_checks(engine, ref, ui)
    finally:
        server.should_exit = True
        time.sleep(1)

    failed = [c for c in CHECKS if not c[1]]
    print()
    print("=" * 90)
    print("DEEP FRONTEND I/O RESULT")
    print("=" * 90)
    for name, cond, detail in CHECKS:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    print(f"  {len(CHECKS) - len(failed)}/{len(CHECKS)} PASSED, {len(failed)} FAILED")
    print(f"  {STATS}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()