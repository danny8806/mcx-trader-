"""Option paper trading scheduler. Runs entry/exit checks at scheduled times."""
from __future__ import annotations

import threading
import time
from datetime import datetime, date

from option.config import ENTRY_TIME, RECHECK_TIME, EOD_EXIT_TIME
from option import trader


_scheduler_thread = None
_running = False

_today_check_done = set()
_today_recheck_done = set()
MAX_RETRIES = 2
RETRY_DELAY = 30


def _safe_run(fn, name: str):
    """Run a function safely, catching all exceptions so scheduler never dies."""
    try:
        return fn()
    except Exception as e:
        print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — ERROR in {name}: {e}")
        import traceback
        traceback.print_exc()
        return None


def _scheduled_check():
    """Run morning check with retry."""
    today = date.today().isoformat()
    if today in _today_check_done:
        return

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Morning check (attempt {attempt}/{MAX_RETRIES})")
        trades = _safe_run(trader.run_morning_check, "morning_check")
        if trades is not None:
            if trades:
                for t in trades:
                    print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
            else:
                print("[Option] No trades opened")
            _today_check_done.add(today)
            return

        if attempt < MAX_RETRIES:
            print(f"[Option] Check failed, retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    print("[Option] Morning check failed after all retries")
    _today_check_done.add(today)


def _scheduled_recheck():
    """Run 10 AM recheck with retry. Independent of morning check status."""
    today = date.today().isoformat()
    if today in _today_recheck_done:
        return

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Recheck (attempt {attempt}/{MAX_RETRIES})")
        trades = _safe_run(trader.run_recheck, "recheck")
        if trades is not None:
            if trades:
                for t in trades:
                    print(f"[Option] OPENED: {t['underlying']} {t['strike']} — Margin: Rs {t['margin']:,.0f}")
            else:
                print("[Option] No trades opened")
            _today_recheck_done.add(today)
            return

        if attempt < MAX_RETRIES:
            print(f"[Option] Recheck failed, retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    print("[Option] Recheck failed after all retries")
    _today_recheck_done.add(today)


def _scheduled_exit():
    """Run EOD exit with retry."""
    print(f"[Option] {datetime.now().strftime('%H:%M:%S')} — Running EOD exit")
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[Option] EOD exit (attempt {attempt}/{MAX_RETRIES})")
        results = _safe_run(trader.run_eod_exit, "eod_exit")
        if results is not None:
            if results:
                for r in results:
                    print(f"[Option] CLOSED: {r['underlying']} {r['strike']} — P&L: Rs {r['pnl']:,.0f} ({r['reason']})")
            else:
                print("[Option] No trades to exit")
            return

        if attempt < MAX_RETRIES:
            print(f"[Option] EOD exit failed, retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    print("[Option] EOD exit failed after all retries")


def _parse_time(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' string to (hour, minute)."""
    parts = t.split(":")
    return int(parts[0]), int(parts[1])


def _catch_up():
    """If started mid-day, run any missed checks."""
    now = datetime.now()
    today = date.today().isoformat()
    h, m = now.hour, now.minute

    entry_h, entry_m = _parse_time(ENTRY_TIME)
    recheck_h, recheck_m = _parse_time(RECHECK_TIME)

    if (h > entry_h or (h == entry_h and m >= entry_m)) and today not in _today_check_done:
        print(f"[Option] {now.strftime('%H:%M:%S')} — Catch-up: running missed morning check")
        _scheduled_check()

    if (h > recheck_h or (h == recheck_h and m >= recheck_m)) and today not in _today_recheck_done:
        print(f"[Option] {now.strftime('%H:%M:%S')} — Catch-up: running missed recheck")
        _scheduled_recheck()


def _run_loop():
    """Simple scheduler loop using time checks."""
    import schedule

    schedule.every().day.at(ENTRY_TIME).do(_scheduled_check)
    schedule.every().day.at(RECHECK_TIME).do(_scheduled_recheck)
    schedule.every().day.at(EOD_EXIT_TIME).do(_scheduled_exit)

    print(f"[Option] Scheduler started — {ENTRY_TIME} check, {RECHECK_TIME} recheck, {EOD_EXIT_TIME} exit")

    _safe_run(_catch_up, "catch_up")

    while _running:
        _safe_run(schedule.run_pending, "run_pending")
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
