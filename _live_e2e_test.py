"""Live End-to-End Test — Real Telegram, Real DB, Real Trades.

Runs ON THE SERVER against actual production data.
Verifies every layer of the new signal candle + Telegram changes.

Usage:
    cd /app
    python _live_e2e_test.py          # run all tests
    python _live_e2e_test.py --fast   # skip Telegram send (DB-only)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

DB_DIR = Path("/app/data/db")
TRADING_DB = DB_DIR / "trading.db"
ANALYTICS_DB = DB_DIR / "analytics.db"
STATE_FILE = DB_DIR / "system_state.json"

CHECKS = []
TOTAL = 0
PASSED = 0
FAILED = 0


def ok(name: str, cond: bool, detail: str = ""):
    global TOTAL, PASSED, FAILED
    TOTAL += 1
    if cond:
        PASSED += 1
        status = "PASS"
    else:
        FAILED += 1
        status = "FAIL"
    CHECKS.append((name, cond, detail))
    print(f"  {status}  {name}: {detail}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ═══════════════════════════════════════════════════════════════
# 1. FILE CHECK — DBs exist and are readable
# ═══════════════════════════════════════════════════════════════
def test_db_files_exist():
    section("1. DB FILES EXIST")
    for db, label in [(TRADING_DB, "trading.db"), (ANALYTICS_DB, "analytics.db"), (STATE_FILE, "system_state.json")]:
        ok(f"{label} exists", db.exists(), str(db))
        if db.exists():
            size = db.stat().st_size
            ok(f"{label} not empty", size > 0, f"{size:,} bytes")


# ═══════════════════════════════════════════════════════════════
# 2. TRADES TABLE — has data and correct schema
# ═══════════════════════════════════════════════════════════════
def test_trades_table():
    section("2. TRADES TABLE")
    if not ANALYTICS_DB.exists():
        ok("analytics DB readable", False, "file missing")
        return

    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Check table exists
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    ok("trades_analytics table exists", "trades_analytics" in tables, f"tables: {tables}")

    # Check schema has entry_dema and entry_htf_value
    cols = [r[1] for r in con.execute("PRAGMA table_info(trades_analytics)").fetchall()]
    ok("has entry_dema column", "entry_dema" in cols, f"columns: {cols}")
    ok("has entry_htf_value column", "entry_htf_value" in cols, f"columns: {cols}")
    ok("has gross_pnl column", "gross_pnl" in cols, f"columns: {cols}")
    ok("has fees column", "fees" in cols, f"columns: {cols}")

    # Count trades
    row = con.execute("SELECT COUNT(*) FROM trades_analytics").fetchone()
    total_trades = row[0]
    ok("has trades", total_trades > 0, f"{total_trades} trades")

    # Count open trades
    row = con.execute("SELECT COUNT(*) FROM trades_analytics WHERE status='OPEN'").fetchone()
    open_trades = row[0]
    print(f"    Open trades: {open_trades}")

    # Count closed trades
    row = con.execute("SELECT COUNT(*) FROM trades_analytics WHERE status='CLOSED'").fetchone()
    closed_trades = row[0]
    print(f"    Closed trades: {closed_trades}")

    con.close()


# ═══════════════════════════════════════════════════════════════
# 3. SIGNAL CANDLE DATA — entry_dema and entry_htf populated
# ═══════════════════════════════════════════════════════════════
def test_signal_candle_data():
    section("3. SIGNAL CANDLE DATA IN DB")
    if not ANALYTICS_DB.exists():
        ok("analytics DB readable", False, "file missing")
        return

    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Check entry_dema populated
    row = con.execute("""
        SELECT COUNT(*) FROM trades_analytics
        WHERE entry_dema IS NOT NULL AND entry_dema != 0
    """).fetchone()
    dema_count = row[0]

    row = con.execute("SELECT COUNT(*) FROM trades_analytics").fetchone()
    total = row[0]

    ok("entry_dema populated", dema_count > 0,
       f"{dema_count}/{total} trades have entry_dema")

    # Check entry_htf_value populated
    row = con.execute("""
        SELECT COUNT(*) FROM trades_analytics
        WHERE entry_htf_value IS NOT NULL AND entry_htf_value != 0
    """).fetchone()
    htf_count = row[0]
    ok("entry_htf_value populated", htf_count > 0,
       f"{htf_count}/{total} trades have entry_htf_value")

    # Show last 5 trades with signal candle details
    rows = con.execute("""
        SELECT trade_id, strategy_id, instrument, side, status,
               entry_price, exit_price, entry_dema, entry_htf_value,
               gross_pnl, fees, net_pnl, exit_reason
        FROM trades_analytics
        ORDER BY created_at DESC
        LIMIT 5
    """).fetchall()

    if rows:
        print(f"\n    Last {len(rows)} trades:")
        print(f"    {'ID':<12} {'Strat':<10} {'Instr':<10} {'Side':<6} {'Status':<8} {'Entry':<10} {'DEMA':<10} {'HTF':<10} {'Gross':<10} {'Fees':<8} {'Net':<10} {'Reason'}")
        print(f"    {'-'*140}")
        for r in rows:
            print(f"    {str(r[0])[:12]:<12} {str(r[1]):<10} {str(r[2]):<10} {str(r[3]):<6} {str(r[4]):<8} "
                  f"{r[5] or 0:<10.0f} {r[7] or 0:<10.0f} {r[8] or 0:<10.0f} "
                  f"{r[9] or 0:<10.0f} {r[10] or 0:<8.0f} {r[11] or 0:<10.0f} {str(r[12] or '')}")
    else:
        print("    No trades found")

    con.close()


# ═══════════════════════════════════════════════════════════════
# 4. TRADE LEGS — individual fills recorded
# ═══════════════════════════════════════════════════════════════
def test_trade_legs():
    section("4. TRADE LEGS (FILLS)")
    if not ANALYTICS_DB.exists():
        ok("analytics DB readable", False, "file missing")
        return

    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Check trade_legs table
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    ok("trade_legs table exists", "trade_legs" in tables, f"tables: {tables}")

    if "trade_legs" in tables:
        row = con.execute("SELECT COUNT(*) FROM trade_legs").fetchone()
        total_legs = row[0]
        ok("has trade legs", total_legs > 0, f"{total_legs} legs")

        # Show last 5 legs
        rows = con.execute("""
            SELECT l.leg_id, l.trade_id, l.side, l.quantity, l.price,
                   l.timestamp, l.is_entry, t.instrument
            FROM trade_legs l
            LEFT JOIN trades_analytics t ON l.trade_id = t.trade_id
            ORDER BY l.timestamp DESC
            LIMIT 5
        """).fetchall()
        if rows:
            print(f"\n    Last {len(rows)} fills:")
            for r in rows:
                ts = datetime.fromtimestamp(r[5], tz=IST).strftime("%Y-%m-%d %H:%M") if r[5] else "?"
                entry = "ENTRY" if r[6] else "EXIT"
                print(f"      {r[1][:12]:<12} {r[7] or '?':<10} {r[2]:<6} {r[3]:<4} @ {r[4]:<10.0f}  {ts}  {entry}")

    con.close()


# ═══════════════════════════════════════════════════════════════
# 5. TRADING.DB — orders and fills
# ═══════════════════════════════════════════════════════════════
def test_trading_db():
    section("5. TRADING.DB")
    if not TRADING_DB.exists():
        ok("trading DB readable", False, "file missing")
        return

    con = sqlite3.connect(f"file:{TRADING_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"    Tables: {tables}")

    for tbl in ["orders", "fills", "positions"]:
        if tbl in tables:
            row = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            print(f"    {tbl}: {row[0]} records")
        else:
            print(f"    {tbl}: MISSING")

    # Show last 3 orders
    if "orders" in tables:
        rows = con.execute("""
            SELECT order_id, strategy_id, instrument, side, order_type,
                   quantity, price, status, created_at
            FROM orders
            ORDER BY created_at DESC
            LIMIT 3
        """).fetchall()
        if rows:
            print(f"\n    Last 3 orders:")
            for r in rows:
                ts = datetime.fromtimestamp(r[8], tz=IST).strftime("%Y-%m-%d %H:%M") if r[8] else "?"
                print(f"      {r[0][:16]:<16} {r[1]:<10} {r[2]:<10} {r[3]:<6} {r[4]:<8} qty={r[5]} @ {r[6]:.0f}  {r[7]}  {ts}")

    con.close()


# ═══════════════════════════════════════════════════════════════
# 6. SYSTEM STATE — engine state
# ═══════════════════════════════════════════════════════════════
def test_system_state():
    section("6. SYSTEM STATE")
    if not STATE_FILE.exists():
        ok("system_state.json exists", False, str(STATE_FILE))
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    ok("has market_status", "market_status" in state, f"keys: {list(state.keys())}")
    ok("has engine_status", "engine_status" in state, f"keys: {list(state.keys())}")
    ok("has strategy_states", "strategy_states" in state, f"keys: {list(state.keys())}")

    ms = state.get("market_status", {})
    es = state.get("engine_status", {})
    print(f"    market_status: {ms.get('market_state', '?')}")
    print(f"    engine_status: {es.get('status', '?')}")
    print(f"    last_update: {es.get('last_update', '?')}")

    # Show strategy states
    strats = state.get("strategy_states", {})
    if strats:
        print(f"\n    Strategy states:")
        for sid, ss in strats.items():
            print(f"      {sid}: state={ss.get('state','?')}, "
                  f"position={ss.get('position_side','?')}, "
                  f"stop={ss.get('stop_price','?')}")


# ═══════════════════════════════════════════════════════════════
# 7. TELEGRAM — send test message
# ═══════════════════════════════════════════════════════════════
def test_telegram():
    section("7. TELEGRAM NOTIFICATION TEST")

    # Read bot token from config
    config_path = Path("/app/config/settings.json")
    if not config_path.exists():
        ok("config exists", False, str(config_path))
        return

    with open(config_path) as f:
        cfg = json.load(f)

    tg = cfg.get("telegram", {})
    bot_token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")
    enabled = tg.get("enabled", False)

    ok("telegram enabled", enabled, f"enabled={enabled}")
    ok("bot_token configured", bool(bot_token), f"token={'SET' if bot_token else 'EMPTY'}")
    ok("chat_id configured", bool(chat_id), f"chat_id={'SET' if chat_id else 'EMPTY'}")

    if not bot_token or not chat_id:
        print("    Cannot send test message — missing credentials")
        return

    # Send test message
    test_msg = (
        f"🧪 <b>LIVE E2E TEST</b>\n\n"
        f"<b>Time:</b> {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        f"<b>Source:</b> _live_e2e_test.py\n\n"
        f"This is a test message to verify Telegram notifications work.\n"
        f"Signal candle details and trade notifications should appear on actual trades."
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": test_msg,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            ok("test message sent", data.get("ok", False), f"message_id={data.get('result',{}).get('message_id','?')}")
            print(f"    Check your Telegram — you should see the test message")
    except Exception as e:
        ok("test message sent", False, f"error: {e}")

    # Get recent updates to verify bot is active
    try:
        updates_url = f"https://api.telegram.org/bot{bot_token}/getUpdates?limit=3"
        with urllib.request.urlopen(updates_url, timeout=10) as resp:
            data = json.loads(resp.read())
            results = data.get("result", [])
            ok("bot receiving updates", len(results) > 0, f"{len(results)} recent updates")
            if results:
                for u in results[-3:]:
                    msg = u.get("message", {})
                    text = msg.get("text", "")[:50]
                    ts = msg.get("date", 0)
                    t = datetime.fromtimestamp(ts, tz=IST).strftime("%H:%M") if ts else "?"
                    print(f"    {t}: {text}")
    except Exception as e:
        ok("bot receiving updates", False, f"error: {e}")


# ═══════════════════════════════════════════════════════════════
# 8. PNL CHECK — verify financial calculations
# ═══════════════════════════════════════════════════════════════
def test_pnl_calculations():
    section("8. PNL CALCULATIONS")
    if not ANALYTICS_DB.exists():
        ok("analytics DB readable", False, "file missing")
        return

    con = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Check gross_pnl + fees = net_pnl for closed trades
    rows = con.execute("""
        SELECT trade_id, gross_pnl, fees, net_pnl,
               entry_price, exit_price, entry_quantity, multiplier, side
        FROM trades_analytics
        WHERE status = 'CLOSED' AND net_pnl IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT 10
    """).fetchall()

    if not rows:
        ok("has closed trades with P&L", False, "no closed trades")
        con.close()
        return

    ok("has closed trades with P&L", True, f"{len(rows)} trades checked")
    math_correct = 0
    math_wrong = 0

    print(f"\n    P&L verification (last {len(rows)} closed trades):")
    for r in rows:
        gross = r[1] or 0
        fees = r[2] or 0
        net = r[3] or 0
        expected_net = gross - abs(fees)
        match = abs(net - expected_net) < 1.0  # 1 point tolerance
        if match:
            math_correct += 1
        else:
            math_wrong += 1
            print(f"    MISMATCH: {r[0][:12]} gross={gross} fees={fees} net={net} expected={expected_net}")

    ok("gross + fees = net (all trades)", math_wrong == 0,
       f"{math_correct} correct, {math_wrong} wrong")

    con.close()


# ═══════════════════════════════════════════════════════════════
# 9. LIVE ENGINE STATUS — via dashboard endpoint
# ═══════════════════════════════════════════════════════════════
def test_engine_status():
    section("9. ENGINE STATUS (via HTTP)")
    try:
        import httpx
        resp = httpx.get("http://localhost:8000/api/health", timeout=5)
        data = resp.json()
        ok("dashboard responds", resp.status_code == 200, f"status={resp.status_code}")
        ok("market_status live", data.get("market_status") == "live_trading",
           f"market_status={data.get('market_status')}")
        ok("engine_status trading", data.get("engine_status") == "trading",
           f"engine_status={data.get('engine_status')}")
    except ImportError:
        print("    httpx not available, trying urllib...")
        try:
            with urllib.request.urlopen("http://localhost:8000/api/health", timeout=5) as resp:
                data = json.loads(resp.read())
                ok("dashboard responds", True, f"status={resp.status}")
                ok("market_status live", data.get("market_status") == "live_trading",
                   f"market_status={data.get('market_status')}")
        except Exception as e:
            ok("dashboard responds", False, f"error: {e}")
    except Exception as e:
        ok("dashboard responds", False, f"error: {e}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  LIVE END-TO-END TEST")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"  DB dir: {DB_DIR}")
    print("=" * 60)

    test_db_files_exist()
    test_trades_table()
    test_signal_candle_data()
    test_trade_legs()
    test_trading_db()
    test_system_state()
    test_telegram()
    test_pnl_calculations()
    test_engine_status()

    print(f"\n{'='*60}")
    print(f"  RESULTS: {PASSED}/{TOTAL} passed, {FAILED} failed")
    print(f"{'='*60}")

    if FAILED > 0:
        print("\n  FAILED CHECKS:")
        for name, cond, detail in CHECKS:
            if not cond:
                print(f"    FAIL  {name}: {detail}")

    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
