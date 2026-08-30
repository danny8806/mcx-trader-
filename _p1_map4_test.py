"""PHASE-1 / PART 53 — FRESH RE-VERIFICATION: backtest vs live parity for ALL
four live strategies, value-by-value, from the raw 5m feed through resampling
and the HTF DEMA-ATR mapping at every fast bar.

This is a NEW test file; previous results were NOT trusted.  Every number below
is recomputed from the current engine code and compared against an independent
reference (aggregation, DEMA-ATR, and mapping are derived in `_p1_lib`, never
copied from earlier runs).

For a cold engine fed the real 870-bar LAST5 stream (5m + 15m + 1h interleaved
as CandleFetcher emits them), while the LIVE pipeline runs unchanged, we verify:

  M1  resample OHLCV value-level equality: every 15m (290) and 1h (75,
      keep-all incl. trailing 23:00 partial) bucket open/high/low/close/volume
      == independent session resample
  M2  5m raw OHLCV identity: every consumed 5m bar == the source CSV row
  M3  DEMA-ATR line value-level equality for 5m (870), 15m (290) and 1h (75)
      against ref_dema_atr over the same series
  M4  strategy-consumed mapping equality at EVERY fast bar:
        htf_mapped  == 1h line[bisect_right(1h ends, bar.end_ts)-1]
        mid_mapped  == 15m line[bisect_right(15m ends, bar.end_ts)-1]
        fast value  == fast-tf line[position of this fast bar]
      for all four LIVE strategies (gold_01 5m, gold_02 15m,
                                     silver_02 5m, silver_01 15m),
      including confirmation flags and source timestamps.

Outputs:
  FOUR_STRATEGY_HTF_MAPPING_PARITY.csv   (per fast-bar detail)
  FOUR_STRATEGY_HTF_MAPPING_SUMMARY.md   (matrix + verdict)
Exit 0 iff all pass.
"""
from __future__ import annotations

import bisect
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

import _p1_lib as L
from core.market_status import DataStatus, EngineStatus, MarketState
from full_simulator import LIVE_STRATEGIES, build_bars


def isod(ts):
    return L.ist_from_epoch(float(ts)).replace(tzinfo=None)


def cdf(rows, name):
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def bucket_key(ts):
    if isinstance(ts, (pd.Timestamp,)):
        v = ts.timestamp()
    else:
        v = float(ts)
    return int(round(v))


def ist_epoch_naive(t):
    """IST-true epoch for a naive wall-clock Timestamp (matches engine/CSV ts)."""
    return pd.Timestamp(t).tz_localize("Asia/Kolkata").timestamp()


STRAT_FAST = {s: LIVE_STRATEGIES[s]["fast_timeframe"] for s in LIVE_STRATEGIES}
RESULT_CSV = L.AUDIT_DIR / "FOUR_STRATEGY_HTF_MAPPING_PARITY.csv"
if RESULT_CSV.exists():
    RESULT_CSV.unlink()

rows_out = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<60s} {detail[:100]}")


all_pass = True
tot_bars = {"5m": 0, "15m": 0, "1h": 0}
tot_fast = 0


def tol(a, b, rtol=1e-6):
    return abs(a - b) <= rtol * (1.0 + abs(b))


