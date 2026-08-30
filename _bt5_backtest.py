"""FRESH LAST-5-DAYS BACKTEST — real engine, real Dhan 5m candles, all 4 strategies.

NEW test file (replaces the deleted scratch set).  No stored results are reused:
  - fetches fresh 5m candles from Dhan REST for the last 5 trading days
  - replays every bar through the REAL TradingEngine (real indicators, real HTF
    engine, real strategies, real execution/ledger) via full_simulator pieces
  - rechecks: reconciliation + independent gross/fees/net per trade
  - reports every closed trade with real bar timestamps (the replay ledger
    stores wall-clock times, so entry/exit bars are reconstructed from an
    INDEPENDENT cross tracker + stop/reversal rules)

Usage:  python _bt5_backtest.py [start_iso stop_iso]
        (default window = last 5 trading days)
"""
import sqlite3
import sys
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_ARGS = sys.argv[1:]
START = _ARGS[0] if len(_ARGS) > 0 else "2026-08-24"
STOP = _ARGS[1] if len(_ARGS) > 1 else "2026-08-28"
TOKEN = ROOT / "data" / "dhan_token.json"
try:
    from config import Config
    _cfg = Config()
    _cfg.load()
    _tf = _cfg.get("dhan.token_file", "").strip()
    if _tf and (ROOT / _tf).exists():
        TOKEN = ROOT / _tf
except Exception:
    pass
RUN_ROOT = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"bt5_{START}_{STOP}"
FTF = {"gold_01": "5m", "gold_02": "15m", "silver_01": "15m", "silver_02": "5m"}


def fmt(ts):
    return ist(ts).strftime("%m-%d %H:%M") if ts else "?"


import full_simulator as sim
from full_simulator import (
    LIVE_INSTRUMENTS, LIVE_STRATEGIES, _TF_RANK, ist,
    fetch_real_candles, build_bars, write_config, build_engine, teardown,
    indep_gross, indep_charges,
)
from htf.backtest_style_htf import BacktestStyleHTFEngine
from indicators.dema_atr import DEMAATR


def fmt(ts):
    return ist(ts).strftime("%m-%d %H:%M") if ts else "?"


# ── 1. Fresh data + bars ──
if RUN_ROOT.exists():
    shutil.rmtree(RUN_ROOT)
RUN_ROOT.mkdir(parents=True, exist_ok=True)

candles, stream_all, bars_fast = {}, [], {}
for name, meta in LIVE_INSTRUMENTS.items():
    rows = fetch_real_candles(TOKEN, meta["security_id"], START, STOP)
    candles[name] = rows
    b5, b15, b1h = build_bars(name, rows)
    bars_fast[name] = {"5m": b5, "15m": b15}
    stream_all += b5 + b15 + b1h
    print(f"[Data] {name}: {len(rows)} x5m | {len(b15)} x15m | {len(b1h)} x1h", flush=True)
if not candles or all(not v for v in candles.values()):
    print("FATAL: no candle data"); sys.exit(1)

stream_by_day = {}
for bar in stream_all:
    stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
for d in stream_by_day:
    stream_by_day[d].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))
print(f"[Data] {len(stream_all)} bars across {len(stream_by_day)} trading days", flush=True)

# ── 2. Real engine ──
import trading_engine as te
te.DhanDataAdapter = sim.ReplayDataAdapter
from core.market_status import MarketState
from core.trade_close import TradeCloseManager

cfg = write_config(RUN_ROOT)
engine, persistence = build_engine(cfg)
engine._trade_close_manager = TradeCloseManager(
    position_manager=engine.position_manager, pnl_engines=engine.pnl_engines,
    account_engines=engine.account_engines, global_account=engine.account_engine,
    risk_engine=engine.risk_engine, persistence=engine._persistence,
    event_store=engine.event_store, telegram=engine.telegram,
    event_callback=engine._event_callback, trade_ledger=engine.trade_ledger,
)

# ── 3. Independent cross tracker (parallel, bar-time keyed) ──
xhtf = BacktestStyleHTFEngine()
xinds = {}
for name in LIVE_INSTRUMENTS:
    for tf in ("5m", "15m", "1h"):
        xinds[f"{name}:{tf}"] = DEMAATR(3, 6, 1.0)
    xhtf.register(name, "1h", 3, 6, 1.0, "09:00")
    xhtf.register(name, "15m", 3, 6, 1.0, "09:00")
