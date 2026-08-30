"""FAST LIVE-LIKE FLOW CHECK — every component input -> output, end to end.

Drives the REAL production stack (same seams as main.py live run) with
controlled inputs and asserts each component's output: CandleFetcher,
DEMAATR, BacktestStyleHTFEngine, MarketStatus, RiskEngine, AccountEngine,
PaperExecutionEngine, OrderManager, TradingEngine._process_signal/_on_fill,
TradeCloseManager, PersistenceManager, ReconciliationEngine, and the DB
trail (orders / fills / trades / trades_analytics).

Explicit scenario coverage (the issues we fixed):
  S1  entry fill -> position open -> DB write
  S2a margin blocking at the RISK stage (order rejected, no ghost, no DB)
  S2b margin blocking at the FILL stage (no position, strategy reset)
  S3  safe mode: entry BLOCKED but exit EXECUTES -> trade saved to DB
  S4  market not allowed: entry BLOCKED but exit EXECUTES
  S5  opposite (reversal) trade placement: exit at next open + opposite entry
  S6  EOD force close -> DB trade saved

Usage:  python _live_flow_check.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
RUN_BASE = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / "liveflow_check"

import full_simulator as sim
from full_simulator import (LIVE_INSTRUMENTS, LIVE_STRATEGIES, write_config,
                            build_engine, teardown, indep_gross, indep_charges)

from strategies.types import Signal, SignalType, StrategyState
from execution.paper_broker import Fill
from core.market_status import MarketState, EngineStatus
from core.timeframe_engine import Bar, BarState
from core.trade_close import TradeCloseManager

CHECKS = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}: {detail}")


def readonly_sql(db_path, query, *params):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return [tuple(r) for r in con.execute(query, params).fetchall()]
    finally:
        con.close()


def make_engine(root: Path):
    cfg = write_config(root)
    engine, persistence = build_engine(cfg)
    engine._running = True
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    engine.market_status.update_data_status(True, time.time())
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status._eod_close_done_today = False
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager, pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines, global_account=engine.account_engine,
        risk_engine=engine.risk_engine, persistence=engine._persistence,
        event_store=engine.event_store, telegram=engine.telegram,
        event_callback=engine._event_callback, trade_ledger=engine.trade_ledger,
    )
    return engine, persistence


def arm_entry(engine, sid, side, price, stop, state):
    strat = engine.strategies[sid]
    strat.state = state
    strat.position_side = side
    strat.stop_price = stop
    strat.pending_entry = None
    strat.same_bar_stop = None
    strat.pending_exit_at_open = False
    return strat


def entry_signal(ts, price, stop, sid="gold_01", instr="GOLDM", kind=SignalType.LONG, side="LONG", meta=None, qty=1):
    return Signal(kind, instr, sid, ts, price, stop, qty, side=side, metadata=meta)


def run_blocked(engine, sid, state, ts, price, stop):
    strat = arm_entry(engine, sid, "LONG" if state in (StrategyState.PENDING_LONG, StrategyState.LONG_POSITION) else "SHORT",
                      price, stop, state)
    engine.execution_engine.update_price("GOLDM", price)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine._process_signal(entry_signal(ts, price, stop, sid=sid))
    return strat, buf.getvalue()


def reconcile(engine):
    from reconciliation.engine import ReconciliationEngine
    recon = ReconciliationEngine(
        persistence=engine._persistence, position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines, account_engines=engine.account_engines,
        strategies=engine.strategies, order_manager=engine.order_manager,
    )
    return recon.reconcile(phase="live")


def counts(db):
    return len(readonly_sql(db, "SELECT 1 FROM orders")), \
           len(readonly_sql(db, "SELECT 1 FROM fills")), \
           len(readonly_sql(db, "SELECT 1 FROM trades"))


def ana_db(engine) -> Path:
    """analytics.db path (trades_analytics / events) — separate from trading.db."""
    ledger = getattr(engine, "trade_ledger", None)
    if ledger is not None:
        p = getattr(ledger, "_db_path", None)
        if p:
            return Path(str(p))
    return engine._persistence.db_path.parent / "analytics.db"


# ═══════════════════════════════════════════════════════════════════
print("=" * 96)
print("PART 1 — COMPONENT INPUT → OUTPUT (fast probes, real objects)")
print("=" * 96)

# 1. CandleFetcher
from core.candle_fetcher import CandleFetcher
cf = CandleFetcher(data_adapter=None, instruments={}, on_candle_closed=None)
t = datetime(2026, 8, 28, 9, 0, tzinfo=IST).timestamp()
bar = cf._create_bar("GOLDM", "5m", [int(t), 100.0, 103.0, 99.0, 102.0, 10], datetime.fromtimestamp(t, IST).replace(tzinfo=None), 5)
ok("CandleFetcher: 5m input -> Bar", bar is not None and bar.open == 100.0 and bar.high == 103.0
   and bar.low == 99.0 and bar.close == 102.0 and bar.timeframe == "5m",
   f"o/h/l/c={bar.open}/{bar.high}/{bar.low}/{bar.close}")

# 2. DEMAATR indicator
from indicators.dema_atr import DEMAATR
ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
vals = []
p = 100.0
for i in range(30):
    p += (i % 7) - 3
    v = ind.update(p, p + 2.0, p - 2.0, p + 0.5)
    if v is not None:
        vals.append(v)
ok("DEMAATR: 30 bars -> initialized + finite value",
   ind.initialized and ind.value is not None and abs(ind.value) > 0,
   f"count={ind._count} initialized={ind.initialized} value={ind.value:.3f}")
before_reset = ind.value
ind.reset()
ok("DEMAATR: reset() clears state", ind.initialized is False and ind.value is None,
   f"before={before_reset:.3f} after_reset={ind.value}")

# 3. HTF engine
from htf.backtest_style_htf import BacktestStyleHTFEngine
htf = BacktestStyleHTFEngine()
htf.register("GOLDM", "1h")
htf.register("GOLDM", "15m")
hb_start = int(t)
for i in range(12):
    c = 100.0 + i * 0.5
    htf.on_htf_bar_closed(Bar("GOLDM", "1h", hb_start + i * 3600, hb_start + (i + 1) * 3600,
                             c - 1.0, c + 2.0, c - 2.0, c, 100, BarState.CLOSED))
fb = Bar("GOLDM", "5m", hb_start + 12 * 3600, hb_start + 12 * 3600 + 300,
         106.0, 107.0, 105.0, 106.5, 5, BarState.CLOSED)
mapped = htf.map_to_fast_bar(fb, "5m")
ok("HTFEngine: 1h bars -> map_to_fast_bar finite", mapped.htf_confirmed and mapped.htf_value is not None,
   f"htf_value={mapped.htf_value} prev={mapped.prev_htf_value}")
htf.reset_instrument("GOLDM")
ok("HTFEngine: reset_instrument clears", not htf._engines.get("GOLDM"), "engine cleared")

# 4. MarketStatus
from core.market_status import MarketStatus
ms = MarketStatus()
ms.set_engine_status(EngineStatus.READY)
ok("MarketStatus: non-trading by default", ms.is_trading_allowed is False, f"state={ms.state}")
ms.set_engine_status(EngineStatus.TRADING)
ms.update_data_status(True, time.time())
ms.force_state(MarketState.LIVE_TRADING)
ok("MarketStatus: TRADING+CONNECTED+LIVE_TRADING -> allowed", ms.is_trading_allowed is True, f"state={ms.state}")
ms.force_state(MarketState.AFTER_MARKET)
ok("MarketStatus: AFTER_MARKET -> blocked", ms.is_trading_allowed is False, f"state={ms.state}")

# 5. AccountEngine margin
from portfolio.account import AccountEngine
acct = AccountEngine(starting_capital=300_000.0)
bl = acct.block_margin(200_000.0)
used = acct.used_margin
release_ok = acct.release_margin(200_000.0) is None and acct.used_margin == 0.0
ok("Account: block/release margin", bl and used == 200_000.0 and release_ok,
   f"blocked=True used={used} released>0 used={acct.used_margin}")

# 6. RiskEngine margin / loss / positions
from core.risk_engine import RiskEngine
from unittest.mock import MagicMock
risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8, max_daily_loss=1000.0)
sig_mock = MagicMock()
a1, r1 = risk.check_order(sig_mock, 0, 0, 500_000, 50_000, 300_000)
a2, r2 = risk.check_order(sig_mock, 0, 0, 10_000, 50_000, 300_000)
a3, r3 = risk.check_order(sig_mock, 0, 1, 500_000, 50_000, 300_000)
ok("Risk: margin ok / insufficient / pos limit",
   a1 and (not a2 and r2 == "insufficient_margin") and (not a3 and r3 == "max_positions_per_strategy_reached"),
   f"ok={a1} insufficient={r2} pos_limit={r3}")

# 7. PaperExecutionEngine order -> fill (latency + slippage)
from execution.paper_broker import PaperExecutionEngine
from execution.order_manager import OrderManager
pe = PaperExecutionEngine(latency_ms=15, slippage_ticks=1, partial_fill_probability=0.0)
pe.update_price("GOLDM", 162_000.0)
t0 = time.monotonic()
om = OrderManager(pe)
sig7 = Signal(SignalType.LONG, "GOLDM", "gold_01", time.time(), 162_000.0, 161_800.0, 1, side="LONG")
order = om.submit_signal(sig7, multiplier=10.0)
elapsed = time.monotonic() - t0
fils = om.drain_fills()
ok("PaperBroker: MARKET order -> fill w/ latency+slippage",
   order is not None and order.state.name == "FILLED" and order.order_type == "MARKET"
   and len(fils) == 1 and fils[0].price == 162_001.0 and elapsed >= 0.012,
   f"state={order.state.name} fill@{fils[0].price if fils else None:.1f} latency={elapsed*1000:.0f}ms")

# 8. PersistenceManager round trip
tmp_ps = RUN_BASE / "p1"
tmp_ps.mkdir(parents=True, exist_ok=True)
from persistence.manager import PersistenceManager
ps = PersistenceManager(str(tmp_ps / "state.json"), str(tmp_ps / "trading.db"))
ps.save_state({"strategies": {"gold_01": {"state": "long_position"}}, "probe": 42})
loaded = ps.load_state()
ps.close()
ok("Persistence: snapshot save/load round trip",
   loaded and loaded.get("probe") == 42 and loaded.get("strategies", {}).get("gold_01", {}).get("state") == "long_position",
   f"probe={loaded and loaded.get('probe')}")

# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 96)
print("PART 2 — ENGINE INTEGRATION (REAL pipeline, direct market inputs)")
print("=" * 96)


def db_evidence(engine, label):
    db = engine._persistence.db_path
    print(f"\n  --- DB evidence after {label} ({engine._persistence.db_path.name}) ---")
    for tbl, q in [("orders", "order_id, side, order_type, state, quantity, average_fill_price"),
                   ("fills", "fill_id, order_id, side, quantity, price, strategy_id"),
                   ("trades", "trade_id, strategy_id, side, status")]:
        rows = readonly_sql(db, f"SELECT {q} FROM {tbl}")
        print(f"  {tbl}: {len(rows)}")
        for r in rows[:8]:
            print("    ", r)


# ── S1: entry fill -> position open -> DB ─────────────────────────
print("\n### S1  entry (MARKET) -> fill -> position open -> DB")
r1 = RUN_BASE / "s1"
if r1.exists():
    shutil.rmtree(r1)
r1.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r1)
expl = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.execution_engine.update_price("GOLDM", 162_000.0)
eng._process_signal(entry_signal(time.time(), 162_000.0, 161_800.0))
opens = list(eng.position_manager.open_positions)
pos = opens[0] if opens else None
ok("S1 position opened", len(opens) == 1 and pos.side.value == "LONG" and abs(pos.average_entry - 162000) < 0.01,
   f"side={pos.side.value} entry={pos.average_entry if pos else None} margin={pos.margin if pos else None:.0f}")
ok("S1 strategy state", expl.state == StrategyState.LONG_POSITION and expl.position_side == "LONG"
   and expl.stop_price == 161_800.0,
   f"state={expl.state} side={expl.position_side} stop={expl.stop_price}")
db1 = eng._persistence.db_path
o, f_, t_ = counts(db1)
ok("S1 DB rows (order/fill/trade)", o == 1 and f_ == 1 and t_ == 0,
   f"orders={o} fills={f_} trades={t_} (trading.db row only written at close)")
ana1 = ana_db(eng)
ta = readonly_sql(ana1, "SELECT strategy_id, side, entry_price, initial_stop, multiplier, status FROM trades_analytics")
ok("S1 trades_analytics OPEN row", len(ta) == 1 and ta[0][1] == "LONG" and abs(ta[0][2] - 162000) < 0.01
   and ta[0][5] == "OPEN",
   f"row={ta}")
ok("S1 margin blocked", abs(eng.account_engines["gold_01"].used_margin - pos.margin) < 1.0,
   f"used_margin={eng.account_engines['gold_01'].used_margin:.0f} pos_margin={pos.margin:.0f}")
db_evidence(eng, "S1")
recon = reconcile(eng)
ok("S1 reconciliation consistent", recon.is_consistent, recon.summary().splitlines()[0] if recon.summary() else "")
teardown(eng, pers)

# ── S2a: margin blocking at RISK stage ─────────────────────────────
print("\n### S2a  margin blocking — RISK stage (order rejected, ghost cleared, no DB)")
r2 = RUN_BASE / "s2a"
if r2.exists():
    shutil.rmtree(r2)
r2.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r2)
eng.account_engines["gold_01"].realized_pnl = -299_000.0   # equity=1000 -> margin check fails
pre = counts(eng._persistence.db_path)
strat, out = run_blocked(eng, "gold_01", StrategyState.LONG_POSITION, time.time(), 162_000.0, 161_800.0)
post = counts(eng._persistence.db_path)
ok("S2a order rejected (insufficient_margin)", "Order rejected: insufficient_margin" in out,
   [l for l in out.splitlines() if "Risk" in l or "rejected" in l][:1])
ok("S2a no position opened", len(list(eng.position_manager.open_positions)) == 0, "position_manager empty")
ok("S2a strategy reset to FLAT (no ghost)", strat.state == StrategyState.FLAT
   and strat.position_side is None and strat.same_bar_stop is None,
   f"state={strat.state} side={strat.position_side} sbs={strat.same_bar_stop}")
ok("S2a no DB rows added", post == pre, f"pre={pre} post={post}")
teardown(eng, pers)

# ── S2b: margin blocking at FILL stage ─────────────────────────────
print("\n### S2b  margin blocking — FILL stage (_on_fill, ghost cleared)")
r3 = RUN_BASE / "s2b"
if r3.exists():
    shutil.rmtree(r3)
r3.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r3)
strat = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.account_engines["gold_01"].realized_pnl = -299_000.0  # available ~1000 < margin ~146k
fill = Fill("fill_x", "o_x", "GOLDM", "BUY", 1, 162_000.0, time.time(), "gold_01", 10.0)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    eng._on_fill(fill)
out = buf.getvalue()
ok("S2b fill-level MARGIN BLOCKED", "MARGIN BLOCKED" in out,
   [l for l in out.splitlines() if "MARGIN" in l][:1])
ok("S2b no position opened", len(list(eng.position_manager.open_positions)) == 0, "position_manager empty")
ok("S2b strategy reset to FLAT (ghost cleared)", strat.state == StrategyState.FLAT
   and strat.position_side is None and strat.stop_price is None,
   f"state={strat.state} side={strat.position_side}")
ok("S2b no DB trade row", counts(eng._persistence.db_path)[2] == 0, f"trades={counts(eng._persistence.db_path)[2]}")
teardown(eng, pers)

# ── S3: safe mode — entry BLOCKED, exit EXECUTES, DB saved ─────────
print("\n### S3  safe mode: entry blocked / exit executes + trade saved to DB")
r4 = RUN_BASE / "s3"
if r4.exists():
    shutil.rmtree(r4)
r4.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r4)
# open a LONG position first
expl = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.execution_engine.update_price("GOLDM", 162_000.0)
eng._process_signal(entry_signal(time.time(), 162_000.0, 161_800.0))
eng.safe_mode.enter_safe_mode("unit_test", "simulated failure")
# try a NEW entry on silver_02 while safe mode is active
strat_blk, out_blk = run_blocked(eng, "silver_02", StrategyState.LONG_POSITION, time.time() + 1, 252_000.0, 251_500.0)
ok("S3 entry BLOCKED by safe mode", "BLOCKED by safe mode" in out_blk,
   [l for l in out_blk.splitlines() if "BLOCKED" in l][:1])
ok("S3 blocked entry left no position", len(list(eng.position_manager.get_positions_by_strategy("silver_02"))) == 0,
   "silver_02 empty")
ok("S3 blocked strategy reset", strat_blk.state == StrategyState.FLAT, f"state={strat_blk.state}")
# EXIT the long while safe mode is active
expl.last_exit_reason = "stop_loss_hit"
eng.execution_engine.update_price("GOLDM", 161_500.0)
exit_sig = Signal(SignalType.SHORT, "GOLDM", "gold_01", time.time() + 2, 161_500.0, 0.0, 1,
                  side="SHORT", metadata={"exit": True, "exit_reason": "stop_loss_hit", "fill_price": 161_500.0})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    eng._process_signal(exit_sig)
out_ex = buf.getvalue()
ok("S3 exit EXECUTED in safe mode", len(list(eng.position_manager.open_positions)) == 0,
   f"open_positions={len(list(eng.position_manager.open_positions))}")
db4 = eng._persistence.db_path
trow = readonly_sql(db4, "SELECT strategy_id, side, entry_price, exit_price, exit_reason, gross_pnl, charges, net_pnl, status FROM trades")
ana4 = ana_db(eng)
tri = readonly_sql(ana4, "SELECT strategy_id, side, entry_price, average_exit_price, exit_reason, gross_pnl, fees, net_pnl, r_status FROM trades_analytics")
ok("S3 DB trade closed + details saved", len(trow) == 1 and trow[0][8] == "closed"
   and trow[0][4] == "stop_loss_hit" and abs(trow[0][2] - 162000) < 0.01 and abs(trow[0][3] - 161500) < 0.01,
   f"trade={trow}")
ref_g = indep_gross("LONG", 162000.0, 161500.0, 1, 10.0)
ok("S3 analytics row gross/fees/net vs independent", len(tri) == 1
   and abs(tri[0][5] - ref_g) < 1.0 and tri[0][6] and abs(tri[0][7] - (ref_g - tri[0][6])) < 1.0,
   f"analytics={tri[0][5:8]}")
ok("S3 margin released", abs(eng.account_engines["gold_01"].used_margin) < 1.0
   and abs(eng.account_engines["gold_01"].realized_pnl - (ref_g - tri[0][6])) < 1.0,
   f"used_margin={eng.account_engines['gold_01'].used_margin:.2f} realized={eng.account_engines['gold_01'].realized_pnl:.2f} ref_net={ref_g - tri[0][6]:.2f}")
ok("S3 reconciliation consistent after safe-mode exit", reconcile(eng).is_consistent, "recon")
teardown(eng, pers)

# ── S4: market not allowed — entry BLOCKED, exit EXECUTES ──────────
print("\n### S4  market not allowed: entry blocked / exit executes")
r5 = RUN_BASE / "s4"
if r5.exists():
    shutil.rmtree(r5)
r5.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r5)
eng.market_status.force_state(MarketState.AFTER_MARKET)
strat_blk, out_blk = run_blocked(eng, "gold_01", StrategyState.LONG_POSITION, time.time(), 162_000.0, 161_800.0)
ok("S4 entry BLOCKED by market state", "BLOCKED by market state" in out_blk
   and len(list(eng.position_manager.open_positions)) == 0,
   [l for l in out_blk.splitlines() if "BLOCKED" in l][:1])
ok("S4 blocked strategy reset", strat_blk.state == StrategyState.FLAT, f"state={strat_blk.state}")
# now open during live session, then close in after-market (exits must work)
eng.market_status.force_state(MarketState.LIVE_TRADING)
expl = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.execution_engine.update_price("GOLDM", 162_000.0)
eng._process_signal(entry_signal(time.time() + 1, 162_000.0, 161_800.0))
eng.market_status.force_state(MarketState.AFTER_MARKET)
expl.last_exit_reason = "stop_loss_hit"
eng.execution_engine.update_price("GOLDM", 161_600.0)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    eng._process_signal(Signal(SignalType.SHORT, "GOLDM", "gold_01", time.time() + 2, 161_600.0, 0.0, 1,
                               side="SHORT", metadata={"exit": True, "exit_reason": "stop_loss_hit", "fill_price": 161_600.0}))
ok("S4 exit EXECUTED after market", len(list(eng.position_manager.open_positions)) == 0, "closed")
db5 = eng._persistence.db_path
t5 = readonly_sql(db5, "SELECT exit_reason, status FROM trades")
ok("S4 DB trade saved w/ reason", len(t5) == 1 and t5[0][0] == "stop_loss_hit" and t5[0][1] == "closed", f"{t5}")
teardown(eng, pers)

# ── S5: opposite (reversal) trade placing ──────────────────────────
print("\n### S5  opposite/reversal placement: exit@next-open + opposite entry@trigger")
r6 = RUN_BASE / "s5"
if r6.exists():
    shutil.rmtree(r6)
r6.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r6)
expl = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.execution_engine.update_price("GOLDM", 162_000.0)
eng._process_signal(entry_signal(time.time(), 162_000.0, 161_800.0))
seq = [eng.position_manager.get_positions_by_strategy("gold_01")[0].side.value]
ts = time.time() + 1
# opposite SHORT signal on the crossing bar
sig_r = expl._create_reversal_signal("SHORT", close=162_000.0, high=162_100.0, low=161_900.0,
                                     timestamp=ts, prev_high=162_050.0, prev_low=161_950.0)
ok("S5 reversal armed (None returned, exit scheduled)", sig_r is None and expl.pending_exit_at_open
   and expl.pending_exit_reason == "short_reversal" and expl.pending_entry.side == "SHORT"
   and expl.pending_entry.trigger_price == 161_900.0,
   f"pending_exit={expl.pending_exit_at_open} reason={expl.pending_exit_reason} trig={expl.pending_entry.trigger_price}")
# next fast bar: engine consumes the deferred exit at the OPEN
bar_next = Bar("GOLDM", "5m", int(ts), int(ts) + 300, 161_980.0, 162_020.0, 161_930.0, 161_960.0, 10, BarState.CLOSED)
eng.execution_engine.update_price("GOLDM", bar_next.open)
consumed = eng._process_deferred_exit(expl, bar_next)
seq.append("FLAT" if not eng.position_manager.get_positions_by_strategy("gold_01") else eng.position_manager.get_positions_by_strategy("gold_01")[0].side.value)
db6 = eng._persistence.db_path
cl = readonly_sql(db6, "SELECT side, entry_price, exit_price, exit_reason, net_pnl, status FROM trades ORDER BY rowid DESC LIMIT 1")
ok("S5 deferred exit at next bar OPEN (long_reversal)",
   consumed and abs(cl[0][2] - bar_next.open) < 0.01 and cl[0][3] == "short_reversal" and cl[0][5] == "closed",
   f"{cl}")
ana6 = ana_db(eng)
# later bar crosses the SHORT trigger 161900 -> opposite entry fills at trigger
bar_trig = Bar("GOLDM", "5m", int(ts) + 301, int(ts) + 601, 161_910.0, 161_915.0, 161_880.0, 161_890.0, 10, BarState.CLOSED)
eng.execution_engine.update_price("GOLDM", bar_trig.low)
sig_entry = expl._check_pending_entry(bar_trig)
ok("S5 opposite SHORT breakout triggered at 161900", sig_entry is not None
   and sig_entry.signal_type == SignalType.SHORT and abs(sig_entry.trigger_price - 161_900.0) < 0.01,
   f"trig={sig_entry.trigger_price if sig_entry else None}")
if sig_entry:
    eng._process_signal(sig_entry)
pos6 = eng.position_manager.get_positions_by_strategy("gold_01")
seq.append(pos6[0].side.value if pos6 else "NONE")
ok("S5 opposite position placed (SHORT @161900)", len(pos6) == 1 and pos6[0].side.value == "SHORT"
   and abs(pos6[0].average_entry - 161_900.0) < 0.01,
   f"side={pos6[0].side.value if pos6 else '?'} entry={pos6[0].average_entry if pos6 else '?'}")
tr6 = readonly_sql(ana6, "SELECT strategy_id, side, entry_price, initial_stop, multiplier FROM trades_analytics ORDER BY rowid")
ok("S5 2 DB trades (closed LONG + open SHORT)", len(tr6) == 2
   and tr6[0][1] == "LONG" and tr6[1][1] == "SHORT" and abs(tr6[1][3] - 162_100.0) < 0.01,
   f"trades={len(tr6)} {tr6}")
ok("S5 sequence LONG -> FLAT -> SHORT", seq == ["LONG", "FLAT", "SHORT"], f"seq={seq}")
ok("S5 reconciliation consistent", reconcile(eng).is_consistent, "recon")
db_evidence(eng, "S5")
teardown(eng, pers)

# ── S6: EOD force close ────────────────────────────────────────────
print("\n### S6  EOD force close -> DB trade saved")
r7 = RUN_BASE / "s6"
if r7.exists():
    shutil.rmtree(r7)
r7.mkdir(parents=True, exist_ok=True)
eng, pers = make_engine(r7)
expl = arm_entry(eng, "gold_01", "LONG", 162_000.0, 161_800.0, StrategyState.LONG_POSITION)
eng.execution_engine.update_price("GOLDM", 162_500.0)
eng._process_signal(entry_signal(time.time(), 162_000.0, 161_800.0))
eng.market_status._eod_close_done_today = False
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    eng._execute_eod_close()
out = buf.getvalue()
db7 = eng._persistence.db_path
t7 = readonly_sql(db7, "SELECT entry_price, exit_price, exit_reason, status, net_pnl FROM trades")
ok("S6 EOD position closed", len(list(eng.position_manager.open_positions)) == 0, "open=0")
ok("S6 EOD trade saved in DB (exit_reason=eod_close)", len(t7) == 1 and t7[0][2] == "eod_close"
   and t7[0][3] == "closed" and t7[0][4] is not None,
   f"{t7}")
ok("S6 margin released after EOD", abs(eng.account_engines["gold_01"].used_margin) < 1.0,
   f"used_margin={eng.account_engines['gold_01'].used_margin:.2f}")
teardown(eng, pers)

# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 96)
print("RESULT")
print("=" * 96)
failed = 0
for name, cond, detail in CHECKS:
    if not cond:
        failed += 1
        print(f"  FAIL  {name}: {detail}")
print(f"  {len(CHECKS) - failed}/{len(CHECKS)} PASSED, {failed} FAILED")
print(f"  RESULT: {'ALL CHECKS PASSED' if failed == 0 else f'{failed} FAILED'}")
print(f"  run artifacts: {RUN_BASE}")
sys.exit(1 if failed else 0)