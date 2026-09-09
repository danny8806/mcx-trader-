"""Simple option paper trader. Strategy → Signal → Trade → Monitor → Exit."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, date

from option.dhan_client import get_expiry_list, get_option_chain, get_margin
from option.strategy import parse_option_chain, StrategySignal
from option.database import (
    OptionTrade, save_trade, close_trade, get_open_trades,
    get_today_trades, count_today_trades, init_db,
)

SL_PERCENT = 0.01
OI_THRESHOLD = 5_000_000
MAX_TRADES_PER_DAY = 2

INSTRUMENTS = {
    "NIFTY": {"scrip": 13, "lot_size": 65, "exchange": "NSE_FNO"},
    "SENSEX": {"scrip": 51, "lot_size": 20, "exchange": "BSE_FNO"},
}

init_db()


def is_expiry_day(d: date | None = None) -> bool:
    d = d or date.today()
    return d.weekday() in (2, 3)


def get_signal(underlying: str) -> StrategySignal | None:
    """Fetch option chain and generate signal if OI threshold met."""
    cfg = INSTRUMENTS.get(underlying)
    if not cfg:
        return None

    expiries = get_expiry_list(cfg["scrip"])
    if not expiries:
        return None

    expiry = expiries[0]
    chain = get_option_chain(cfg["scrip"], expiry)
    if not chain:
        return None

    signal = parse_option_chain(chain, cfg["lot_size"], cfg["exchange"], underlying)
    if not signal:
        return None

    signal.expiry = expiry

    # Calculate margin
    ce_margin = get_margin(signal.ce_sec, cfg["lot_size"], cfg["exchange"])
    pe_margin = get_margin(signal.pe_sec, cfg["lot_size"], cfg["exchange"])
    signal.margin = ce_margin + pe_margin

    return signal


def open_trade(signal: StrategySignal) -> OptionTrade:
    """Create a paper trade from a signal."""
    trade_id = f"OPT-{uuid.uuid4().hex[:8].upper()}"
    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Entry credit = premium received from selling
    entry_credit = (signal.ce_ltp + signal.pe_ltp) * signal.lot_size
    sl_amount = signal.margin * SL_PERCENT

    trade = OptionTrade(
        trade_id=trade_id,
        underlying=signal.underlying,
        expiry=signal.expiry,
        strike=signal.selected_strike,
        ce_security_id=signal.ce_sec,
        pe_security_id=signal.pe_sec,
        entry_time=entry_time,
        entry_ce_premium=signal.ce_ltp,
        entry_pe_premium=signal.pe_ltp,
        entry_credit=entry_credit,
        quantity=signal.lot_size,
        lot_size=signal.lot_size,
        margin=signal.margin,
        sl_amount=sl_amount,
        status="OPEN",
        pcr=signal.pcr,
        selection_reason=signal.selection_reason,
        spot_at_entry=signal.spot,
    )

    save_trade(trade)
    return trade


def check_and_exit() -> list[dict]:
    """Check open trades for SL hit or EOD exit. Returns list of exit results."""
    open_trades = get_open_trades()
    results = []
    now = datetime.now()
    is_eod = now.hour >= 15 and now.minute >= 15

    for trade in open_trades:
        # Fetch current premiums
        try:
            expiries = get_expiry_list(INSTRUMENTS[trade.underlying]["scrip"])
            if not expiries:
                continue
            chain = get_option_chain(INSTRUMENTS[trade.underlying]["scrip"], trade.expiry)
            if not chain:
                continue

            oc = chain.get("oc", {})
            strike_data = oc.get(str(int(trade.strike)), {})
            ce_data = strike_data.get("ce", {})
            pe_data = strike_data.get("pe", {})

            current_ce = ce_data.get("last_price", trade.entry_ce_premium)
            current_pe = pe_data.get("last_price", trade.entry_pe_premium)
        except Exception:
            continue

        # P&L: (entry_premium - current_premium) × quantity
        # For selling: profit when premium decreases
        ce_pnl = (trade.entry_ce_premium - current_ce) * trade.quantity
        pe_pnl = (trade.entry_pe_premium - current_pe) * trade.quantity
        total_pnl = ce_pnl + pe_pnl

        exit_reason = None

        # Check SL: if loss > sl_amount
        if total_pnl < 0 and abs(total_pnl) >= trade.sl_amount:
            exit_reason = "SL"

        # Check EOD
        elif is_eod:
            exit_reason = "EOD"

        if exit_reason:
            exit_time = now.strftime("%Y-%m-%d %H:%M:%S")
            close_trade(trade.trade_id, exit_time, current_ce, current_pe, total_pnl, exit_reason)
            results.append({
                "trade_id": trade.trade_id,
                "underlying": trade.underlying,
                "strike": trade.strike,
                "pnl": total_pnl,
                "reason": exit_reason,
                "exit_ce": current_ce,
                "exit_pe": current_pe,
            })

    return results


def get_current_premiums(trade: OptionTrade) -> dict | None:
    """Get current premiums for an open trade (for live monitoring)."""
    try:
        cfg = INSTRUMENTS.get(trade.underlying)
        if not cfg:
            return None
        chain = get_option_chain(cfg["scrip"], trade.expiry)
        if not chain:
            return None

        oc = chain.get("oc", {})
        strike_data = oc.get(str(int(trade.strike)), {})
        ce = strike_data.get("ce", {})
        pe = strike_data.get("pe", {})

        current_ce = ce.get("last_price", trade.entry_ce_premium)
        current_pe = pe.get("last_price", trade.entry_pe_premium)

        ce_pnl = (trade.entry_ce_premium - current_ce) * trade.quantity
        pe_pnl = (trade.entry_pe_premium - current_pe) * trade.quantity

        return {
            "current_ce": current_ce,
            "current_pe": current_pe,
            "ce_pnl": ce_pnl,
            "pe_pnl": pe_pnl,
            "total_pnl": ce_pnl + pe_pnl,
        }
    except Exception:
        return None


def run_morning_check() -> list[dict]:
    """9:30 AM check. Returns list of trades opened."""
    if not is_expiry_day():
        return []

    today_count = count_today_trades()
    if today_count >= MAX_TRADES_PER_DAY:
        return []

    trades = []
    for underlying in ["NIFTY", "SENSEX"]:
        # Check if already traded today
        today = get_today_trades()
        if any(t.underlying == underlying for t in today):
            continue

        signal = get_signal(underlying)
        if not signal:
            continue

        oi_diff = abs(signal.ce_oi - signal.pe_oi)
        if oi_diff >= OI_THRESHOLD:
            trade = open_trade(signal)
            trades.append({
                "trade_id": trade.trade_id,
                "underlying": trade.underlying,
                "strike": trade.strike,
                "margin": trade.margin,
                "credit": trade.entry_credit,
            })

    return trades


def run_recheck() -> list[dict]:
    """10:00 AM recheck. Same as morning check."""
    return run_morning_check()


def run_eod_exit() -> list[dict]:
    """3:15 PM EOD exit."""
    return check_and_exit()


def get_status() -> dict:
    """Get current status for dashboard."""
    open_trades = get_open_trades()
    today_trades = get_today_trades()

    return {
        "status": "RUNNING" if is_expiry_day() else "CLOSED",
        "open_count": len(open_trades),
        "today_count": len(today_trades),
        "today_pnl": sum(
            (t.entry_ce_premium - t.entry_ce_premium) * t.quantity +  # placeholder
            (t.entry_pe_premium - t.entry_pe_premium) * t.quantity
            for t in today_trades if t.status == "CLOSED"
        ),
        "is_expiry_day": is_expiry_day(),
    }