xprev, cross_ev = {}, []


def feed_tracker(bar):
    k = f"{bar.instrument}:{bar.timeframe}"
    xinds[k].update(bar.open, bar.high, bar.low, bar.close)
    if bar.timeframe in ("1h", "15m"):
        xhtf.on_htf_bar_closed(bar)


def check_cross(bar, sid):
    fk = f"{bar.instrument}:{FTF[sid]}"
    if not xinds[fk].initialized:
        return
    hm = xhtf.map_to_fast_bar(bar, FTF[sid])
    mm = xhtf.map_mid_to_fast_bar(bar, FTF[sid])
    if hm.htf_value is None:
        return
    ctx = xprev.get(sid, {})
    h, m = hm.htf_value, mm.htf_value
    pc, p1, pm = ctx.get("close"), ctx.get("htf"), ctx.get("mid")
    long_c = pc is not None and p1 is not None and bar.close > h and pc <= p1 and (m is None or m < h)
    short_c = pc is not None and p1 is not None and bar.close < h and pc >= p1 and (m is None or m > h)
    if long_c or short_c:
        cross_ev.append((bar.end_ts, sid, "LONG" if long_c else "SHORT", bar.close))
    xprev[sid] = {"close": bar.close, "htf": h, "mid": m}


# ── 4. Replay ──
from core.market_status import EngineStatus
engine._running = True
engine.market_status.set_engine_status(EngineStatus.READY)
ws = engine.data_adapter.ws


def live_tick(instrument, ltp, ts):
    ws._last_tick_time = time.time()
    engine._on_tick({"instrument": instrument, "ltp": ltp, "event_timestamp": ts})


t0 = time.time()
for day, bars in sorted(stream_by_day.items()):
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status._eod_close_done_today = False
    for bar in bars:
        feed_tracker(bar)
        for sid in LIVE_STRATEGIES:
            scfg = LIVE_STRATEGIES[sid]
            if scfg["instrument"] == bar.instrument and scfg["fast_timeframe"] == bar.timeframe:
                check_cross(bar, sid)
        strat = None
        for s in engine.strategies.values():
            if s.instrument == bar.instrument and s.fast_timeframe == bar.timeframe:
                strat = s
        if strat is not None:
            engine.execution_engine.update_price(bar.instrument, bar.close)
        engine._on_bar_closed(bar)
        live_tick(bar.instrument, bar.close, bar.end_ts)
print(f"[Replay] done in {time.time()-t0:.1f}s  | independent cross events = {len(cross_ev)}", flush=True)

# ── 5. Recheck: reconciliation + independent P&L ──
from reconciliation.engine import ReconciliationEngine
recon = ReconciliationEngine(
    persistence=engine._persistence, position_manager=engine.position_manager,
    pnl_engines=engine.pnl_engines, account_engines=engine.account_engines,
    strategies=engine.strategies, order_manager=engine.order_manager,
)
rr = recon.reconcile(phase="live")
closed = engine.trade_ledger.get_closed_trades()
ok = rr.is_consistent
chk = [("reconciliation", ok)]
for tr in closed:
    mult = LIVE_INSTRUMENTS[tr.instrument]["multiplier"]
    ref_g = indep_gross(tr.side, tr.average_entry_price, tr.average_exit_price,
                        tr.filled_quantity or tr.entry_quantity, mult)
    ref_f = indep_charges(tr.average_entry_price, tr.average_exit_price,
                          tr.filled_quantity or tr.entry_quantity, mult, tr.side)
    ref_n = round(ref_g - ref_f, 2)
    close_ok = abs(tr.net_pnl - ref_n) < 1.0
    chk.append((f"pnl {tr.strategy_id}/{tr.side}", close_ok))
    if not close_ok:
        print(f"  !! PnL mismatch {tr.strategy_id} {tr.side}: ledger {tr.net_pnl} vs indep {ref_n}")

DB = RUN_ROOT / "data" / "db" / "analytics.db"

