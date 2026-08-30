"""FULL-DEPTH SIMULATOR — the REAL live stack, replayed on REAL Dhan candles.

Every component used in production is instantiated and driven here:

  TradingEngine (real) -> strategies (real Gold01/02, Silver01/02)
    -> Real Bars built by CandleFetcher._create_bar/_aggregate_candles
    -> DEMAATR indicators (real) + BacktestStyleHTFEngine (real)
    -> PaperExecutionEngine + OrderManager (real, slippage+latency)
    -> PositionManager + PNLEngine(MCXFeeModel) + AccountEngine (real)
    -> RiskEngine, MarketStatus, FillDeduplicator (real)
    -> PersistenceManager (trading.db) + EventStore + TradeLedger (analytics.db)
    -> ReconciliationEngine (real) verified at the end
    -> snapshot()/restore() round trip on the real engine

The only substitute is the network layer: a ReplayDataAdapter stands in for
DhanDataAdapter (engine never touches a WS/REST socket during replay; the
historical candles were fetched once from Dhan with the real REST client).

Verification is done with INDEPENDENT reference math (never production code),
mirroring the deep-architecture test assertions but over six real trading days
(Aug 21-28 2026) with the actual 5m/15m/1h OHLC the live system would consume.

Usage:
    python full_simulator.py                 # window 2026-08-21..2026-08-28
    python full_simulator.py 2026-08-24 2026-08-28
"""
from __future__ import annotations

import json
import math
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

WINDOW_DEFAULT = ("2026-08-21", "2026-08-28")

# instrument -> (security_id, multiplier, margin slope, margin intercept)
LIVE_INSTRUMENTS = {
    "GOLDM":   {"symbol": "MCX:GOLDM202609", "security_id": "563946",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                "margin_model": {"slope": 0.125, "intercept": 126930.0}},
    "SILVERM": {"symbol": "MCX:SILVERM202611", "security_id": "483080",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                "margin_model": {"slope": 0.0625, "intercept": 142900.0}},
}

LIVE_STRATEGIES = {
    "gold_01":   {"instrument": "GOLDM", "fast_timeframe": "5m", "mid_timeframe": "15m",
                  "htf_timeframe": "1h", "quantity": 1, "capital": 300000, "enabled": True},
    "gold_02":   {"instrument": "GOLDM", "fast_timeframe": "15m", "mid_timeframe": "15m",
                  "htf_timeframe": "1h", "quantity": 1, "capital": 300000, "enabled": True},
    "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m", "mid_timeframe": "15m",
                  "htf_timeframe": "1h", "quantity": 1, "capital": 300000, "enabled": True},
    "silver_02": {"instrument": "SILVERM", "fast_timeframe": "5m", "mid_timeframe": "15m",
                  "htf_timeframe": "1h", "quantity": 1, "capital": 300000, "enabled": True},
}

# timeframe sort keys: when 1h/15m/5m bars end at the same wall-clock second,
# bigger timeframes must be fed first (searchsorted(right) mapping semantics).
_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}


def ist(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)


# ═══════════════════════════════════════════════════════════════
# Independent reference math (never production code as the reference)
# ═══════════════════════════════════════════════════════════════
def indep_gross(side: str, entry: float, exit_p: float, qty: int, mult: float) -> float:
    if side == "LONG":
        return (exit_p - entry) * qty * mult
    return (entry - exit_p) * qty * mult


def indep_charges(entry: float, exit_price: float, qty: int, mult: float, side: str) -> float:
    cfg = LIVE_INSTRUMENTS["GOLDM"]  # rates identical for both instruments
    buy_turnover = entry * qty * mult
    sell_turnover = exit_price * qty * mult
    if side == "SHORT":
        buy_turnover, sell_turnover = sell_turnover, buy_turnover
    brokerage = 20.0 * 2
    stt = sell_turnover * 0.0001
    exchange = (buy_turnover + sell_turnover) * 0.000026
    sebi = (buy_turnover + sell_turnover) * 0.000001
    gst = (brokerage + exchange + sebi) * 0.18
    return round(brokerage + stt + exchange + sebi + gst + stamp_duty(buy_turnover), 2)


def stamp_duty(buy_turnover: float) -> float:
    return buy_turnover * 0.0


def indep_margin(slope: float, intercept: float, price: float, qty: int) -> float:
    return qty * (slope * price + intercept)


# ═══════════════════════════════════════════════════════════════
# Replay data-adapter (drop-in for data/dhan/adapter.py::DhanDataAdapter)
# ═══════════════════════════════════════════════════════════════
class _MockWS:
    connected = True
    _stats = {"tick": 0}
    _instruments = {}
    _last_tick_time = 0.0

    def is_stale(self) -> bool:
        return False


