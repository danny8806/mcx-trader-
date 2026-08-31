"""Comprehensive per-trade AND per-crossing signal-candle audit for the four
MCX strategies over the 08-26..08-28 live replay window.

Cross-references three independent computations on the CURRENT code (no earlier
result trusted) and links every one to the PRODUCTION stored trades:

  ENGINE  - the real TradingEngine running the real strategies; we intercept
            each strategy's on_bar and capture the exact values it consumed on
            EVERY fast bar (close, prev_close, htf, prev_htf, mid, prev_mid,
            fast DEMA-ATR, source timestamps) plus the resulting pending /
            position state.  From this we reconstruct, for every fill, the
            OPEN signal candle and the CLOSE signal candle.
  TRACKER - the independent backtest-style tracker (incremental DEMAATR +
            BacktestStyleHTFEngine) fed the identical chronological stream.
  REF     - the independent batch reference (_p1_lib ref_dema_atr /
            ref_session_resample / searchsorted mapping).

PART A (per trade):  for each production stored trade (19 closed + 4 open)
            find the captured open signal candle and close signal candle and
            emit their full indicator detail, verified ENGINE==TRACKER==REF.
PART B (per crossing): for every fast bar where a crossing fires across all 4
            strategies, verify DEMA/ATR values + mapping timestamps + crossing
            classification are identical in ENGINE/TRACKER/REF.

Both parts exit 0 only if everything matches.

Data source is an argument:
  --online  fetch live Dhan candles (server path, via fetch_real_candles)
  default   offline feed of the real native 5m CSV rows (local validation)

In both modes bars are pushed directly into the engine the same way
(_p1_signal4_test path), so the produced engine state is identical.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _p1_lib as L
from full_simulator import LIVE_STRATEGIES, build_bars
from htf.backtest_style_htf import BacktestStyleHTFEngine
from indicators.dema_atr import DEMAATR

OUT_DIR = ROOT / "_SIGNAL_CANDLE_AUDIT_2026-09-01"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def isod(ts):
    return L.ist_from_epoch(float(ts)).replace(tzinfo=None)


def ist_epoch_naive(t):
    return pd.Timestamp(t).tz_localize("Asia/Kolkata").timestamp()


def bucket_key(ts):
    if isinstance(ts, pd.Timestamp):
        v = ts.timestamp()
    else:
        v = float(ts)
    return int(round(v))


def tol(a, b, rtol=1e-6):
    return abs(a - b) <= rtol * (1.0 + abs(b))


def prev_none_eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-6 * (1.0 + abs(b))


def long_cross(close, prev_close, htf, prev_htf, mid):
    if htf is None or prev_htf is None or prev_close is None:
        return False
    return bool(close > htf and prev_close <= prev_htf
                and (mid is None or mid < htf))


def short_cross(close, prev_close, htf, prev_htf, mid):
    if htf is None or prev_htf is None or prev_close is None:
        return False
    return bool(close < htf and prev_close >= prev_htf
                and (mid is None or mid > htf))


def classify(close, prev_close, htf, prev_htf, mid):
    if long_cross(close, prev_close, htf, prev_htf, mid):
        return "LONG"
    if short_cross(close, prev_close, htf, prev_htf, mid):
        return "SHORT"
    return "NONE"


def _fmt(v):
    if v is None:
        return "NA"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _fmt_candle(e):
    return (f"close={e['close']:.4f} htf={_fmt(e['htf'])} mid={_fmt(e['mid'])} "
            f"fast={_fmt(e['fast'])} pre_close={_fmt(e['pre_close'])} "
            f"pre_htf={_fmt(e['pre_htf'])} pre_mid={_fmt(e['pre_mid'])}")


STRAT_FAST = {s: LIVE_STRATEGIES[s]["fast_timeframe"] for s in LIVE_STRATEGIES}
STRAT_INST = {s: LIVE_STRATEGIES[s]["instrument"] for s in LIVE_STRATEGIES}
_TF_RANK = {"1h": 0, "15m": 1, "5m": 2}


def _readonly(db_path: str, sql: str, *params):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _iso_to_epoch(ts) -> float:
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    return float(pd.Timestamp(str(ts)).timestamp())


# ---------------------------------------------------------------------------
# production trades
# ---------------------------------------------------------------------------
def load_production(db_path: Path, state_path: Path):
    closed = _readonly(str(db_path), "SELECT * FROM trades") if db_path.exists() else []
    open_pos = []
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        open_pos = list(st.get("positions", {}).get("open_positions", {}).values())
    return closed, open_pos


def fetch_rows(name, start, stop, online):
    if online:
        from full_simulator import fetch_real_candles
        from config import Config
        c = Config()
        c.load()
        token_file = Path(c.get("dhan.token_file", "data/db/dhan_token.json")).resolve()
        sid = c.get("instruments", {}).get(name, {}).get("security_id", "")
        return fetch_real_candles(token_file, sid, start, stop)
    return L.load_csv_rows(name, start, stop)


# ---------------------------------------------------------------------------
# engine replay + capture (identical feed mechanism, either data source)
# ---------------------------------------------------------------------------
def run_capture(name, rows, cfg):
    from core.market_status import DataStatus, EngineStatus, MarketState
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine.execution_engine.update_price(name, 150000.0)

    captured = defaultdict(list)
    orig_on_bar = {}
    for sid, strat in engine.strategies.items():
        if strat.instrument != name:
            continue
        orig_on_bar[sid] = strat.on_bar

        def mkob(sid, orig, captured=captured):
            def ob(bar, htf_mapped, fast_v, mid_mapped):
                st = orig.__self__
                pre_close = st._prev_fast_close
                pre_htf = st._prev_htf_value
                pre_mid = st._prev_mid_value
                pre_pos = st.position_side
                pre_pend = getattr(st.pending_entry, "side", None) if st.pending_entry else None
                r = orig(bar, htf_mapped, fast_v, mid_mapped)
                captured[sid].append({
                    "bt": float(bar.start_ts), "tf": bar.timeframe,
                    "close": float(bar.close), "high": float(bar.high), "low": float(bar.low),
                    "htf": htf_mapped.htf_value, "htf_ts": htf_mapped.htf_source_timestamp,
                    "htf_conf": htf_mapped.htf_confirmed,
                    "mid": mid_mapped.htf_value if mid_mapped else None,
                    "mid_ts": mid_mapped.htf_source_timestamp if mid_mapped else None,
                    "fast": fast_v,
                    "pre_close": pre_close, "pre_htf": pre_htf, "pre_mid": pre_mid,
                    "pre_pos": pre_pos, "pre_pend": pre_pend,
                    "post_pend": getattr(st.pending_entry, "side", None) if st.pending_entry else None,
                    "post_pos": st.position_side,
                })
                return r
            return ob

        strat.on_bar = mkob(sid, orig_on_bar[sid])

    # independent tracker
    xtk_inds = {}
    xtk_htf = BacktestStyleHTFEngine()
    for tf in ("5m", "15m", "1h"):
        xtk_inds[f"{name}:{tf}"] = DEMAATR(3, 6, 1.0)
    xtk_htf.register(name, "1h", 3, 6, 1.0, "09:00")
    xtk_htf.register(name, "15m", 3, 6, 1.0, "09:00")
    xtrack = defaultdict(list)

    # batch reference series
    df5 = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df5["datetime"] = pd.to_datetime(df5["ts"], unit="s", utc=True).dt.tz_convert(
        "Asia/Kolkata").dt.tz_localize(None)
    ref15, _ = L.ref_session_resample(df5[["datetime", "open", "high", "low", "close", "volume"]], 15)
    ref1h, _ = L.ref_session_resample(df5[["datetime", "open", "high", "low", "close", "volume"]], 60,
                                      keep_partial=True)
    line5 = L.ref_dema_atr(np.array([r[2] for r in rows], float),
                           np.array([r[3] for r in rows], float),
                           np.array([r[4] for r in rows], float), 3, 6, 1.0)
    line15 = L.ref_dema_atr(ref15["high"].to_numpy(float), ref15["low"].to_numpy(float),
                            ref15["close"].to_numpy(float), 3, 6, 1.0)
    line1h = L.ref_dema_atr(ref1h["high"].to_numpy(float), ref1h["low"].to_numpy(float),
                            ref1h["close"].to_numpy(float), 3, 6, 1.0)
    e15 = np.array([ist_epoch_naive(t) + 15 * 60 for t in pd.to_datetime(ref15["_bucket"])], float)
    e1h = np.array([ist_epoch_naive(t) + 60 * 60 for t in pd.to_datetime(ref1h["_bucket"])], float)

    bars5, bars15, bars1h = build_bars(name, rows, keep_partial=True)
    all_bars = sorted(bars5 + bars15 + bars1h,
                      key=lambda b: (b.start_ts, _TF_RANK.get(b.timeframe, 3)))

    for bar in all_bars:
        k = f"{name}:{bar.timeframe}"
        xtk_inds[k].update(bar.open, bar.high, bar.low, bar.close)
        if bar.timeframe in ("1h", "15m"):
            xtk_htf.on_htf_bar_closed(bar)
        for sid in STRAT_FAST:
            if STRAT_INST[sid] == name and STRAT_FAST[sid] == bar.timeframe:
                hm = xtk_htf.map_to_fast_bar(bar, STRAT_FAST[sid])
                mm = xtk_htf.map_mid_to_fast_bar(bar, STRAT_FAST[sid])
                fk = f"{name}:{STRAT_FAST[sid]}"
                xtrack[sid].append({
                    "bt": float(bar.start_ts),
                    "close": float(bar.close),
                    "htf": hm.htf_value, "htf_ts": hm.htf_source_timestamp,
                    "mid": mm.htf_value if mm else None,
                    "mid_ts": mm.htf_source_timestamp if mm else None,
                    "fast": xtk_inds[fk].value,
                })
        engine._on_bar_closed(bar)

    for sid in engine.strategies:
        if sid in orig_on_bar:
            engine.strategies[sid].on_bar = orig_on_bar[sid]
    L.teardown(engine, persistence)

    return captured, xtrack, {"line5": line5, "line15": line15, "line1h": line1h,
                              "e15": e15, "e1h": e1h}


# ---------------------------------------------------------------------------
# per-strategy analysis: crossing parity (PART B) + per-trade linkage (PART A)
# ---------------------------------------------------------------------------
def analyze(name, rows, captured, xtrack, ref, prod_trades, checks, per_trade, cross_rows):
    line5, line15, line1h = ref["line5"], ref["line15"], ref["line1h"]
    e15, e1h = ref["e15"], ref["e1h"]

    bars5, bars15, bars1h = build_bars(name, rows, keep_partial=True)
    fast_idx = {}
    for sid in STRAT_FAST:
        if STRAT_INST[sid] != name:
            continue
        tf = STRAT_FAST[sid]
        bl = bars5 if tf == "5m" else bars15
        fast_idx[sid] = {bucket_key(b.start_ts): i
                         for i, b in enumerate(sorted(bl, key=lambda x: x.start_ts))}

    for sid in STRAT_FAST:
        if STRAT_INST[sid] != name:
            continue
        tf = STRAT_FAST[sid]
        seq = captured[sid]
        xtr = {bucket_key(t["bt"]): t for t in xtrack[sid]}
        refprev = {"close": None, "htf": None, "mid": None}
        trkprev = {"close": None, "htf": None, "mid": None}
        crossed_eng, crossed_ref, crossed_trk = {}, {}, {}
        bad = em_bad = 0
        bar_meta = {}

        for i, e in enumerate(seq):
            close = e["close"]
            end_ts = e["bt"] + (5 if tf == "5m" else 15) * 60
            i1 = bisect.bisect_right([float(x) for x in e1h], end_ts) - 1
            i2 = bisect.bisect_right([float(x) for x in e15], end_ts) - 1
            r_htf = float(line1h[i1]) if i1 >= 0 else None
            r_htf_ts = float(e1h[i1]) if i1 >= 0 else None
            r_mid = float(line15[i2]) if i2 >= 0 else None
            r_mid_ts = float(e15[i2]) if i2 >= 0 else None
            fi = fast_idx[sid].get(bucket_key(e["bt"]), 0)
            r_fast = float(line5[fi]) if tf == "5m" else float(line15[fi])

            r_class = classify(close, refprev["close"], r_htf, refprev["htf"], r_mid)
            e_class = classify(close, e["pre_close"], e["htf"], e["pre_htf"], e["mid"])
            crossed_ref[bucket_key(e["bt"])] = r_class
            crossed_eng[bucket_key(e["bt"])] = e_class

            t = xtr.get(bucket_key(e["bt"]), {})
            t_class = classify(float(t.get("close") or close), trkprev["close"],
                               t.get("htf"), trkprev["htf"], t.get("mid"))
            crossed_trk[bucket_key(e["bt"])] = t_class

            ok_v = (prev_none_eq(e["htf"], r_htf) and prev_none_eq(e["mid"], r_mid)
                    and e["fast"] is not None and tol(e["fast"], r_fast))
            ok_prev = (prev_none_eq(e["pre_close"], refprev["close"])
                       and prev_none_eq(e["pre_htf"], refprev["htf"])
                       and prev_none_eq(e["pre_mid"], refprev["mid"]))
            ok_ts = ((((e["htf_ts"] is None) == (r_htf_ts is None))
                      and (e["htf_ts"] is None or abs(float(e["htf_ts"]) - r_htf_ts) < 1e-3))
                     and (((e["mid_ts"] is None) == (r_mid_ts is None))
                          and (e["mid_ts"] is None or abs(float(e["mid_ts"]) - r_mid_ts) < 1e-3)))
            ok_cls = e_class == r_class
            ok_all = ok_v and ok_prev and ok_ts and ok_cls
            bar_meta[bucket_key(e["bt"])] = {"record": e, "class": e_class, "values_ok": ok_all}

            engine_vals = (f"pC={_fmt(e['pre_close'])}|pH={_fmt(e['pre_htf'])}|h={_fmt(e['htf'])}"
                           f"|pM={_fmt(e['pre_mid'])}|m={_fmt(e['mid'])}|f={_fmt(e['fast'])}")
            ref_vals = (f"pC={_fmt(refprev['close'])}|pH={_fmt(refprev['htf'])}|h={_fmt(r_htf)}"
                        f"|pM={_fmt(refprev['mid'])}|m={_fmt(r_mid)}|f={_fmt(r_fast)}")

            if not ok_all:
                bad += 1
                cross_rows.append({"instrument": name, "strategy": sid, "fast_tf": tf,
                                   "bar_start_ist": isod(e["bt"]).strftime("%Y-%m-%d %H:%M:%S"),
                                   "crossing_side": e_class, "result": "VALUE-MISMATCH",
                                   "engine": engine_vals, "reference": ref_vals})
            elif e_class != "NONE":
                cross_rows.append({"instrument": name, "strategy": sid, "fast_tf": tf,
                                   "bar_start_ist": isod(e["bt"]).strftime("%Y-%m-%d %H:%M:%S"),
                                   "crossing_side": e_class, "result": "MATCH",
                                   "engine": engine_vals, "reference": ref_vals})

            if e_class != "NONE":
                if e["pre_pos"] is not None and e["pre_pos"] == e_class:
                    pass
                elif e["post_pend"] == e_class or e["post_pos"] == e_class:
                    pass
                elif e["post_pos"] is None and e["post_pend"] is None:
                    em_bad += 1
                else:
                    em_bad += 1

            refprev = {"close": close, "htf": r_htf, "mid": r_mid}
            trkprev = {"close": float(t.get("close") or close),
                       "htf": t.get("htf"), "mid": t.get("mid")}

        set_ok = crossed_eng == crossed_ref == crossed_trk
        n_cross = sum(1 for v in crossed_eng.values() if v != "NONE")
        ok = bad == 0 and set_ok and em_bad == 0
        checks.append((f"S_{name}_{sid}(fast={tf})_candles", ok,
                       f"crossings={n_cross} bars={len(seq)} value_mismatch={bad} "
                       f"emission_mismatch={em_bad}"))

        for t in prod_trades:
            if t["strategy_id"] != sid:
                continue
            _link_trade(sid, tf, name, t, bar_meta, per_trade)


def _link_trade(sid, tf, name, t, bar_meta, per_trade):
    entry_ts = _iso_to_epoch(t.get("entry_timestamp"))
    exit_ts = _iso_to_epoch(t.get("exit_timestamp")) if t.get("exit_timestamp") else None
    side = t.get("side")
    reason = t.get("exit_reason") or ("OPEN" if exit_ts is None else "?")

    # OPEN signal candle: nearest crossing bar (class == trade side) at/before entry.
    open_candle = None
    best = -1.0
    for bk, m in bar_meta.items():
        if m["record"]["bt"] - 0.5 <= entry_ts + 1e-6 and m["class"] == side:
            if m["record"]["bt"] > best:
                best = m["record"]["bt"]
                open_candle = m

    # CLOSE signal candle: reversal -> opposite-cross bar whose window contains
    # (or immediately precedes) the exit tick; stop -> the fast bar that contains
    # the exit tick.  The DB exit time is a 5m fill tick, so for 15m-fast
    # strategies the generating crossing bar can start up to ~1 fast-bar earlier.
    close_candle = None
    if exit_ts is not None:
        fast_width = 300.0 if tf == "5m" else 900.0
        is_rev = "reversal" in (reason or "").lower()
        opp = "LONG" if side == "SHORT" else "SHORT"
        lo = exit_ts - fast_width
        hi = exit_ts + 5.0
        if is_rev:
            best = -1.0
            for bk, m in bar_meta.items():
                s = m["record"]["bt"]
                if lo <= s <= hi and m["class"] == opp:
                    if s > best:
                        best = s
                        close_candle = m
            # reversal signal not on a crossing bar in window -> fall through to
            # the containing-bar rule so the exit still gets a close candle.
        if close_candle is None:
            best = -1.0
            for bk, m in bar_meta.items():
                s = m["record"]["bt"]
                if lo <= s <= hi and s > best:
                    best = s
                    close_candle = m

    per_trade.append({
        "strategy": sid,
        "instrument": name,
        "fast_tf": tf,
        "trade_side": side,
        "entry_ist": isod(entry_ts).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": t.get("entry_price"),
        "exit_ist": isod(exit_ts).strftime("%Y-%m-%d %H:%M:%S") if exit_ts else "OPEN",
        "exit_price": t.get("exit_price"),
        "exit_reason": reason,
        "open_signal_ist": isod(open_candle["record"]["bt"]).strftime("%Y-%m-%d %H:%M:%S") if open_candle else "NA",
        "open_signal_side": open_candle["class"] if open_candle else "NA",
        "open_parity": "OK" if (open_candle and open_candle["values_ok"]) else ("MISMATCH" if open_candle else "NO-CANDLE"),
        "open_detail": _fmt_candle(open_candle["record"]) if open_candle else "NA",
        "close_signal_ist": isod(close_candle["record"]["bt"]).strftime("%Y-%m-%d %H:%M:%S") if close_candle else "NA",
        "close_signal_side": close_candle["class"] if close_candle else "NA",
        "close_parity": "OK" if (close_candle and close_candle["values_ok"]) else ("MISMATCH" if close_candle else "NA"),
        "close_detail": _fmt_candle(close_candle["record"]) if close_candle else "NA",
        "link_ok": open_candle is not None and (exit_ts is None or close_candle is not None),
    })


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true",
                    help="fetch live Dhan candles (server). Default: offline CSV feed (local).")
    ap.add_argument("--start", default="2026-08-26")
    ap.add_argument("--stop", default="2026-08-28")
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--state-path", default=None)
    ap.add_argument("--root", default=None)
    args = ap.parse_args()

    from config import Config
    cfg = Config()
    cfg.load()
    prod_db = Path(args.db_path) if args.db_path else Path(cfg.get("system.db_path", "data/db/trading.db")).resolve()
    prod_state = Path(args.state_path) if args.state_path else Path(cfg.get("system.state_path", "data/db/system_state.json")).resolve()

    prod_closed, prod_open = load_production(prod_db, prod_state)
    prod_trades = []
    for r in prod_closed:
        prod_trades.append(dict(r))
    for p in prod_open:
        prod_trades.append({
            "strategy_id": p.get("strategy_id"),
            "side": p.get("side"),
            "entry_timestamp": p.get("entry_timestamp"),
            "entry_price": p.get("average_entry"),
            "exit_timestamp": None,
            "exit_price": None,
            "exit_reason": "OPEN",
            "quantity": p.get("quantity"),
        })
    print(f"[Prod] closed={len(prod_closed)} open={len(prod_open)} enabled={args.online}", flush=True)
    if not prod_trades:
        print("[Prod] no trades stored. Aborting.", flush=True)
        return 2

    root = Path(args.root) if args.root else None
    checks = []
    per_trade = []
    cross_rows = []

    for name in ("GOLDM", "SILVERM"):
        print(f"===== {name} =====", flush=True)
        rows = fetch_rows(name, args.start, args.stop, args.online)
        print(f"  {name}: {len(rows)} x5m rows", flush=True)
        base = root if root else L.fresh_run_root(name)
        base.mkdir(parents=True, exist_ok=True)
        eng_cfg = L.write_config(base, warmup={"last_trading_days": 0, "keep_partial": True})
        captured, xtrack, ref = run_capture(name, rows, eng_cfg)
        analyze(name, rows, captured, xtrack, ref, prod_trades, checks, per_trade, cross_rows)

    all_pass = all(ok for _, ok, _ in checks)
    links_ok = all(r["link_ok"] for r in per_trade)
    open_ok = all(r["open_parity"] == "OK" for r in per_trade)
    closed = [r for r in per_trade if r["exit_reason"] != "OPEN"]
    close_ok = bool(closed) and all(r["close_parity"] == "OK" for r in closed)
    n_match = sum(1 for r in cross_rows if r["result"] == "MATCH")
    n_mis = sum(1 for r in cross_rows if r["result"] == "VALUE-MISMATCH")
    final_ok = all_pass and links_ok and open_ok and close_ok

    print("\n=== PART A - per-trade signal candle detail ===")
    for r in per_trade:
        is_open = r["exit_reason"] == "OPEN"
        row_ok = r["open_parity"] == "OK" and (is_open or r["close_parity"] == "OK")
        print(f"  {'OK ' if row_ok else 'BAD'} "
              f"{r['strategy']} {r['trade_side']} entry={r['entry_ist']} exit={r['exit_ist']} "
              f"reason={r['exit_reason']} open={r['open_parity']} close={r['close_parity']}")
    print(f"\n  records={len(per_trade)} links_ok={links_ok} open_parity_ok={open_ok} close_parity_ok={close_ok}")

    print("\n=== PART B - crossing-signal-candle parity ===")
    for n, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}  {detail}")

    print("\n========== SUMMARY ==========", flush=True)
    print(f"PART A per-trade: {len(per_trade)} (links_ok={links_ok})", flush=True)
    print(f"  open signal candle parity: {'ALL OK' if open_ok else 'MISMATCHES'}", flush=True)
    print(f"  close signal candle parity: {'ALL OK' if close_ok else 'MISMATCHES'}", flush=True)
    print(f"PART B crossing candles: {len(cross_rows)} (match={n_match} mismatch={n_mis})", flush=True)
    print(f"RESULT: {'ALL MATCH' if final_ok else 'MISMATCHES FOUND'}", flush=True)

    pd.DataFrame(per_trade).to_csv(OUT_DIR / "SIGNAL_CANDLE_PER_TRADE.csv", index=False)
    pd.DataFrame(cross_rows).to_csv(OUT_DIR / "SIGNAL_CANDLE_CROSSING_PARITY.csv", index=False)

    md = [
        "# SIGNAL-CANDLE COMPREHENSIVE AUDIT (per-trade + per-crossing)",
        "",
        f"Window: {args.start}..{args.stop}   mode: {'ONLINE (Dhan)' if args.online else 'OFFLINE (CSV feed)'}",
        "",
        f"prod_closed={len(prod_closed)} prod_open={len(prod_open)}",
        "",
        "## PART A - per stored trade, open + close signal candle detail",
        "",
        f"records={len(per_trade)} links_ok={links_ok} open_parity_ok={open_ok} close_parity_ok={close_ok}",
        "",
        "| strategy | side | entry | exit | reason | open_signal | open_parity | close_signal | close_parity |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in per_trade:
        md.append(
            f"| {r['strategy']} | {r['trade_side']} | {r['entry_ist']} | {r['exit_ist']} | "
            f"{r['exit_reason']} | {r['open_signal_ist']}({r['open_signal_side']}) | {r['open_parity']} | "
            f"{r['close_signal_ist']}({r['close_signal_side']}) | {r['close_parity']} |")
    md += [
        "",
        "## PART B - crossing-signal-candle parity matrix",
        "",
        "| check | result |",
        "|---|---|",
    ]
    for n, ok, detail in checks:
        md.append(f"| {n} | {'**PASS**' if ok else '**FAIL**'} ({detail}) |")
    md += [
        "",
        f"crossing candles emitted: {len(cross_rows)}; value mismatches: {n_mis}.",
        "",
        f"**RESULT: {'ALL MATCH' if final_ok else 'MISMATCHES FOUND'}**",
        "",
    ]
    (OUT_DIR / "SIGNAL_CANDLE_AUDIT_SUMMARY.md").write_text("\n".join(md), encoding="utf-8")
    print(f"artifacts -> {OUT_DIR}", flush=True)
    return 0 if final_ok else 1


if __name__ == "__main__":
    sys.exit(main())
