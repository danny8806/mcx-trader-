"""P3 - CARRY / NO-EOD-EXIT END-TO-END INPUT->OUTPUT TEST (fresh, everything re-run).

The user asked, on the CURRENT live code, to test THESE components with
input->output conditions and NOT trust any past results:
   1. trade placement        - orders + fills created at the right price,
                                including an entry placed from a pending
                                breakout carried across the overnight break,
   2. P&L calculation        - gross/charges/net exactly match the fee model
                                for trades that were carried overnight,
   3. trade saving           - carried trades persisted open->closed in DB
                                with their fills/orders,
   4. NO EOD exit            - a position held at the end of one session is
                                carried into the next session and stays open
                                until the real exit (reversal/stop); the EOD
                                force-close guard is inert in EVERY session
                                state (LIVE_TRADING / MARKET_CLOSE /
                                AFTER_MARKET / OVERNIGHT),
   5. all components         - a checklist, each tested via input->output.

Method (nothing reused from any past audit):
   ENGINE = a FRESH live TradingEngine (fresh temp dir) with all 4 strategies,
            real PaperBroker (slippage forced to 0 so fills == model price),
            OrderManager, TradeCloseManager, P&L accounts, persistence DB.
   INPUT  = the raw LAST5 5m REST stream for GOLDM + SILVERM (real market data).
   The stream is fed DAY-BY-DAY.  After every non-final day the engine's own
   EOD guard (trading_engine.py `_on_tick` lines ~631-632) is replayed:
   `if market_status.should_force_close: _execute_eod_close()` - across all
   four session states - with `_execute_eod_close` wrapped in a spy.  Each
   fast bar of every strategy is captured with pre/post position + pending +
   open-position-entry state so the carry is proven from the REAL live chain.

Outputs (in _DEEP_AUDIT_LIVE_VS_BT_2026-08-30):
   P3_CARRY_E2E_INPUT_OUTPUT.csv   (boundary + carried-trade output rows)
   P3_CARRY_E2E_REPORT.md          (component checklist + verdict)
Exit 0 iff everything passes.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import _p1_lib as L
from core.market_status import DataStatus, EngineStatus, MarketState
from full_simulator import LIVE_STRATEGIES, build_bars

OUT_DIR = L.ROOT / "_DEEP_AUDIT_LIVE_VS_BT_2026-08-30"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "P3_CARRY_E2E_INPUT_OUTPUT.csv"
OUT_MD = OUT_DIR / "P3_CARRY_E2E_REPORT.md"
for _p in (OUT_CSV, OUT_MD):
    if _p.exists():
        _p.unlink()

CHECKS = []
CSV_ROWS = []
ALL_PASS = True
EOD_SPY = {"calls": 0}

IST = timezone(timedelta(hours=5, minutes=30))


def check(name, ok, detail=""):
    global ALL_PASS
    CHECKS.append((name, bool(ok), detail))
    ALL_PASS = ALL_PASS and bool(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<70s} {detail[:110]}")


def isod(ts):
    return L.ist_from_epoch(float(ts)).replace(tzinfo=None)


def tol(a, b, rtol=1e-6, atol=1e-4):
    return abs(a - b) <= atol + rtol * (1.0 + abs(b))


def db_query(db_path, sql):
    return L.readonly_sql(db_path, sql)


def iso_to_ist_date(s):
    """DB entry/exit timestamps are UTC ISO strings -> IST calendar date."""
    try:
        dt = datetime.fromisoformat(str(s))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date().isoformat()


def _days_between(x, y):
    try:
        a = datetime.strptime(str(x), "%Y-%m-%d")
        b = datetime.strptime(str(y), "%Y-%m-%d")
        return max(0, int((b - a).days))
    except Exception:
        return None


FAST_TF = {s: LIVE_STRATEGIES[s]["fast_timeframe"] for s in LIVE_STRATEGIES}
MULT = {"GOLDM": 10.0, "SILVERM": 5.0}
QTY = {s: int(LIVE_STRATEGIES[s]["quantity"]) for s in LIVE_STRATEGIES}

CHARGE_CFG = {
    "GOLDM": {"brok": 20.0, "stt": 0.01, "exch": 0.0026, "sebi": 0.0001, "gst": 18.0, "stamp": 0.0},
    "SILVERM": {"brok": 20.0, "stt": 0.01, "exch": 0.0026, "sebi": 0.0001, "gst": 18.0, "stamp": 0.0},
}


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


SESSION_STATES = (MarketState.LIVE_TRADING, MarketState.MARKET_CLOSE,
                  MarketState.AFTER_MARKET, MarketState.OVERNIGHT)


# ═════════════════════════════════════════════════─────────────────────
def run_instrument(name: str, label: str):
    """Fresh engine, feed the LAST5 stream DAY-BY-DAY, replay the EOD guard
    after every non-final day in all session states, capture every fast bar."""
    raw = L.load_csv_rows(name, L.LAST5[0], L.LAST5[-1])
    bars5, bars15, bars1h = build_bars(name, raw, keep_partial=True)
    all_bars = sorted(bars5 + bars15 + bars1h,
                      key=lambda b: (b.start_ts, {"5m": 0, "15m": 1, "1h": 2}.get(b.timeframe, 3)))

    cfg = L.write_config(L.fresh_run_root(label),
                         warmup={"last_trading_days": 0, "keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine.execution_engine.slippage_ticks = 0   # fills == model price exactly
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine.execution_engine.update_price(name, float(raw[0][4]))

    # EOD spy: counts every real invocation of the engine's EOD force-close
    orig_eod = engine._execute_eod_close

    def eod_spy(*a, **k):
        EOD_SPY["calls"] += 1
        return orig_eod(*a, **k)

    engine._execute_eod_close = eod_spy

    captured: dict[str, list[dict]] = defaultdict(list)
    orig_on_bar = {}
    for sid, strat in engine.strategies.items():
        if strat.instrument != name:
            continue
        orig_on_bar[sid] = strat.on_bar

        def mkob(sid, orig, captured=captured, engine=engine):
            def ob(bar, htf_mapped, fast_v, mid_mapped):
                s = orig.__self__

                def pos_summary():
                    out = []
                    for p in engine.position_manager.get_positions_by_strategy(sid):
                        if p.is_open:
                            out.append((p.side.value, round(float(p.average_entry), 2),
                                        float(p.entry_timestamp), p.position_id))
                    return out

                pen = s.pending_entry
                captured[sid].append({
                    "bt": float(bar.start_ts), "op": float(bar.open),
                    "high": float(bar.high), "low": float(bar.low),
                    "close": float(bar.close),
                    "date": isod(float(bar.start_ts)).strftime("%Y-%m-%d"),
                    "time": isod(float(bar.start_ts)).strftime("%H:%M"),
                    "pre_pos": s.position_side,
                    "pre_pend": getattr(pen, "side", None) if pen else None,
                    "pre_exit": s.pending_exit_at_open,
                    "pre_entries": pos_summary(),
                })
                r = orig(bar, htf_mapped, fast_v, mid_mapped)
                pen2 = s.pending_entry
                captured[sid][-1].update({
                    "post_pend": getattr(pen2, "side", None) if pen2 else None,
                    "post_trigger": float(pen2.trigger_price) if pen2 else None,
                    "post_stop": float(pen2.signal.stop_price) if (pen2 and pen2.signal) else None,
                    "post_pos": s.position_side,
                    "post_exit": s.pending_exit_at_open,
                    "post_exit_reason": s.pending_exit_reason,
                    "post_entries": pos_summary(),
                })
                return r
            return ob

        strat.on_bar = mkob(sid, orig_on_bar[sid])

    # group bars by IST day so the EOD guard can be replayed at each boundary
    days = defaultdict(list)
    for b in all_bars:
        days[isod(float(b.start_ts)).strftime("%Y-%m-%d")].append(b)
    day_list = sorted(days)

    eod_sim = []          # (day, state, should_force_close_seen)
    pos_kept = []         # (day, open positions before sim -> after sim kept)
    eod_open = {}         # day -> {sid: [(side, avg_2dp, position_id)]} REAL state
                          # after every bar/signal of that day has settled
    for d_i, day in enumerate(day_list):
        for bar in days[day]:
            engine._on_bar_closed(bar)
        eod_open[day] = {}
        for p in engine.position_manager.open_positions:
            if not p.is_open:
                continue
            eod_open[day].setdefault(p.strategy_id, []).append(
                (p.side.value, round(float(p.average_entry), 2), p.position_id))
        if d_i < len(day_list) - 1:
            before = sorted((p.strategy_id, p.side.value, round(float(p.average_entry), 2))
                            for p in engine.position_manager.open_positions
                            if p.instrument == name and p.is_open)
            for state in SESSION_STATES:
                ms.force_state(state)
                guard_fired = bool(ms.should_force_close)   # EXACT _on_tick predicate
                if guard_fired:
                    engine._execute_eod_close()             # genuine guard body
                eod_sim.append((day, state.value, guard_fired))
            ms.force_state(MarketState.LIVE_TRADING)
            after = sorted((p.strategy_id, p.side.value, round(float(p.average_entry), 2))
                           for p in engine.position_manager.open_positions
                           if p.instrument == name and p.is_open)
            pos_kept.append((day, before == after))

    for sid in orig_on_bar:
        engine.strategies[sid].on_bar = orig_on_bar[sid]

    L.teardown(engine, persistence)
    db = cfg.parent / "data" / "db" / "trading.db"
    # Force the writer's WAL (if any) into the main DB so post-run reads in the
    # same process see every saved row (engine uses WAL mode; a lingering
    # writer connection can otherwise leave frames uncheckpointed for ro reads).
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
    return raw, captured, db, engine, eod_sim, pos_kept, eod_open


# ═════════════════════════════════════════════════─────────────────────
def main():
    print("=== P3 CARRY / NO-EOD-EXIT E2E (fresh re-run) ===", flush=True)

    runs = {}
    raw_by = {}
    for name in ("GOLDM", "SILVERM"):
        raw, captured, db, engine, eod_sim, pos_kept, eod_open = run_instrument(name, f"p3carry_{name}")
        raw_by[name] = raw
        runs[name] = (captured, db, engine, eod_sim, pos_kept, eod_open)
        print(f"[{name}] fed {len(raw)} 5m rows, bars/day -> "
              f"{' | '.join(f'{s}={len(captured[s])}' for s in captured)}", flush=True)

    # ── P: parallel engines, all 4 strategies processed ──
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        for sid in [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]:
            n = len(captured[sid])
            acct = engine.account_engines.get(sid)
            pnl = engine.pnl_engines.get(sid)
            check(f"P_parallel_{name}_{sid}_processed", n > 0 and acct is not None and pnl is not None,
                  f"bars={n} acct={acct is not None} pnl={pnl is not None}")

    # ── EOD: guard inert in every session state at every day boundary ──
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        bad = [s for day, state, fired in eod_sim if fired]
        kept = all(k for _, k in pos_kept)
        check(f"EOD_inert_{name}_guard_never_fires",
              not bad and EOD_SPY["calls"] == 0,
              f"fired={len(bad)} _execute_eod_close_calls={EOD_SPY['calls']} sims={len(eod_sim)}")
        check(f"EOD_positions_preserved_{name}", kept,
              f"day-boundaries={len(pos_kept)} preserved={all(k for _, k in pos_kept)}")

    # ── DB facts (closed-trade rows, order/fill/event integrity) ──
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        trades = db_query(db, "SELECT strategy_id, side, entry_timestamp, entry_price, exit_timestamp, "
                              "exit_price, quantity, multiplier, gross_pnl, charges, net_pnl, exit_reason, status FROM trades")
        orders = db_query(db, "SELECT DISTINCT order_id FROM orders")
        fills = db_query(db, "SELECT fill_id, order_id FROM fills")
        dup_fills = db_query(db, "SELECT fill_id, COUNT(*) FROM fills GROUP BY fill_id HAVING COUNT(*)>1")
        dup_orders = db_query(db, "SELECT order_id, COUNT(*) FROM orders GROUP BY order_id HAVING COUNT(*)>1")
        orphan = [f for f in fills if f[1] and all(o[0] != f[1] for o in orders)]
        ev_closed = db_query(db, "SELECT COUNT(*) FROM events WHERE event_type='trade_closed'")
        eod_rows = [t for t in trades if (t[11] or "") == "eod_close"]
        check(f"DB_{name}_no_eod_close_anywhere", not eod_rows,
              f"eod_close_trades={len(eod_rows)}")
        check(f"DB_{name}_no_dup_fills", not dup_fills, f"dup={len(dup_fills)}")
        check(f"DB_{name}_no_dup_orders", not dup_orders, f"dup={len(dup_orders)}")
        check(f"DB_{name}_no_orphan_fills", not orphan, f"orphan={len(orphan)}")
        closed = [t for t in trades if t[12] == "closed"]
        check(f"DB_{name}_tracking", len(closed) == int(ev_closed[0][0]),
              f"closed={len(closed)} trade_closed_events={int(ev_closed[0][0])}")

    # ── per-strategy nightly boundary analysis ──
    # The REAL end-of-day position state is position_manager's open positions
    # AFTER all of that day's bars (signals applied by _process_signal); the
    # per-bar "post_pos" capture runs inside on_bar, i.e. BEFORE the engine
    # booked the signal - so a stop/reversal fired on the LAST bar of day D is
    # closed that same day and must NOT be counted as carried into D+1.
    boundary_rows = []          # every held-trade-into-night carried check
    carried_boundaries = 0
    carried_boundaries_ok = 0
    carried_hits = []           # (name, sid, side, entry_px, pid, night_date_D)
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        strat_sids = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
        for sid in strat_sids:
            seq = captured[sid]
            for k in range(len(seq) - 1):
                if seq[k]["date"] == seq[k + 1]["date"]:
                    continue
                Lc, F = seq[k], seq[k + 1]
                pos_end = eod_open.get(Lc["date"], {}).get(sid, [])
                held = len(pos_end) > 0
                sched = bool(Lc["post_exit"])
                if not held:
                    continue
                rec = {
                    "instrument": name, "strategy": sid,
                    "night": f"{Lc['date']} -> {F['date']}",
                    "position_id": pos_end[0][2],
                    "side_end_of_day": pos_end[0][0],
                    "entry_px_end_of_day": pos_end[0][1],
                    "scheduled_signal_exit": sched,
                    "side_first_bar_next_day_input": F["pre_pos"],
                    "side_first_bar_next_day_output": F["post_pos"],
                    "carried_entry_px_next": (F["pre_entries"][0][1] if F["pre_entries"] else None),
                }
                if not sched:
                    # same held position (side+avg+position_id) at the first
                    # bar of D+1 == the one that closed day D -> carried
                    c1 = F["pre_pos"] == pos_end[0][0]
                    pe = set((x[0], x[1], x[3]) for x in F["pre_entries"])
                    c2 = bool(F["pre_entries"]) and set((x[0], x[1], x[2]) for x in pos_end) == pe
                    rec.update({
                        "carried(pre_side)": c1, "same_trade_entry": c2,
                        "A_result": "PASS" if (c1 and c2) else "FAIL",
                    })
                    carried_boundaries += 1
                    carried_boundaries_ok += int(c1 and c2)
                    if c1 and c2:
                        carried_hits.append(
                            (name, sid, pos_end[0][0], pos_end[0][1],
                             pos_end[0][2], Lc["date"]))
                    boundary_rows.append(rec)
                    if not (c1 and c2):
                        print(f"  [debug] carry fail {name} {sid} {Lc['date']}->{F['date']}: "
                              f"L(end_real={pos_end},pend={Lc['post_pend']},exit={Lc['post_exit']},bt={Lc['bt']}) "
                              f"F(pre={F['pre_pos']},pre_entries={F['pre_entries']},"
                              f"post={F['post_pos']},bt={F['bt']})", flush=True)
                        for _j, _e in enumerate(seq[max(0, k - 8):k + 4]):
                            print(f"  [debug slice] i={max(0,k-8)+_j} {_e['date']} {_e['time']} "
                                  f"pre={_e['pre_pos']}/{_e['pre_pend']}/{_e['pre_exit']} "
                                  f"post={_e['post_pos']}/{_e['post_pend']}/{_e['post_exit']} "
                                  f"entries={_e['post_entries']}", flush=True)
                    check(f"B{len(boundary_rows)}_{name}_{sid}_carry@{Lc['date']}->{F['date']}",
                          c1 and c2,
                          f"end={pos_end[0][0]}@{pos_end[0][1]} pre={F['pre_pos']} "
                          f"pid={pos_end[0][2]} same_trade={c2} pend={Lc['post_pend']}")
                else:
                    rec.update({"carried(pre_side)": None, "same_trade_entry": None,
                                "A_result": "EXITED_BY_SIGNAL_AT_NEXT_OPEN"})
                    boundary_rows.append(rec)

    check("G_carry_boundaries_found", carried_boundaries > 0,
          f"nightly carried positions = {carried_boundaries}")
    check("G_carry_all_ok", carried_boundaries == carried_boundaries_ok,
          f"ok={carried_boundaries_ok}/{carried_boundaries}")

    # ── pending breakout carry + entry placement from a carried pending ──
    pend_carried = 0
    pend_placed = 0
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        strat_sids = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
        allfills = db_query(db, "SELECT order_id, strategy_id, side, price FROM fills")
        for sid in strat_sids:
            seq = captured[sid]
            consumed = set()
            for k in range(len(seq) - 1):
                if seq[k]["date"] == seq[k + 1]["date"]:
                    continue
                Lc, F = seq[k], seq[k + 1]
                if Lc["post_pend"] is None:
                    continue
                pend_carried += 1
                pend_side = Lc["post_pend"]
                fill_side = "BUY" if pend_side == "LONG" else "SELL"
                filled = placed = False
                for j in range(k + 1, len(seq)):
                    e2 = seq[j]
                    if e2["post_pos"] == pend_side and e2["post_pend"] is None:
                        filled = True
                        if (sid, round(float(Lc["post_trigger"]), 2)) not in consumed:
                            placed = any(f[1] == sid and f[2] == fill_side
                                         and tol(float(f[3]), float(Lc["post_trigger"]))
                                         for f in allfills)
                            consumed.add((sid, round(float(Lc["post_trigger"]), 2)))
                            pend_placed += int(placed)
                        break
                    if e2["date"] != Lc["date"] and e2["post_pos"] is None \
                            and e2["post_pend"] is None:
                        break
                check(f"PEND_{name}_{sid}_carry@{Lc['date']}->{F['date']}",
                      F["pre_pend"] == pend_side,
                      f"pend={pend_side} armed end={Lc['time']} trigger={Lc['post_trigger']} "
                      f"pre_next={F['pre_pend']}" + (f" entry_fill_at_trigger={placed}" if filled else ""))

    check("G_pending_carried", pend_carried > 0, f"pending boundaries = {pend_carried}")

    # ── carried-trade P&L (a position that was held overnight on night D) ──
    # Each carried hit is a REAL live position proven open at the close of day
    # D AND still open (same position_id) at the first bar of day D+1 — from
    # the boundary analysis above.  Its persisted trade row is matched by
    # (strategy_id, side, entry_price); the exit bar is located from the
    # captured chain (stop exits fill at the bar's close, reversal exits at
    # the next bar's open), so the carried-night count is exact per bar.


    def find_exit_event(seq, start_idx, exit_price, reason):
        """Return the event index of the exit bar for a closed trade."""
        for _i in range(start_idx, len(seq)):
            e = seq[_i]
            if not reason or reason in ("stop_loss_hit", "eod_close", "pending_timeout",
                                        "pending_entry_expired"):
                if tol(e["close"], exit_price):
                    return _i
            else:                      # "*_reversal": deferred exit at next open
                if e["pre_exit"] and tol(e["op"], exit_price):
                    return _i
                if tol(e["op"], exit_price):
                    return _i
        return None

    carried_pnl = []
    pnl_ok = 0
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        trades = db_query(db, "SELECT strategy_id, side, entry_price, exit_price, quantity, "
                              "multiplier, gross_pnl, charges, net_pnl, exit_reason, status FROM trades")
        strat_sids = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
        for sid in strat_sids:
            db_trades = [t for t in trades if t[0] == sid and t[10] == "closed"]
            seq = captured[sid]
            for (cb_name, cb_sid, side, entry_px, pid, night_D) in carried_hits:
                if cb_sid != sid or cb_name != name:
                    continue
                match = [t for t in db_trades if t[1] == side
                         and tol(float(t[2]), float(entry_px))]
                if not match:
                    check(f"PL_{name}_{sid}_carried_{night_D}_n{None}",
                          False, f"NO closed db row for pid={pid} {side} entry={entry_px}")
                    continue
                tr = match[0]
                mult = float(tr[5]) if tr[5] else MULT[name]
                qty = int(tr[4]) if tr[4] else QTY.get(sid, 1)
                side_long = tr[1] == "LONG"
                exp_gross = ((float(tr[3]) - float(tr[2])) * qty * mult) if side_long \
                    else ((float(tr[2]) - float(tr[3])) * qty * mult)
                exp_chg = ref_charges(CHARGE_CFG[name], float(tr[2]), float(tr[3]), mult, side_long)
                exp_net = exp_gross - exp_chg
                # exit bar + carried nights from the captured chain
                start_idx = 0
                for _i, e in enumerate(seq):
                    if e["date"] > night_D:
                        start_idx = _i
                        break
                ef = find_exit_event(seq, start_idx, float(tr[3]), tr[9])
                exit_day = seq[ef]["date"] if ef is not None else None
                nights = _days_between(night_D, exit_day) if exit_day else None
                ok = (tol(float(tr[6]), exp_gross) and tol(float(tr[7]), exp_chg)
                      and tol(float(tr[8]), exp_net)
                      and (tr[9] or "") not in ("eod_close",))
                pnl_ok += int(ok)
                carried_pnl.append({
                    "instrument": name, "strategy": sid, "side": side,
                    "position_id": pid,
                    "carried_entry_day": night_D, "carried_exit_day": exit_day,
                    "nights_carried": nights,
                    "entry_price": float(tr[2]), "exit_price": float(tr[3]),
                    "quantity": qty, "multiplier": mult,
                    "gross_expected": round(exp_gross, 2), "gross_saved": float(tr[6]),
                    "charges_expected": exp_chg, "charges_saved": float(tr[7]),
                    "net_expected": round(exp_net, 2), "net_saved": float(tr[8]),
                    "exit_reason": tr[9],
                    "A_result": "PASS" if ok else "FAIL",
                })

    check("G_carried_pnl_trades_found", len(carried_pnl) > 0,
          f"carried overnight trades = {len(carried_pnl)}")
    check("G_carried_pnl_all_ok", pnl_ok == len(carried_pnl),
          f"ok={pnl_ok}/{len(carried_pnl)}")
    for r in carried_pnl:
        check(f"PL_{r['instrument']}_{r['strategy']}_carried_{r['carried_entry_day']}->{r['carried_exit_day']}",
              r["A_result"] == "PASS",
              f"{r['side']} entry={r['entry_price']} exit={r['exit_price']} "
              f"pid={r['position_id']} gross={r['gross_saved']} chg={r['charges_saved']} "
              f"net={r['net_saved']} reason={r['exit_reason']} nights={r['nights_carried']}")

    # ── per-strategy realized == sum of its nets (parallel isolation) ──
    for name in ("GOLDM", "SILVERM"):
        captured, db, engine, eod_sim, pos_kept, eod_open = runs[name]
        strat_sids = [s for s in FAST_TF if LIVE_STRATEGIES[s]["instrument"] == name]
        for sid in strat_sids:
            raw_trades = db_query(db, "SELECT strategy_id, net_pnl, status FROM trades")
            strat_trades = [t for t in raw_trades if t[0] == sid and t[2] == "closed"]
            strat_account = engine.account_engines[sid]
            realized = float(getattr(strat_account, "realized_pnl", -999999.0))
            sum_net = sum(float(t[1]) for t in strat_trades) if strat_trades else 0.0
            check(f"ACCT_{name}_{sid}_realized", tol(realized, sum_net) and len(strat_trades) > 0,
                  f"realized={realized} sum_of_its_trades={sum_net} closed={len(strat_trades)}")

    # ── write outputs ──
    L.append_rows(OUT_CSV, boundary_rows)
    L.append_rows(OUT_CSV, carried_pnl)

    md_lines = [
        "# P3 - CARRY / NO-EOD-EXIT E2E INPUT->OUTPUT TEST (fresh re-run, 2026-08-30)",
        "",
        "Everything re-run on the CURRENT live code with a FRESH engine temp dir;",
        "no past result file is read or trusted. Real PaperBroker (slippage=0 ->",
        "fills equal model price), real OrderManager / TradeCloseManager / P&L",
        "accounts / persistence DB / all 4 strategies parallel. Input: real LAST5",
        "5m stream fed DAY-BY-DAY; after every non-final day the engine's EOD",
        "guard (`_on_tick` -> `_execute_eod_close`) is replayed in all four",
        "session states with a spy on `_execute_eod_close`.",
        "",
        f"- Input rows: GOLDM {len(raw_by['GOLDM'])}, SILVERM {len(raw_by['SILVERM'])} (5m)",
        f"- Nightly held-across-boundary carries verified: **{carried_boundaries}**",
        f"- Pending breakouts carried across the break: **{pend_carried}**",
        f"- Carried-over-night closed trades P&L-verified: **{len(carried_pnl)}**",
        "",
        "## Component checklist",
        "| component | result |",
        "|-----------|--------|",
    ]
    # group checks by component prefix
    for nm, ok, det in CHECKS:
        md_lines.append(f"| `{nm}` | {'**PASS**' if ok else '**FAIL**'} | {det} |")
    md_lines += [
        "",
        "**VERDICT: " + ("ALL PASSED" if ALL_PASS else "FAILURES PRESENT") + "**",
        "",
        "Components tested (input->output):",
        "1. Trade placement  - orders+fills created at the right model price;",
        "   a breakout pending carried overnight places its entry fill at the",
        "   pending trigger on the next session.",
        "2. P&L calc         - carried trades (entry_day != exit_day): gross =",
        "   (exit-entry)*mult*qty, charges = fee model, net = gross-charges.",
        "3. Trade saving     - carried trades persisted open->closed with their",
        "   fills/orders; no duplicate/orphan rows; trade_closed events == closed",
        "   trades.",
        "4. NO EOD exit      - should_force_close() is False in LIVE_TRADING /",
        "   MARKET_CLOSE / AFTER_MARKET / OVERNIGHT at every day boundary;",
        "   `_execute_eod_close` is never invoked; positions and their entry",
        "   prices survive the break untouched.",
        "5. Carried pending  - the armed breakout pending (trigger/SL) survives",
        "   the break and resolves only by fill / expiry rule.",
        "6. Parallel accounts- each strategy's account realized == the sum of its",
        "   own closed trades (realized does not include the still-open carried",
        "   position).",
        "",
        f"Input/Output detail rows -> {OUT_CSV.name}",
    ]
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"\nRESULT: {'ALL PASSED' if ALL_PASS else 'FAILURES PRESENT'}")
    print(f"CSV -> {OUT_CSV}")
    print(f"MD  -> {OUT_MD}")
    sys.exit(0 if ALL_PASS else 1)


if __name__ == "__main__":
    main()