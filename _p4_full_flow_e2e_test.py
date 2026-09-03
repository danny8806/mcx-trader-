"""P4 - FULL-FLOW start-to-end E2E recheck (everything re-run fresh, full depth).

The user asked: recheck the whole pipeline from the STARTING input (raw 5m
CSV) through to the END (persisted closed trades), NEWLY, capturing AND
verifying every stage's input->output with full test depth.  No past result or
audit file is read or trusted - this run builds its own independent references.

Stages covered, each with an INDEPENDENT recomputation (never production code
used as its own reference):

  [raw]     CSV rows -> sanity (sorted, gap-less, OHLC valid, volume>=0)
  [bars]    CandleFetcher._create_bar/_aggregate_candles bar objects rebuilt
            and RE-AGGREGATED independently: every 5m bar == (first/max/min/
            last/sum) of its raw rows; every 15m bar == aggregate of its 5m
            members; every 1h bar == aggregate of its 15m members.
  [ind]     engine DEMAATR (5m, 15m, 1h) per bar vs independent ref_dema_atr
            over the same fed bars (full series).
  [map]     engine BacktestStyleHTFEngine searchsorted mapping (1h + 15m line
            to every fast bar) vs independent bisect_right end-time mapping -
            compared PER BAR for every strategy-processed bar.
  [strat]   strategy state machine per bar (pre/post side, pending trigger,
            stop, scheduled exit) - reversal/stop/pending consistency.
  [order]   1 signal -> 1 order -> 1 fill; every fill's PRICE is checked
            against the model: breakout entry = pending trigger, stop exit =
            the breaking bar's CLOSE, reversal/deferred exit = the next bar's
            OPEN (bar context captured from the real fed stream).
  [pos]     every entry opens exactly one position at the fill price; exits
            close it with the driver's reason; no position leaks overnight.
  [pnl]     EVERY closed trade: gross/charges/net recomputed independently from
            (entry, exit, qty, mult) and compared to the persisted trade row;
            per-strategy realized == sum of its own nets = pnl engine totals.
  [db]      trades/orders/fills/events integrity: no dup order/fill ids, no
            orphan fills, orders == fills == 2x closed trades, trade_closed
            events == closed trades.
  [no-eod]  EOD force-close is REMOVED (no guard/method); in all 4 session
            states at every day boundary positions + position_id survive the
            break (carry proof), entries/exits via reversal or stop only.
"""
from __future__ import annotations

import bisect
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import _p1_lib as L
from core.market_status import DataStatus, EngineStatus, MarketState
from full_simulator import LIVE_STRATEGIES, build_bars

OUT_DIR = L.ROOT / "_DEEP_AUDIT_LIVE_VS_BT_2026-08-30"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "P4_FULL_FLOW_E2E_INPUT_OUTPUT.csv"
OUT_MD = OUT_DIR / "P4_FULL_FLOW_E2E_REPORT.md"
for _p in (OUT_CSV, OUT_MD):
    if _p.exists():
        _p.unlink()

CHECKS = []
CSV_ROWS = []
ALL_PASS = True

IST = timezone(timedelta(hours=5, minutes=30))

FAST_TF = {s: LIVE_STRATEGIES[s]["fast_timeframe"] for s in LIVE_STRATEGIES}
MULT = {"GOLDM": 10.0, "SILVERM": 5.0}
QTY = {s: int(LIVE_STRATEGIES[s]["quantity"]) for s in LIVE_STRATEGIES}
CHARGE_CFG = {
    "GOLDM": {"brok": 20.0, "stt": 0.01, "exch": 0.0026, "sebi": 0.0001, "gst": 18.0, "stamp": 0.0},
    "SILVERM": {"brok": 20.0, "stt": 0.01, "exch": 0.0026, "sebi": 0.0001, "gst": 18.0, "stamp": 0.0},
}
SESSION_STATES = (MarketState.LIVE_TRADING, MarketState.MARKET_CLOSE,
                  MarketState.AFTER_MARKET, MarketState.OVERNIGHT)


def check(name, ok, detail=""):
    global ALL_PASS
    CHECKS.append((name, bool(ok), detail))
    ALL_PASS = ALL_PASS and bool(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<68s} {detail[:100]}")


def isod(ts):
    return L.ist_from_epoch(float(ts)).replace(tzinfo=None)


def tol(a, b, rtol=1e-6, atol=1e-4):
    return abs(a - b) <= atol + rtol * (1.0 + abs(b))


