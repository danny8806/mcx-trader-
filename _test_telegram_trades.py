"""Send simulated trade notifications to Telegram for visual verification.

Shows exactly what NEW TRADE and TRADE CLOSED messages look like
with signal candle details, indicator values, and P&L breakdown.

Usage:
    cd /app
    python _test_telegram_trades.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))

from notifications.telegram_formatter import format_new_trade, format_trade_exit
from notifications.telegram_client import TelegramClient


def main():
    print("=" * 60)
    print("  TELEGRAM TRADE NOTIFICATION TEST")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 60)

    # Initialize Telegram client
    client = TelegramClient()
    if not client.bot_token or not client.chat_ids:
        print("\n  ERROR: Telegram not configured!")
        print("  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")
        return 1

    print(f"\n  Bot token: ...{client.bot_token[-8:]}")
    print(f"  Chat IDs: {client.chat_ids}")

    # ─────────────────────────────────────────────
    # 1. NEW TRADE — BUY GOLDM with signal candle
    # ─────────────────────────────────────────────
    print("\n  Sending: NEW TRADE (BUY GOLDM with signal candle)...")
    fill_dict = {
        "side": "BUY",
        "instrument": "GOLDM",
        "strategy_id": "gold_01",
        "price": 96450,
        "quantity": 1,
        "multiplier": 10,
        "order_id": f"ORD-TEST-{int(time.time())}",
        "signal_bar_open": 96380,
        "signal_bar_high": 96500,
        "signal_bar_low": 96350,
        "signal_bar_close": 96420,
        "signal_bar_timeframe": "5m",
        "signal_dema_atr": 96410,
        "signal_htf_dema_atr": 96300,
        "signal_mid_dema_atr": 96350,
    }
    strategy = {"stop_price": 96200, "htf_dema_atr": 96300, "entry_value": 964500}
    account = {"equity": 1200000, "used_margin": 96450}

    msg1 = format_new_trade(fill_dict, strategy, account)
    print(f"\n  --- Message Preview ---")
    for line in msg1.split("\n"):
        print(f"  {line}")
    print(f"  --- End Preview ---\n")

    ok1 = client.send_sync(msg1)
    print(f"  Result: {'SENT' if ok1 else 'FAILED'}")

    # ─────────────────────────────────────────────
    # 2. NEW TRADE — SELL SILVERM with signal candle
    # ─────────────────────────────────────────────
    print("\n  Sending: NEW TRADE (SELL SILVERM with signal candle)...")
    fill_dict2 = {
        "side": "SELL",
        "instrument": "SILVERM",
        "strategy_id": "silver_01",
        "price": 114200,
        "quantity": 1,
        "multiplier": 5,
        "order_id": f"ORD-TEST-{int(time.time())+1}",
        "signal_bar_open": 114300,
        "signal_bar_high": 114350,
        "signal_bar_low": 114150,
        "signal_bar_close": 114250,
        "signal_bar_timeframe": "15m",
        "signal_dema_atr": 114280,
        "signal_htf_dema_atr": 114400,
        "signal_mid_dema_atr": 114350,
    }
    strategy2 = {"stop_price": 114600, "htf_dema_atr": 114400, "entry_value": 571000}
    account2 = {"equity": 1200000, "used_margin": 571000}

    msg2 = format_new_trade(fill_dict2, strategy2, account2)
    print(f"\n  --- Message Preview ---")
    for line in msg2.split("\n"):
        print(f"  {line}")
    print(f"  --- End Preview ---\n")

    ok2 = client.send_sync(msg2)
    print(f"  Result: {'SENT' if ok2 else 'FAILED'}")

    # ─────────────────────────────────────────────
    # 3. TRADE CLOSED — profit
    # ─────────────────────────────────────────────
    print("\n  Sending: TRADE CLOSED (profit)...")
    close_data1 = {
        "instrument": "GOLDM",
        "strategy_id": "gold_01",
        "side": "LONG",
        "entry_price": 96450,
        "exit_price": 96800,
        "gross_pnl": 3500,
        "fees": 82.5,
        "net_pnl": 3417.5,
        "exit_reason": "signal_exit",
        "duration": "3h 30m",
    }
    msg3 = format_trade_exit(close_data1)
    print(f"\n  --- Message Preview ---")
    for line in msg3.split("\n"):
        print(f"  {line}")
    print(f"  --- End Preview ---\n")

    ok3 = client.send_sync(msg3)
    print(f"  Result: {'SENT' if ok3 else 'FAILED'}")

    # ─────────────────────────────────────────────
    # 4. TRADE CLOSED — loss
    # ─────────────────────────────────────────────
    print("\n  Sending: TRADE CLOSED (loss)...")
    close_data2 = {
        "instrument": "SILVERM",
        "strategy_id": "silver_01",
        "side": "SHORT",
        "entry_price": 114200,
        "exit_price": 114500,
        "gross_pnl": -1500,
        "fees": 65,
        "net_pnl": -1565,
        "exit_reason": "stop_loss_hit",
        "duration": "1h 15m",
    }
    msg4 = format_trade_exit(close_data2)
    print(f"\n  --- Message Preview ---")
    for line in msg4.split("\n"):
        print(f"  {line}")
    print(f"  --- End Preview ---\n")

    ok4 = client.send_sync(msg4)
    print(f"  Result: {'SENT' if ok4 else 'FAILED'}")

    # ─────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────
    results = [ok1, ok2, ok3, ok4]
    print(f"\n{'='*60}")
    print(f"  RESULTS: {sum(results)}/{len(results)} messages sent")
    print(f"{'='*60}")
    print(f"\n  Check your Telegram for 4 messages:")
    print(f"    1. NEW TRADE BUY GOLDM — with 5m signal candle + DEMA-ATR values")
    print(f"    2. NEW TRADE SELL SILVERM — with 15m signal candle + DEMA-ATR values")
    print(f"    3. TRADE CLOSED profit — with gross + fees + net P&L")
    print(f"    4. TRADE CLOSED loss — with negative P&L breakdown")

    return 0 if all(results) else 1


if __name__ == "__main__":
    import time  # needed for order_id
    sys.exit(main())