for name in ("GOLDM", "SILVERM"):
    print(f"===== {name} =====", flush=True)
    raw = L.load_csv_rows(name, L.LAST5[0], L.LAST5[-1])
    df5 = cdf(raw, name)

    ref15, _ = L.ref_session_resample(df5, 15)
    ref1h, _ = L.ref_session_resample(df5, 60, keep_partial=True)
    line5 = L.ref_dema_atr(np.array([r[2] for r in raw], float),
                           np.array([r[3] for r in raw], float),
                           np.array([r[4] for r in raw], float), 3, 6, 1.0)
    line15 = L.ref_dema_atr(ref15["high"].to_numpy(float), ref15["low"].to_numpy(float),
                            ref15["close"].to_numpy(float), 3, 6, 1.0)
    line1h = L.ref_dema_atr(ref1h["high"].to_numpy(float), ref1h["low"].to_numpy(float),
                            ref1h["close"].to_numpy(float), 3, 6, 1.0)
    end15_s = np.array([ist_epoch_naive(t) + 15 * 60 for t in pd.to_datetime(ref15["_bucket"])], float)
    end1h_s = np.array([ist_epoch_naive(t) + 60 * 60 for t in pd.to_datetime(ref1h["_bucket"])], float)

    ref15_key = {bucket_key(ist_epoch_naive(t)): {"o": o, "h": h, "l": l_, "c": c, "v": v}
                 for t, o, h, l_, c, v in zip(pd.to_datetime(ref15["_bucket"]),
                                              ref15["open"], ref15["high"],
                                              ref15["low"], ref15["close"], ref15["volume"])}
    ref1h_key = {bucket_key(ist_epoch_naive(t)): {"o": o, "h": h, "l": l_, "c": c, "v": v}
                 for t, o, h, l_, c, v in zip(pd.to_datetime(ref1h["_bucket"]),
                                              ref1h["open"], ref1h["high"],
                                              ref1h["low"], ref1h["close"], ref1h["volume"])}
    raw5_key = {bucket_key(r[0]): {"o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                for r in raw}

    # ---- cold engine, LIVE path intact, signals recorded not executed ----
    cfg = L.write_config(L.fresh_run_root(f"map4_{name}"),
                         warmup={"last_trading_days": 0, "keep_partial": True})
    engine, persistence = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
    L.wire_trade_close(engine)
    engine._running = True
    ms = engine.market_status
    ms._engine_status = EngineStatus.TRADING
    ms._data_status = DataStatus.CONNECTED
    ms.force_state(MarketState.LIVE_TRADING)
    engine.execution_engine.update_price(name, 150000.0)

    signals_seen = []
    engine._process_signal = lambda sig: signals_seen.append(sig) or True

    ind_hist = defaultdict(list)
    for tf in ("5m", "15m", "1h"):
        ind = engine.indicators.get(f"{name}:{tf}")
        if ind is None:
            continue
        orig = ind.update

        def mk(orig, tf, ind_hist=ind_hist):
            def upd(o, h, l, c):
                r = orig(o, h, l, c)
                ind_hist[tf].append((o, h, l, c))
                return r
            return upd

        ind.update = mk(orig, tf)

    strat_hist = defaultdict(list)
    bar_hist = defaultdict(list)
    orig_bc = engine._on_bar_closed

    def bc(bar):
        bar_hist[bar.timeframe].append((bar.start_ts, bar.open, bar.high,
                                        bar.low, bar.close, bar.volume))
        return orig_bc(bar)

    engine._on_bar_closed = bc

    bars5, bars15, bars1h = build_bars(name, raw, keep_partial=True)
    all_bars = sorted(bars5 + bars15 + bars1h,
                      key=lambda b: (b.start_ts, {"5m": 0, "15m": 1, "1h": 2}.get(b.timeframe, 3)))

    orig_on_bar = {}
    for sid, strat in engine.strategies.items():
        if strat.instrument != name:
            continue
        orig_on_bar[sid] = strat.on_bar

        def mkob(sid, orig, strat_hist=strat_hist):
            def ob(bar, htf_mapped, fast_v, mid_mapped):
                r = orig(bar, htf_mapped, fast_v, mid_mapped)
                strat_hist[sid].append(
                    (bar.start_ts, bar.timeframe, htf_mapped, fast_v, mid_mapped, r))
                return r
            return ob

        strat.on_bar = mkob(sid, orig_on_bar[sid])

    for b in all_bars:
        engine._on_bar_closed(b)

    # ---------- M1 / M2 resample + raw OHLCV value-level ----------
    def cmp_buckets(hist_entries, ref_key, what):
        if len(hist_entries) != len(ref_key):
            return False, len(hist_entries), len(ref_key)
        for st, o, h, l_, c, v in hist_entries:
            r = ref_key.get(bucket_key(st))
            if r is None:
                return False, len(hist_entries), len(ref_key)
            if not (tol(o, r["o"]) and tol(h, r["h"]) and tol(l_, r["l"])
                    and tol(c, r["c"]) and abs(v - r["v"]) <= 1e-6 * (1.0 + abs(r["v"]))):
                return False, len(hist_entries), len(ref_key)
        return True, len(hist_entries), len(ref_key)

    ok15, n15, n15r = cmp_buckets(bar_hist["15m"], ref15_key, "15m")
    ok1h, n1h, n1hr = cmp_buckets(bar_hist["1h"], ref1h_key, "1h")
    check(f"M1_{name}_15m_bucket_ohlcv", ok15, f"{n15}/{n15r} buckets")
    check(f"M1_{name}_1h_bucket_ohlcv_keepall", ok1h, f"{n1h}/{n1hr} buckets")

    ok5, n5, n5r = cmp_buckets(bar_hist["5m"], raw5_key, "5m")
    check(f"M2_{name}_5m_raw_ohlcv_identity", ok5, f"{n5}/{n5r} rows")

    # ---------- M3 DEMA-ATR line value-level ----------
    from indicators.dema_atr import DEMAATR

    def prod_line(ohlcv):
        m = DEMAATR(3, 6, 1.0)
        out = []
        for o, h, l_, c in ohlcv:
            m.update(o, h, l_, c)
            out.append(m.value)
        return out

    line5_prod = prod_line(ind_hist["5m"])
    line15_prod = prod_line(ind_hist["15m"])
    line1h_prod = prod_line(ind_hist["1h"])
    m5 = max((abs(a - b) for a, b in zip(line5_prod, line5)), default=0.0)
    m15 = max((abs(a - b) for a, b in zip(line15_prod, line15)), default=0.0)
    m1h = max((abs(a - b) for a, b in zip(line1h_prod, line1h)), default=0.0)
    check(f"M3_{name}_dema_atr_line_values_5m15m1h",
          m5 < 1e-6 and m15 < 1e-6 and m1h < 1e-6,
          f"maxdiff 5m={m5:.2e} 15m={m15:.2e} 1h={m1h:.2e}")
    ind5 = engine.indicators[f"{name}:5m"]
    ind15 = engine.indicators[f"{name}:15m"]
    ind1h = engine.indicators[f"{name}:1h"]
    ok_end = (ind5.value is not None and ind15.value is not None and ind1h.value is not None
              and abs(ind5.value - line5[-1]) < 1e-6
              and abs(ind15.value - line15[-1]) < 1e-6
              and abs(ind1h.value - line1h[-1]) < 1e-6)
    check(f"M3_{name}_engine_indicator_final_values", ok_end,
          f"5m={ind5.value} 15m={ind15.value} 1h={ind1h.value}")

    # ---------- M4 per-strategy per-fast-bar mapping ----------
    fast_bars_idx = {}
    fast_bars_idx["5m"] = {bucket_key(b.start_ts): i
                           for i, b in enumerate(sorted(bars5, key=lambda x: x.start_ts))}
    fast_bars_idx["15m"] = {bucket_key(b.start_ts): i
                            for i, b in enumerate(sorted(bars15, key=lambda x: x.start_ts))}
    e1 = [float(e) for e in end1h_s]
    e15 = [float(e) for e in end15_s]
    n_fast_call = 0
    for sid in STRAT_FAST:
        if LIVE_STRATEGIES[sid]["instrument"] != name:
            continue
        ftf = STRAT_FAST[sid]
        line_fast = line5 if ftf == "5m" else line15
        bad = 0
        examined = 0
        for (bt, btf, htf, fv, mid, sig) in strat_hist[sid]:
            if btf != ftf:
                continue
            examined += 1
            idxF = fast_bars_idx[ftf].get(bucket_key(bt))
            okf = idxF is not None and fv is not None and tol(fv, float(line_fast[idxF]), 1e-6)
            end_ts = float(bt) + (5 if ftf == "5m" else 15) * 60
            i1 = bisect.bisect_right(e1, end_ts) - 1
            okh = htf.htf_confirmed == (i1 >= 0)
            okh_val = okh_ts = True
            if i1 >= 0:
                okh_val = tol(htf.htf_value, float(line1h[i1]), 1e-6)
                okh_ts = abs(float(htf.htf_source_timestamp) - e1[i1]) < 1e-3
            i2 = bisect.bisect_right(e15, end_ts) - 1
            okm = mid.htf_confirmed == (i2 >= 0)
            okm_val = okm_ts = True
            if i2 >= 0:
                okm_val = tol(mid.htf_value, float(line15[i2]), 1e-6)
                okm_ts = abs(float(mid.htf_source_timestamp) - e15[i2]) < 1e-3
            bad_this = not (okf and okh and okh_val and okh_ts and okm and okm_val and okm_ts)
            bad += bad_this
            if bad_this:
                rows_out.append({"instrument": name, "strategy": sid, "fast_tf": ftf,
                                 "bar_start_ist": isod(bt).strftime("%Y-%m-%d %H:%M:%S"),
                                 "result": "MIDMATCH",
                                 "fast_engine": fv,
                                 "fast_ref": line_fast[idxF] if idxF is not None else "NA",
                                 "htf_engine": htf.htf_value if htf.htf_confirmed else "NC",
                                 "htf_ref": line1h[i1] if i1 >= 0 else "NC",
                                 "mid_engine": mid.htf_value if mid.htf_confirmed else "NC",
                                 "mid_ref": line15[i2] if i2 >= 0 else "NC"})
        n_fast_call += examined
        ok = examined > 0 and bad == 0
        check(f"M4_{name}_{sid}(fast={ftf})_mapped_values", ok,
              f"bars={examined} midmatch={bad}")

    tot_bars["5m"] += len(bar_hist["5m"])
    tot_bars["15m"] += len(bar_hist["15m"])
    tot_bars["1h"] += len(bar_hist["1h"])
    tot_fast += n_fast_call

    for sid, strat in engine.strategies.items():
        if sid in orig_on_bar:
            strat.on_bar = orig_on_bar[sid]
    L.teardown(engine, persistence)

all_pass = all(ok for _, ok, _ in checks)
print("\n=== FOUR-STRATEGY HTF MAPPING PARITY (fresh re-verification) ===")
for c in checks:
    print(f"  {'PASS' if c[1] else 'FAIL'}  {c[0]}")
print(f"\nTOTALS: raw5m={tot_bars['5m']} 15m_buckets={tot_bars['15m']} "
      f"1h_buckets_keepall={tot_bars['1h']} strategy_fast_bars_mapped={tot_fast} "
      f"signals_generated={len(signals_seen)}")
print(f"RESULT: {'ALL PASSED' if all_pass else 'FAILURES PRESENT'}")

L.append_rows(RESULT_CSV, rows_out)
summary_md = L.AUDIT_DIR / "FOUR_STRATEGY_HTF_MAPPING_SUMMARY.md"
summary_md.write_text(
    "# FOUR-STRATEGY HTF DEMA-ATR MAPPING PARITY (fresh re-verification)\n\n"
    "Re-verified from a cold CURRENT-code engine fed the real 870-bar LAST5 "
    "stream; reference math in `_p1_lib` (independent).\n\n"
    "| check | result |\n|-------|--------|\n"
    + "".join(f"| {name} | {'**PASS**' if ok else '**FAIL**'} |\n"
              for name, ok, _ in checks)
    + f"\n**VERDICT: {'ALL PASSED' if all_pass else 'FAILURES PRESENT'}**\n"
    f"raw 5m bars consumed: {tot_bars['5m']}; 15m buckets: {tot_bars['15m']}; "
    f"1h buckets (keep-all incl. 23:00 partial): {tot_bars['1h']}; "
    f"strategy fast-bar mappings checked: {tot_fast}; "
    f"signals generated during capture: {len(signals_seen)}.\n",
    encoding="utf-8")
print(f"\nFOUR_STRATEGY_HTF_MAPPING_PARITY.csv rows: {len(rows_out)} (should be 0)")
print(f"SUMMARY -> {summary_md}")
sys.exit(0 if all_pass else 1)