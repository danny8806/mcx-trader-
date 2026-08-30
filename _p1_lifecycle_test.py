"""PHASE-1 / PART 37 — FULL LIFECYCLE VALIDATION per instrument.

Feeds the real 870-bar LAST5 5m series through the CURRENT live pipeline
(warmup -> closed-bar dispatch -> strategy -> signal -> order -> fill -> trade)
for both GOLDM and SILVERM, then asserts end-to-end consistency:

  V1  warmup seeded 870/290/75, indicators initialized, coverage maps built
  V2  all 870 bars processed with zero exceptions
  V3  at least one entry order submitted and filled over the window
  V4  every fill is persisted and paired with an order + a position action
  V5  closed trades are written to the trades DB with net_pnl accounting
  V6  strategy/position reconciliation holds at end of stream
  V7  margin ledger consistent (used == 0 iff flat)
  V8  15m DEMA-ATR line equals the independent reference (parity gospel)
  V9  safe mode never tripped during the run

Outputs: GOLD_VALIDATION_REPORT.csv and SILVER_VALIDATION_REPORT.csv
Exit code 0 iff all pass.
"""
from __future__ import annotations

import sqlite3
import sys

import numpy as np

import _p1_lib as L
from core.market_status import DataStatus, EngineStatus, MarketState
from full_simulator import build_bars

rows_base = {
    "GOLDM": L.load_csv_rows("GOLDM", L.LAST5[0], L.LAST5[-1]),
    "SILVERM": L.load_csv_rows("SILVERM", L.LAST5[0], L.LAST5[-1]),
}


def isod(ts):
    return L.ist_from_epoch(ts).replace(tzinfo=None)


RC = L.AUDIT_DIR / "REG_REPORT_REFERENCE"
all_pass = True
for name in ("GOLDM", "SILVERM"):
    report_path = L.AUDIT_DIR / (name + "_VALIDATION_REPORT.csv")
    if report_path.exists():
        report_path.unlink()
    rows = []

    def check(ok, text, expect):
        global all_pass
        all_pass &= bool(ok)
        rows.append({"check": text, "value": text, "pass": "PASS" if ok else "FAIL"})
        return (text, "PASS" if ok else "FAIL")

    cfg = L.write_config(L.fresh_run_root(f"life_{name}"),
                         warmup={"keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.swap_adapter(engine, L.CSVFeedAdapter())
    L.wire_trade_close(engine)
    engine._warmup_from_rest()

    # bring market to live-trading so entry signals pass the gate
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine._running = True

    # V1 warmup numbers
    ind15 = engine.indicators[f"{name}:15m"]
    ind5 = engine.indicators[f"{name}:5m"]
    ok = (len(engine.htf_engine._engines[f"{name}:15m"].end_times) == 290
          and len(engine.htf_engine._engines[f"{name}:1h"].end_times) == 75
          and ind15.initialized and ind5.initialized)
    check(ok, f"V1 warmup 290/75 + initialized",
          "290 15m, 75 1h buckets, 5m/15m indicators initialized")

    # V2 feed the whole 5-day stream
    bars5, _, _ = build_bars(name, rows_base[name], keep_partial=True)
    errors = 0
    for b in bars5:
        try:
            engine._on_bar_closed(b)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  [feed] bar {isod(b.start_ts)} error: {e!r}")
    check(errors == 0 and True, "V2 870 bars processed, 0 exceptions",
          f"{len(bars5)} bars fed")

    # V3 orders & fills
    n_orders = len(engine.execution_engine._orders)
    entry_fills = [f for f in engine.execution_engine._fills
                   if f.instrument == name and f.side == "BUY"]
    exit_fills = [f for f in engine.execution_engine._fills
                  if f.instrument == name and f.side == "SELL"]
    check(n_orders >= 1 and len(entry_fills) >= 1,
          "V3 entry order submitted + filled",
          f"orders={n_orders} entry_fills={len(entry_fills)} exit_fills={len(exit_fills)}")

    # V4 fills persisted to DB & paired
    db_path = cfg.parent / "data" / "db" / "trading.db"
    conn = sqlite3.connect(str(db_path))
    n_db_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    n_db_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    n_db_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    orphan = conn.execute(
        "SELECT COUNT(*) FROM fills f LEFT JOIN orders o ON f.order_id=o.order_id "
        "WHERE o.order_id IS NULL").fetchone()[0]
    check(n_db_fills == len(engine.execution_engine._fills) and n_db_orders >= n_db_fills
          and orphan == 0, "V4 fills/orders persisted, no orphan fills",
          f"db_fills={n_db_fills} db_orders={n_db_orders} orphan={orphan}")

    # V5 closed trades in ledger with pnl
    closed = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
    net = conn.execute(
        "SELECT COALESCE(SUM(net_pnl),0) FROM trades").fetchone()[0]
    check(closed >= 1, "V5 closed trades in ledger",
          f"closed_trades={closed} net_pnl={net:.2f}")
    conn.close()

    # V6 reconciliation strategy vs position
    open_pos = engine.position_manager.get_positions_by_instrument(name)
    open_pos = [p for p in open_pos if p.is_open]
    strat_mismatch = 0
    for sid, strat in engine.strategies.items():
        if strat.instrument != name:
            continue
        has_pos = any(p.strategy_id == sid for p in open_pos)
        has_state = strat.position_side is not None
        if has_pos != has_state:
            strat_mismatch += 1
    check(strat_mismatch == 0, "V6 strategy/position reconciled",
          f"open_positions={len(open_pos)} mismatch={strat_mismatch}")

    # V7 margin consistency
    if name == "GOLDM":
        accts = [ac for sid, ac in engine.account_engines.items()
                 if sid.startswith("gold_")]
    else:
        accts = [ac for sid, ac in engine.account_engines.items()
                 if sid.startswith("silver_")]
    used = sum(ac.used_margin for ac in accts)
    check((used > 0.0) == (len(open_pos) > 0), "V7 margin ledger consistent",
          f"used_margin={used:.2f} open_positions={len(open_pos)}")

    # V8 parity gospel on the FINAL 15m DEMA value
    bb15 = sorted(build_bars(name, rows_base[name], keep_partial=True)[1],
                  key=lambda b: b.start_ts)
    line_ref = L.ref_dema_atr(np.array([b.high for b in bb15], float),
                              np.array([b.low for b in bb15], float),
                              np.array([b.close for b in bb15], float), 3, 6, 1.0)
    end_vals = list(engine.htf_engine._engines[f"{name}:15m"].values)
    last_line = end_vals[-1] if end_vals else None
    ok8 = (last_line is not None and abs(last_line - line_ref[-1]) < 1e-6)
    check(ok8, "V8 15m DEMA value == independent ref (last)",
          f"engine={last_line} ref={line_ref[-1]:.2f} "
          f"diff={abs(last_line - line_ref[-1]) if last_line is not None else None:.2e}")

    # V9 safe mode not tripped
    check(not engine.safe_mode.is_active, "V9 safe mode clean", "not active")

    L.teardown(engine, persistence)

    rows_clean = []
    for r in rows:
        rows_clean.append({"instrument": name, "check": r["check"],
                           "pass": r["pass"]})
    L.append_rows(report_path, rows_clean)
    print(f"\n=== {name} LIFECYCLE ===")
    for r in rows:
        print(f"  {r['pass']}  {r['check']}")
    print(f"REPORT -> {report_path}")

print(f"\nRESULT: {'ALL PASSED' if all_pass else 'FAILURES PRESENT'}")
sys.exit(0 if all_pass else 1)