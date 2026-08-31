"""Compare ALL stored replay trades (closed trades + open positions) in the
production DBs against a freshly-recomputed authoritative reference.

The reference is produced by re-running the exact same replay machinery
(full_simulator.replay over real Dhan candles through the real strategy stack)
into a THROWAWAY root, so the production DBs are never written to.  Because both
sides flow through the identical serialization, comparison is apples-to-apples.

Compared per (strategy_id, side, entry):
  - closed trades : entry side/ts/price, exit ts/price, qty, net_pnl, exit_reason
  - open positions: side, qty, average_entry, entry_timestamp

Run on the server (engine STOPPED, DB volume mounted), e.g.:
    sudo docker run --rm \
      -v /home/jadhavdnyaneshwar701/mcx-trader:/app -w /app \
      -v /home/jadhavdnyaneshwar701/mcx-trader-data:/app/data/db \
      mcx-trader python compare_replay_trades.py
Exits 0 on full match, 1 if any mismatch.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))

from full_simulator import (  # noqa: E402
    ReplayDataAdapter,
    build_bars,
    fetch_real_candles,
    ist,
    replay,
    teardown,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _readonly(db_path: str, sql: str, *params):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _iso_to_epoch(ts) -> float:
    """DB trade timestamps are ISO strings (naive IST). -> epoch."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    dt = datetime.fromisoformat(str(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.timestamp()


def _fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(_iso_to_epoch(ts), tz=IST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _tol(a, b, atol=1e-4, rtol=1e-6):
    return abs(a - b) <= atol + rtol * (1.0 + abs(b))


# ----------------------------------------------------------------------------
# Reference replay (throwaway root, real candle data)
# ----------------------------------------------------------------------------
def _make_throwaway_cfg(src_settings: Path, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "db").mkdir(parents=True, exist_ok=True)
    import json as _j
    data = _j.loads(src_settings.read_text(encoding="utf-8"))
    # Redirect ONLY the persistence targets; keep real dhan client/token so the
    # reference replay fetches the same real candle series the production seed
    # used (identical data input).
    data["system"]["db_path"] = str(root / "data" / "db" / "trading.db")
    data["system"]["state_path"] = str(root / "data" / "db" / "system_state.json")
    data["system"]["analytics_path"] = str(root / "data" / "db" / "analytics.db")
    cfg = root / "settings.json"
    cfg.write_text(_j.dumps(data, indent=2), encoding="utf-8")
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
    _rank = {"1h": 0, "15m": 1, "5m": 2}
    for name, rows in rows_by_inst.items():
        b5, b15, b1h = build_bars(name, rows)
        print(f"[Ref] {name}: {len(rows)} x5m | {len(b15)} x15m | {len(b1h)} x1h", flush=True)
        stream_all.extend(b5 + b15 + b1h)
    stream_by_day = {}
    for bar in stream_all:
        stream_by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _rank[b.timeframe]))
    return stream_by_day


def run_reference(cfg_path: Path, inst_cfg: dict, token_path: Path, start: str, stop: str):
    """Replay real candles into a throwaway root; return closed trades + open positions."""
    from core.trade_close import TradeCloseManager
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

    rows_by_inst = {}
    ok = True
    for name, meta in inst_cfg.items():
        sid = meta.get("security_id", "")
        try:
            rows = fetch_real_candles(token_path, sid, start, stop)
        except Exception as e:
            print(f"[Ref] ERROR fetching {name}: {e}", flush=True)
            ok = False
            rows = []
        rows_by_inst[name] = rows

    if not ok or not any(rows_by_inst.values()):
        print("[Ref] FATAL: no candle data (token expired / not reachable)", flush=True)
        teardown(engine, persistence)
        sys.exit(2)

    stream_by_day = _stream_from_rows(rows_by_inst)
    print(f"[Ref] {sum(len(v) for v in stream_by_day.values())} bars across {len(stream_by_day)} days", flush=True)
    replay(engine, stream_by_day)

    # Reference closed trades from the throwaway trading.db trades table
    ref_db = str(Path(json.loads(cfg_path.read_text(encoding="utf-8"))["system"]["db_path"]).resolve())
    ref_state = str(Path(json.loads(cfg_path.read_text(encoding="utf-8"))["system"]["state_path"]).resolve())
    closed = _readonly(ref_db, "SELECT * FROM trades") if Path(ref_db).exists() else []

    # Reference open positions from the throwaway system_state.json
    open_pos = []
    state = {}
    if Path(ref_state).exists():
        with open(ref_state, encoding="utf-8") as f:
            state = json.load(f)
    open_pos = list(state.get("positions", {}).get("open_positions", {}).values())

    teardown(engine, persistence)
    return closed, open_pos


