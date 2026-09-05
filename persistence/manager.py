"""Persistence layer for system state and trade logging."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .database import Database, shared_path_lock


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
        # Public write methods call _get_conn while holding this lock.  It must
        # therefore be re-entrant; a plain Lock deadlocks on the first write.
        # This is the process-wide lock SHARED with every other Database
        # instance writing the same file, so all writers serialize together.
        self._lock = shared_path_lock(self.db_path)

        # Initialize the canonical schema eagerly (legacy behavior: existing
        # databases get their tables/migrations on construction) but do not
        # hold a connection afterwards.  Runtime writes share the
        # process-wide connection created lazily on the first DB write.
        Database(self.db_path).close()

        # Runtime writes open the schema via the shared Database, created
        # lazily on the first DB write.
        self._db: Optional[Database] = None

        # Persistent connection (created lazily through the shared Database)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        """Return the process-wide shared connection (thread-safe)."""
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is not None:
                return self._conn
            if self._db is None:
                self._db = Database(self.db_path)
            self._conn = self._db.get_conn()
            return self._conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Run a DB write inside an EXPLICIT transaction on the process-wide
        shared connection.

        Every writer in the process (Database, TradeLedger, EventStore and this
        manager) shares ONE connection and ONE write lock.  Write statements
        MUST go through an explicit BEGIN/COMMIT (rollback on error); a bare
        ``execute()`` would open a pysqlite implicit transaction and, if it
        ever failed, leak an open transaction that would then break the next
        ``BEGIN IMMEDIATE`` from any other component on the same connection.
        """
        with self._lock:
            if self._db is None:
                self._db = Database(self.db_path)
            with self._db.transaction() as conn:
                yield conn

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
                    created_at TEXT DEFAULT (datetime('now')),
                    entry_signal_id TEXT,
                    exit_signal_id TEXT
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
                    updated_at TEXT,
                    entry_signal_id TEXT,
                    trade_id TEXT
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
                    timestamp TEXT,
                    entry_signal_id TEXT,
                    trade_id TEXT
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT UNIQUE,
                    strategy_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    side TEXT,
                    signal_type TEXT,
                    timestamp REAL,
                    trigger_price REAL,
                    stop_price REAL,
                    quantity INTEGER,
                    candle_data TEXT,
                    indicator_data TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS trade_signal_link (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL,
                    signal_id TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(trade_id, signal_id, relationship_type)
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
            # Run migrations for existing databases
            self._migrate_db(conn)
        finally:
            conn.close()

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        """Add missing columns to existing tables (idempotent)."""
        cursor = conn.cursor()
        # Check which columns exist on each table
        existing = {}
        for table in ("trades", "orders", "fills"):
            cursor.execute(f"PRAGMA table_info({table})")
            existing[table] = {row[1] for row in cursor.fetchall()}

        # trades table
        if "entry_signal_id" not in existing.get("trades", set()):
            cursor.execute("ALTER TABLE trades ADD COLUMN entry_signal_id TEXT")
        if "exit_signal_id" not in existing.get("trades", set()):
            cursor.execute("ALTER TABLE trades ADD COLUMN exit_signal_id TEXT")

        # orders table
        if "entry_signal_id" not in existing.get("orders", set()):
            cursor.execute("ALTER TABLE orders ADD COLUMN entry_signal_id TEXT")
        if "trade_id" not in existing.get("orders", set()):
            cursor.execute("ALTER TABLE orders ADD COLUMN trade_id TEXT")

        # fills table
        if "entry_signal_id" not in existing.get("fills", set()):
            cursor.execute("ALTER TABLE fills ADD COLUMN entry_signal_id TEXT")
        if "trade_id" not in existing.get("fills", set()):
            cursor.execute("ALTER TABLE fills ADD COLUMN trade_id TEXT")

        conn.commit()

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
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO trades (
                    trade_id, strategy_id, instrument, side,
                    entry_timestamp, entry_price, exit_timestamp, exit_price,
                    quantity, multiplier, gross_pnl, charges, net_pnl,
                    exit_reason, status, entry_signal_id, exit_signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    strategy_id=excluded.strategy_id, instrument=excluded.instrument,
                    side=excluded.side, entry_timestamp=excluded.entry_timestamp,
                    entry_price=excluded.entry_price, exit_timestamp=excluded.exit_timestamp,
                    exit_price=excluded.exit_price, quantity=excluded.quantity,
                    multiplier=excluded.multiplier, gross_pnl=excluded.gross_pnl,
                    charges=excluded.charges, net_pnl=excluded.net_pnl,
                    exit_reason=excluded.exit_reason, status=excluded.status,
                    entry_signal_id=excluded.entry_signal_id,
                        exit_signal_id=excluded.exit_signal_id
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
                trade.get("entry_signal_id"),
                trade.get("exit_signal_id"),
            ))

    def save_order(self, order: dict) -> None:
        """Save order to database."""
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO orders (
                    order_id, strategy_id, instrument, side,
                    quantity, order_type, price, state,
                    filled_quantity, average_fill_price,
                    created_at, updated_at,
                    signal_id, trade_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    state=excluded.state, filled_quantity=excluded.filled_quantity,
                    average_fill_price=excluded.average_fill_price,
                        updated_at=excluded.updated_at
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
                order.get("signal_id", order.get("entry_signal_id")),
                order.get("trade_id"),
            ))

    def save_fill(self, fill: dict) -> None:
        """Save fill to database."""
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO fills (
                    fill_id, order_id, strategy_id, instrument,
                    side, quantity, price, timestamp, trade_id, entry_signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fill_id) DO NOTHING
            """, (
                fill.get("fill_id"),
                fill.get("order_id"),
                fill.get("strategy_id"),
                fill.get("instrument"),
                fill.get("side"),
                fill.get("quantity"),
                fill.get("price"),
                fill.get("timestamp"),
                fill.get("trade_id"),
                fill.get("entry_signal_id"),
            ))

    def save_signal(self, signal_data: dict) -> None:
        """Save signal to the signals table for audit trail."""
        with self._tx() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signals (
                    signal_id, strategy_id, instrument, side, signal_type,
                    signal_timestamp, trigger_price, stop_price, quantity,
                    candle_data, indicator_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_data.get("signal_id"),
                signal_data.get("strategy_id"),
                signal_data.get("instrument"),
                signal_data.get("side"),
                signal_data.get("signal_type"),
                signal_data.get("timestamp"),
                signal_data.get("trigger_price"),
                signal_data.get("stop_price"),
                signal_data.get("quantity"),
                json.dumps(signal_data.get("candle_data")) if signal_data.get("candle_data") else None,
                json.dumps(signal_data.get("indicator_data")) if signal_data.get("indicator_data") else None,
            ))

    def save_trade_signal_link(self, trade_id: str, signal_id: str, relationship_type: str) -> None:
        """Save a trade-signal relationship link."""
        with self._tx() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO trade_signal_link (
                    trade_id, signal_id, relationship_type
                ) VALUES (?, ?, ?)
            """, (trade_id, signal_id, relationship_type))

    def get_fill(self, fill_id: str) -> Optional[dict]:
        """Fetch a single fill row by fill_id (DB-backed idempotency guard)."""
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM fills WHERE fill_id = ?", (fill_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_trade_and_fill(self, trade: dict, fill: dict) -> None:
        """Persist a closed trade and its exit fill in one transaction."""
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO trades (
                    trade_id, strategy_id, instrument, side,
                    entry_timestamp, entry_price, exit_timestamp, exit_price,
                    quantity, multiplier, gross_pnl, charges, net_pnl,
                    exit_reason, status, entry_signal_id, exit_signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    exit_timestamp=excluded.exit_timestamp,
                    exit_price=excluded.exit_price,
                    gross_pnl=excluded.gross_pnl,
                    charges=excluded.charges,
                    net_pnl=excluded.net_pnl,
                    exit_reason=excluded.exit_reason,
                    status=excluded.status,
                    exit_signal_id=excluded.exit_signal_id
            """, (
                trade.get("trade_id"), trade.get("strategy_id"), trade.get("instrument"),
                trade.get("side"), trade.get("entry_timestamp"), trade.get("entry_price"),
                trade.get("exit_timestamp"), trade.get("exit_price"), trade.get("quantity"),
                trade.get("multiplier"), trade.get("gross_pnl"), trade.get("charges"),
                trade.get("net_pnl"), trade.get("exit_reason"), trade.get("status", "closed"),
                trade.get("entry_signal_id"), trade.get("exit_signal_id"),
            ))
            conn.execute("""
                INSERT INTO fills (
                    fill_id, order_id, strategy_id, instrument,
                    side, quantity, price, timestamp, trade_id, entry_signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fill_id) DO NOTHING
            """, (
                fill.get("fill_id"), fill.get("order_id"), fill.get("strategy_id"),
                fill.get("instrument"), fill.get("side"), fill.get("quantity"),
                fill.get("price"), fill.get("timestamp"), fill.get("trade_id"),
                fill.get("entry_signal_id"),
            ))

    def save_event(self, event: dict) -> None:
        """Save event to audit log."""
        with self._tx() as conn:
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

    def save_position(self, position) -> None:
        """Persist a position to the canonical positions table.

        The position_id is a SEPARATE identity from the trade_id (enforced by
        the canonical uniqueness trigger). status='open' on entry; the row is
        flipped to 'closed' by close_position_record() on exit.
        """
        entry_time = position.entry_timestamp
        if isinstance(entry_time, (int, float)) and entry_time:
            entry_time = datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO positions (
                    position_id, trade_id, strategy_id, instrument, side,
                    quantity, average_entry_price, status, entry_time,
                    realized_pnl, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    strategy_id=excluded.strategy_id, instrument=excluded.instrument,
                    side=excluded.side, quantity=excluded.quantity,
                    average_entry_price=excluded.average_entry_price,
                    status=excluded.status, realized_pnl=excluded.realized_pnl,
                    updated_at=excluded.updated_at
            """, (
                position.position_id,
                position.trade_id,
                position.strategy_id,
                position.instrument,
                position.side.value if hasattr(position.side, "value") else str(position.side),
                position.quantity,
                position.average_entry,
                position.status.value if hasattr(position.status, "value") else str(position.status),
                entry_time,
                position.realized_pnl,
                datetime.now(timezone.utc).isoformat(),
            ))

    def close_position_record(self, position) -> None:
        """Flip a position row to closed with exit price/timestamp/realized P&L."""
        exit_price = None
        exit_time = None
        if getattr(position, "exit_fills", None):
            last_exit = position.exit_fills[-1]
            exit_price = last_exit.price
            if last_exit.timestamp:
                exit_time = datetime.fromtimestamp(
                    last_exit.timestamp, tz=timezone.utc
                ).isoformat()
        with self._tx() as conn:
            conn.execute("""
                UPDATE positions SET
                    status='closed',
                    average_exit_price=?,
                    exit_time=?,
                    realized_pnl=?,
                    updated_at=?
                WHERE position_id=?
            """, (
                exit_price,
                exit_time,
                position.realized_pnl,
                datetime.now(timezone.utc).isoformat(),
                position.position_id,
            ))

    def get_open_positions(self, strategy_id: Optional[str] = None) -> list[dict]:
        """Get open position rows from the canonical positions table."""
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            if strategy_id:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status='open' AND strategy_id=?",
                    (strategy_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status='open'"
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
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO account_snapshots (
                    timestamp, equity, realized_pnl, unrealized_pnl,
                    used_margin, available_margin
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()),
                snapshot.get("equity"),
                snapshot.get("realized_pnl"),
                snapshot.get("unrealized_pnl"),
                snapshot.get("used_margin"),
                snapshot.get("available_margin"),
            ))

    def save_account_snapshot_from_state(self, state: dict) -> None:
        """Derive and persist an account-snapshot row from an engine snapshot."""
        acct = (state or {}).get("account")
        if not acct:
            return
        self.save_account_snapshot({
            "timestamp": state.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "equity": acct.get("equity"),
            "realized_pnl": acct.get("realized_pnl"),
            "unrealized_pnl": acct.get("unrealized_pnl"),
            "used_margin": acct.get("used_margin"),
            "available_margin": acct.get("available_margin"),
        })

    def close(self) -> None:
        """Close the persistent database connection."""
        with self._lock:
            self._conn = None
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:
                    pass
            self._db = None

    def __del__(self) -> None:
        """Auto-close connection on garbage collection."""
        try:
            self.close()
        except Exception:
            pass
