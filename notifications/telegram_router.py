"""Telegram alert router - routes trading events to Telegram messages."""
from __future__ import annotations

import logging
from typing import Any, Optional

from notifications.telegram_client import TelegramClient
from notifications.telegram_formatter import (
    format_new_trade, format_trade_exit, format_risk_alert, format_error_alert, format_daily_summary,
)

logger = logging.getLogger(__name__)


class TelegramRouter:
    def __init__(self, client: Optional[TelegramClient] = None):
        self.client = client or TelegramClient()
        try:
            from config import Config
            self._enabled = Config.get("telegram.enabled", True)
        except Exception:
            self._enabled = True

    def start(self) -> None:
        self.client.start()

    def stop(self) -> None:
        self.client.stop()

    def on_fill(self, fill: dict, strategy: dict, account: dict) -> None:
        if not self._enabled:
            return
        text = format_new_trade(fill, strategy, account)
        self.client.send(text)

    def on_trade_close(self, close_data: dict) -> None:
        if not self._enabled:
            return
        text = format_trade_exit(close_data)
        self.client.send(text)

    def on_risk_alert(self, alert_data: dict) -> None:
        if not self._enabled:
            return
        text = format_risk_alert(alert_data)
        self.client.send(text)

    def on_error(self, error_data: dict) -> None:
        if not self._enabled:
            return
        text = format_error_alert(error_data)
        self.client.send(text, silent=True)

    def send_daily_summary(self, account: dict, pnl_data: dict, risk: dict) -> None:
        text = format_daily_summary(account, pnl_data, risk)
        self.client.send(text)

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def get_stats(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            **self.client.get_stats(),
        }

    def send_sync(self, text: str) -> bool:
        """Send message synchronously (bypasses queue)."""
        return self.client.send_sync(text)
