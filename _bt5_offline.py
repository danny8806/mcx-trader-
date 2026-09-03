"""OFFLINE LAST-5-DAYS BACKTEST — real engine, exact liveflow CSVs, all 4 strategies.

Drop-in substitute for _bt5_backtest.py when the Dhan token is expired: the
5m rows are read from the nifty project's data_mcx CSVs (the SAME Dhan data the
liveflow_4strats reference consumes), converted to Dhan REST row format
[epoch, o, h, l, c, v], then replayed through the REAL TradingEngine.

This makes the engine-vs-liveflow comparison perfectly apples-to-apples (same
underlying bars, same 08-24..08-28 window).

Usage:  python _bt5_offline.py
"""
import os
import sys
import time
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
START, STOP = "2026-08-24", "2026-08-28"
CSV_DIR = Path(r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx")
CSVS = {
    "GOLDM":   "gold/GOLDM_04Sep2026_5m.csv",
    "SILVERM": "silver/SILVERM_30Nov2026_5m.csv",
}
RUN_ROOT = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"bt5off_{START}_{STOP}"
FTF = {"gold_01": "5m", "gold_02": "15m", "silver_01": "15m", "silver_02": "5m"}

import full_simulator as sim
from full_simulator import (
    LIVE_INSTRUMENTS, LIVE_STRATEGIES, _TF_RANK, ist,
    build_bars, write_config, build_engine, teardown,
    indep_gross, indep_charges,
)
from core.market_status import MarketState
from core.trade_close import TradeCloseManager


def load_rows(name):
    """CSV -> Dhan REST row format [epoch_ist, open, high, low, close, volume]."""
    import csv as _csv
    out = []
    with open(CSV_DIR / CSVS[name], encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            d = r["datetime"][:10]
            if d < START or d > STOP:
                continue
            naive = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
            epoch = naive.replace(tzinfo=IST).timestamp()
            out.append([epoch,
                        float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"]),
                        float(r["volume"])])
    out.sort(key=lambda r: r[0])
    return out


# ── 1. Data + bars ──
if RUN_ROOT.exists():
    shutil.rmtree(RUN_ROOT)
RUN_ROOT.mkdir(parents=True, exist_ok=True)

stream_all, bars_fast = [], {}
for name in LIVE_INSTRUMENTS:
    rows = load_rows(name)
    b5, b15, b1h = build_bars(name, rows, keep_partial=True)
    bars_fast[name] = {"5m": b5, "15m": b15}
    stream_all += b5 + b15 + b1h
    print(f"[Data] {name}: {len(rows)} x5m | {len(b15)} x15m | {len(b1h)} x1h", flush=True)
if not stream_all:
    print("FATAL: no bars loaded"); sys.exit(1)

stream_by_day = {}
for bar in stream_all:
    stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
for d in stream_by_day:
    stream_by_day[d].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))
print(f"[Data] {len(stream_all)} bars across {len(stream_by_day)} trading days", flush=True)

# ── 2. Real engine ──
import trading_engine as te
te.DhanDataAdapter = sim.ReplayDataAdapter

cfg = write_config(RUN_ROOT)
engine, persistence = build_engine(cfg)
engine.tick_signal_processing = False  # bar-model replay: no tick breakout/SL
for _st in engine.strategies.values():
    _st.pending_timeout_bars = 10**9  # liveflow parity: pendings never expire
engine._trade_close_manager = TradeCloseManager(
    position_manager=engine.position_manager, pnl_engines=engine.pnl_engines,
    account_engines=engine.account_engines, global_account=engine.account_engine,
    risk_engine=engine.risk_engine, persistence=engine._persistence,
    event_store=engine.event_store, telegram=engine.telegram,
    event_callback=engine._event_callback, trade_ledger=engine.trade_ledger,
)

# ── 3. Replay ──
from core.market_status import EngineStatus
engine._running = True
engine.market_status.set_engine_status(EngineStatus.READY)
ws = engine.data_adapter.ws

import strategies.base_dema_strategy as _bds


def live_tick(instrument, ltp, ts):
    ws._last_tick_time = time.time()
    engine._on_tick({"instrument": instrument, "ltp": ltp, "event_timestamp": ts})