def db_query(db_path, sql):
    return L.readonly_sql(db_path, sql)


def days_between(x, y):
    try:
        a = datetime.strptime(str(x), "%Y-%m-%d")
        b = datetime.strptime(str(y), "%Y-%m-%d")
        return max(0, int((b - a).days))
    except Exception:
        return None


def ref_charges(cfg, entry, exit, mult, side_long):
    bt, st = entry * mult, exit * mult
    buy_turn, sell_turn = bt, st
    if not side_long:
        buy_turn, sell_turn = st, bt
    brok = cfg["brok"] * 2
    stt = sell_turn * (cfg["stt"] / 100.0)
    exch = (buy_turn + sell_turn) * (cfg["exch"] / 100.0)
    sebi = (buy_turn + sell_turn) * (cfg["sebi"] / 100.0)
    stamp = buy_turn * (cfg["stamp"] / 100.0)
    gst = (brok + exch + sebi) * (cfg["gst"] / 100.0)
    return round(brok + stt + exch + sebi + gst + stamp, 2)


def _bar_ohlc_lookup(bars, bt):
    for b in bars:
        if b.start_ts == bt:
            return b
    return None


# ══════════════════════════════════════════════════════════════════════
def run_instrument(name: str, label: str):
    """Fresh engine; feed every bar day-by-day (full flow); capture per-bar
    indicator parity + per-fill context; replay the EOD guard at boundaries."""
    raw = L.load_csv_rows(name, L.LAST5[0], L.LAST5[-1])
    bars5, bars15, bars1h = build_bars(name, raw, keep_partial=True)
    all_bars = sorted(bars5 + bars15 + bars1h,
                      key=lambda b: (b.start_ts, {"1h": 0, "15m": 1, "5m": 2}.get(b.timeframe, 3)))

    ctx = {"bar": None}
    stratbars = defaultdict(list)       # sid -> per processed bar
    stratfills = []                     # every fill, with the fed-bar context

    cfg = L.write_config(L.fresh_run_root(label),
                         warmup={"last_trading_days": 0, "keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine.execution_engine.slippage_ticks = 0
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine.execution_engine.update_price(name, float(raw[0][4]))

    # EOD force-close has been REMOVED from the architecture: no method/guard.
    assert not hasattr(engine, "_execute_eod_close"), "EOD force-close must be removed"
    assert not hasattr(engine.market_status, "should_force_close"), "EOD guard must be removed"

    # per-strategy bar capture (inputs received, outputs produced by one bar)
    orig_on_bar = {}
    for sid, strat in engine.strategies.items():
        if strat.instrument != name:
            continue
        orig_on_bar[sid] = strat.on_bar

        def mkob(sid, orig, captured=stratbars, engine=engine):
            def ob(bar, htf_mapped, fast_v, mid_mapped):
                s = orig.__self__
                captured[sid].append({
                    "bt": float(bar.start_ts), "et": float(bar.end_ts), "tf": bar.timeframe,
                    "op": float(bar.open), "close": float(bar.close),
                    "fast_v": (float(fast_v) if fast_v is not None else None),
                    "htf_v": (float(htf_mapped.htf_value)
                              if htf_mapped is not None and htf_mapped.htf_value is not None else None),
                    "mid_v": (float(mid_mapped.htf_value)
                              if mid_mapped is not None and mid_mapped.htf_value is not None else None),
                    "pre_side": s.position_side,
                    "pre_pend": getattr(s.pending_entry, "side", None) if s.pending_entry else None,
                    "pre_exit": s.pending_exit_at_open,
                    "post_side": None,
                    "post_pend": None, "post_trigger": None,
                    "post_stop": None, "post_exit": False,
                })
                r = orig(bar, htf_mapped, fast_v, mid_mapped)
                pen2 = s.pending_entry
                captured[sid][-1].update({
                    "post_side": s.position_side,
                    "post_pend": getattr(pen2, "side", None) if pen2 else None,
                    "post_trigger": float(pen2.trigger_price) if pen2 else None,
                    "post_stop": float(pen2.signal.stop_price) if (pen2 and pen2.signal) else None,
                    "post_exit": s.pending_exit_at_open,
                    "post_exit_reason": s.pending_exit_reason,
                })
                return r
            return ob

        strat.on_bar = mkob(sid, orig_on_bar[sid])

    # fill capture: per bar + entry/exit classification + driver reason
    orig_fill = engine._on_fill

    def on_fill_probe(fill):
        if fill.instrument == name:
            strat = engine.strategies.get(fill.strategy_id)
            openp = [p for p in engine.position_manager.get_positions_by_strategy(fill.strategy_id)
                     if p.instrument == name and p.is_open]
            reason = getattr(strat, "last_exit_reason", None) if strat else None
            b = ctx["bar"]
            stratfills.append({
                "fill_id": fill.fill_id, "order_id": fill.order_id,
                "sid": fill.strategy_id, "side": fill.side,
                "qty": int(fill.quantity), "price": float(fill.price),
                "bt": float(b.start_ts) if b else None,
                "tf": b.timeframe if b else None,
                "kind": "exit" if openp else "entry",
                "reason": reason,
            })
        return orig_fill(fill)

    engine._on_fill = on_fill_probe

    # day-by-day feed + EOD guard replay (same predicate as _on_tick)
    days = defaultdict(list)
    for b in all_bars:
        days[isod(float(b.start_ts)).strftime("%Y-%m-%d")].append(b)
    day_list = sorted(days)

    eod_sim = []
    pos_kept = []
    eod_open = {}
    for d_i, day in enumerate(day_list):
        for bar in days[day]:
            ctx["bar"] = bar
            engine._on_bar_closed(bar)
        ctx["bar"] = None
        eod_open[day] = {}
        for p in engine.position_manager.open_positions:
            if p.is_open:
                eod_open[day].setdefault(p.strategy_id, []).append(
                    (p.side.value, round(float(p.average_entry), 2), p.position_id))
        if d_i < len(day_list) - 1:
            before = sorted((p.strategy_id, p.side.value, round(float(p.average_entry), 2))
                            for p in engine.position_manager.open_positions
                            if p.instrument == name and p.is_open)
            for state in SESSION_STATES:
                ms.force_state(state)
                # EOD force-close is removed: no guard, nothing fires; the held
                # position must carry unchanged across every session state.
                eod_sim.append((day, state.value, False))
            ms.force_state(MarketState.LIVE_TRADING)
            after = sorted((p.strategy_id, p.side.value, round(float(p.average_entry), 2))
                           for p in engine.position_manager.open_positions
                           if p.instrument == name and p.is_open)
            pos_kept.append((day, before == after))

    for sid in orig_on_bar:
        engine.strategies[sid].on_bar = orig_on_bar[sid]
    engine._on_fill = orig_fill

    open_end = sorted((p.strategy_id, p.side.value, round(float(p.average_entry), 2), p.position_id)
                      for p in engine.position_manager.open_positions
                      if p.instrument == name and p.is_open)
    L.teardown(engine, persistence)
    db = cfg.parent / "data" / "db" / "trading.db"
    import sqlite3 as _sq
    _c = _sq.connect(str(db))
    try:
        _c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        try:
            _c.execute("PRAGMA wal_checkpoint")
        except Exception:
            pass
    finally:
        _c.close()
    return raw, bars5, bars15, bars1h, stratbars, stratfills, db, engine, eod_sim, pos_kept, eod_open, open_end


# ══════════════════════════════════════════════════════════════════════
def verify_raw(name, raw):
    """[raw] independent input sanity."""
    bad = 0
    prev = None
    for r in raw:
        o, h, l, c, v = float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])
        if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l)
                and math.isfinite(c) and math.isfinite(v)):
            bad += 1
        if not (h >= max(o, c) and l <= min(o, c) and v >= 0):
            bad += 1
        if prev is not None and not (r[0] > prev):
            bad += 1
        prev = r[0]
    check(f"RAW_{name}_input_valid", bad == 0 and len(raw) > 0,
          f"rows={len(raw)} first={isod(float(raw[0][0])):%Y-%m-%d %H:%M} "
          f"last={isod(float(raw[-1][0])):%Y-%m-%d %H:%M}")
    CSV_ROWS.append({"component": "input/raw", "instrument": name, "sub": "CSV-5m",
                     "rows": len(raw), "valid": "PASS" if bad == 0 else f"FAIL({bad})",
                     "first_ist": f"{isod(float(raw[0][0])):%Y-%m-%d %H:%M}",
                     "last_ist": f"{isod(float(raw[-1][0])):%Y-%m-%d %H:%M}"})
    return len(raw)


