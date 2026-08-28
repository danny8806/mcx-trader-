"""Persistence layer for system state and trade logging."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class PersistenceManager:
    """Manages persistence of system state and trade data.
    
    Uses:
    - JSON for system state (fast, atomic writes)
    - SQLite for trade history (queryable, auditable)
    
    Thread safety: All DB operations are serialized via _lock.
    Single persistent connection avoids connection churn.
    """

    def __init__(
        self,
        state_path: str = "system_state.json",
        db_path: str = "trading.db",
    ):
        self.state_path = Path(state_path)
        self.db_path = Path(db_path)
        self._lock = threading.Lock()

        # Initialize database schema
        self._init_db()

        # Persistent connection (created lazily)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create persistent SQLite connection (thread-safe)."""
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is not None:
                return self._conn
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            return self._conn

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE,
                    strategy_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_timestamp TEXT,
                    entry_price REAL,
                    exit_timestamp TEXT,
                    exit_price REAL,
                    quantity INTEGER,
                    multiplier REAL,
                    gross_pnl REAL,
                    charges REAL,
                    net_pnl REAL,
                    exit_reason TEXT,
                    status TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE,
                    strategy_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER,
                    order_type TEXT,
                    price REAL,
                    state TEXT,
                    filled_quantity INTEGER,
                    average_fill_price REAL,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fill_id TEXT UNIQUE,
                    order_id TEXT,
                    strategy_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER,
                    price REAL,
                    timestamp TEXT
                );

                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    equity REAL,
                    realized_pnl REAL,
                    unrealized_pnl REAL,
                    used_margin REAL,
                    available_margin REAL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    strategy_id TEXT,
                    instrument TEXT,
                    details TEXT
                );
            """)
        finally:
            conn.close()

    def save_state(self, state: dict) -> None:
        """Save system state to JSON file (atomic write, thread-safe)."""
        with self._lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
            tmp.replace(self.state_path)

    def load_state(self) -> Optional[dict]:
        """Load system state from JSON file."""
        if not self.state_path.exists():
            return None
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return None

    def save_trade(self, trade: dict) -> None:
        """Save a completed trade to database."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, strategy_id, instrument, side,
                    entry_timestamp, entry_price, exit_timestamp, exit_price,
                    quantity, multiplier, gross_pnl, charges, net_pnl,
                    exit_reason, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("trade_id"),
                trade.get("strategy_id"),
                trade.get("instrument"),
                trade.get("side"),
                trade.get("entry_timestamp"),
                trade.get("entry_price"),
                trade.get("exit_timestamp"),
                trade.get("exit_price"),
                trade.get("quantity"),
                trade.get("multiplier"),
                trade.get("gross_pnl"),
                trade.get("charges"),
                trade.get("net_pnl"),
                trade.get("exit_reason"),
                trade.get("status", "closed"),
            ))
            conn.commit()

    def save_order(self, order: dict) -> None:
        """Save order to database."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, strategy_id, instrument, side,
                    quantity, order_type, price, state,
                    filled_quantity, average_fill_price,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.get("order_id"),
                order.get("strategy_id"),
                order.get("instrument"),
                order.get("side"),
                order.get("quantity"),
                order.get("order_type"),
                order.get("price"),
                order.get("state"),
                order.get("filled_quantity"),
                order.get("average_fill_price"),
                order.get("created_at"),
                order.get("updated_at"),
            ))
            conn.commit()

    def save_fill(self, fill: dict) -> None:
        """Save fill to database."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO fills (
                    fill_id, order_id, strategy_id, instrument,
                    side, quantity, price, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fill.get("fill_id"),
                fill.get("order_id"),
                fill.get("strategy_id"),
                fill.get("instrument"),
                fill.get("side"),
                fill.get("quantity"),
                fill.get("price"),
                fill.get("timestamp"),
            ))
            conn.commit()

    def save_event(self, event: dict) -> None:
        """Save event to audit log."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO events (
                    timestamp, event_type, strategy_id, instrument, details
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                event.get("timestamp", datetime.now(timezone.utc).isoformat()),
                event.get("event_type"),
                event.get("strategy_id"),
                event.get("instrument"),
                json.dumps(event.get("details", {})),
            ))
            conn.commit()

    def get_trades(self, strategy_id: Optional[str] = None) -> list[dict]:
        """Get trades from database."""
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            if strategy_id:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE strategy_id=? ORDER BY id DESC",
                    (strategy_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_account_snapshots(self, limit: int = 100) -> list[dict]:
        """Get recent account snapshots."""
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_account_snapshot(self, snapshot: dict) -> None:
        """Save account snapshot."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO account_snapshots (
                    timestamp, equity, realized_pnl, unrealized_pnl,
                    used_margin, available_margin
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()),
                snapshot.get("equity"),
                snapshot.get("realized_pnl"),
                snapshot.get("unrealized_pnl"),
                snapshot.get("used_margin"),
                snapshot.get("available_margin"),
            ))
            conn.commit()

    def close(self) -> None:
        """Close the persistent database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def __del__(self) -> None:
        """Auto-close connection on garbage collection."""
        try:
            self.close()
        except Exception:
            pass
