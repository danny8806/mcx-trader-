"""Fill deduplication to prevent duplicate fill processing.

Fills are identified by fill_id (UUID). This module provides:
- In-memory set for fast lookups
- Database-backed persistence for crash recovery
- Atomic check-and-mark to prevent race conditions
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Optional


class FillDeduplicator:
    """Prevents duplicate fill processing."""

    def __init__(self, db_path: str = "trading.db"):
        self._db_path = db_path
        self._processed_fills: set[str] = set()
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the processed_fills tracking table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_fills (
                    fill_id TEXT PRIMARY KEY,
                    processed_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def load_from_database(self) -> int:
        """Load processed fill IDs from database on startup.

        Returns:
            Number of processed fills loaded.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute("SELECT fill_id FROM processed_fills").fetchall()
            with self._lock:
                self._processed_fills = {row[0] for row in rows}
            return len(self._processed_fills)
        finally:
            conn.close()

    def is_duplicate(self, fill_id: str) -> bool:
        """Check if this fill was already processed.

        Checks in-memory set first (fast path), then falls back to database.
        """
        # Fast path: check in-memory set
        with self._lock:
            if fill_id in self._processed_fills:
                return True

        # Slow path: check database
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM processed_fills WHERE fill_id = ?", (fill_id,)
            ).fetchone()
            if row is not None:
                # Found in DB but not in memory — populate memory cache
                with self._lock:
                    self._processed_fills.add(fill_id)
                return True
            return False
        finally:
            conn.close()

    def mark_processed(self, fill_id: str) -> bool:
        """Mark a fill as processed.

        Atomically inserts into database and adds to in-memory set.
        Returns True if newly marked, False if already existed.

        Raises:
            sqlite3.IntegrityError: If fill_id already exists (caller should
                catch and treat as duplicate).
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO processed_fills (fill_id) VALUES (?)",
                (fill_id,),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Already in database — treat as duplicate
            return False
        finally:
            conn.close()

        with self._lock:
            self._processed_fills.add(fill_id)
        return True

    def note_processed(self, fill_id: str) -> None:
        """Add a fill to the in-memory dedup set only (no DB write).

        Used to hold the in-process duplicate lock the moment a fill is handed
        to the engine, while the durable DB mark (mark_processed) happens only
        AFTER all financial effects are applied — closing the crash window in
        which a fill was indelibly marked before its rows were written.
        """
        with self._lock:
            self._processed_fills.add(fill_id)

    def is_processed(self, fill_id: str) -> bool:
        """Alias for is_duplicate — checks if fill was already processed."""
        return self.is_duplicate(fill_id)

    def cleanup_old(self, days: int = 30) -> int:
        """Remove processed fill records older than N days.

        Returns:
            Number of records deleted.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM processed_fills WHERE processed_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            conn.commit()
            deleted = cursor.rowcount
        finally:
            conn.close()

        if deleted > 0:
            # Rebuild in-memory set from database
            self.load_from_database()

        return deleted

    @property
    def count(self) -> int:
        """Number of processed fills tracked."""
        with self._lock:
            return len(self._processed_fills)
