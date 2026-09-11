"""Simple option paper trader. Strategy -> Signal -> Trade -> Monitor -> Exit."""
from __future__ import annotations

import uuid
from datetime import datetime, date

from option.config import SL_PERCENT, MAX_TRADES_PER_DAY, INSTRUMENTS, EXPIRY_DAYS
from option.dhan_client import get_expiry_list, get_option_chain, get_margin, is_token_valid
from option.strategy import parse_option_chain, StrategySignal
from option.database import (
    OptionTrade, save_trade, close_trade, get_open_trades,
    get_today_trades, count_today_trades, init_db, get_today_pnl,
)

init_db()


def is_expiry_day(d: date | None = None) -> bool:
    d = d or date.today()
    return d.weekday() in EXPIRY_DAYS


def _validate_security_ids(signal: StrategySignal) -> bool:
    """Return True if both ce_sec and pe_sec are valid non-None strings."""
    if not signal.ce_sec or not signal.pe_sec:
        print(f"[signal:{signal.underlying}] Missing security ID: "
              f"ce_sec={signal.ce_sec!r} pe_sec={signal.pe_sec!r}")
        return False
    return True


def get_signal(underlying: str) -> StrategySignal | None:
    """Fetch option chain and generate signal if OI threshold met."""
    tag = f"[signal:{underlying}]"
    cfg = INSTRUMENTS.get(underlying)
    if not cfg:
        print(f"{tag} Unknown underlying")
        return None

    valid, reason = is_token_valid()
    if not valid:
        print(f"{tag} Token invalid: {reason}")
        return None

    print(f"{tag} Fetching expiry list...")
    expiries = get_expiry_list(cfg["scrip"])
    if not expiries:
        print(f"{tag} No expiry found")
        return None

    expiry = expiries[0]
    print(f"{tag} Expiry: {expiry}")

    chain = get_option_chain(cfg["scrip"], expiry)
    if not chain:
        print(f"{tag} No chain data")
        return None

    spot = chain.get("last_price", 0)
    oc = chain.get("oc", {})
    print(f"{tag} Spot: {spot}, strikes: {len(oc)}")

    signal = parse_option_chain(chain, cfg["lot_size"], cfg["exchange"], underlying)
    if not signal:
        print(f"{tag} No signal (parse failed)")
        return None

    signal.expiry = expiry

    if not _validate_security_ids(signal):
        return None

    oi_diff = abs(signal.ce_oi - signal.pe_oi)
    oi_threshold = cfg.get("oi_threshold", 5_000_000)
    print(f"{tag} Signal: CE OI={signal.ce_oi:,} PE OI={signal.pe_oi:,} diff={oi_diff:,} threshold={oi_threshold:,}")
    print(f"{tag} Strike: {signal.selected_strike} CE={signal.ce_ltp} PE={signal.pe_ltp}")

    if oi_diff < oi_threshold:
        print(f"{tag} OI diff {oi_diff:,} < threshold {oi_threshold:,} — no trade")
        return None

    print(f"{tag} Calculating margin...")
    ce_margin = get_margin(signal.ce_sec, cfg["lot_size"], cfg["exchange"])
    pe_margin = get_margin(signal.pe_sec, cfg["lot_size"], cfg["exchange"])
    signal.margin = ce_margin + pe_margin
    print(f"{tag} Margin: CE={ce_margin:,.0f} PE={pe_margin:,.0f} Total={signal.margin:,.0f}")

    return signal


def open_trade(signal: StrategySignal) -> OptionTrade:
    """Create a paper trade from a signal."""
    trade_id = f"OPT-{uuid.uuid4().hex[:8].upper()}"
    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    print(f"[trade:OPEN] {trade_id} {signal.underlying} {signal.selected_strike} "
          f"CE={signal.ce_ltp} PE={signal.pe_ltp} credit={entry_credit:,.0f} margin={signal.margin:,.0f} SL={sl_amount:,.0f}")
    return trade