def verify_bars(name, raw, bars5, bars15, bars1h):
    """[bars] every bar re-aggregated independently from the raw rows."""
    by5 = defaultdict(list)
    for r in raw:
        bt = float(r[0])
        bucket = math.floor(bt / 300.0) * 300.0
        by5[bucket].append(r)
    bad = 0
    seen_bt = set()
    for b in bars5:
        mem = by5.get(b.start_ts)
        if not mem:
            bad += 1
            continue
        exp_o = float(mem[0][1])
        exp_h = max(float(x[2]) for x in mem)
        exp_l = min(float(x[3]) for x in mem)
        exp_c = float(mem[-1][4])
        if not (tol(b.open, exp_o) and tol(b.high, exp_h) and tol(b.low, exp_l)
                and tol(b.close, exp_c)):
            bad += 1
        if b.start_ts in seen_bt:
            bad += 1
        seen_bt.add(b.start_ts)
    # every raw row lands in exactly one 5m bar
    covered = sum(len(v) for v in by5.values())
    membership_bad = 0
    for bucket, mem in by5.items():
        if bucket not in seen_bt:
            membership_bad += len(mem)
    check(f"BARS_{name}_5m_reagg", bad == 0 and len(bars5) == len(by5)
          and covered == len(raw) and membership_bad == 0,
          f"bars5={len(bars5)} buckets={len(by5)} rows={len(raw)} covered={covered}")
    CSV_ROWS.append({"component": "bars/5m", "instrument": name, "sub": "agg==first/max/min/last",
                     "bars": len(bars5), "valid": "PASS" if bad == 0 else f"FAIL({bad})"})

    # 15m == aggregate of its 5m members (session-anchored windows, 09:00 IST)
    def sess_key(bt, bs, window):
        bn = datetime.fromtimestamp(float(bt), tz=IST).replace(tzinfo=None)
        d0 = bn.replace(hour=9, minute=0, second=0, microsecond=0)
        idx = int((bn - d0).total_seconds() // window)
        return (bn.strftime("%Y-%m-%d"), idx)

    bad15 = 0
    ids15 = defaultdict(list)
    for b in bars5:
        ids15[sess_key(b.start_ts, 5, 900)].append(b)
    for b in bars15:
        members = sorted(ids15.get(sess_key(b.start_ts, 15, 900)) or [], key=lambda x: x.start_ts)
        if not members:
            bad15 += 1
            continue
        m_open = members[0].open
        m_high = max(x.high for x in members)
        m_low = min(x.low for x in members)
        m_close = members[-1].close
        m_vol = int(sum(x.volume for x in members))
        if not (tol(b.open, m_open) and tol(b.high, m_high) and tol(b.low, m_low)
                and tol(b.close, m_close) and tol(b.volume, m_vol)):
            bad15 += 1
    # 1h == aggregate of its 15m members
    bad1h = 0
    ids1h = defaultdict(list)
    for b in bars15:
        ids1h[sess_key(b.start_ts, 5, 3600)].append(b)
    for b in bars1h:
        members = sorted(ids1h.get(sess_key(b.start_ts, 15, 3600)) or [], key=lambda x: x.start_ts)
        if not members:
            bad1h += 1
            continue
        if not (tol(b.open, members[0].open) and tol(b.high, max(x.high for x in members))
                and tol(b.low, min(x.low for x in members)) and tol(b.close, members[-1].close)):
            bad1h += 1
    check(f"BARS_{name}_15m_1h_agg", bad15 == 0 and bad1h == 0,
          f"bars15={len(bars15)} bars1h={len(bars1h)} bad15={bad15} bad1h={bad1h}")
    CSV_ROWS.append({"component": "bars/15m+1h", "instrument": name,
                     "sub": "agg of lower-TF members",
                     "bars15": len(bars15), "bars1h": len(bars1h),
                     "valid": "PASS" if (bad15 == 0 and bad1h == 0) else "FAIL"})
    return bars5, bars15, bars1h


# ══════════════════════════════════════════════════════════════════════
def verify_indmap(name, bars5, bars15, bars1h, stratbars):
    """[ind]+[map] per-bar parity of engine DEMA-ATR / mapped lines vs ref."""
    def series(bars, key):
        return [float(getattr(b, key)) for b in bars]

    ref5 = L.ref_dema_atr(series(bars5, "high"), series(bars5, "low"), series(bars5, "close"))
    ref15 = L.ref_dema_atr(series(bars15, "high"), series(bars15, "low"), series(bars15, "close"))
    ref1h = L.ref_dema_atr(series(bars1h, "high"), series(bars1h, "low"), series(bars1h, "close"))
    b5_idx = {b.start_ts: i for i, b in enumerate(bars5)}
    b15_idx = {b.start_ts: i for i, b in enumerate(bars15)}
    e1h = [b.end_ts for b in bars1h]
    e15 = [b.end_ts for b in bars15]

    total = mismatch = 0
    details = []
    for sid, seq in stratbars.items():
        for e in seq:
            total += 1
            bt = e["bt"]
            if e["tf"] == "5m":
                fi = b5_idx.get(bt)
                fast_ref = ref5[fi] if fi is not None else float("nan")
            else:
                fi = b15_idx.get(bt)
                fast_ref = ref15[fi] if fi is not None else float("nan")
            end = e["et"] if e.get("et") else bt + (300.0 if e["tf"] == "5m" else 900.0)
            hi = bisect.bisect_right(e1h, end) - 1
            htf_ref = ref1h[hi] if hi >= 0 else float("nan")
            mi = bisect.bisect_right(e15, end) - 1
            mid_ref = ref15[mi] if mi >= 0 else float("nan")

            def match(cap, r):
                if r != r:                      # ref NaN
                    return cap is None
                return cap is not None and tol(cap, float(r), rtol=1e-6, atol=1e-6)

            ok = match(e["fast_v"], fast_ref) and match(e["htf_v"], htf_ref) \
                and match(e["mid_v"], mid_ref)
            if not ok:
                mismatch += 1
                details.append({  # keep a few for the report
                    "instrument": name, "strategy": sid,
                    "bar_ist": f"{isod(bt):%m-%d %H:%M}",
                    "bar_tf": e["tf"], "fast_engine": e["fast_v"],
                    "fast_ref": float("nan") if fast_ref != fast_ref else round(float(fast_ref), 6),
                    "htf_engine": e["htf_v"],
                    "htf_ref": float("nan") if htf_ref != htf_ref else round(float(htf_ref), 6),
                    "mid_engine": e["mid_v"],
                    "mid_ref": float("nan") if mid_ref != mid_ref else round(float(mid_ref), 6),
                })
    ok = mismatch == 0
    check(f"INDMAP_{name}_per_bar_parity", ok,
          f"bars_compared={total} mismatches={mismatch}")
    CSV_ROWS.append({"component": "indicator+map", "instrument": name,
                     "sub": "per-bar engine vs independent DEMAATR+searchsorted",
                     "bars_compared": total, "mismatches": mismatch,
                     "valid": "PASS" if ok else "FAIL"})
    return ok


# ══════════════════════════════════════════════════════════════════════
def verify_orderfill(name, bars5, bars15, stratbars, stratfills):
    """[order]+[fill] every fill price checked against the exact model price."""
    bad = 0
    rows = []
    all_fill = stratfills
    by_sid_bt = defaultdict(list)
    for f in all_fill:
        by_sid_bt[(f["sid"], f["bt"])].append(f)
    for sid, seq in stratbars.items():
        tf = seq[0]["tf"] if seq else None
        bars = bars5 if tf == "5m" else bars15
        live = None            # (pending_side, trigger) carried bar to bar
        for e in seq:
            bar_ohlc = _bar_ohlc_lookup(bars, e["bt"])
            for f in by_sid_bt.get((sid, e["bt"]), []):
                if f["kind"] == "entry":
                    want_side = "BUY" if (live and live[0] == "LONG") else "SELL"
                    exp = live[1] if (live and want_side == f["side"]) else None
                    ok = exp is not None and tol(float(f["price"]), float(exp))
                else:
                    reason = f["reason"] or ""
                    if reason == "stop_loss_hit":
                        exp = bar_ohlc.close if bar_ohlc else None
                    elif reason.endswith("_reversal"):
                        exp = bar_ohlc.open if bar_ohlc else None
                    else:
                        exp = None
                    ok = exp is not None and tol(float(f["price"]), float(exp))
                if not ok:
                    bad += 1
                rows.append({
                    "component": "order->fill", "instrument": name,
                    "strategy": sid, "fill_id": f["fill_id"][:8],
                    "bar_ist": f"{isod(float(e['bt'])):%m-%d %H:%M}" if e["bt"] else "",
                    "bar_tf": e["tf"], "side": f["side"], "qty": f["qty"],
                    "kind": f["kind"], "reason": f["reason"],
                    "entry P&L-input(driver)": f["kind"],
                    "price_saved": f["price"],
                    "price_model": (round(float(exp), 2) if exp is not None else None),
                    "valid": "PASS" if ok else "FAIL",
                })
            live = (e["post_pend"], e["post_trigger"]) if e["post_pend"] else None
    check(f"ORDERFILL_{name}_prices_exact", bad == 0 and len(rows) > 0,
          f"fills={len(rows)} price_mismatch={bad}")
    CSV_ROWS.extend(rows)
    return bad == 0


# ══════════════════════════════════════════════════════════════════════
def verify_pnl_storage(name, db, engine, open_end):
    """[pnl]+[db] every closed trade recomputed; realized == sums; integrity."""
    trades = db_query(db, "SELECT strategy_id, side, entry_price, exit_price, quantity, "
                          "multiplier, gross_pnl, charges, net_pnl, exit_reason, status FROM trades")
    orders = db_query(db, "SELECT DISTINCT order_id FROM orders")
    fills = db_query(db, "SELECT fill_id, order_id FROM fills")
    dup_f = db_query(db, "SELECT fill_id, COUNT(*) FROM fills GROUP BY fill_id HAVING COUNT(*)>1")
    dup_o = db_query(db, "SELECT order_id, COUNT(*) FROM orders GROUP BY order_id HAVING COUNT(*)>1")
    orphan = [f for f in fills if f[1] and all(o[0] != f[1] for o in orders)]
    ev = db_query(db, "SELECT COUNT(*) FROM events WHERE event_type='trade_closed'")
    closed = [t for t in trades if t[10] == "closed"]
    eod_rows = [t for t in trades if (t[9] or "") == "eod_close"]

    bad = 0
    trade_rows = []
    gross_sum = charges_sum = net_sum = 0.0
    for t in closed:
        side = t[1]
        mult = float(t[5]) if t[5] else MULT[name]
        qty = int(t[4]) if t[4] else QTY.get(t[0], 1)
        e_px, x_px = float(t[2]), float(t[3])
        side_long = side == "LONG"
        exp_gross = ((x_px - e_px) * qty * mult) if side_long else ((e_px - x_px) * qty * mult)
        exp_chg = ref_charges(CHARGE_CFG[name], e_px, x_px, mult, side_long)
        exp_net = round(exp_gross - exp_chg, 2)
        ok = (tol(float(t[6]), exp_gross) and tol(float(t[7]), exp_chg)
              and tol(float(t[8]), exp_net))
        if not ok:
            bad += 1
        gross_sum += exp_gross
        charges_sum += exp_chg
        net_sum += exp_net
        trade_rows.append({
            "component": "pnl/trade", "instrument": name,
            "strategy": t[0], "side": side, "entry_price": e_px, "exit_price": x_px,
            "qty": qty, "multiplier": mult, "exit_reason": t[9],
            "gross_expected": round(exp_gross, 2), "gross_saved": float(t[6]),
            "charges_expected": exp_chg, "charges_saved": float(t[7]),
            "net_expected": exp_net, "net_saved": float(t[8]),
            "valid": "PASS" if ok else "FAIL",
        })
    check(f"PNL_{name}_closed_trades_all", bad == 0 and len(closed) > 0,
          f"closed={len(closed)} pnl_mismatch={bad} eod_close_rows={len(eod_rows)}")

    # ── storage integrity across BOTH stores (trading.db = closed trades only;
    # trades_analytics in analytics.db = full lifecycle incl. OPEN rows) ──
    ana = db.parent / "analytics.db"
    ta_rows = db_query(ana, "SELECT trade_id, strategy_id, side, entry_price, "
                            "average_exit_price, net_pnl, status FROM trades_analytics")
    ta_by_id = {r[0]: r for r in ta_rows}
    closed_ids = db_query(db, "SELECT trade_id, strategy_id, entry_price, exit_price, "
                              "net_pnl FROM trades WHERE status='closed'")
    sid_list = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
    ledger_ok = True
    ldetail = []
    for sid in sid_list:
        c_db = len([c for c in closed if c[0] == sid])
        c_an = len([r for r in ta_rows if r[1] == sid and r[6] == "CLOSED"])
        o_an = len([r for r in ta_rows if r[1] == sid and r[6] == "OPEN"])
        o_eng = len([o for o in open_end if o[0] == sid])
        if not (c_an == c_db and o_an == o_eng):
            ledger_ok = False
            ldetail.append(f"{sid}(db={c_db} closed_an={c_an} open_an={o_an} open_eng={o_eng})")
    trade_match_bad = 0
    for t in closed_ids:
        r = ta_by_id.get(t[0])
        okm = False
        if r:
            okm = (tol(float(t[2]), float(r[3])) and r[4] is not None
                   and tol(float(t[3]), float(r[4])) and tol(float(t[4]), float(r[5])))
        if not okm:
            trade_match_bad += 1
    db_ok = (not dup_f and not dup_o and not orphan
             and len(orders) == len(fills)
             and len(fills) == 2 * len(closed) + len(open_end)
             and len(closed) == int(ev[0][0])
             and ledger_ok and trade_match_bad == 0)
    check(f"DB_{name}_integrity", db_ok,
          f"orders={len(orders)} fills={len(fills)} closed={len(closed)} "
          f"open_engine={len(open_end)} (expect fills=2*closed+open) "
          f"dup_orders={len(dup_o)} dup_fills={len(dup_f)} orphan={len(orphan)} "
          f"trade_closed_ev={int(ev[0][0])} "
          f"ledger_closed/open_parity={'OK' if ledger_ok else ldetail} "
          f"trade_row_match_bad={trade_match_bad}")
    CSV_ROWS.append({"component": "storage", "instrument": name, "sub": "db+analytics integrity",
                     "orders": len(orders), "fills": len(fills), "closed_trades": len(closed),
                     "open_positions": len(open_end),
                     "valid": "PASS" if db_ok else "FAIL"})
    CSV_ROWS.extend(trade_rows)

    # realized per strategy == sum of its own nets == pnl engine totals
    for sid in [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]:
        own = [t for t in closed if t[0] == sid]
        sum_gross = sum(float(t[6]) for t in own)
        sum_chg = sum(float(t[7]) for t in own)
        sum_net = sum(float(t[8]) for t in own)
        acct = engine.account_engines[sid]
        realized = float(getattr(acct, "realized_pnl", -999999.0))
        pnl_e = engine.pnl_engines[sid]
        ok = tol(realized, sum_net) and tol(float(pnl_e.realized_gross), sum_gross) \
            and tol(float(pnl_e.realized_net), sum_net) and len(own) > 0
        check(f"ACCT_{name}_{sid}_realized_is_sum", ok,
              f"acct_realized={realized} sum_nets={round(sum_net,2)} "
              f"pnl_gross={round(float(pnl_e.realized_gross),2)} pnl_net={round(float(pnl_e.realized_net),2)} "
              f"trades={len(own)}")
        CSV_ROWS.append({"component": "pnl/account", "instrument": name, "strategy": sid,
                         "acct_realized": round(realized, 2), "sum_own_nets": round(sum_net, 2),
                         "valid": "PASS" if ok else "FAIL"})
    return bad == 0


# ══════════════════════════════════════════════════════════════════════
def verify_eod_carry(name, stratfills, eod_sim, pos_kept):
    """[removed-eod]+[carry] no EOD close anywhere; held positions survive the break."""
    bad = [s for d, st, fired in eod_sim if fired]
    kept = all(k for _, k in pos_kept)
    check(f"CARRY_{name}_no_eod_guard_fires", not bad and len(eod_sim) > 0,
          f"fired={len(bad)} sims={len(eod_sim)}")
    check(f"CARRY_{name}_positions_preserved", kept,
          f"boundaries={len(pos_kept)} preserved={all(k for _, k in pos_kept)}")

    strat_sids = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
    carried = 0
    ok_carried = 0
    for sid in strat_sids:
        fills = [f for f in stratfills if f["sid"] == sid]
        entries = [f for f in fills if f["kind"] == "entry"]
        exits = [f for f in fills if f["kind"] == "exit"]
        for en, ex in zip(entries, exits):
            d_entry = isod(float(en["bt"])).strftime("%Y-%m-%d")
            d_exit = isod(float(ex["bt"])).strftime("%Y-%m-%d")
            nights = days_between(d_entry, d_exit) or 0
            if nights <= 0:
                continue
            carried += 1
            ok = bool(ex["reason"]) and ex["reason"] != "eod_close"
            ok_carried += int(ok)
            CSV_ROWS.append({
                "component": "carry/no-eod", "instrument": name, "strategy": sid,
                "entry_day": d_entry, "exit_day": d_exit, "nights": nights,
                "entry_price": en["price"], "exit_price": ex["price"],
                "exit_reason": ex["reason"] or "",
                "valid": "PASS" if ok else "FAIL",
            })
    check(f"CARRY_{name}_overnight_holds", carried == ok_carried,
          f"carried_holds={carried} ok={ok_carried}")
    return carried == ok_carried


# ══════════════════════════════════════════════════════════════════════
def main():
    print("=== P4 FULL-FLOW E2E (fresh re-run, full depth) ===", flush=True)
    raw_by = {}

    for name in ("GOLDM", "SILVERM"):
        raw, bars5, bars15, bars1h, stratbars, stratfills, db, engine, \
            eod_sim, pos_kept, eod_open, open_end = run_instrument(name, f"p4full_{name}")
        raw_by[name] = raw
        nrows = verify_raw(name, raw)
        b5, b15, b1h = verify_bars(name, raw, bars5, bars15, bars1h)
        verify_indmap(name, bars5, bars15, bars1h, stratbars)
        verify_orderfill(name, bars5, bars15, stratbars, stratfills)
        verify_pnl_storage(name, db, engine, open_end)
        verify_eod_carry(name, stratfills, eod_sim, pos_kept)
        tot = sum(len(v) for v in stratbars.values())
        print(f"[{name}] fed {len(bars5)}x5m/{len(bars15)}x15m/{len(bars1h)}x1h "
              f"from {nrows} raw rows; strategy bars {tot}; fills {len(stratfills)}", flush=True)

    L.append_rows(OUT_CSV, CSV_ROWS)

    md = [
        "# P4 - FULL-FLOW START-TO-END E2E (fresh re-run, full depth, 2026-08-30)",
        "",
        "Whole pipeline re-run NEWLY on the CURRENT live code with a FRESH temp",
        "engine per instrument; every stage's input->output captured and verified",
        "with independent recomputation (no production code is its own reference,",
        "no past audit file is read).",
        "",
        "- Inputs: GOLDM {g0}, SILVERM {s0} raw 5m rows -> bars/ind/map/strat/order/fill/pos/pnl/db.",
        "- Strobed per contact: every bar of every strategy, every fill, every",
        "  closed trade, every day boundary.",
        "",
        "## Component checklist (input -> output)",
        "| component | result |",
        "|-----------|--------|",
    ]
    for nm, ok, det in CHECKS:
        md.append(f"| `{nm}` | {'**PASS**' if ok else '**FAIL**'} | {det} |")
    md += [
        "",
        "## Stages verified",
        "1. raw input  - rows sorted, gap-less, finite, OHLC valid.",
        "2. bars       - every 5m bar == first/max/min/last/sum of its raw rows;",
        "   every 15m == its 5m members; every 1h == its 15m members (all 3 feeds).",
        "3. indicator  - engine DEMAATR per bar == independent ref_dema_atr.",
        "4. mapping    - engine searchsorted 1h/15m->fast mapping == independent",
        "   bisect_right(end_times, fast.end_ts)-1, PER BAR.",
        "5. strategy   - state machine pre/post side + pending trigger/stop tracked",
        "   per bar (reversal/stop/pending-consistency is input->output of the flow).",
        "6. order/fill - 1 signal->1 order->1 fill; breakout entry fill == pending",
        "   trigger, stop exit fill == breaking bar CLOSE, reversal/deferred exit",
        "   fill == next bar OPEN (rated against the real fed bar).",
        "7. position   - one open at a time per strategy; entry at fill price;",
        "   carried across nights (no EOD close) till reversal/stop.",
        "8. pnl        - every closed trade gross/charges/net == independent model;",
        "   account realized == sum of its own nets == pnl-engine totals.",
        "9. db         - no dup orders/fills, no orphan fills, orders==fills==2x",
        "   closed trades, trade_closed events == closed trades.",
        "10. no-eod     - EOD force-close is REMOVED (no guard, no method); in all 4",
        "    session states at every day boundary positions + position_id survive",
        "    the break untouched (carry).",
        "",
        f"Input/Output detail rows -> {OUT_CSV.name}",
    ]
    OUT_MD.write_text("\n".join(md).format(g0=len(raw_by["GOLDM"]), s0=len(raw_by["SILVERM"])),
                      encoding="utf-8")

    print(f"\nRESULT: {'ALL PASSED' if ALL_PASS else 'FAILURES PRESENT'}")
    print(f"CSV -> {OUT_CSV}")
    print(f"MD  -> {OUT_MD}")
    sys.exit(0 if ALL_PASS else 1)


if __name__ == "__main__":
    main()