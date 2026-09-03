"""Local helper: build a realistic production-like trading.db + system_state.json
from the OFFLINE CSV replay of the real native candles, so audit_signal_candles.py
can be validated end-to-end locally (where the real server DB is not present).

This reproduces the same engine/trades the audit re-runs, giving a truth set the
PART A per-trade linkage should match 1:1.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _p1_lib as L
from full_simulator import LIVE_STRATEGIES, build_bars

START, STOP = "2026-08-26", "2026-08-31"
_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}


def run(name, root):
    import full_simulator as FS
    from core.market_status import DataStatus, EngineStatus, MarketState
    _TF_RANK = {"1h": 0, "15m": 1, "5m": 2}
    rows = L.load_csv_rows(name, START, STOP)
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
        stream_by_day.setdefault(FS.ist(bar.end_ts).date(), []).append(bar)
    for day in stream_by_day:
        stream_by_day[day].sort(key=lambda b: (b.end_ts, _TF_RANK.get(b.timeframe, 3)))

    FS.replay(engine, stream_by_day)
    persistence.save_state(engine.snapshot())
    L.teardown(engine, persistence)


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else L.fresh_run_root("prodseed")
    out_db = ROOT / "_local_prod" / "trading.db"
    out_state = ROOT / "_local_prod" / "system_state.json"
    out_db.parent.mkdir(parents=True, exist_ok=True)

    import time
    run_id = int(time.time())
    # collect trades from both instruments into one DB
    trades_all = []
    for name in ("GOLDM", "SILVERM"):
        r = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"prodseed_{name}_{run_id}"
        r.mkdir(parents=True, exist_ok=True)
        run(name, r)
        dbp = r / "data" / "db" / "trading.db"
        if dbp.exists():
            con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            trades_all += [dict(x) for x in con.execute("SELECT * FROM trades").fetchall()]
            con.close()
        # merge system state open positions
        stp = r / "data" / "db" / "system_state.json"
        if stp.exists():
            pass  # handled separately below

    # write combined trading.db
    if out_db.exists():
        out_db.unlink()
    schema_tpl = None
    for name in ("GOLDM", "SILVERM"):
        dbp = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"prodseed_{name}_{run_id}" / "data" / "db" / "trading.db"
        if dbp.exists() and schema_tpl is None:
            con = sqlite3.connect(str(dbp))
            schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
            schema_tpl = schema[0] if schema else None
            con.close()
    con = sqlite3.connect(str(out_db))
    if schema_tpl:
        con.execute(schema_tpl)
    cols = [d[1] for d in con.execute("PRAGMA table_info(trades)")]
    placeholders = ",".join("?" * len(cols))
    auto = 1
    for tr in trades_all:
        vals = [tr.get(c) for c in cols]
        if "id" in cols:
            vals[cols.index("id")] = auto
            auto += 1
        con.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({placeholders})", vals)
    con.commit()
    con.close()

    # merge open positions from each instrument's state file
    open_positions = {}
    for name in ("GOLDM", "SILVERM"):
        stp = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"prodseed_{name}_{run_id}" / "data" / "db" / "system_state.json"
        if not stp.exists():
            continue
        with open(stp, encoding="utf-8") as f:
            st = json.load(f)
        ops = st.get("positions", {}).get("open_positions", {})
        for k, v in ops.items():
            if v.get("instrument") == name:
                open_positions[k] = v
    state = {"positions": {"open_positions": open_positions}}
    out_state.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"local prod db  : {out_db}  (trades={len(trades_all)})", flush=True)
    print(f"local prod state: {out_state}  (open={len(open_positions)})", flush=True)


if __name__ == "__main__":
    main()
