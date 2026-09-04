"""UNIFIED REPLAY — native Dhan candles (5m + 15m + 60m), real engine, single script.

Fetches native candles from Dhan REST for each timeframe, builds Bar objects
directly (no resampling), replays through the real production pipeline, and
writes fresh DB state.

Usage:
    python _replay.py --start 2026-08-21 --end 2026-08-28
    python _replay.py --start 2026-08-21                    # end = now
    python _replay.py --start 2026-08-21 --dry-run          # no DB write
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))

LIVE_INSTRUMENTS = {
    "GOLDM": {"symbol": "MCX:GOLDM202609", "security_id": "563946",
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

_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}
_TF_DAHN_INTERVAL = {"5m": "5", "15m": "15", "1h": "60"}
_TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def ist(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)


# ═══════════════════════════════════════════════════════════════
# Independent reference math
# ═══════════════════════════════════════════════════════════════
def indep_gross(side: str, entry: float, exit_p: float, qty: int, mult: float) -> float:
    if side == "LONG":
        return (exit_p - entry) * qty * mult
    return (entry - exit_p) * qty * mult


def indep_charges(entry: float, exit_price: float, qty: int, mult: float, side: str) -> float:
    buy_turnover = entry * qty * mult
    sell_turnover = exit_price * qty * mult
    if side == "SHORT":
        buy_turnover, sell_turnover = sell_turnover, buy_turnover
    brokerage = 20.0 * 2
    stt = sell_turnover * 0.0001
    exchange = (buy_turnover + sell_turnover) * 0.000026
    sebi = (buy_turnover + sell_turnover) * 0.000001
    gst = (brokerage + exchange + sebi) * 0.18
    stamp = buy_turnover * 0.0
    return round(brokerage + stt + exchange + sebi + gst + stamp, 2)


# ═══════════════════════════════════════════════════════════════
# Replay data-adapter (drop-in for DhanDataAdapter)
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
# Native candle fetching (5m + 15m + 60m from Dhan REST)
# ═══════════════════════════════════════════════════════════════
def fetch_native_candles(token_file: Path, security_id: str,
                         from_dt: datetime, to_dt: datetime,
                         interval: str) -> list:
    """Fetch native candles directly from Dhan REST. No resampling.

    to_dt should be datetime.now(IST) to guarantee only closed candles are
    returned — Dhan never returns forming candles, but using now() as the
    upper bound ensures we never request past the last available close.
    """
    from data.dhan.rest_client import DhanRESTClient
    rest = DhanRESTClient(token_file=str(token_file), client_id="1102461741")
    rows = rest.fetch_intraday(str(security_id), interval, from_dt, to_dt,
                               "MCX_COMM", "FUTCOM")
    rows.sort(key=lambda r: r[0])
    return rows


def build_bars_native(name: str, rows: list, timeframe: str,
                      now_epoch: float) -> list:
    """Build Bar objects directly from native candle rows.

    Excludes any candle whose end_ts > now_epoch (still forming).
    This guarantees every bar in the output is a CLOSED candle.
    """
    from core.timeframe_engine import Bar, BarState
    tf_min = _TF_MINUTES[timeframe]
    bars = []
    for r in rows:
        ts = r[0]
        naive = datetime.fromtimestamp(ts, tz=IST).replace(tzinfo=None)
        start_ts = naive.timestamp()
        end_ts = start_ts + tf_min * 60
        if end_ts > now_epoch:
            continue
        bar = Bar(
            instrument=name,
            timeframe=timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=int(r[5]),
            state=BarState.CLOSED,
        )
        bars.append(bar)
    return bars


# ═══════════════════════════════════════════════════════════════
# Engine setup
# ═══════════════════════════════════════════════════════════════
def write_config(root: Path) -> Path:
    data = {
        "system": {"name": "Replay", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake",
                 "token_file": str(root / "data" / "db" / "dhan_token.json"),
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
    """Real TradingEngine with ReplayDataAdapter."""
    import trading_engine as te
    te.DhanDataAdapter = ReplayDataAdapter

    from persistence.manager import PersistenceManager
    from analytics.schema import init_analytics_db

    (cfg_path.parent / "data" / "db").mkdir(parents=True, exist_ok=True)
    init_analytics_db(str(cfg_path.parent / "data" / "db" / "analytics.db"))

    persistence = PersistenceManager(
        state_path=str(cfg_path.parent / "data" / "db" / "system_state.json"),
        db_path=str(cfg_path.parent / "data" / "db" / "trading.db"))
    engine = te.TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    return engine, persistence


# ═══════════════════════════════════════════════════════════════
# Replay driver
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

    def live_tick(instrument, ltp, ts):
        ws._last_tick_time = time.time()
        engine._on_tick({"instrument": instrument, "ltp": ltp, "event_timestamp": ts})

    stats = {"bars": 0, "days": 0}
    for day, bars in sorted(stream_by_day.items()):
        engine.market_status.force_state(MarketState.LIVE_TRADING)
        engine.market_status._eod_close_done_today = False
        stats["days"] += 1
        for bar in bars:
            stats["bars"] += 1
            strat = _fast_strategy(engine, bar)
            if strat is not None:
                engine.execution_engine.update_price(bar.instrument, bar.close)
            engine._on_bar_closed(bar)
            live_tick(bar.instrument, bar.close, bar.end_ts)

    engine.market_status.force_state(MarketState.AFTER_MARKET)
    return stats


# ═══════════════════════════════════════════════════════════════
# DB operations
# ═══════════════════════════════════════════════════════════════
def backup_dbs(data_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = data_dir / f"backup_{ts}"
    backup_dir.mkdir(exist_ok=True)
    for name in ["trading.db", "trading.db-wal", "trading.db-shm",
                 "analytics.db", "analytics.db-wal", "analytics.db-shm",
                 "system_state.json"]:
        src = data_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    return backup_dir


def wipe_dbs(data_dir: Path):
    for name in ["trading.db", "trading.db-wal", "trading.db-shm",
                 "analytics.db", "analytics.db-wal", "analytics.db-shm",
                 "system_state.json"]:
        src = data_dir / name
        if src.exists():
            src.unlink()


# ═══════════════════════════════════════════════════════════════
# Verification
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
    import argparse
    parser = argparse.ArgumentParser(description="Unified Replay — native Dhan candles")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: last closed candle)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write DBs")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup")
    parser.add_argument("--no-wipe", action="store_true", help="Don't wipe DBs before replay")
    args = parser.parse_args()

    start_iso = args.start
    now_ist = datetime.now(IST)
    now_epoch = now_ist.timestamp()

    if args.end:
        to_dt = datetime.fromisoformat(args.end + "T23:59:59+05:30")
        stop_label = args.end
    else:
        to_dt = now_ist
        stop_label = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")

    from_dt = datetime.fromisoformat(start_iso + "T00:00:00+05:30")

    token_file = ROOT / "data" / "dhan_token.json"
    data_dir = ROOT / "data" / "db"

    print(f"=== UNIFIED REPLAY  {start_iso} -> {stop_label}  (native candles) ===", flush=True)
    print(f"[Clock] now = {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')} (epoch {now_epoch:.0f})", flush=True)

    # ── 1. Backup & wipe DBs ──
    if not args.dry_run:
        if not args.no_backup:
            bdir = backup_dbs(data_dir)
            print(f"[DB] Backed up to {bdir}", flush=True)
        if not args.no_wipe:
            wipe_dbs(data_dir)
            print("[DB] Wiped old databases", flush=True)

    # ── 2. Fetch native candles + build bars ──
    all_bars = []
    for name, meta in LIVE_INSTRUMENTS.items():
        for tf in ["5m", "15m", "1h"]:
            interval = _TF_DAHN_INTERVAL[tf]
            rows = fetch_native_candles(token_file, meta["security_id"],
                                        from_dt, to_dt, interval)
            bars = build_bars_native(name, rows, tf, now_epoch)
            all_bars.extend(bars)
            raw_count = len(rows)
            filtered_count = len(bars)
            dropped = raw_count - filtered_count
            last_ts = ""
            if bars:
                last_dt = ist(bars[-1].start_ts)
                last_ts = last_dt.strftime("%Y-%m-%d %H:%M")
            print(f"[Data] {name} {tf}: {filtered_count} closed bars"
                  f" (fetched={raw_count}, forming_dropped={dropped})"
                  f"  last_closed={last_ts}", flush=True)

    if not all_bars:
        print("FATAL: no candle data", flush=True)
        return 1

    # ── 3. Sort by (start_ts, TF_RANK) then group by day ──
    all_bars.sort(key=lambda b: (b.start_ts, _TF_RANK.get(b.timeframe, 99)))

    stream_by_day = defaultdict(list)
    for bar in all_bars:
        day = ist(bar.start_ts).date()
        stream_by_day[day].append(bar)

    # Within each day, sort by (end_ts, TF_RANK) — same as full_simulator
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))

    print(f"[Data] {len(all_bars)} total bars across {len(stream_by_day)} trading days", flush=True)

    # ── 4. Build engine ──
    run_root = data_dir.parent.parent  # ROOT
    if not args.dry_run:
        cfg_path = write_config(run_root)
    else:
        cfg_path = ROOT / "settings.json"

    engine, persistence = build_engine(cfg_path)
    engine.tick_signal_processing = False
    for st in engine.strategies.values():
        st.pending_timeout_bars = 10 ** 9

    from core.trade_close import TradeCloseManager
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

    # ── 5. Replay ──
    print("\n[Replay] processing bars through the REAL pipeline...", flush=True)
    t0 = time.time()
    stats = replay(engine, stream_by_day)
    elapsed = time.time() - t0
    print(f"[Replay] done: {stats['bars']} bars, {stats['days']} days in {elapsed:.1f}s", flush=True)

    # ── 6. Verification ──
    checks = []
    ok = lambda name, cond, detail: checks.append((name, bool(cond), detail))

    recon = reconcile_result(engine, phase="live")
    ok("Reconciliation", recon.is_consistent,
       recon.summary().strip().replace("\n", " | ")[:200])
    print(f"\n{recon.summary()}", flush=True)

    db = engine._persistence.db_path
    orders = readonly_sql(db, "SELECT order_id, state, side FROM orders")
    fills = readonly_sql(db, "SELECT fill_id, order_id, side, price FROM fills")
    ok("DB: all orders filled", orders and all(o[1] == "filled" for o in orders),
       f"{len(orders)} orders")
    ok("DB: fills reference orders", fills and all(
        any(f[1] == o[0] for o in orders) for f in fills if f[1]),
       f"{len(fills)} fills")
    trades_db = readonly_sql(db, "SELECT trade_id, status FROM trades")
    ok("DB: closed trades", trades_db and all(t[1] in ("open", "closed") for t in trades_db),
       f"{len(trades_db)} trades")

    closed = engine.trade_ledger.get_closed_trades()
    gross_sum = net_sum = fee_sum = 0.0
    trade_rows = []
    for tr in sorted(closed, key=lambda t: t.first_fill_time or 0):
        mult = LIVE_INSTRUMENTS[tr.instrument]["multiplier"]
        qty = tr.filled_quantity or tr.entry_quantity
        ref_gross = indep_gross(tr.side, tr.average_entry_price, tr.average_exit_price, qty, mult)
        ref_fees = indep_charges(tr.average_entry_price, tr.average_exit_price, qty, mult, tr.side)
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

    total_acct_net = sum(a.realized_pnl for a in engine.account_engines.values())
    total_acct_charges = sum(a.charges for a in engine.account_engines.values())
    ok("Accounts match ledger",
       abs(total_acct_net - net_sum) < 1.0 and abs(total_acct_charges - fee_sum) < 1.0,
       f"acct net {total_acct_net:.2f}/charges {total_acct_charges:.2f} vs {net_sum:.2f}/{fee_sum:.2f}")
    ok("Global account == sum strategies",
       abs(engine.account_engine.realized_pnl - total_acct_net) < 1.0
       and abs(engine.account_engine.charges - total_acct_charges) < 1.0,
       f"global {engine.account_engine.realized_pnl:.2f}/{engine.account_engine.charges:.2f}")
    final_equity = engine.account_engine.equity
    open_pos = list(engine.position_manager.open_positions)
    unrealized_sum = sum(p.unrealized_pnl for p in open_pos)
    margin_sum = sum(p.margin for p in open_pos)
    ok("Final equity (incl. unrealized)",
       abs(final_equity - (1200000.0 + net_sum + unrealized_sum)) < 1.0,
       f"equity {final_equity:,.0f} vs 1200000+net({net_sum:,.0f})+unrlz({unrealized_sum:,.0f}) "
       f"= {1200000.0+net_sum+unrealized_sum:,.0f}")
    ok("Used margin == open positions margin",
       abs(engine.account_engine.used_margin - margin_sum) < 1.0,
       f"used_margin {engine.account_engine.used_margin:.2f} vs positions {margin_sum:.2f}")

    teardown(engine, persistence)

    # ── 7. Report ──
    print("\n=== REPLAY REPORT ===")
    print(f"window {start_iso} .. {stop_label} | bars {len(all_bars)} | days {len(stream_by_day)}")
    print(f"\nCLOSED TRADES:")
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
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
