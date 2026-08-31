"""Seed engine state by replaying real Dhan candles through the real stack.

Reproduces what the engine would have traded on a past window (default: the
warmup week ending Friday 2026-08-28) and persists the resulting closed trades
and carried positions into the production DBs + state file, so the live
container resumes from that state on its next boot.

Usage (production, on the server with the volume mounted, engine STOPPED):
    python seed_replay.py 2026-08-24 2026-08-28

Dev/validation against a throwaway root (no network in --synthetic):
    python seed_replay.py --root PATH --synthetic 2026-08-24 2026-08-28
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}

from full_simulator import (  # noqa: E402
    LIVE_INSTRUMENTS,
    ReplayDataAdapter,
    build_bars,
    fetch_real_candles,
    ist,
    reconcile_result,
    replay,
    teardown,
)


def _synthetic_rows(security_id: str, days: int) -> list:
    rows = []
    base = 160000.0
    start_day = 28 - (days - 1)
    for d in range(days):
        day = datetime(2026, 8, start_day + d, 9, 0, tzinfo=IST)
        for i in range(174):
            ts = day + timedelta(minutes=i * 5)
            o = base * (1 + i * 0.0008)
            c = base * (1 + i * 0.0011)
            h = max(o, c) * 1.0008
            l = min(o, c) * 0.9992
            rows.append([ts.timestamp(), o, h, l, c, 1])
        base = rows[-1][4]
    return rows


def _write_overrides(root: Path, src_settings: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "db").mkdir(parents=True, exist_ok=True)
    data = json.loads(src_settings.read_text(encoding="utf-8"))
    data["system"]["db_path"] = str(root / "data" / "db" / "trading.db")
    data["system"]["state_path"] = str(root / "data" / "db" / "system_state.json")
    data["dhan"]["token_file"] = str(root / "data" / "db" / "dhan_token.json")
    data["dhan"]["client_id"] = ""
    data["dhan"]["access_token"] = ""
    data["dhan"]["pin"] = ""
    data["dhan"]["totp_secret"] = ""
    data["telegram"]["bot_token"] = ""
    data["telegram"]["chat_id"] = ""
    cfg = root / "settings.json"
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg


def _build_engine(cfg_path: Path):
    import trading_engine as te

    te.DhanDataAdapter = ReplayDataAdapter
    from analytics.schema import init_analytics_db
    from persistence.manager import PersistenceManager

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    db_path = str(Path(data["system"]["db_path"]).resolve())
    state_path = str(Path(data["system"]["state_path"]).resolve())
    init_analytics_db(str(Path(db_path).parent / "analytics.db"))
    persistence = PersistenceManager(state_path=state_path, db_path=db_path)
    engine = te.TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    return engine, persistence


def _stream_from_rows(rows_by_inst) -> dict:
    stream_all = []
    for name, rows in rows_by_inst.items():
        b5, b15, b1h = build_bars(name, rows)
        print(f"[Data] {name}: {len(rows)} x5m | {len(b15)} x15m | {len(b1h)} x1h", flush=True)
        stream_all.extend(b5 + b15 + b1h)
    stream_by_day = {}
    for bar in stream_all:
        stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))
    return stream_by_day


def _count(db_path: str, table: str) -> int:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return -1


def wipe_trade_dbs(cfg_path: Path, prod_db: Optional[str] = None) -> None:
    """Delete ALL previously recorded trade data from the production DBs.

    Removes every row the replay regenerates (trades/orders/fills/snapshots/
    events in trading.db, and the analytics ledger/legs/events/snapshots plus
    derived performance tables in analytics.db).  This is the "delete all
    trades" step that precedes a full fresh replay to the last state.

    prod_db may be passed explicitly (tests); otherwise it is resolved from the
    production config.
    """
    if prod_db is None:
        from config import Config
        _cfg = Config()
        _cfg.load()
        prod_db = _cfg.get("system.db_path", "data/db/trading.db")

    tables_trading = ["trades", "orders", "fills", "account_snapshots", "events"]
    tables_analytics = [
        "trade_events", "trades_analytics", "trade_legs", "trade_snapshots",
        "strategy_daily_performance", "strategy_monthly_performance",
        "strategy_performance_snapshots", "strategy_parameter_results",
    ]

    trading_db = str(Path(prod_db).resolve())
    analytics_db = str(Path(trading_db).parent / "analytics.db")

    for db_path, tables in [(trading_db, tables_trading), (analytics_db, tables_analytics)]:
        if not Path(db_path).exists():
            print(f"[Wipe] skip (absent): {db_path}", flush=True)
            continue
        con = sqlite3.connect(db_path)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            for t in tables:
                try:
                    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    con.execute(f"DELETE FROM {t}")
                    print(f"[Wipe] {Path(db_path).name}::{t} cleared ({n} rows)", flush=True)
                except sqlite3.OperationalError:
                    pass  # table does not exist
            con.commit()
        finally:
            con.close()
    print("[Wipe] all trade/analytics rows deleted", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("stop")
    ap.add_argument("--root", default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--wipe", action="store_true",
                    help="DELETE all existing trade/analytics rows before replay, "
                         "then replay from scratch and persist the last state (incl. open trades).")
    args = ap.parse_args()

    src_settings = ROOT / "config" / "settings.json"
    if args.root:
        root = Path(args.root)
        if root.exists():
            shutil.rmtree(root)
        cfg_path = _write_overrides(root, src_settings)
        print(f"[Seed] throwaway root: {root}", flush=True)
    else:
        cfg_path = src_settings
        print("[Seed] production mode (real config + real DBs)", flush=True)

    from analytics.schema import init_analytics_db  # noqa: F401
    from config import Config
    from core.trade_close import TradeCloseManager
    from persistence.manager import PersistenceManager

    cfg = Config()
    cfg.load()
    prod_db = cfg.get("system.db_path", "data/db/trading.db")
    db_path = str(Path(prod_db).resolve())
    if args.wipe and not args.root:
        wipe_trade_dbs(cfg_path)
    elif not args.root and not args.force and Path(db_path).exists():
        n = _count(db_path, "trades")
        if n > 0:
            print(f"[Seed] ABORT: {db_path} already has {n} trades (use --force to overwrite or --wipe to clear)", flush=True)
            return 2

    inst_cfg = cfg.get("instruments", {})
    if not args.root:
        token_file = cfg.get("dhan.token_file", "data/db/dhan_token.json")
        token_path = Path(token_file).resolve()
    else:
        token_path = cfg_path.parent / "data" / "db" / "dhan_token.json"

    engine, persistence = _build_engine(cfg_path)
    engine.tick_signal_processing = False
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

    if args.synthetic:
        days = 5
        rows_by_inst = {
            name: _synthetic_rows(meta.get("security_id", ""), days)
            for name, meta in inst_cfg.items()
        }
    else:
        rows_by_inst = {}
        for name, meta in inst_cfg.items():
            rows = fetch_real_candles(token_path, meta.get("security_id", ""), args.start, args.stop)
            rows_by_inst[name] = rows

    if not rows_by_inst or all(not v for v in rows_by_inst.values()):
        print("[Seed] FATAL: no candle data (token expired / not reachable)", flush=True)
        teardown(engine, persistence)
        return 1

    stream_by_day = _stream_from_rows(rows_by_inst)
    print(f"[Data] {sum(len(v) for v in stream_by_day.values())} bars across {len(stream_by_day)} days", flush=True)

    t0 = time.time()
    replay(engine, stream_by_day)
    print(f"[Seed] replay done in {time.time()-t0:.1f}s", flush=True)

    snapshot = engine.snapshot()
    persistence.save_state(snapshot)
    try:
        persistence.save_account_snapshot_from_state(snapshot)
    except Exception as e:
        print(f"[Seed] account snapshot warning: {e}", flush=True)

    closed = engine.trade_ledger.get_closed_trades()
    open_pos = list(engine.position_manager.open_positions)
    print("\n[Seed] CLOSED TRADES:", flush=True)
    for tr in sorted(closed, key=lambda t: t.first_fill_time or 0):
        print(f"  {tr.strategy_id:10s} {tr.side:5s} {tr.instrument:7s} "
              f"net {tr.net_pnl:9.2f}  ({tr.exit_reason})", flush=True)
    print("[Seed] OPEN POSITIONS (will resume on next boot):", flush=True)
    for p in open_pos:
        print(f"  {p.strategy_id:10s} {p.side:5s} {p.instrument:7s} qty {p.quantity}", flush=True)
    print(f"[Seed] count closed={len(closed)} open={len(open_pos)}", flush=True)

    recon = reconcile_result(engine, phase="seed")
    print(f"[Seed] reconciliation: {'OK' if recon.is_consistent else 'INCONSISTENT'}", flush=True)
    if not recon.is_consistent:
        print(recon.summary(), flush=True)

    print(f"[Seed] DB rows -> trades={_count(persistence.db_path, 'trades')} "
          f"fills={_count(persistence.db_path, 'fills')} "
          f"orders={_count(persistence.db_path, 'orders')} "
          f"account_snapshots={_count(persistence.db_path, 'account_snapshots')}", flush=True)

    teardown(engine, persistence)
    print("[Seed] DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())