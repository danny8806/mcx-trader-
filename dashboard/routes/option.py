"""Option paper trading API routes — market data, trades, dashboard."""
from __future__ import annotations

import os
from datetime import datetime, date
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from option import database as db
from option import trader
from option.config import (
    SL_PERCENT, MAX_TRADES_PER_DAY, INSTRUMENTS, EXPIRY_DAYS,
    ENTRY_TIME, RECHECK_TIME, EOD_EXIT_TIME,
)

router = APIRouter(prefix="/api/options", tags=["options"])

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "option_dashboard.html")


@router.get("/dashboard", response_class=HTMLResponse)
def option_dashboard():
    try:
        with open(_HTML_PATH, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Dashboard file not found</h1>", status_code=500)


# ── Overview ──────────────────────────────────────────────────────────

class OverviewResponse(BaseModel):
    status: str
    open_count: int
    today_count: int
    today_pnl: float
    total_pnl: float
    win_rate: float
    is_expiry_day: bool
    open_trades: list[dict]


@router.get("/overview", response_model=OverviewResponse)
def get_overview():
    open_trades = db.get_open_trades()
    today_trades = db.get_today_trades()

    open_list = []
    for t in open_trades:
        premiums = trader.get_current_premiums(t)
        open_list.append({
            "trade_id": t.trade_id,
            "underlying": t.underlying,
            "strike": t.strike,
            "expiry": t.expiry,
            "entry_ce": t.entry_ce_premium,
            "entry_pe": t.entry_pe_premium,
            "credit": t.entry_credit,
            "margin": t.margin,
            "sl_amount": t.sl_amount,
            "current_ce": premiums["current_ce"] if premiums else t.entry_ce_premium,
            "current_pe": premiums["current_pe"] if premiums else t.entry_pe_premium,
            "pnl": premiums["total_pnl"] if premiums else 0,
            "entry_time": t.entry_time,
            "pcr": t.pcr,
            "selection_reason": t.selection_reason,
            "spot_at_entry": t.spot_at_entry,
            "quantity": t.quantity,
            "lot_size": t.lot_size,
        })

    return OverviewResponse(
        status="RUNNING",
        open_count=len(open_trades),
        today_count=len(today_trades),
        today_pnl=db.get_today_pnl(),
        total_pnl=db.get_total_pnl(),
        win_rate=db.get_win_rate(),
        is_expiry_day=trader.is_expiry_day(),
        open_trades=open_list,
    )


# ── Market Status ─────────────────────────────────────────────────────

@router.get("/market")
def get_market():
    now = datetime.now()
    today = date.today()
    weekday = today.weekday()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    is_expiry = weekday in EXPIRY_DAYS
    h, m = now.hour, now.minute
    market_open = h >= 9 and (h > 9 or m >= 15)
    market_closed = h >= 15 and (h > 15 or m >= 30)
    in_market_hours = market_open and not market_closed

    session_status = "CLOSED"
    if in_market_hours:
        if h < 10:
            session_status = "OPEN"
        elif h < 15:
            session_status = "TRADING"
        else:
            session_status = "CLOSING"

    return {
        "date": today.isoformat(),
        "day": day_names[weekday],
        "time": now.strftime("%H:%M:%S"),
        "is_expiry_day": is_expiry,
        "session_status": session_status,
        "entry_time": ENTRY_TIME,
        "recheck_time": RECHECK_TIME,
        "eod_exit_time": EOD_EXIT_TIME,
    }


# ── Config ────────────────────────────────────────────────────────────

@router.get("/config")
def get_config():
    return {
        "sl_percent": SL_PERCENT,
        "sl_display": f"{SL_PERCENT * 100:.0f}%",
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "entry_time": ENTRY_TIME,
        "recheck_time": RECHECK_TIME,
        "eod_exit_time": EOD_EXIT_TIME,
        "trading_days": "Every Day",
        "instruments": {
            k: {
                "lot_size": v["lot_size"],
                "exchange": v["exchange"],
                "oi_threshold": v.get("oi_threshold", 5_000_000),
                "oi_threshold_lakhs": f"{v.get('oi_threshold', 5_000_000) / 100000:.0f}L",
            } for k, v in INSTRUMENTS.items()
        },
    }


# ── Trades ────────────────────────────────────────────────────────────

@router.get("/trades")
def get_trades(limit: int = 100):
    trades = db.get_trade_history(limit)
    return {"trades": [_trade_to_dict(t) for t in trades]}


@router.get("/trades/open")
def get_open_trades():
    trades = db.get_open_trades()
    result = []
    for t in trades:
        d = _trade_to_dict(t)
        premiums = trader.get_current_premiums(t)
        if premiums:
            d["current_ce"] = premiums["current_ce"]
            d["current_pe"] = premiums["current_pe"]
            d["live_pnl"] = premiums["total_pnl"]
            d["ce_pnl"] = premiums["ce_pnl"]
            d["pe_pnl"] = premiums["pe_pnl"]
        result.append(d)
    return {"trades": result}


@router.get("/pnl")
def get_pnl():
    today = db.get_today_pnl()
    total = db.get_total_pnl()
    win_rate = db.get_win_rate()

    trades = db.get_trade_history(1000)
    wins = sum(1 for t in trades if t.status == "CLOSED" and (t.exit_pnl or 0) > 0)
    losses = sum(1 for t in trades if t.status == "CLOSED" and (t.exit_pnl or 0) <= 0)
    total_closed = wins + losses
    avg_win = 0
    avg_loss = 0
    pnl_list = [t.exit_pnl for t in trades if t.status == "CLOSED" and t.exit_pnl]
    if pnl_list:
        wins_list = [p for p in pnl_list if p > 0]
        losses_list = [p for p in pnl_list if p <= 0]
        avg_win = sum(wins_list) / len(wins_list) if wins_list else 0
        avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0

    return {
        "today": today,
        "total": total,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "total_closed": total_closed,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


# ── Status ────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    status = trader.get_status()
    try:
        from option.scheduler import _running, _scheduler_thread
        status["scheduler_running"] = _running
        status["scheduler_alive"] = _scheduler_thread.is_alive() if _scheduler_thread else False
    except Exception:
        status["scheduler_running"] = False
        status["scheduler_alive"] = False
    return status


# ── Actions ───────────────────────────────────────────────────────────

@router.post("/check")
def manual_check():
    trades = trader.run_morning_check()
    return {"trades_opened": len(trades), "trades": trades}


@router.post("/recheck")
def manual_recheck():
    trades = trader.run_recheck()
    return {"trades_opened": len(trades), "trades": trades}


@router.post("/exit")
def manual_exit():
    results = trader.run_eod_exit()
    return {"trades_exited": len(results), "results": results}


def _trade_to_dict(t: db.OptionTrade) -> dict:
    return {
        "trade_id": t.trade_id,
        "underlying": t.underlying,
        "expiry": t.expiry,
        "strike": t.strike,
        "entry_time": t.entry_time,
        "entry_ce_premium": t.entry_ce_premium,
        "entry_pe_premium": t.entry_pe_premium,
        "entry_credit": t.entry_credit,
        "quantity": t.quantity,
        "lot_size": t.lot_size,
        "margin": t.margin,
        "sl_amount": t.sl_amount,
        "status": t.status,
        "exit_time": t.exit_time,
        "exit_ce_premium": t.exit_ce_premium,
        "exit_pe_premium": t.exit_pe_premium,
        "exit_pnl": t.exit_pnl,
        "exit_reason": t.exit_reason,
        "pcr": t.pcr,
        "selection_reason": t.selection_reason,
        "spot_at_entry": t.spot_at_entry,
    }