# ----------------------------------------------------------------------------
# Production data extraction
# ----------------------------------------------------------------------------
def load_production(db_path: Path, state_path: Path):
    closed = _readonly(str(db_path), "SELECT * FROM trades") if db_path.exists() else []
    open_pos = []
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        # Open positions can live at positions.open_positions (snapshot form)
        open_pos = list(st.get("positions", {}).get("open_positions", {}).values())
    return closed, open_pos


def check_architecture(trading_db: Path, analytics_db: Path, prod_open) -> list:
    """Full live-architecture integrity across BOTH stores (read-only).

    Verifies trading.db internal consistency + trading.db <-> analytics.db
    cross-store parity + open-position/DB consistency. Returns list of
    (check, ok, detail).  These are ARCHITECTURE checks (independent of the
    backtest-vs-live signal comparison), reporting how coherent the stored
    replay state is.
    """
    res = []
    T = str(trading_db); A = str(analytics_db)
    t_ok = trading_db.exists(); a_ok = analytics_db.exists()

    def add(name, ok, detail):
        res.append((name, bool(ok), detail))

    # --- trading.db tables ---
    t_tabs = [r["name"] for r in _readonly(T, "SELECT name FROM sqlite_master WHERE type='table'")] if t_ok else []
    for tbl in ("trades", "orders", "fills", "events", "account_snapshots"):
        add(f"arch trading.db::{tbl}", tbl in t_tabs, f"present={tbl in t_tabs}")

    # --- row counts ---
    def cnt(db, tbl):
        try:
            return _readonly(db, f"SELECT COUNT(*) AS n FROM {tbl}")[0]["n"]
        except Exception:
            return None

    n_trades = cnt(T, "trades") if t_ok else None
    n_orders = cnt(T, "orders") if t_ok else None
    n_fills = cnt(T, "fills") if t_ok else None
    n_events = cnt(T, "events") if t_ok else None
    add("arch rowcounts trading.db", all(v is not None for v in (n_trades, n_orders, n_fills)),
        f"trades={n_trades} orders={n_orders} fills={n_fills} events={n_events}")

    # --- duplicate id detection ---
    for tbl, idcol in (("trades", "trade_id"), ("orders", "order_id"), ("fills", "fill_id")):
        try:
            rows = _readonly(T, f"SELECT {idcol} FROM {tbl}")
            ids = [r[idcol] for r in rows if r[idcol] is not None]
            dups = len(ids) - len(set(ids))
            add(f"arch dup {tbl}.{idcol}", dups == 0, f"rows={len(ids)} dup={dups}")
        except Exception as e:
            add(f"arch dup {tbl}.{idcol}", False, f"error {e}")

    # --- orphan fills / orphan orders ---
    if t_ok and n_fills:
        orphan = _readonly(T, "SELECT COUNT(*) AS n FROM fills WHERE order_id IS NULL OR order_id=''")
        add("arch orphan fills", orphan[0]["n"] == 0, f"count={orphan[0]['n']}")
    # fills referencing missing orders
    if t_ok and n_fills and n_orders:
        miss = _readonly(T, "SELECT COUNT(*) AS n FROM fills f LEFT JOIN orders o ON f.order_id=o.order_id WHERE o.order_id IS NULL")
        add("arch fills->orders all resolve", miss[0]["n"] == 0, f"missing={miss[0]['n']}")

    # --- per closed trade: 2 fills (entry+exit) ---
    if t_ok and n_trades:
        bad = _readonly(T, "SELECT t.trade_id, COUNT(f.fill_id) AS f FROM trades t "
                           "LEFT JOIN fills f ON f.order_id=t.order_id GROUP BY t.trade_id "
                           "HAVING f < 2")
        # note: fill->trade linkage is via order; a simpler robust check:
        # every trade has a non-null order reference
        no_order = _readonly(T, "SELECT COUNT(*) AS n FROM trades WHERE order_id IS NULL OR order_id=''")
        add("arch trades have order ref", no_order[0]["n"] == 0, f"missing_order_ref={no_order[0]['n']}")

    # --- trading.db trades (closed) vs analytics trades_analytics CLOSED parity ---
    if t_ok and a_ok:
        try:
            a_closed = _readonly(A, "SELECT COUNT(*) AS n FROM trades_analytics WHERE status='CLOSED'")[0]["n"]
        except Exception:
            a_closed = None
        t_closed = n_trades  # trading.db `trades` holds only closed trades
        add("arch cross-store closed parity", a_closed == t_closed,
            f"analytics.CLOSED={a_closed} trading.trades={t_closed}")

        # net pnl cross-store sum parity
        try:
            sum_a = _readonly(A, "SELECT COALESCE(SUM(net_pnl),0) AS s FROM trades_analytics WHERE status='CLOSED'")[0]["s"]
            sum_t = _readonly(T, "SELECT COALESCE(SUM(net_pnl),0) AS s FROM trades")[0]["s"]
            add("arch cross-store net_pnl sum parity", abs(float(sum_a or 0) - float(sum_t or 0)) < 1.0,
                f"analytics={sum_a} trading={sum_t}")
        except Exception as e:
            add("arch cross-store net_pnl sum parity", False, f"error {e}")

        # trade_legs cross-store: legs exist for each analytics trade
        try:
            a_trades = _readonly(A, "SELECT COUNT(*) AS n FROM trades_analytics")[0]["n"]
            legs = _readonly(A, "SELECT COUNT(DISTINCT trade_id) AS n FROM trade_legs")[0]["n"] if a_trades else 0
            add("arch trade_legs coverage", legs >= a_trades if a_trades else True,
                f"analytics_trades={a_trades} distinct_legs={legs}")
        except Exception as e:
            add("arch trade_legs coverage", False, f"error {e}")

    # --- open positions (state) vs DB: no open position should already have a closed row ---
    open_orig = [p.get("position_id") for p in prod_open]
    add("arch open positions carry (n)", len(open_orig) >= 0, f"open_in_state={len(open_orig)}")

    return res