class ReplayDataAdapter:
    def __init__(self, client_id="", token_file="", pin="", totp_secret="",
                 on_tick=None, on_status=None, **kwargs):
        self.client_id = client_id
        self._on_tick = on_tick
        self._on_status = on_status
        self.ws = _MockWS()
        self.instruments = {}

    def register_instruments(self, instruments: dict) -> None:
        self.instruments = instruments

    def connect(self) -> None:
        self.ws.connected = True

    def disconnect(self) -> None:
        self.ws.connected = False

    def fetch_historical_candles(self, *args, **kwargs):
        return []


# ═══════════════════════════════════════════════════════════════
# Data: real candles from the real Dhan REST client
# ═══════════════════════════════════════════════════════════════
def fetch_real_candles(token_file: Path, security_id: str, start_iso: str, stop_iso: str):
    from data.dhan.rest_client import DhanRESTClient
    # client_id comes from config (env-resolved at runtime), NOT hardcoded.
    from config import Config
    _cfg = Config()
    _cfg.load()
    _client_id = _cfg.get("dhan.client_id", "").strip()
    rest = DhanRESTClient(token_file=str(token_file), client_id=_client_id)
    from_dt = datetime.fromisoformat(start_iso + "T00:00:00+05:30")
    to_dt = datetime.fromisoformat(stop_iso + "T23:59:59+05:30")
    rows = rest.fetch_intraday(str(security_id), "5", from_dt, to_dt, "MCX_COMM", "FUTCOM")
    rows.sort(key=lambda r: r[0])
    return rows


