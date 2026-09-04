"""Inspect production DB — trades with signal candle + exit candle details.

Shows every trade with:
  - Signal candle: timestamp, OHLCV, timeframe
  - Entry: time, price, DEMA-ATR, HTF DEMA-ATR
  - Exit candle: timestamp, close price
  - Exit: time, price, reason, LONG EXIT / SHORT EXIT
  - P&L: Gross, Fees, Net

Usage:
    cd /app
    python _inspect_db.py
    python _inspect_db.py --trade-id <id>
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
DB_DIR = Path("/app/data/db")
ANALYTICS_DB = DB_DIR / "analytics.db"
TRADING_DB = DB_DIR / "trading.db"


def ts_str(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M:%S")


def exit_label(side: str) -> str:
    return f"{side} EXIT" if side else "EXIT"


def fmt(val, decimals=0):
    if val is None:
        return "-"
    if decimals == 0:
        return f"{val:,.0f}"
    return f"{val:,.{decimals}f}"


def show_all_trades():
    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    total = con.execute("SELECT COUNT(*) FROM trades_analytics").fetchone()[0]
    open_t = con.execute("SELECT COUNT(*) FROM trades_analytics WHERE status='OPEN'").fetchone()[0]
    closed = con.execute("SELECT COUNT(*) FROM trades_analytics WHERE status='CLOSED'").fetchone()[0]

    print(f"\n  TRADES: {total} total, {open_t} open, {closed} closed")

    rows = con.execute("""
        SELECT trade_id, strategy_id, instrument, side, status,
               entry_price, exit_price, entry_quantity,
               entry_dema, entry_htf_value,
               gross_pnl, fees, net_pnl, exit_reason,
               signal_time, created_at, closed_at,
               signal_bar_open, signal_bar_high, signal_bar_low,
               signal_bar_close, signal_bar_volume, signal_bar_timeframe,
               signal_bar_timestamp,
               multiplier,
               exit_bar_close, exit_bar_timestamp,
               first_fill_time
        FROM trades_analytics
        ORDER BY created_at DESC
    """).fetchall()

    if not rows:
        print("  No trades found.")
        con.close()
        return

    for r in rows:
        trade_id = str(r[0])[:14]
        strat = str(r[1])
        instr = str(r[2])
        side = str(r[3])
        status = str(r[4])
        entry_price = r[5]
        exit_price = r[6]
        qty = r[7] or 0
        dema = r[8]
        htf = r[9]
        gross = r[10] or 0
        fees = r[11] or 0
        net = r[12] or 0
        reason = str(r[13] or "")
        sig_time = r[14]
        created = r[15]
        closed_at = r[16]
        sig_o = r[17]
        sig_h = r[18]
        sig_l = r[19]
        sig_c = r[20]
        sig_v = r[21]
        sig_tf = str(r[22] or "")
        sig_ts = r[23]
        multiplier = r[24] or 1.0
        exit_bar_close = r[25]
        exit_bar_ts = r[26]
        entry_time = r[27]

        # Exit label
        if status == "CLOSED":
            action = exit_label(side)
        else:
            action = f"OPEN {side}"

        pnl_sign = "+" if gross >= 0 else ""
        net_sign = "+" if net >= 0 else ""

        print(f"\n  {'='*70}")
        print(f"  {trade_id}  {instr}  {strat}  {action}  qty={qty}")
        print(f"  {'='*70}")

        # Entry details
        print(f"  ENTRY:")
        print(f"    Time       = {ts_str(entry_time)}")
        print(f"    Price      = {fmt(entry_price)}")
        print(f"    Stop       = {fmt(dema)}")
        print(f"    Multiplier = {fmt(multiplier)}")

        # Signal candle
        has_candle = any(v is not None for v in [sig_o, sig_h, sig_l, sig_c])
        if has_candle:
            print(f"  SIGNAL CANDLE ({sig_tf or '?'}):")
            print(f"    Timestamp  = {ts_str(sig_ts)}")
            print(f"    Open       = {fmt(sig_o)}")
            print(f"    High       = {fmt(sig_h)}")
            print(f"    Low        = {fmt(sig_l)}")
            print(f"    Close      = {fmt(sig_c)}")
            if sig_v is not None:
                print(f"    Volume     = {fmt(sig_v)}")
        else:
            print(f"  SIGNAL CANDLE: (not recorded)")

        # Indicator values
        print(f"  INDICATORS:")
        print(f"    Entry DEMA-ATR  = {fmt(dema)}")
        print(f"    Entry HTF (1H)  = {fmt(htf)}")

        # Exit details
        if exit_price:
            print(f"  EXIT:")
            print(f"    Time       = {ts_str(closed_at)}")
            print(f"    Price      = {fmt(exit_price)}")
            print(f"    Reason     = {reason}")
            if exit_bar_close:
                print(f"    Exit Bar   = close={fmt(exit_bar_close)} @ {ts_str(exit_bar_ts)}")

        # P&L
        print(f"  P&L:")
        print(f"    Gross = {pnl_sign}{fmt(gross)}")
        print(f"    Fees  = {fmt(fees)}")
        print(f"    Net   = {net_sign}{fmt(net)}")

        # Duration
        if entry_time and closed_at:
            dur_s = closed_at - entry_time
            hours = int(dur_s // 3600)
            mins = int((dur_s % 3600) // 60)
            print(f"  DURATION: {hours}h {mins}m")

        print(f"  CREATED: {ts_str(created)}")

    con.close()


def show_trade_detail(trade_id: str):
    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    row = con.execute("SELECT * FROM trades_analytics WHERE trade_id = ?", (trade_id,)).fetchone()
    if not row:
        print(f"  Trade {trade_id} not found")
        con.close()
        return

    side = row["side"]
    status = row["status"]
    if status == "CLOSED":
        action = exit_label(side)
    else:
        action = f"OPEN {side}"

    print(f"\n  TRADE DETAIL: {row['trade_id']}")
    print(f"  {'='*70}")
    print(f"  {row['instrument']}  {row['strategy_id']}  {action}")

    groups = {
        "TIMING": ["signal_time", "trigger_time", "order_time", "first_fill_time",
                    "last_exit_fill_time", "created_at", "closed_at"],
        "ENTRY": ["entry_price", "average_entry_price", "entry_quantity",
                  "filled_quantity", "entry_order_id"],
        "EXIT": ["exit_price", "average_exit_price", "exit_quantity",
                 "exit_reason", "exit_order_id"],
        "STOP": ["initial_stop", "initial_risk"],
        "INDICATORS": ["entry_dema", "entry_atr", "entry_dema_atr", "entry_htf_value"],
        "SIGNAL CANDLE": ["signal_bar_open", "signal_bar_high", "signal_bar_low",
                          "signal_bar_close", "signal_bar_volume", "signal_bar_timeframe",
                          "signal_bar_timestamp"],
        "EXIT CANDLE": ["exit_bar_open", "exit_bar_high", "exit_bar_low",
                        "exit_bar_close", "exit_bar_volume", "exit_bar_timeframe",
                        "exit_bar_timestamp"],
        "P&L": ["gross_pnl", "fees", "slippage_cost", "net_pnl", "return_pct", "r_multiple"],
        "POSITION": ["multiplier", "mfe", "mae", "max_favorable_price", "max_adverse_price",
                     "duration_seconds", "duration_minutes", "position_id", "session_id"],
    }

    for group_name, fields in groups.items():
        has_data = any(row[f] is not None for f in fields if f in row.keys())
        if has_data:
            print(f"\n  {group_name}:")
            for f in fields:
                if f in row.keys() and row[f] is not None:
                    val = row[f]
                    if ("_time" in f or "_at" in f) and isinstance(val, (int, float)):
                        val = ts_str(val)
                    print(f"    {f:<30} = {val}")

    legs = con.execute("""
        SELECT * FROM trade_legs WHERE trade_id = ? ORDER BY timestamp
    """, (trade_id,)).fetchall()

    if legs:
        print(f"\n  TRADE LEGS ({len(legs)} fills):")
        for leg in legs:
            label = "ENTRY" if leg["is_entry"] else exit_label(side)
            print(f"    {label:<12} {leg['side']:<6} {leg['quantity']:<4} @ {leg['price']:<10.0f}  "
                  f"{ts_str(leg['timestamp'])}")

    con.close()


def show_orders_fills():
    if not TRADING_DB.exists():
        print("  trading.db not found")
        return

    con = sqlite3.connect(f"file:{TRADING_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "orders" in tables:
        rows = con.execute("""
            SELECT order_id, strategy_id, instrument, side, order_type,
                   quantity, price, status, created_at
            FROM orders ORDER BY created_at DESC LIMIT 10
        """).fetchall()
        if rows:
            print(f"\n  LAST {len(rows)} ORDERS:")
            for r in rows:
                print(f"    {r[0][:16]:<16} {r[1]:<10} {r[2]:<10} {r[3]:<6} {r[4]:<8} "
                      f"qty={r[5]} @ {r[6]:.0f}  {r[7]}  {ts_str(r[8])}")

    if "fills" in tables:
        rows = con.execute("""
            SELECT fill_id, order_id, side, quantity, price, timestamp
            FROM fills ORDER BY timestamp DESC LIMIT 10
        """).fetchall()
        if rows:
            print(f"\n  LAST {len(rows)} FILLS:")
            for r in rows:
                print(f"    {r[0][:16]:<16} {r[1][:16]:<16} {r[2]:<6} {r[3]:<4} @ {r[4]:.0f}  {ts_str(r[5])}")

    con.close()


def main():
    print("=" * 70)
    print("  PRODUCTION DB INSPECTOR")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 70)

    trade_id = None
    if "--trade-id" in sys.argv:
        idx = sys.argv.index("--trade-id")
        if idx + 1 < len(sys.argv):
            trade_id = sys.argv[idx + 1]

    if trade_id:
        show_trade_detail(trade_id)
    else:
        show_all_trades()
        show_orders_fills()


if __name__ == "__main__":
    main()
