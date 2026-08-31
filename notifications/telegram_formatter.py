"""Telegram message formatters for trading events."""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))


def _ist(ts: Optional[float] = None) -> str:
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _inr(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:,.0f}"


def format_new_trade(fill: dict, strategy: dict, account: dict) -> str:
    direction = fill.get("side", "BUY")
    emoji = "\U0001f534" if direction == "BUY" else "\U0001f535"
    instrument = fill.get("instrument", "?")
    strategy_id = fill.get("strategy_id", "?")
    price = fill.get("price", 0)
    qty = fill.get("quantity", 0)
    multiplier = fill.get("multiplier", 1)
    value = price * qty * multiplier
    entry_value = strategy.get("entry_value", value)
    stop = strategy.get("stop_price", 0)
    htf_val = strategy.get("htf_dema_atr", 0)
    equity = account.get("equity", 0)
    margin = account.get("used_margin", 0)

    return (
        f"{emoji} <b>NEW TRADE</b>\n\n"
        f"<b>Instrument:</b> {instrument}\n"
        f"<b>Strategy:</b> {strategy_id}\n"
        f"<b>Direction:</b> {direction}\n\n"
        f"<b>Signal Time:</b> {_ist()}\n"
        f"<b>Entry Fill:</b> {price:,.0f}\n"
        f"<b>Quantity:</b> {qty}\n"
        f"<b>Stop:</b> {stop:,.0f}\n\n"
        f"<b>Entry Value:</b> {_inr(entry_value)}\n"
        f"<b>HTF DEMA-ATR:</b> {htf_val:,.0f}\n\n"
        f"<b>Account Equity:</b> {_inr(equity)}\n"
        f"<b>Margin Used:</b> {_inr(margin)}\n\n"
        f"<b>Order ID:</b> {fill.get('order_id', '?')}\n"
        f"<b>Mode:</b> PAPER\n"
        f"<b>Timestamp:</b> {_ist()}"
    )


def format_signal_alert(signal_data: dict) -> str:
    """Signal-candle alert: the candle that produced the cross AND the candle
    the trade was actually placed on (may be a later bar / tick)."""
    direction = signal_data.get("side", "LONG")
    emoji = "\U0001f4c8" if direction == "LONG" else "\U0001f4c9"
    instrument = signal_data.get("instrument", "?")
    strategy_id = signal_data.get("strategy_id", "?")

    def _fmt(field: str) -> Optional[str]:
        val = signal_data.get(field)
        if val is None:
            return None
        return f"{float(val):,.0f}"

    sig_time = signal_data.get("signal_candle_time")
    sig_close = _fmt("signal_candle_close")
    sig_high = _fmt("signal_candle_high")
    sig_low = _fmt("signal_candle_low")
    sig_htf = _fmt("signal_htf_dema_atr")
    sig_mid = _fmt("signal_mid_dema_atr")
    sig_fast = _fmt("signal_fast_dema_atr")
    sig_trigger = _fmt("signal_trigger_price")

    place_time = signal_data.get("placement_candle_time")
    place_fill = _fmt("fill_price")

    lines = [
        f"{emoji} <b>SIGNAL CANDLE ALERT</b>\n",
        f"<b>Instrument:</b> {instrument}",
        f"<b>Strategy:</b> {strategy_id}",
        f"<b>Signal:</b> {direction}\n",
        f"<b>— Signal Candle —</b>",
        f"<b>Time:</b> {sig_time or '?'}",
    ]
    if sig_close is not None:
        lines.append(f"<b>Close:</b> {sig_close}")
    if sig_high is not None:
        lines.append(f"<b>High:</b> {sig_high}")
    if sig_low is not None:
        lines.append(f"<b>Low:</b> {sig_low}")
    if sig_trigger is not None:
        lines.append(f"<b>Trigger Level:</b> {sig_trigger}")
    if sig_fast is not None:
        lines.append(f"<b>Fast DEMA-ATR:</b> {sig_fast}")
    if sig_htf is not None:
        lines.append(f"<b>1H DEMA-ATR:</b> {sig_htf}")
    if sig_mid is not None:
        lines.append(f"<b>15m DEMA-ATR:</b> {sig_mid}")

    lines.append("")
    lines.append(f"<b>— Trade Placement —</b>")
    lines.append(f"<b>Time:</b> {place_time or '?'}")
    if place_fill is not None:
        lines.append(f"<b>Entry Fill:</b> {place_fill}")
    lines.append(f"<b>Timestamp:</b> {_ist()}")

    return "\n".join(lines)


