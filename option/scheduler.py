"""Option paper trading scheduler. Runs entry/exit checks at scheduled times."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from option import trader


_scheduler_thread = None
_running = False


def _scheduled_check():
    """Run morning check."""
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running morning check")
    trades = trader.run_morning_check()
    if trades:
        for t in trades:
            print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
    else:
        print("[Option] No trades opened")


def _scheduled_recheck():
    """Run 10 AM recheck."""
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running recheck")
    trades = trader.run_recheck()
    if trades:
        for t in trades:
            print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
    else:
        print("[Option] No trades opened")


def _scheduled_exit():
    """Run EOD exit."""
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running EOD exit")
    results = trader.run_eod_exit()
    if results:
        for r in results:
            print(f"[Option] CLOSED: {r['underlying']} {r['strike']} — P&L: Rs {r['pnl']:,.0f} ({r['reason']})")
    else:
        print("[Option] No trades to exit")


def _run_loop():
    """Simple scheduler loop using time checks."""
    import schedule

    schedule.every().day.at("09:30").do(_scheduled_check)
    schedule.every().day.at("10:00").do(_scheduled_recheck)
    schedule.every().day.at("15:15").do(_scheduled_exit)

    print("[Option] Scheduler started — 09:30 check, 10:00 recheck, 15:15 exit")

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
