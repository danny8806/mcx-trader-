"""Telegram Bot API client with queue-based async sending."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Optional

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        if bot_token:
            self.bot_token = bot_token
        else:
            try:
                from config import Config
                cfg = Config.get("telegram") or {}
                self.bot_token = cfg.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
            except Exception:
                self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        # Support multiple chat_ids (comma-separated or list)
        if chat_id:
            raw = chat_id
        else:
            try:
                from config import Config
                cfg = Config.get("telegram") or {}
                raw = cfg.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
            except Exception:
                raw = os.environ.get("TELEGRAM_CHAT_ID", "")
        if isinstance(raw, list):
            self.chat_ids = [str(c).strip() for c in raw if str(c).strip()]
        else:
            self.chat_ids = [c.strip() for c in str(raw).split(",") if c.strip()]
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._running = False
        self._worker: Optional[threading.Thread] = None
        self._sent_count = 0
        self._error_count = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="telegram-worker")
        self._worker.start()
        logger.info("Telegram worker started")

    def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.join(timeout=5)

    def _run_worker(self) -> None:
        while self._running:
            try:
                text, parse_mode, silent = self._queue.get(timeout=1.0)
                try:
                    self._send_message(text, parse_mode, silent)
                finally:
                    self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Telegram worker error: {e}")

    def _send_message(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
        if not self.bot_token or not self.chat_ids:
            logger.warning("Telegram not configured - message dropped")
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        all_ok = True
        for chat_id in self.chat_ids:
            payload = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": silent,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        self._sent_count += 1
                    else:
                        all_ok = False
            except urllib.error.URLError as e:
                self._error_count += 1
                logger.error(f"Telegram send to {chat_id} failed: {e}")
                all_ok = False
            except Exception as e:
                self._error_count += 1
                logger.error(f"Telegram error to {chat_id}: {e}")
                all_ok = False
        return all_ok

    def send(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
        try:
            self._queue.put_nowait((text, parse_mode, silent))
            return True
        except queue.Full:
            logger.warning("Telegram queue full - message dropped")
            return False

    def send_sync(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
        return self._send_message(text, parse_mode, silent)

    def get_stats(self) -> dict[str, Any]:
        return {
            "configured": bool(self.bot_token and self.chat_ids),
            "chat_ids": self.chat_ids,
            "sent_count": self._sent_count,
            "error_count": self._error_count,
            "queue_size": self._queue.qsize(),
            "running": self._running,
        }