def check_and_exit() -> list[dict]:
    """Check open trades for SL hit or EOD exit. Returns list of exit results."""
    open_trades = get_open_trades()
    results = []
    now = datetime.now()
    is_eod = now.hour >= 15 and now.minute >= 15

    if not open_trades:
        print("[exit] No open trades")
        return results

    print(f"[exit] Checking {len(open_trades)} open trade(s), is_eod={is_eod}")

    for trade in open_trades:
        tag = f"[exit:{trade.trade_id[:12]}]"
        try:
            cfg = INSTRUMENTS.get(trade.underlying)
            if not cfg:
                print(f"{tag} Unknown underlying {trade.underlying} — skipping")
                continue

            chain = get_option_chain(cfg["scrip"], trade.expiry)
            if not chain:
                print(f"{tag} Cannot fetch chain — skipping")
                continue

            oc = chain.get("oc", {})
            strike_data = oc.get(str(int(trade.strike)), {})
            ce_data = strike_data.get("ce", {})
            pe_data = strike_data.get("pe", {})

            current_ce = ce_data.get("last_price", trade.entry_ce_premium)
            current_pe = pe_data.get("last_price", trade.entry_pe_premium)
        except Exception as e:
            print(f"{tag} Error fetching premiums: {e}")
            continue

        ce_pnl = (trade.entry_ce_premium - current_ce) * trade.quantity
        pe_pnl = (trade.entry_pe_premium - current_pe) * trade.quantity
        total_pnl = ce_pnl + pe_pnl

        stale = (current_ce == trade.entry_ce_premium and current_pe == trade.entry_pe_premium)
        if stale:
            print(f"{tag} WARNING: Exit premiums SAME as entry (CE={current_ce} PE={current_pe}) — possible stale data")

        print(f"{tag} Entry CE={trade.entry_ce_premium} PE={trade.entry_pe_premium} "
              f"-> Exit CE={current_ce} PE={current_pe} "
              f"PnL={total_pnl:+,.0f} (CE:{ce_pnl:+,.0f} PE:{pe_pnl:+,.0f})")

        exit_reason = None

        if total_pnl < 0 and abs(total_pnl) >= trade.sl_amount:
            exit_reason = "SL"
            print(f"{tag} SL HIT: loss {abs(total_pnl):,.0f} >= SL {trade.sl_amount:,.0f}")
        elif is_eod:
            exit_reason = "EOD"
            print(f"{tag} EOD EXIT")

        if exit_reason:
            exit_time = now.strftime("%Y-%m-%d %H:%M:%S")
            close_trade(trade.trade_id, exit_time, current_ce, current_pe, total_pnl, exit_reason)
            print(f"[trade:CLOSED] {trade.trade_id} {trade.underlying} {trade.strike} "
                  f"PnL={total_pnl:+,.0f} reason={exit_reason}")
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
    except Exception as e:
        print(f"[premiums] Error: {e}")
        return None


def run_morning_check() -> list[dict]:
    """9:30 AM check. Returns list of trades opened. Runs EVERY DAY."""
    print("[check] Starting morning check...")
    today_count = count_today_trades()
    if today_count >= MAX_TRADES_PER_DAY:
        print(f"[check] Already {today_count} trades today (max {MAX_TRADES_PER_DAY})")
        return []

    valid, reason = is_token_valid()
    if not valid:
        print(f"[check] BLOCKED: Token invalid — {reason}")
        return []

    trades = []
    for underlying in ["NIFTY", "SENSEX"]:
        today = get_today_trades()
        if any(t.underlying == underlying for t in today):
            print(f"[check] {underlying}: already traded today, skipping")
            continue

        cfg = INSTRUMENTS.get(underlying)
        if not cfg:
            print(f"[check] {underlying}: unknown instrument, skipping")
            continue

        print(f"[check] {underlying}: checking signal...")
        signal = get_signal(underlying)
        if not signal:
            continue

        oi_diff = abs(signal.ce_oi - signal.pe_oi)
        oi_threshold = cfg.get("oi_threshold", 5_000_000)
        if oi_diff >= oi_threshold:
            trade = open_trade(signal)
            trades.append({
                "trade_id": trade.trade_id,
                "underlying": trade.underlying,
                "strike": trade.strike,
                "margin": trade.margin,
                "credit": trade.entry_credit,
            })
        else:
            print(f"[check] {underlying}: OI diff {oi_diff:,} < threshold {oi_threshold:,}")

    print(f"[check] Morning check complete: {len(trades)} trade(s) opened")
    return trades


def run_recheck() -> list[dict]:
    """10:00 AM recheck. Same as morning check."""
    print("[recheck] Starting 10:00 recheck...")
    return run_morning_check()


def run_eod_exit() -> list[dict]:
    """3:15 PM EOD exit."""
    print("[eod] Starting EOD exit...")
    return check_and_exit()


def get_status() -> dict:
    """Get current status for dashboard."""
    open_trades = get_open_trades()
    today_trades = get_today_trades()
    valid, token_reason = is_token_valid()

    return {
        "status": "RUNNING",
        "open_count": len(open_trades),
        "today_count": len(today_trades),
        "today_pnl": get_today_pnl(),
        "is_expiry_day": is_expiry_day(),
        "token_valid": valid,
        "token_info": token_reason,
    }
