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
    """Drive the real engine bar-by-bar exactly like audit_signal_candles.run_capture
    (engine._on_bar_closed per bar + replay clock), NOT full_simulator.replay (which
    additionally feeds live _on_tick and diverges from the validated ledger).

    This reproduces the audit's authoritative behaviour: gold SHORTs stay OPEN at
    window end, and silver SHORTs are long_reversal-closed on 2026-08-31.
    """
    from core.market_status import DataStatus, EngineStatus, MarketState
    from audit_signal_candles import STRAT_FAST, _TF_RANK   # reuse fast-mapping/tf-rank
    cfg = L.write_config(root, warmup={"last_trading_days": 0, "keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine.execution_engine.update_price(name, 150000.0)

    clock_holder = {"ts": 0.0}
    engine.execution_engine._clock = lambda: clock_holder["ts"]

    bars5, bars15, bars1h = build_bars(name, rows, keep_partial=True)
    all_bars = sorted(bars5 + bars15 + bars1h,
                      key=lambda b: (b.start_ts, _TF_RANK.get(b.timeframe, 3)))
    for bar in all_bars:
        clock_holder["ts"] = bar.end_ts
        engine._on_bar_closed(bar)

    snap = engine.snapshot()
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
    auto_id = "id" in cols
    start = 1
    n = 0
    for r in rows:
        vals = [r.get(c) for c in cols]
        if auto_id:
            # Reassign the autoincrement id so per-instrument replays (each with
            # ids starting at 1) don't collide/overwrite each other in the
            # combined production DB.
            vals[cols.index("id")] = start
            start += 1
        con.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})",
                    vals)
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
    merged_order_cols = merged_fill_cols = merged_event_cols = None
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
                merged_order_cols = cols
                merged_orders += data
            elif table == "fills":
                merged_fill_cols = cols
                merged_fills += data
            elif table == "events":
                merged_event_cols = cols
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
        # also persist the accompanying order/fill/event rows (completeness)
        if merged_orders:
            write_rows(con, "orders", merged_order_cols, merged_orders)
        if merged_fills:
            write_rows(con, "fills", merged_fill_cols, merged_fills)
        if merged_events:
            write_rows(con, "events", merged_event_cols, merged_events)
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

    # ---- 4. merge system state (BUILD FRESH from replay snapshots) ----
    # The state file is rebuilt entirely from the replayed engine snapshots so
    # nothing stale from the previously-frozen system_state.json survives.
    open_positions = {}
    acct = None
    merged_ts = None
    strat_by_snap = {}   # instrument -> snapshot strategies dict
    snap_by_inst = {}    # instrument -> full snapshot (for pnl/account per strategy)
    for name, snap in merged_snaps:
        if not snap:
            continue
        snap_by_inst[name] = snap
        ops = snap.get("positions", {}).get("open_positions", {})
        for k, v in ops.items():
            if v.get("instrument") == name:
                open_positions[k] = v
        if snap.get("strategies"):
            strat_by_snap[name] = snap["strategies"]
        if acct is None and snap.get("account"):
            acct = snap["account"]
        if merged_ts is None and snap.get("timestamp"):
            merged_ts = snap["timestamp"]

    new_state = {}
    if acct is not None:
        new_state["account"] = acct
    if merged_ts is not None:
        new_state["timestamp"] = merged_ts
    new_state["positions"] = {"open_positions": open_positions}

    # Build the strategies section wholesale from each replayed snapshot (correct
    # by construction), then force-sync position_side/state/stop_price against the
    # authoritative merged open_positions so strategy objects always match
    # position_manager on restart (no desync / no resurrected silver SHORTs).
    strategy_state = {}
    strat_by_pos = {
        (pos.get("instrument"), pos.get("strategy_id")): pos
        for pos in open_positions.values()
    }
    for strat_id, strat_cfg in LIVE_STRATEGIES.items():
        sid = strat_cfg.get("instrument")
        snapped = (strat_by_snap.get(sid) or {}).get(strat_id) or {}
        pos = strat_by_pos.get((sid, strat_id))
        if pos is not None:
            side = pos.get("side")
            state_val = "long_position" if side == "LONG" else "short_position"
            strategy_state[strat_id] = {
                **snapped,
                "state": state_val,
                "position_side": side,
                "stop_price": pos.get("stop_price"),
                "pending_entry": None,
            }
        else:
            strategy_state[strat_id] = {
                **snapped,
                "state": "flat",
                "position_side": None,
                "stop_price": None,
                "pending_entry": None,
            }
    new_state["strategies"] = strategy_state

    # Merge the restorable runtime sections per-strategy by instrument.
    # Each replay snapshot runs the full engine but only ONE instrument's bars,
    # so a strategy only realizes P&L / holds margin in the snapshot whose
    # instrument matches ITS instrument. Take each strategy's pnl + per-strategy
    # account from the matching instrument's snapshot (identical pattern to
    # strategy_state above).  Omitting these (as before) restored P&L/accounts
    # to zero at startup -> reconciliation "critical errors" -> SAFE MODE
    # halting all trading.
    pnl_state = {}
    accounts_by_strategy = {}
    for strat_id, strat_cfg in LIVE_STRATEGIES.items():
        sid = strat_cfg.get("instrument")
        snap = snap_by_inst.get(sid) or {}
        pnl = (snap.get("pnl") or {}).get(strat_id)
        if pnl is not None:
            pnl_state[strat_id] = pnl
        acct_s = (snap.get("accounts_by_strategy") or {}).get(strat_id)
        if acct_s is not None:
            accounts_by_strategy[strat_id] = acct_s
    if pnl_state:
        new_state["pnl"] = pnl_state
    if accounts_by_strategy:
        new_state["accounts_by_strategy"] = accounts_by_strategy

    # Representative runtime sections (best-effort from the first non-empty snapshot).
    for key in ("market_status", "risk", "execution"):
        if key in new_state:
            continue
        for _, snap in merged_snaps:
            if snap and snap.get(key):
                new_state[key] = snap[key]
                break

    state_path.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
    n_open = len(open_positions)
    print(f"[State] wrote {n_open} open positions (fresh from replay, {len(strategy_state)} strategies synced, pnl={len(pnl_state)}, accounts={len(accounts_by_strategy)}) -> {state_path}", flush=True)

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
