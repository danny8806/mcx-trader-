"""Option paper trading scheduler. Runs entry/exit checks at scheduled times."""
from __future__ import annotations

import threading
import time
from datetime import datetime, date

from option import trader


_scheduler_thread = None
_running = False

_SCHEDULED_CHECKS = [
    ("09:30", "morning_check", "_scheduled_check"),
    ("10:00", "recheck", "_scheduled_recheck"),
    ("15:15", "eod_exit", "_scheduled_exit"),
]

_today_checks_done = set()


def _scheduled_check():
    """Run morning check."""
    today = date.today().isoformat()
    if today in _today_checks_done:
        return
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running morning check")
    trades = trader.run_morning_check()
    if trades:
        for t in trades:
            print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
    else:
        print("[Option] No trades opened")
    _today_checks_done.add(today)


def _scheduled_recheck():
    """Run 10 AM recheck."""
    today = date.today().isoformat()
    if today in _today_checks_done:
        return
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running recheck")
    trades = trader.run_recheck()
    if trades:
        for t in trades:
            print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
    else:
        print("[Option] No trades opened")
    _today_checks_done.add(today)


def _scheduled_exit():
    """Run EOD exit."""
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running EOD exit")
    results = trader.run_eod_exit()
    if results:
        for r in results:
            print(f"[Option] CLOSED: {r['underlying']} {r['strike']} — P&L: Rs {r['pnl']:,.0f} ({r['reason']})")
    else:
        print("[Option] No trades to exit")


def _catch_up():
    """If started mid-day, run any missed checks."""
    now = datetime.now()
    today = date.today().isoformat()
    h, m = now.hour, now.minute

    # If it's past 09:30 but no check done today, run it
    if (h > 9 or (h == 9 and m >= 30)) and today not in _today_checks_done:
        print(f"[Option] {now.strftime('%H:%M:%S')} — Catch-up: running missed morning check")
        _scheduled_check()

    # If it's past 10:00 but no recheck done today, run it
    if (h > 10 or (h == 10 and m >= 0)) and today not in _today_checks_done:
        print(f"[Option] {now.strftime('%H:%M:%S')} — Catch-up: running missed recheck")
        _scheduled_recheck()


def _run_loop():
    """Simple scheduler loop using time checks."""
    import schedule

    schedule.every().day.at("09:30").do(_scheduled_check)
    schedule.every().day.at("10:00").do(_scheduled_recheck)
    schedule.every().day.at("15:15").do(_scheduled_exit)

    print("[Option] Scheduler started — 09:30 check, 10:00 recheck, 15:15 exit")

    # Run catch-up on start
    _catch_up()

    while _running:
        schedule.run_pending()
        time.sleep(30)


def start_scheduler():
    """Start the option scheduler in a background thread."""
    global _scheduler_thread, _running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _running = True
    _scheduler_thread = threading.Thread(target=_run_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    """Stop the option scheduler."""
    global _running
    _running = False
