"""Rebuild the live production trading.db + system_state.json from a fresh,
full-window replay of the real (online Dhan) candles.

The live production DB froze at 2026-08-28 (the live engine stopped processing),
so it never recorded the 08-29..09-01 sessions -- including the silver_01/silver_02
SHORTs that should have been long_reversal-closed on 2026-08-31.

This script:
  1. Backs up the current production trading.db + system_state.json (timestamped).
  2. Replays GOLDM and SILVERM over the full window via the REAL engine
     (full_simulator.replay -> real PersistenceManager), writing into temp roots.
  3. Combines both replays' trades/orders/fills/events into the PRODUCTION trading.db
     (replacing), recreating the exact `trades` schema.
  4. Merges both replays' full system_state.json snapshots into the PRODUCTION
     system_state.json (open_positions union; account/equity from merged replay).

Run on the server (engine STOPPED for trading), e.g.:
    docker exec mcx-trader python _rebuild_prod_db.py \
        --online \
        --db-path /app/data/db/trading.db \
        --state-path /app/data/db/system_state.json \
        --start 2026-08-26 --stop 2026-09-01 \
        --backup-dir /app/data/db/backups
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _p1_lib as L
from full_simulator import LIVE_STRATEGIES, build_bars, fetch_real_candles, ist

_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fetch_rows(name, start, stop, online):
    if online:
        from config import Config
        c = Config()
        c.load()
        token_file = Path(c.get("dhan.token_file", "data/db/dhan_token.json")).resolve()
        sid = c.get("instruments", {}).get(name, {}).get("security_id", "")
        return fetch_real_candles(token_file, sid, start, stop)
    return L.load_csv_rows(name, start, stop)


def replay_instrument(name, rows, root):
    import full_simulator as FS
    from core.market_status import DataStatus, EngineStatus, MarketState
    cfg = L.write_config(root, warmup={"last_trading_days": 0, "keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)

    bars5, bars15, bars1h = build_bars(name, rows, keep_partial=True)
    stream_all = sorted(bars5 + bars15 + bars1h,
                        key=lambda b: (b.start_ts, _TF_RANK.get(b.timeframe, 3)))
    stream_by_day = {}
    for bar in stream_all:
        stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _TF_RANK.get(b.timeframe, 3)))

    snap = FS.replay(engine, stream_by_day)
    persistence.save_state(snap)
    L.teardown(engine, persistence)
    return snap


def read_db_rows(db_path: Path, table: str):
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        cols = [d[1] for d in con.execute(f"PRAGMA table_info({table})").fetchall()]
        if not cols:
            return None
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        return cols, [dict(r) for r in rows]
    finally:
        con.close()


def write_rows(con, table, cols, rows):
    if not rows:
        return 0
    ph = ",".join("?" * len(cols))
    n = 0
    for r in rows:
        con.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})",
                    [r.get(c) for c in cols])
        n += 1
    return n


def ensure_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            strategy_id TEXT NOT NULL,
            instrument TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_timestamp TEXT,
            entry_price REAL,
            exit_timestamp TEXT,
            exit_price REAL,
            quantity INTEGER,
            multiplier REAL,
            gross_pnl REAL,
            charges REAL,
            net_pnl REAL,
            exit_reason TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            strategy_id TEXT NOT NULL,
            instrument TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER,
            order_type TEXT,
            price REAL,
            state TEXT,
            filled_quantity INTEGER,
            average_fill_price REAL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fill_id TEXT UNIQUE,
            order_id TEXT,
            strategy_id TEXT NOT NULL,
            instrument TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER,
            price REAL,
            timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            equity REAL,
            realized_pnl REAL,
            unrealized_pnl REAL,
            used_margin REAL,
            available_margin REAL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            event_type TEXT,
            strategy_id TEXT,
            instrument TEXT,
            details TEXT
        );
    """)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--state-path", default=None)
    ap.add_argument("--analytics-path", default=None)
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--start", default="2026-08-26")
    ap.add_argument("--stop", default="2026-09-01")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    db_path = Path(args.db_path).resolve() if args.db_path else ROOT / "_prod_rec" / "trading.db"
    state_path = Path(args.state_path).resolve() if args.state_path else ROOT / "_prod_rec" / "system_state.json"
    analytics_path = Path(args.analytics_path).resolve() if args.analytics_path else db_path.parent / "analytics.db"
    backup_dir = Path(args.backup_dir).resolve() if args.backup_dir else db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists() and not args.db_path:
        # no existing prod db -> create dirs
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- 1. backup ----
    stamp = _utc_iso()
    print(f"[Backup] backing up current production files to {backup_dir}", flush=True)
    for f in (db_path, state_path, analytics_path):
        if f and f.exists():
            for suf in ("", "-wal", "-shm"):
                src = Path(str(f) + suf)
                if src.exists():
                    dst = backup_dir / f"{src.name}.{stamp}.bak"
                    shutil.copy2(src, dst)
                    print(f"  backed up {src} -> {dst}", flush=True)

    # ---- 2. replay both instruments into temp roots ----
    run_id = int(time.time())
    merged_trades_cols = merged_trades = None
    merged_orders, merged_fills, merged_events, merged_snaps = [], [], [], []
    merged_analytic = {"trades_analytics": None, "trade_legs": None, "trade_events": None}
    for name in ("GOLDM", "SILVERM"):
        print(f"\n===== replay {name} ({args.start}..{args.stop}) =====", flush=True)
        rows = fetch_rows(name, args.start, args.stop, args.online)
        print(f"  {name}: {len(rows)} x5m rows", flush=True)
        root = ROOT / "_rebuild_tmp" / f"replay_{name}_{run_id}"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        snap = replay_instrument(name, rows, root)
        merged_snaps.append((name, snap))

        rdb = root / "data" / "db" / "trading.db"
        for table in ("trades", "orders", "fills", "events"):
            cols, data = read_db_rows(rdb, table)
            if not cols:
                continue
            if table == "trades":
                merged_trades_cols = cols
                merged_trades = (merged_trades or []) + data
            elif table == "orders":
                merged_orders += data
            elif table == "fills":
                merged_fills += data
            elif table == "events":
                merged_events += data

        adb = root / "data" / "db" / "analytics.db"
        for table in ("trades_analytics", "trade_legs", "trade_events"):
            got = read_db_rows(adb, table)
            if not got:
                continue
            cols, data = got
            if merged_analytic[table] is None:
                merged_analytic[table] = (cols, [])
            merged_analytic[table][1].extend(data)

    # ---- 3. write production DB (replace) ----
    if db_path.exists():
        # remove stale DB + sidecars so we start clean (already backed up)
        for suf in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suf)
            if p.exists():
                p.unlink()
    con = sqlite3.connect(str(db_path))
    try:
        ensure_schema(con)
        n_t = write_rows(con, "trades", merged_trades_cols or [], merged_trades or [])
        print(f"\n[DB] wrote {n_t} trades -> {db_path}", flush=True)
        con.commit()
    finally:
        con.close()

    # ---- 3b. write analytics DB (replace) ----
    if analytics_path.exists():
        for suf in ("", "-wal", "-shm"):
            p = Path(str(analytics_path) + suf)
            if p.exists():
                p.unlink()
    from analytics.schema import init_analytics_db
    init_analytics_db(analytics_path)
    acon = sqlite3.connect(str(analytics_path))
    try:
        for table, (cols, data) in merged_analytic.items():
            if not cols:
                continue
            n = write_rows(acon, table, cols, data)
            print(f"  [Analytics] wrote {n} {table} -> {analytics_path}", flush=True)
        acon.commit()
    finally:
        acon.close()

    # ---- 4. merge system state ----
    open_positions = {}
    acct = None
    merged_ts = None
    for name, snap in merged_snaps:
        if not snap:
            continue
        ops = snap.get("positions", {}).get("open_positions", {})
        for k, v in ops.items():
            if v.get("instrument") == name:
                open_positions[k] = v
        if acct is None and snap.get("account"):
            acct = snap["account"]
        if merged_ts is None and snap.get("timestamp"):
            merged_ts = snap["timestamp"]
    old_state = {}
    if state_path.exists():
        try:
            with open(state_path, encoding="utf-8") as f:
                old_state = json.load(f)
        except Exception:
            old_state = {}
    new_state = dict(old_state)
    new_state["positions"] = {"open_positions": open_positions}
    if acct is not None:
        new_state["account"] = acct
    if merged_ts is not None:
        new_state["timestamp"] = merged_ts
    state_path.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
    print(f"[State] wrote {len(open_positions)} open positions -> {state_path}", flush=True)

    # ---- summary ----
    print("\n===== REBUILD SUMMARY =====", flush=True)
    print(f"trades  : {len(merged_trades or [])}", flush=True)
    print(f"orders  : {len(merged_orders)}", flush=True)
    print(f"fills   : {len(merged_fills)}", flush=True)
    print(f"events  : {len(merged_events)}", flush=True)
    print(f"analytics: { {t: (len(d) if d else 0) for t, (_, d) in merged_analytic.items()} }", flush=True)
    print(f"open    : {len(open_positions)}", flush=True)
    print(f"backups : {backup_dir}", flush=True)


if __name__ == "__main__":
    main()
