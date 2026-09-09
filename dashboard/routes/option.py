"""Option paper trading API routes. Simple endpoints for dashboard."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from option import database as db
from option import trader

router = APIRouter(prefix="/api/options", tags=["options"])


class TradeResponse(BaseModel):
    trade_id: str
    underlying: str
    expiry: str
    strike: float
    entry_time: str
    entry_ce_premium: float
    entry_pe_premium: float
    entry_credit: float
    quantity: int
    margin: float
    sl_amount: float
    status: str
    exit_time: str | None = None
    exit_ce_premium: float | None = None
    exit_pe_premium: float | None = None
    exit_pnl: float | None = None
    exit_reason: str | None = None
    pcr: float | None = None
    selection_reason: str | None = None
    spot_at_entry: float | None = None


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
            "current_ce": premiums["current_ce"] if premiums else t.entry_ce_premium,
            "current_pe": premiums["current_pe"] if premiums else t.entry_pe_premium,
            "pnl": premiums["total_pnl"] if premiums else 0,
            "entry_time": t.entry_time,
            "pcr": t.pcr,
            "selection_reason": t.selection_reason,
        })

    return OverviewResponse(
        status="RUNNING" if trader.is_expiry_day() else "CLOSED",
        open_count=len(open_trades),
        today_count=len(today_trades),
        today_pnl=db.get_today_pnl(),
        total_pnl=db.get_total_pnl(),
        win_rate=db.get_win_rate(),
        is_expiry_day=trader.is_expiry_day(),
        open_trades=open_list,
    )


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
        result.append(d)
    return {"trades": result}


@router.get("/pnl")
def get_pnl():
    return {
        "today": db.get_today_pnl(),
        "total": db.get_total_pnl(),
        "win_rate": db.get_win_rate(),
    }


@router.get("/status")
def get_status():
    return trader.get_status()


@router.post("/check")
def manual_check():
    """Manual morning check."""
    trades = trader.run_morning_check()
    return {"trades_opened": len(trades), "trades": trades}


@router.post("/recheck")
def manual_recheck():
    """Manual 10 AM recheck."""
    trades = trader.run_recheck()
    return {"trades_opened": len(trades), "trades": trades}


@router.post("/exit")
def manual_exit():
    """Manual EOD exit."""
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
