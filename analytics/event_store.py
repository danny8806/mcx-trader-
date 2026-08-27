"""Canonical Event Store - immutable append-only trading event log."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from typing import Optional
from pathlib import Path


class EventStore:
    """Immutable append-only store for all trading lifecycle events."""

    # All valid event types
    EVENT_TYPES = [
        "SIGNAL_CREATED",
        "TRIGGER_PENDING",
        "TRIGGERED",
        "ORDER_CREATED",
        "ORDER_SUBMITTED",
        "ORDER_ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "POSITION_OPENED",
        "POSITION_UPDATED",
        "STOP_UPDATED",
        "EXIT_SIGNAL",
        "EXIT_ORDER",
        "EXIT_FILL",
        "POSITION_CLOSED",
        "TRADE_CLOSED",
        "FILL",
        "SNAPSHOT",
    ]

    def __init__(self, db_path: str = "analytics.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._sequence = 0
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local persistent connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def record(self, trade_id: str, strategy_id: str, instrument: str,
               event_type: str, payload: Optional[dict] = None,
               source: str = "system", timestamp: Optional[float] = None) -> str:
        """Record an immutable event. Returns event_id."""
        if event_type not in self.EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {event_type}")

        event_id = str(uuid.uuid4())
        ts = timestamp or time.time()

        with self._lock:
            self._sequence += 1
            seq = self._sequence
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO trade_events
                   (event_id, trade_id, strategy_id, instrument, timestamp,
                    event_type, source, payload, sequence_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, trade_id, strategy_id, instrument, ts,
                 event_type, source, json.dumps(payload) if payload else None, seq)
            )
            conn.commit()

        return event_id

    def get_events_for_trade(self, trade_id: str) -> list[dict]:
        """Get all events for a specific trade, ordered by sequence."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY sequence_number",
            (trade_id,)
        ).fetchall()
        conn.row_factory = None
        return [dict(r) for r in rows]

    def get_events_for_strategy(self, strategy_id: str,
                                event_type: Optional[str] = None,
                                limit: int = 1000) -> list[dict]:
        """Get events for a strategy, optionally filtered by type."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        if event_type:
            rows = conn.execute(
                "SELECT * FROM trade_events WHERE strategy_id = ? AND event_type = ? ORDER BY sequence_number DESC LIMIT ?",
                (strategy_id, event_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_events WHERE strategy_id = ? ORDER BY sequence_number DESC LIMIT ?",
                (strategy_id, limit)
            ).fetchall()
        conn.row_factory = None
        return [dict(r) for r in rows]

    def get_events_in_range(self, start_time: float, end_time: float,
                            event_type: Optional[str] = None) -> list[dict]:
        """Get events within a time range."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        if event_type:
            rows = conn.execute(
                "SELECT * FROM trade_events WHERE timestamp BETWEEN ? AND ? AND event_type = ? ORDER BY timestamp",
                (start_time, end_time, event_type)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_events WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
                (start_time, end_time)
            ).fetchall()
        conn.row_factory = None
        return [dict(r) for r in rows]

    def count_events(self, strategy_id: Optional[str] = None) -> int:
        """Count total events, optionally per strategy."""
        conn = self._get_conn()
        if strategy_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM trade_events WHERE strategy_id = ?",
                (strategy_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM trade_events").fetchone()
        return row[0]

    def get_event_by_id(self, event_id: str) -> Optional[dict]:
        """Get a single event by ID."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM trade_events WHERE event_id = ?",
            (event_id,)
        ).fetchone()
        conn.row_factory = None
        return dict(row) if row else None