t0 = time.time()
for day, bars in sorted(stream_by_day.items()):
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    for bar in bars:
        strat = None
        for s in engine.strategies.values():
            if s.instrument == bar.instrument and s.fast_timeframe == bar.timeframe:
                strat = s
        if strat is not None:
            engine.execution_engine.update_price(bar.instrument, bar.close)
        engine._on_bar_closed(bar)
        live_tick(bar.instrument, bar.close, bar.end_ts)
print(f"[Replay] done in {time.time()-t0:.1f}s", flush=True)

# ── 4. Reconciliation + independent P&L ──
from reconciliation.engine import ReconciliationEngine
recon = ReconciliationEngine(
    persistence=engine._persistence, position_manager=engine.position_manager,
    pnl_engines=engine.pnl_engines, account_engines=engine.account_engines,
    strategies=engine.strategies, order_manager=engine.order_manager,
)
rr = recon.reconcile(phase="live")
closed = engine.trade_ledger.get_closed_trades()
chk = [("reconciliation", rr.is_consistent)]
for tr in closed:
    mult = LIVE_INSTRUMENTS[tr.instrument]["multiplier"]
    ref_g = indep_gross(tr.side, tr.average_entry_price, tr.average_exit_price,
                        tr.filled_quantity or tr.entry_quantity, mult)
    ref_f = indep_charges(tr.average_entry_price, tr.average_exit_price,
                          tr.filled_quantity or tr.entry_quantity, mult, tr.side)
    ref_n = round(ref_g - ref_f, 2)
    chk.append((f"pnl {tr.strategy_id}/{tr.side}", abs(tr.net_pnl - ref_n) < 1.0))

DB = RUN_ROOT / "data" / "db" / "analytics.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT strategy_id, side, entry_price, average_exit_price, initial_stop, "
    "net_pnl, gross_pnl, fees, exit_reason, signal_time "
    "FROM trades_analytics ORDER BY signal_time").fetchall()
con.close()

# ── strategy event audit ──
print("\n=== STRATEGY EVENT AUDIT ===")
for sid, s in engine.strategies.items():
    from collections import Counter
    cnt = Counter(e["event_type"] for e in s._events)
    trig_h = [e for e in s._events if e["event_type"] == "ENTRY_EXECUTED"]
    print(f"  {sid:9s} final={s.position_side or 'FLAT':5s} events={dict(cnt)} "
          f"| entries={len(trig_h)}")

teardown(engine, persistence)

# ── 5. Report ──
total_net = 0.0
by_strat = {s: [] for s in ("gold_01", "gold_02", "silver_01", "silver_02")}
print(f"\n=== ENGINE CLOSED TRADES  {START}..{STOP}  (offline CSVs, aligned fill model) ===")
for r in rows:
    sid = r["strategy_id"]
    reason = r["exit_reason"] or ""
    rk = "stop" if reason == "stop_loss_hit" else (reason or "-")[:10]
    net = round(r["net_pnl"] or 0.0, 2)
    total_net += net
    sig_t = ist(r["signal_time"] or 0).strftime("%m-%d %H:%M")
    out_px = r["average_exit_price"]
    out_s = f"{out_px:8,.0f}" if out_px is not None else "    OPEN "
    sl_s = f"{r['initial_stop']:8,.0f}" if r["initial_stop"] is not None else "     NaN"
    print(f"  {sig_t} {sid:9s} {r['side']:5s} in {r['entry_price']:8,.0f} "
          f"sl {sl_s} out {out_s} "
          f"{rk:9s} net {net:9.2f}")
    by_strat.setdefault(sid, []).append((r["entry_price"], out_px,
                                         r["initial_stop"], net, r["side"], rk,
                                         (r["signal_time"] or 0)))
print(f"\n--- TOTALS (by strategy) ---")
for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
    st = by_strat.get(sid, [])
    net = sum(t[3] for t in st)
    wins = sum(1 for t in st if t[3] > 0)
    print(f"  {sid:10s} trades={len(st):2d}  wins={wins:2d}  net {net:10.2f}")
print(f"\nTOTAL closed = {len(rows)}  net P&L = {total_net:9.2f}")

print("\nCHECK:")
for name, c in chk:
    if not c:
        print(f"  FAIL  {name}")
print(f"RESULT: {'ALL CHECKS PASSED' if all(c for _, c in chk) else 'FAILED'}")
print(f"run artifacts: {RUN_ROOT}")
print("[DONE]")