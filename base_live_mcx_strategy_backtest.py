#!/usr/bin/env python3
"""base_live_mcx_strategy_backtest.py — the 4 strategies: {2 base TFs} x {GOLDM, SILVERM}, LIVE from Dhan.

Reads GOLDM/SILVERM directly from Dhan (no CSV), through live_data.fetch_metal,
all bars trimmed to the last fully-CLOSED candle (no in-progress / stale tail).

The 4 STRATEGIES (all DEMA=3 ATR=6 Factor=1.0, HTF 15m + 1H):
    #1 SILVERM base 5m / 15m / 1H
    #2 GOLDM   base 5m / 15m / 1H
    #3 SILVERM base 15m / 15m / 1H
    #4 GOLDM   base 15m / 15m / 1H

Signal = base close crosses the 1H line, confluent with the 15m line.
For EVERY strategy it runs the backtest AND prints ALL crossing signals
(BUY/SELL) with full candle + line + SL prices for the LAST 3 trading days.

Usage:
    python base_live_mcx_strategy_backtest.py                 # all 4 strategies
    python base_live_mcx_strategy_backtest.py --metal GOLDM   # both bases for one metal
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import backtrader as bt

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import live_data
import core.dema_mtf as DM
from goldm_dema_mtf_futures import GoldFuturesDemaStrategy, SESSION_OPEN
import goldm_dema_mtf_futures as bt_mod

MULTIPLIER = {"GOLDM": 10.0, "SILVERM": 5.0}
CAP = {"GOLDM": 300_000, "SILVERM": 300_000}   # Rs 3 Lakh seed capital
DEMA_P, ATR_P, ATR_F = 3, 6, 1.0               # the single SYMMETRIC strategy config
BASES = [5, 15]                                 # the 2 base timeframes -> 4 strategies
LAST_DAYS = 1                                   # crossing-signal window (trading days)


def _out(metal: str, base: int) -> str:
    return f"trades/{metal.lower()}_D{DEMA_P}_A{ATR_P}_F{str(ATR_F).replace('.', '')}_{base}m_15m_1H_trades.csv"


def _cross_signals(df, dema_15m, dema_1h):
    """List of BUY/SELL signal-bar dicts with OHLC + line + SL prices."""
    sig = DM.compute_signals(df, dema_15m, dema_1h)
    buy = sig["raw_buy"]
    sell = sig["raw_sell"]
    sl_buy = sig["sl_buy"]
    sl_sell = sig["sl_sell"]
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    opn = df["open"].to_numpy(float)
    h15 = np.asarray(dema_15m, dtype=float)
    h1 = np.asarray(dema_1h, dtype=float)
    out = []
    for i in range(1, len(df)):
        if buy[i]:
            out.append({"side": "BUY", "dt": df["datetime"].iloc[i],
                        "O": opn[i], "H": high[i], "L": low[i], "C": close[i],
                        "prevC": close[i - 1], "h1": h1[i], "h15": h15[i],
                        "sl": sl_buy[i],
                        "conc": f"{'15m<1h' if h15[i] < h1[i] else '15m>1h'}"})
        elif sell[i]:
            out.append({"side": "SELL", "dt": df["datetime"].iloc[i],
                        "O": opn[i], "H": high[i], "L": low[i], "C": close[i],
                        "prevC": close[i - 1], "h1": h1[i], "h15": h15[i],
                        "sl": sl_sell[i],
                        "conc": f"{'15m>1h' if h15[i] > h1[i] else '15m<1h'}"})
    return out


def _print_crosses(metal, base, df, dema_15m, dema_1h):
    """Print all crossing signals (BUY/SELL) with prices for the last LAST_DAYS."""
    sigs = _cross_signals(df, dema_15m, dema_1h)
    if not sigs:
        print(f"  No crossing signals in window.")
        return
    days = sorted({s["dt"].date() for s in sigs})[-LAST_DAYS:]
    recent = [s for s in sigs if s["dt"].date() in set(days)]

    print("\n" + "=" * 112)
    print(f"  {metal} — last {min(LAST_DAYS, len(days))} trading days CROSSOVER SIGNALS "
          f"(base {base}m close vs 1H line) | DEMA={DEMA_P} ATR={ATR_P} F={ATR_F} | "
          f"BASE {base}m / 15m / 1H | thru {df['datetime'].max():%d-%b %H:%M}")
    print("=" * 112)
    print(f"  {'#':>3} {'SIDE':<5} {'BAR TIME':<17} {'O':>9} {'H':>9} {'L':>9} {'C':>9} "
          f"{'prevC':>9} {'1H':>9} {'15m':>9} {'SL':>9}  CONFLUENCE")
    print("  " + "-" * 118)
    for k, s in enumerate(recent, 1):
        dt = str(s["dt"])[:16]
        print(f"  {k:>3} {s['side']:<5} {dt:<17} {s['O']:>9,.0f} {s['H']:>9,.0f} {s['L']:>9,.0f} "
              f"{s['C']:>9,.0f} {s['prevC']:>9,.0f} {s['h1']:>9,.0f} {s['h15']:>9,.0f} "
              f"{s['sl']:>9,.0f}  {s['conc']}")
    print("  " + "-" * 118)
    print(f"  Total crossing signals in window: {len(recent)}\n")


def run(metal: str, base: int) -> dict:
    """Run one strategy: the given metal on the given base timeframe (5 or 15m)."""
    bt_mod.MULTIPLIER = MULTIPLIER[metal]
    cap = CAP[metal]
    out = _out(metal, base)

    t0 = time.time()
    df5, df15, df60 = live_data.fetch_metal(metal)
    fetch_s = time.time() - t0

    # Choose the base timeline
    if base == 5:
        df = df5.reset_index(drop=True)
    else:
        df = df15.reset_index(drop=True)

    print(f"Fetched {metal} base{base}m: {len(df)} bars "
          f"({df.datetime.min()} -> {df.datetime.max()}) | n15 {len(df15)} | "
          f"n60 {len(df60)}  [{fetch_s:.1f}s]", flush=True)

    if len(df) < 200:
        print(f"ERROR: too few base bars ({len(df)}); aborting", flush=True)
        return {}

    # Line mapping always uses NATIVE 15m / 60m bars -> 15m line + 1H line
    dm = DM.native_map_htf(df, df15, 15, DEMA_P, ATR_P, ATR_F)
    dh = DM.native_map_htf(df, df60, 60, DEMA_P, ATR_P, ATR_F)

    feed = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    feed = feed.set_index("datetime")
    feed.index = feed.index.astype("datetime64[ns]")

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cap)
    cerebro.adddata(bt.feeds.PandasData(dataname=feed, timeframe=bt.TimeFrame.Minutes, compression=base))
    cerebro.addstrategy(GoldFuturesDemaStrategy,
                        dema_15m=np.asarray(dm, dtype=float),
                        dema_1h=np.asarray(dh, dtype=float),
                        capital=cap)

    t1 = time.time()
    strat = cerebro.run(runonce=False, preload=False, live=False, stdstats=False)[0]
    elapsed = time.time() - t1 + fetch_s
    trades = strat.trades
    closed = [t for t in trades if t.get("pnl") is not None]

    pnls = np.array([t["pnl"] for t in closed])
    gp = np.array([t.get("gross_pnl", 0) for t in closed])
    charges = np.array([t.get("charges", 0) for t in closed])
    n = len(pnls)
    w = int(np.sum(pnls > 0))
    lo = n - w
    npnl = float(np.sum(pnls))
    gw = float(np.sum(gp[pnls > 0])) if w else 0
    gl = float(np.abs(np.sum(gp[pnls <= 0]))) if lo else 0
    tc = float(np.sum(charges))
    wr = w / n * 100 if n else 0
    pf = gw / gl if gl > 0 else 99.99
    eq = cap + np.cumsum(pnls)
    pk = np.maximum.accumulate(np.insert(eq, 0, cap))[1:]
    mdd = float(np.max((pk - eq) / pk * 100))
    std = float(np.std(pnls / cap))
    sh = float(np.mean(pnls / cap) / std * np.sqrt(252)) if n > 1 and std > 0 else 0
    roi = npnl / cap * 100

    print(f"Done in {elapsed:.1f}s\n")
    print("=" * 72)
    print(f"  [{metal} | BASE {base}m] DEMA={DEMA_P} ATR={ATR_P} F={ATR_F} | {base}m / 15m / 1H")
    print(f"  Data through {df.datetime.max()}  (contract {live_data.INSTR_SECURITY[metal]})")
    print("=" * 72)
    print(f"  Total trades      : {n} ({w}W / {lo}L)")
    print(f"  Win rate          : {wr:.1f}%")
    print(f"  Net P&L           : Rs {npnl:,.0f}")
    print(f"  Total charges     : Rs {tc:,.0f}")
    print(f"  Profit factor     : {pf:.2f}")
    print(f"  Max drawdown      : {mdd:.1f}%")
    print(f"  Sharpe            : {sh:.2f}")
    print(f"  ROI               : {roi:.1f}%")
    print(f"  Final equity      : Rs {eq[-1]:,.0f}")
    print("=" * 72)

    if closed:
        tdf = pd.DataFrame(closed)
        tdf["entry_datetime"] = pd.to_datetime(tdf["entry_datetime"])
        tdf["month"] = tdf["entry_datetime"].dt.to_period("M")
        print("\n  MONTHLY BREAKDOWN:")
        print(f"  {'Month':>8} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'P&L':>12} {'Cumul':>12}")
        print(f"  {'-' * 55}")
        cum = 0
        for m, grp in tdf.groupby("month"):
            mp = grp["pnl"].values
            mw = int(np.sum(mp > 0))
            cum += float(np.sum(mp))
            print(f"  {str(m):>8} {len(grp):>6} {mw:>5} {mw / len(grp) * 100:>5.1f}% Rs {np.sum(mp):>10,.0f} Rs {cum:>10,.0f}")
        os.makedirs("trades", exist_ok=True)
        tdf.to_csv(out, index=False)
        print(f"\n  Saved: {out} ({len(tdf)} trades)")

    _print_crosses(metal, base, df, dm, dh)

    return {"metal": metal, "base": base, "trades": n, "net_pnl": npnl,
            "pf": pf, "roi": roi, "equity": float(eq[-1])}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LIVE Dhan DEMA-ATR backtest — 4 strategies (2 metals x 2 bases)")
    parser.add_argument("--metal", choices=["GOLDM", "SILVERM"], default=None,
                        help="if given, run only this metal's 2 bases; else run all 4")
    args = parser.parse_args()
    summary = []
    if args.metal:
        for base in BASES:
            summary.append(run(args.metal, base))
    else:
        for metal in ["GOLDM", "SILVERM"]:
            for base in BASES:
                summary.append(run(metal, base))

    print("\n" + "=" * 72)
    print("  ALL STRATEGIES SUMMARY (net P&L, Rs — capital Rs 3 Lakh)")
    print("=" * 72)
    print(f"  {'#':>2} {'Metal':<9} {'Base':>5} {'Trades':>7} {'Net P&L':>12} {'ROI':>8} {'PF':>6} {'Equity':>12}")
    for i, r in enumerate(summary, 1):
        print(f"  {i:>2} {r['metal']:<9} {str(r['base'])+'m':>5} {r['trades']:>7} "
              f"{r['net_pnl']:>12,.0f} {r['roi']:>7.1f}% {r['pf']:>6.2f} {r['equity']:>12,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