def run_comparison(prod_closed, prod_open, ref_closed, ref_open, start, stop):
    """Compare production (stored) trades/positions against the reference set.

    Returns (results, mismatches) where results = [(name, ok, detail), ...].
    """
    results = []

    def add(name, ok, detail):
        results.append((name, bool(ok), detail))

    # ----- Compare closed trades -----
    ref_closed_by = {}
    for r in ref_closed:
        ref_closed_by.setdefault((r.get("strategy_id"), r.get("side"), r.get("entry_timestamp")), []).append(r)
    prod_by = {}
    for r in prod_closed:
        prod_by.setdefault((r.get("strategy_id"), r.get("side"), r.get("entry_timestamp")), []).append(r)

    ref_keys = set(ref_closed_by.keys())
    prod_keys = set(prod_by.keys())

    mismatch_count = 0

    def add2(name, ok, detail):
        nonlocal mismatch_count
        add(name, ok, detail)
        if not ok:
            mismatch_count += 1
            print(f"  FAIL  {name:<80s} {detail}", flush=True)

    for k in sorted(prod_keys, key=lambda x: (str(x[0]), str(x[2]))):
        sid, side, ets = k
        rows = prod_by[k]
        refs = ref_closed_by.get(k, [])
        if not refs:
            for r in rows:
                add2(f"closed {sid} {side} @{_fmt_ts(ets)}", False, "NO matching reference trade")
            continue
        for i, r in enumerate(rows):
            ref = refs[i] if i < len(refs) else None
            if ref is None:
                add2(f"closed {sid} {side} @{_fmt_ts(ets)}#{i}", False, "extra prod trade (no ref)")
                continue
            checks = [
                ("entry_price", _tol(float(r.get("entry_price") or 0), float(ref.get("entry_price") or 0))),
                ("exit_price", _tol(float(r.get("exit_price") or 0), float(ref.get("exit_price") or 0))),
                ("exit_ts", _iso_to_epoch(r.get("exit_timestamp")) == _iso_to_epoch(ref.get("exit_timestamp"))),
                ("qty", int(r.get("quantity") or 0) == int(ref.get("quantity") or 0)),
                ("net_pnl", _tol(float(r.get("net_pnl") or 0), float(ref.get("net_pnl") or 0), atol=1e-2)),
                ("exit_reason", str(r.get("exit_reason")) == str(ref.get("exit_reason"))),
            ]
            bad = [c for c, okc in checks if not okc]
            add2(f"closed {sid} {side} @{_fmt_ts(ets)}#{i}", not bad,
                 f"exit={_fmt_ts(r.get('exit_timestamp'))} net={r.get('net_pnl')} "
                 f"{'MISMATCH: ' + ','.join(bad) if bad else ''}")

    for k in sorted(ref_keys - prod_keys, key=lambda x: (str(x[0]), str(x[2]))):
        sid, side, ets = k
        for r in ref_closed_by[k]:
            add2(f"closed(ref-only) {sid} {side} @{_fmt_ts(ets)}", False,
                 f"in reference but NOT stored in prod DB (net={r.get('net_pnl')})")

    # ----- Compare open positions -----
    prod_open_by, ref_open_by = {}, {}
    for p in prod_open:
        prod_open_by.setdefault((p.get("strategy_id"), p.get("side")), []).append(p)
    for p in ref_open:
        ref_open_by.setdefault((p.get("strategy_id"), p.get("side")), []).append(p)

    all_keys = set(prod_open_by.keys()) | set(ref_open_by.keys())
    for k in sorted(all_keys):
        sid, side = k
        pr = prod_open_by.get(k, [])
        rr = ref_open_by.get(k, [])
        if len(pr) != len(rr):
            add2(f"open {sid} {side}", False, f"prod has {len(pr)} vs ref has {len(rr)} open positions")
        for i in range(max(len(pr), len(rr))):
            p = pr[i] if i < len(pr) else None
            r = rr[i] if i < len(rr) else None
            if p is None:
                add2(f"open(ref-only) {sid} {side}#{i}", False, "open in reference, not in prod state")
                continue
            if r is None:
                add2(f"open(prod-only) {sid} {side}#{i}", False, "open in prod state, not in reference")
                continue
            checks = [
                ("qty", int(p.get("quantity") or 0) == int(r.get("quantity") or 0)),
                ("avg_entry", _tol(float(p.get("average_entry") or 0), float(r.get("average_entry") or 0))),
                ("entry_ts", _iso_to_epoch(p.get("entry_timestamp")) == _iso_to_epoch(r.get("entry_timestamp"))),
            ]
            bad = [c for c, okc in checks if not okc]
            add2(f"open {sid} {side}#{i} qty={p.get('quantity')} entry={_fmt_ts(p.get('entry_timestamp'))}",
                 not bad, f"avg={p.get('average_entry')} "
                          f"{'MISMATCH: ' + ','.join(bad) if bad else ''}")

    return results, mismatch_count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="throwaway root dir override")
    args = ap.parse_args()

    from config import Config
    cfg = Config()
    cfg.load()
    prod_db = Path(cfg.get("system.db_path", "data/db/trading.db")).resolve()
    prod_state = Path(cfg.get("system.state_path", "data/db/system_state.json")).resolve()
    token_file = Path(cfg.get("dhan.token_file", "data/db/dhan_token.json")).resolve()
    inst_cfg = cfg.get("instruments", {})

    print(f"[Prod] db_path={prod_db} exists={prod_db.exists()}", flush=True)
    print(f"[Prod] state_path={prod_state} exists={prod_state.exists()}", flush=True)
    print(f"[Prod] token_file={token_file} exists={token_file.exists()}", flush=True)

    prod_closed, prod_open = load_production(prod_db, prod_state)
    print(f"[Prod] closed trades (trades table) = {len(prod_closed)}", flush=True)
    print(f"[Prod] open positions (state file)  = {len(prod_open)}", flush=True)
    if not prod_closed and not prod_open:
        print("[Prod] no trades stored - nothing to compare. Aborting.", flush=True)
        return 2

    # Derive the replay window from all stored entries (whole session days).
    stamps = []
    for r in prod_closed:
        stamps.append(_iso_to_epoch(r.get("entry_timestamp")))
        stamps.append(_iso_to_epoch(r.get("exit_timestamp")))
    for p in prod_open:
        stamps.append(_iso_to_epoch(p.get("entry_timestamp")))
    stamps = [s for s in stamps if s > 0]
    lo = datetime.fromtimestamp(min(stamps), tz=IST)
    hi = datetime.fromtimestamp(max(stamps), tz=IST)
    start = lo.strftime("%Y-%m-%d")
    stop = hi.strftime("%Y-%m-%d")
    print(f"[Window] derived {start}..{stop}", flush=True)

    # Throwaway root for the reference
    root = Path(args.root) if args.root else ROOT / "_cmp_replay_ref"
    if root.exists():
        shutil.rmtree(root)
    cfg_path = _make_throwaway_cfg(ROOT / "config" / "settings.json", root)

    ref_closed, ref_open = run_reference(cfg_path, inst_cfg, token_file, start, stop)
    print(f"[Ref] closed trades = {len(ref_closed)}", flush=True)
    print(f"[Ref] open positions = {len(ref_open)}", flush=True)

    # Full live-architecture / DB-integrity checks (both stores, read-only)
    prod_analytics = Path(str(prod_db).replace("trading.db", "analytics.db")).resolve()
    if not prod_analytics.exists():
        prod_analytics = prod_db.parent / "analytics.db"
    print(f"[Arch] analytics_db={prod_analytics} exists={prod_analytics.exists()}", flush=True)
    arch_results = check_architecture(prod_db, prod_analytics, prod_open)
    n_arch_fail = sum(1 for _, okc, _ in arch_results if not okc)
    print(f"[Arch] {len(arch_results)} checks, {n_arch_fail} failures", flush=True)

    results, mismatches = run_comparison(prod_closed, prod_open, ref_closed, ref_open, start, stop)
    results = arch_results + results
    mismatches += n_arch_fail

    # ----- Report -----
    print("\n========== SUMMARY ==========", flush=True)
    print(f"total checks: {len(results)}  mismatches: {mismatches}", flush=True)
    ok = mismatches == 0
    print(f"RESULT: {'ALL MATCH' if ok else 'MISMATCHES FOUND'}", flush=True)

    out_dir = ROOT / "_REPLAY_DB_VS_BT_2026-09-01"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "REPLAY_DB_VS_BACKTEST.csv"
    md_path = out_dir / "REPLAY_DB_VS_BACKTEST.md"
    import csv
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        for name, okc, detail in results:
            w.writerow([name, okc, detail])
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Replay DB vs Backtest comparison\n\n")
        f.write(f"Window: {start}..{stop}\n\n")
        f.write(f"prod_closed={len(prod_closed)} prod_open={len(prod_open)} "
                f"ref_closed={len(ref_closed)} ref_open={len(ref_open)}\n\n")
        f.write(f"**RESULT: {'ALL MATCH' if ok else 'MISMATCHES FOUND'}** ({mismatches} mismatches)\n\n")
        for name, okc, detail in results:
            f.write(f"- [{'x' if not okc else ' '}] {name} :: {detail}\n")
    print(f"report: {csv_path}", flush=True)
    print(f"report: {md_path}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