def format_trade_exit(close_data: dict) -> str:
    instrument = close_data.get("instrument", "?")
    strategy_id = close_data.get("strategy_id", "?")
    side = close_data.get("side", "?")
    entry = close_data.get("entry_price", 0)
    exit_p = close_data.get("exit_price", 0)
    pnl = close_data.get("net_pnl", 0)
    emoji = "\u2705" if pnl >= 0 else "\u274c"
    duration = close_data.get("duration", "")
    exit_reason = close_data.get("exit_reason", "signal")

    return (
        f"{emoji} <b>TRADE CLOSED</b>\n\n"
        f"<b>Instrument:</b> {instrument}\n"
        f"<b>Strategy:</b> {strategy_id}\n"
        f"<b>Direction:</b> {side}\n"
        f"<b>Entry:</b> {entry:,.0f}\n"
        f"<b>Exit:</b> {exit_p:,.0f}\n"
        f"<b>P&L:</b> {_inr(pnl)}\n"
        f"<b>Exit Reason:</b> {exit_reason}\n"
        f"<b>Duration:</b> {duration}\n"
        f"<b>Timestamp:</b> {_ist()}"
    )


def format_risk_alert(alert_data: dict) -> str:
    severity = alert_data.get("severity", "WARNING")
    emoji = "\u26a0\ufe0f" if severity == "WARNING" else "\U0001f6a8"
    lines = [
        f"{emoji} <b>RISK ALERT</b>\n",
        f"<b>Type:</b> {alert_data.get('type', 'unknown')}",
        f"<b>Message:</b> {alert_data.get('message', '')}",
    ]
    # Add extra details if present
    for field in ['strategy_id', 'instrument', 'side', 'trigger_price', 'stop_price',
                  'quantity', 'value', 'limit', 'equity', 'available_margin']:
        val = alert_data.get(field)
        if val is not None and val != '':
            label = field.replace('_', ' ').title()
            lines.append(f"<b>{label}:</b> {val}")
    lines.append(f"<b>Timestamp:</b> {_ist()}")
    return "\n".join(lines)


def format_error_alert(error_data: dict) -> str:
    return (
        f"\U0001f6a8 <b>ERROR ALERT</b>\n\n"
        f"<b>Component:</b> {error_data.get('component', 'unknown')}\n"
        f"<b>Error:</b> {error_data.get('message', '')}\n"
        f"<b>Timestamp:</b> {_ist()}"
    )


def format_daily_summary(account: dict, pnl_data: dict, risk: dict) -> str:
    equity = account.get("equity", 0)
    starting = account.get("starting_capital", 0)
    net_pnl = equity - starting
    daily = risk.get("daily_pnl", 0)
    return (
        f"\U0001f4ca <b>DAILY SUMMARY</b>\n\n"
        f"<b>Equity:</b> {_inr(equity)}\n"
        f"<b>Starting:</b> {_inr(starting)}\n"
        f"<b>Net P&L:</b> {_inr(net_pnl)}\n"
        f"<b>Today P&L:</b> {_inr(daily)}\n"
        f"<b>Kill Switch:</b> {'ACTIVE' if risk.get('kill_switch_active') else 'OFF'}\n"
        f"<b>Timestamp:</b> {_ist()}"
    )