# ── 6. Trades from ledger (fresh run) ──
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT strategy_id, side, entry_price, average_exit_price, initial_stop, "
    "net_pnl, gross_pnl, fees, exit_reason, signal_time "
    "FROM trades_analytics ORDER BY signal_time").fetchall()
con.close()

ev_by = {}
for ev in cross_ev:
    ev_by.setdefault((ev[1], ev[2]), []).append(ev)
ptrs = {}


def next_entry(sid, side, entry_p):
    evs = ev_by.get((sid, side), [])
    k = ptrs.get((sid, side), 0)
    for j in range(k, len(evs)):
        if abs(evs[j][3] - entry_p) <= 2.0:
            ptrs[(sid, side)] = j + 1
            return evs[j]
    evs2 = evs[k:]
    if evs2:
        ptrs[(sid, side)] = len(evs)
        return evs2[0]
    return None


def exit_bar(sid, entry_t, reason, exit_px, inst, tf):
    nside = "SHORT" if reason.startswith("long") else ("LONG" if reason.startswith("short") else None)
    if nside is not None:
        for ev in ev_by.get((sid, nside), []):
            if ev[0] and ev[0] > entry_t:
                return ev[0]
        return None
    for b in bars_fast[inst][tf]:
        if b.end_ts <= entry_t:
            continue
        if exit_px and (b.low <= exit_px + 1.0 or b.high >= exit_px - 1.0):
            return b.end_ts
    return None


trades_out = []
for r in rows:
    sid, side = r["strategy_id"], r["side"]
    tf, inst = FTF[sid], LIVE_STRATEGIES[sid]["instrument"]
    ev = next_entry(sid, side, r["entry_price"] or 0.0)
    entry_t = ev[0] if ev else None
    reason = r["exit_reason"] or ""
    exit_px = r["average_exit_price"] or r["initial_stop"]
    exit_t = exit_bar(sid, entry_t, reason, exit_px, inst, tf) if entry_t and reason else None
    trades_out.append((entry_t, exit_t, r["entry_price"], r["average_exit_price"],
                       round(r["net_pnl"] or 0.0, 2), round(r["gross_pnl"] or 0.0, 2),
                       round(r["fees"] or 0.0, 2), sid, side, reason))

teardown(engine, persistence)

# ── 7. Report ──
n_none = sum(1 for t in trades_out if t[0] is None)
n_exit_none = sum(1 for t in trades_out if t[1] is None)
print(f"\n=== BACKTEST TRADES  {START} .. {STOP}  (real engine, fresh data) ===")
by_day = {}
for t in trades_out:
    by_day.setdefault(ist(t[0]).strftime("%a %m-%d") if t[0] else "unmatched", []).append(t)
total_net = 0.0
for d in sorted(by_day, key=lambda x: (0, x) if x != "unmatched" else (1, x)):
    day_net = sum(t[4] for t in by_day[d])
    print(f"\n--- {d}   (net {day_net:9.2f}) ---")
    for (et, xt, ep, xp, net, gross, fee, sid, side, reason) in sorted(by_day[d], key=lambda x: x[0] or 0):
        total_net += net
        r = "stop" if reason == "stop_loss_hit" else (reason or "-")[:8]
        ep_s = f"{ep:,.0f}" if ep else "-"
        xp_s = f"{xp:,.0f}" if xp else "-"
        print(f"  {fmt(et)} {sid:9s} {side:5s} {ep_s:>8} -> {fmt(xt)} {xp_s:>8} {r:9s} "
              f"gross {gross:8.2f} fee {fee:6.2f} net {net:9.2f}")

print("\n--- TOTALS (by strategy) ---")
for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
    st = [t for t in trades_out if t[7] == sid]
    net = sum(t[4] for t in st)
    wins = sum(1 for t in st if t[4] > 0)
    print(f"  {sid:10s} trades={len(st):2d}  wins={wins:2d}  net {net:10.2f}")
print(f"\nTOTAL closed = {len(trades_out)}  net P&L = {total_net:9.2f}  "
      f"(entry unresolved {n_none}, exit unresolved {n_exit_none})")

print("\nCHECK:")
for name, c in chk:
    print(f"  {'PASS' if c else 'FAIL'}  {name}")
print(f"RESULT: {'ALL CHECKS PASSED' if all(c for _, c in chk) else 'FAILED'}")
print("[DONE]")