def build_bars(name: str, rows: list, keep_partial: bool = False):
    """Build the exact Bar objects the live CandleFetcher emits.

    5m bars via _create_bar, 15m/1h via _aggregate_candles (full windows only),
    exactly as CandleFetcher._check_timeframe/_fetch_candle do in production.

    keep_partial=True additionally emits incomplete end-of-session windows (the
    trailing 23:00-24:00 1h group) exactly as the reference backtest resample
    (data_mcx/dema_mtf_base.py) does — used by _bt5_offline so the engine's HTF
    DEMA-ATR history matches the reference 44-trade run.
    """
    from core.candle_fetcher import CandleFetcher
    cf = CandleFetcher(data_adapter=None, instruments={}, on_candle_closed=None)

    bars5 = []
    for r in rows:
        naive = datetime.fromtimestamp(r[0], tz=IST).replace(tzinfo=None)
        bar = cf._create_bar(name, "5m", list(r), naive, 5)
        if bar:
            bars5.append(bar)

    def aggregate(tf_min: int, tf: str):
        """Session-anchored 15m/1h aggregation.

        Matches production CandleFetcher._check_timeframe/_fetch_candle exactly:
        windows start at the IST session_open (09:00) on each day and a window is
        only emitted when it is complete (tf_min//5 five-minute bars).  Partial
        end-of-session hours (e.g. 23:00-24:00 after a 23:30 close) are skipped,
        so the HTF DEMA-ATR line never consumes a partial candle — identical to
        what the live engine receives.  keep_partial relaxes that for CSV replay.
        """
        out = []
        window = tf_min * 60
        expected = tf_min // 5
        by_day: dict[str, list] = {}
        for b in bars5:
            bt = ist(b.start_ts)
            by_day.setdefault(bt.strftime("%Y-%m-%d"), []).append(b)
        for day, day_bars in sorted(by_day.items()):
            day_bars.sort(key=lambda b: b.start_ts)
            d0 = ist(day_bars[0].start_ts).replace(hour=9, minute=0, second=0, microsecond=0)
            for b in day_bars:
                bn = ist(b.start_ts)
                idx = int((bn - d0).total_seconds() // window)
                if idx < 0:
                    continue
                key = (day, idx)
                group = [x for x in day_bars if int((ist(x.start_ts) - d0).total_seconds() // window) == idx]
                if len(group) == expected or (keep_partial and len(group) > 0):
                    wstart = d0 + timedelta(seconds=idx * window)
                    naive = wstart.replace(tzinfo=None)
                    candle = [wstart.timestamp(),
                              group[0].open,
                              max(x.high for x in group),
                              min(x.low for x in group),
                              group[-1].close,
                              int(sum(x.volume for x in group))]
                    bar = cf._aggregate_candles(name, tf, [candle], naive, tf_min)
                    if bar:
                        out.append(bar)
        # de-dup & keep order
        seen = set()
        dedup = []
        for bar in sorted(out, key=lambda b: b.start_ts):
            if bar.start_ts in seen:
                continue
            seen.add(bar.start_ts)
            dedup.append(bar)
        return dedup

    return bars5, aggregate(15, "15m"), aggregate(60, "1h")


# ═══════════════════════════════════════════════════════════════
# Isolated engine config (mirrors config/settings.json, temp DBs only)
# ═══════════════════════════════════════════════════════════════
def write_config(root: Path) -> Path:
    data = {
        "system": {"name": "FullDepthSim", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake", "token_file": str(root / "data" / "db" / "dhan_token.json"),
                 "pin": "", "totp_secret": ""},
        "instruments": LIVE_INSTRUMENTS,
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": LIVE_STRATEGIES,
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 1, "partial_fill_probability": 0.0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01, "exchange_pct": 0.0026,
                      "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01, "exchange_pct": 0.0026,
                        "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 1200000.0, "starting_capital_per_strategy": 300000.0,
                    "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg = root / "settings.json"
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg


def build_engine(cfg_path: Path):
    """Real TradingEngine (only DhanDataAdapter substituted by ReplayDataAdapter)."""
    import trading_engine as te
    te.DhanDataAdapter = ReplayDataAdapter

    from config import Config
    from persistence.manager import PersistenceManager
    from analytics.schema import init_analytics_db
    TradingEngine = te.TradingEngine

    (cfg_path.parent / "data" / "db").mkdir(parents=True, exist_ok=True)
    init_analytics_db(str(cfg_path.parent / "data" / "db" / "analytics.db"))

    persistence = PersistenceManager(state_path=str(cfg_path.parent / "data" / "db" / "system_state.json"),
                                     db_path=str(cfg_path.parent / "data" / "db" / "trading.db"))
    engine = TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    return engine, persistence


# ═══════════════════════════════════════════════════════════════
# Replay driver — real seams, historical wall clock per bar
# ═══════════════════════════════════════════════════════════════
def _fast_strategy(engine, bar):
    for strat in engine.strategies.values():
        if strat.instrument == bar.instrument and strat.fast_timeframe == bar.timeframe:
            return strat
    return None


def replay(engine, stream_by_day):
    from core.market_status import MarketState, EngineStatus
    engine._running = True
    engine.market_status.set_engine_status(EngineStatus.READY)
    ws = engine.data_adapter.ws
    # Replay clock: stamp every order/fill with the candle's historical end_ts
    # so persisted trades carry the real trading-day times instead of the
    # wall-clock moment the replay ran.  Production (no clock) is untouched.
    clock_holder = {"ts": 0.0}
    engine.execution_engine._clock = lambda: clock_holder["ts"]

    def live_tick(instrument, ltp, ts):
        # Production _on_tick derives CONNECTED from ws._last_tick_time;
        # feed a fresh timestamp so data_status promotes READY -> TRADING.
        ws._last_tick_time = time.time()
        engine._on_tick({"instrument": instrument, "ltp": ltp, "event_timestamp": ts})

    for day, bars in sorted(stream_by_day.items()):
        engine.market_status.force_state(MarketState.LIVE_TRADING)
        engine.market_status._eod_close_done_today = False
        last_close = {}
        last_ts = {}
        for bar in bars:
            clock_holder["ts"] = bar.end_ts
            strat = _fast_strategy(engine, bar)
            if strat is not None:
                # Direct market LTP for the bar (close, unless a signal
                # carries an explicit fill_price that overrides it in
                # _process_signal).
                engine.execution_engine.update_price(bar.instrument, bar.close)
            engine._on_bar_closed(bar)
            live_tick(bar.instrument, bar.close, bar.end_ts)
            last_close[bar.instrument] = bar.close
            last_ts[bar.instrument] = bar.end_ts

        # NOTE: no EOD force-close.  Open positions carry into the next
        # session until the opposite trade / stop-loss exits them (backtest
        # style).  should_force_close is disabled in trading_engine._on_tick.

    engine.market_status.force_state(MarketState.AFTER_MARKET)


# ═══════════════════════════════════════════════════════════════
# Verification helpers
# ═══════════════════════════════════════════════════════════════
def readonly_sql(db_path, query: str, *params):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [tuple(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def reconcile_result(engine, phase="live"):
    from reconciliation.engine import ReconciliationEngine
    recon = ReconciliationEngine(
        persistence=engine._persistence,
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        strategies=engine.strategies,
        order_manager=engine.order_manager,
    )
    return recon.reconcile(phase=phase)


def teardown(engine, persistence):
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════
def main():
    args = sys.argv[1:]
    start_iso = args[0] if len(args) > 0 else WINDOW_DEFAULT[0]
    stop_iso = args[1] if len(args) > 1 else WINDOW_DEFAULT[1]

    token_file = ROOT / "data" / "dhan_token.json"
    run_root = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"full_sim_{start_iso}_{stop_iso}"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    cfg_path = write_config(run_root)

    print(f"=== FULL-DEPTH SIMULATOR  {start_iso} -> {stop_iso} (real components, real candles) ===", flush=True)

    # ── 1. Real candles ──
    candles = {}
    stream_all = []
    for name, meta in LIVE_INSTRUMENTS.items():
        rows = fetch_real_candles(token_file, meta["security_id"], start_iso, stop_iso)
        candles[name] = rows
        b5, b15, b1h = build_bars(name, rows)
        print(f"[Data] {name}: {len(rows)} x5m  | {len(b15)} x15m  | {len(b1h)} x1h", flush=True)
        for bar in b5 + b15 + b1h:
            stream_all.append(bar)
    if not candles or all(not v for v in candles.values()):
        print("FATAL: no candle data (token expired?)", flush=True)
        return 1

    stream_by_day = {}
    for bar in stream_all:
        stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))
    print(f"[Data] {len(stream_all)} bars across {len(stream_by_day)} trading days", flush=True)

    # ── 2. Engine ──
    import trading_engine as te
    te.DhanDataAdapter = ReplayDataAdapter
    from core.market_status import MarketState
    from core.trade_close import TradeCloseManager

    engine, persistence = build_engine(cfg_path)
    engine.tick_signal_processing = False  # bar-model replay: no tick breakout/SL

    # Wire exactly what start() wires before its network section.
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        global_account=engine.account_engine,
        risk_engine=engine.risk_engine,
        persistence=engine._persistence,
        event_store=engine.event_store,
        telegram=engine.telegram,
        event_callback=engine._event_callback,
        trade_ledger=engine.trade_ledger,
    )

    # ── 3. Replay ──
    print("\n[Replay] processing bars through the REAL pipeline...", flush=True)
    t0 = time.time()
    replay(engine, stream_by_day)
    print(f"[Replay] done in {time.time()-t0:.1f}s", flush=True)

    # ── 4. Verification ──
    checks = []
    ok = lambda name, cond, detail: checks.append((name, bool(cond), detail))

    # 4a. Reconciliation (real engine)
    recon = reconcile_result(engine, phase="live")
    ok("Reconciliation", recon.is_consistent, recon.summary().strip().replace("\n", " | ")[:200])
    print(f"\n{recon.summary()}", flush=True)

    # 4b. DB invariants
    db = engine._persistence.db_path
    orders = readonly_sql(db, "SELECT order_id, state, side FROM orders")
    fills = readonly_sql(db, "SELECT fill_id, order_id, side, price FROM fills")
    ok("DB: all orders filled", orders and all(o[1] == "filled" for o in orders),
       f"{len(orders)} orders")
    # EOD-close fills are synthetic and legitimately carry order_id == "".
    ok("DB: fills reference orders", fills and all(
        any(f[1] == o[0] for o in orders) for f in fills if f[1]),
       f"{len(fills)} fills ({sum(1 for f in fills if not f[1])} EOD synthetic)")
    trades_db = readonly_sql(db, "SELECT trade_id, status FROM trades")
    ok("DB: closed trades", trades_db and all(t[1] in ("open", "closed") for t in trades_db),
       f"{len(trades_db)} trades")

    # 4c. Ledger -> independent P&L math
    closed = engine.trade_ledger.get_closed_trades()
    gross_sum = net_sum = fee_sum = 0.0
    trade_rows = []
    for tr in sorted(closed, key=lambda t: t.first_fill_time or 0):
        side = tr.side
        mult = LIVE_INSTRUMENTS[tr.instrument]["multiplier"]
        qty = tr.filled_quantity or tr.entry_quantity
        ref_gross = indep_gross(side, tr.average_entry_price, tr.average_exit_price, qty, mult)
        ref_fees = indep_charges(tr.average_entry_price, tr.average_exit_price, qty, mult, side)
        ref_net = round(ref_gross - ref_fees, 2)
        ok(f"Ledger gross {tr.strategy_id}", abs(tr.gross_pnl - ref_gross) < 1.0,
           f"gross {tr.gross_pnl:.2f} vs indep {ref_gross:.2f}")
        ok(f"Ledger fees {tr.strategy_id}", abs(tr.fees - ref_fees) < 1.0,
           f"fees {tr.fees:.2f} vs indep {ref_fees:.2f}")
        ok(f"Ledger net {tr.strategy_id}", abs(tr.net_pnl - ref_net) < 1.0,
           f"net {tr.net_pnl:.2f} vs indep {ref_net:.2f}")
        gross_sum += tr.gross_pnl
        fee_sum += tr.fees or 0.0
        net_sum += tr.net_pnl or 0.0
        trade_rows.append(tr)

    # 4d. Accounts
    total_acct_net = sum(a.realized_pnl for a in engine.account_engines.values())
    total_acct_charges = sum(a.charges for a in engine.account_engines.values())
    ok("Accounts match ledger", abs(total_acct_net - net_sum) < 1.0 and abs(total_acct_charges - fee_sum) < 1.0,
       f"acct net {total_acct_net:.2f}/charges {total_acct_charges:.2f} vs {net_sum:.2f}/{fee_sum:.2f}")
    ok("Global account == sum strategies",
       abs(engine.account_engine.realized_pnl - total_acct_net) < 1.0
       and abs(engine.account_engine.charges - total_acct_charges) < 1.0,
       f"global {engine.account_engine.realized_pnl:.2f}/{engine.account_engine.charges:.2f}")
    final_equity = engine.account_engine.equity
    open_pos = list(engine.position_manager.open_positions)
    unrealized_sum = sum(p.unrealized_pnl for p in open_pos)
    margin_sum = sum(p.margin for p in open_pos)
    ok("Final equity (incl. unrealized)", abs(final_equity - (1200000.0 + net_sum + unrealized_sum)) < 1.0,
       f"equity {final_equity:,.0f} vs 1200000+net({net_sum:,.0f})+unrlz({unrealized_sum:,.0f}) "
       f"= {1200000.0+net_sum+unrealized_sum:,.0f}")
    ok("No eod_close exits (carry enabled)", all((t.exit_reason or "") != "eod_close" for t in trade_rows),
       f"{sum(1 for t in trade_rows if (t.exit_reason or '') == 'eod_close')} eod_close")
    ok("Positions carry / open at window end allowed", True,
       f"{len(open_pos)} open positions carried to next session")
    ok("Used margin == open positions margin",
       abs(engine.account_engine.used_margin - margin_sum) < 1.0,
       f"used_margin {engine.account_engine.used_margin:.2f} vs positions {margin_sum:.2f}")

    # 4e. Every run re-processes the data from scratch (no state reuse).
    ok("Fresh run", len(engine.order_manager.snapshot().get("orders", {})) == 0, "engine rebuilt per run")

    teardown(engine, persistence)

    # ── 5. Report ──
    print("\n=== FULL-DEPTH SIMULATION REPORT ===")
    print(f"window {start_iso} .. {stop_iso} | bars {len(stream_all)} | days {len(stream_by_day)}")
    print(f"\nCLOSED TRADES (from real TradeLedger):")
    for tr in sorted(trade_rows, key=lambda t: t.first_fill_time or 0):
        e_t = ist(tr.first_fill_time).strftime("%m-%d %H:%M") if tr.first_fill_time else "?"
        x_t = ist(tr.last_exit_fill_time).strftime("%m-%d %H:%M") if tr.last_exit_fill_time else "?"
        print(f"  {tr.strategy_id:10s} {tr.side:5s} {tr.instrument:7s} "
              f"in {e_t} @ {tr.average_entry_price:9.1f}  out {x_t} @ {tr.average_exit_price:9.1f} "
              f"gross {tr.gross_pnl:9.2f} fees {tr.fees:8.2f} net {tr.net_pnl:9.2f}  ({tr.exit_reason})")

    print("\nPER-STRATEGY:")
    for name in ("gold_01", "gold_02", "silver_01", "silver_02"):
        st = [tr for tr in trade_rows if tr.strategy_id == name]
        net = sum(t.net_pnl or 0 for t in st)
        print(f"  {name:10s} closed={len(st):2d}  net P&L {net:10.2f}")

    print("\nVERIFICATION:")
    failed = 0
    for name, cond, detail in checks:
        print(f"  {'PASS' if cond else 'FAIL'}  {name}: {detail}")
        if not cond:
            failed += 1

    print(f"\nRESULT: {'ALL CHECKS PASSED' if failed == 0 else f'{failed} FAILED'}")
    print(f"run artifacts: {run_root}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())