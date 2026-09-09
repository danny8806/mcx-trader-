"""Option paper trading database. SQLite with simple schema."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, date
from dataclasses import dataclass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "option_paper_trading.db")


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS option_trades (
            trade_id TEXT PRIMARY KEY,
            underlying TEXT NOT NULL,
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            ce_security_id TEXT,
            pe_security_id TEXT,
            entry_time TEXT NOT NULL,
            entry_ce_premium REAL NOT NULL,
            entry_pe_premium REAL NOT NULL,
            entry_credit REAL NOT NULL,
            quantity INTEGER NOT NULL,
            lot_size INTEGER NOT NULL,
            margin REAL NOT NULL,
            sl_amount REAL NOT NULL,
            exit_time TEXT,
            exit_ce_premium REAL,
            exit_pe_premium REAL,
            exit_pnl REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_reason TEXT,
            pcr REAL,
            selection_reason TEXT,
            spot_at_entry REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_option_trades_status ON option_trades(status);
        CREATE INDEX IF NOT EXISTS idx_option_trades_date ON option_trades(entry_time);
    """)
    conn.commit()
    conn.close()


@dataclass
class OptionTrade:
    trade_id: str
    underlying: str
    expiry: str
    strike: float
    ce_security_id: str
    pe_security_id: str
    entry_time: str
    entry_ce_premium: float
    entry_pe_premium: float
    entry_credit: float
    quantity: int
    lot_size: int
    margin: float
    sl_amount: float
    exit_time: str | None = None
    exit_ce_premium: float | None = None
    exit_pe_premium: float | None = None
    exit_pnl: float | None = None
    status: str = "OPEN"
    exit_reason: str | None = None
    pcr: float | None = None
    selection_reason: str | None = None
    spot_at_entry: float | None = None


def save_trade(trade: OptionTrade):
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO option_trades
        (trade_id, underlying, expiry, strike, ce_security_id, pe_security_id,
         entry_time, entry_ce_premium, entry_pe_premium, entry_credit,
         quantity, lot_size, margin, sl_amount, status, pcr, selection_reason, spot_at_entry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.trade_id, trade.underlying, trade.expiry, trade.strike,
        trade.ce_security_id, trade.pe_security_id,
        trade.entry_time, trade.entry_ce_premium, trade.entry_pe_premium,
        trade.entry_credit, trade.quantity, trade.lot_size,
        trade.margin, trade.sl_amount, trade.status,
        trade.pcr, trade.selection_reason, trade.spot_at_entry
    ))
    conn.commit()
    conn.close()


def close_trade(trade_id: str, exit_time: str, exit_ce: float, exit_pe: float, pnl: float, reason: str):
    conn = get_db()
    conn.execute("""
        UPDATE option_trades SET
        exit_time=?, exit_ce_premium=?, exit_pe_premium=?,
        exit_pnl=?, status='CLOSED', exit_reason=?
        WHERE trade_id=?
    """, (exit_time, exit_ce, exit_pe, pnl, reason, trade_id))
    conn.commit()
    conn.close()


def get_open_trades() -> list[OptionTrade]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM option_trades WHERE status='OPEN'").fetchall()
    conn.close()
    return [_row_to_trade(r) for r in rows]


def get_today_trades() -> list[OptionTrade]:
    today = date.today().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM option_trades WHERE entry_time LIKE ?", (f"{today}%",)
    ).fetchall()
    conn.close()
    return [_row_to_trade(r) for r in rows]


def get_trade_history(limit: int = 100) -> list[OptionTrade]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM option_trades ORDER BY entry_time DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [_row_to_trade(r) for r in rows]


def get_today_pnl() -> float:
    today = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(exit_pnl), 0) FROM option_trades WHERE entry_time LIKE ? AND status='CLOSED'",
        (f"{today}%",)
    ).fetchone()
    conn.close()
    return row[0] if row else 0.0


def get_total_pnl() -> float:
    conn = get_db()
    row = conn.execute("SELECT COALESCE(SUM(exit_pnl), 0) FROM option_trades WHERE status='CLOSED'").fetchone()
    conn.close()
    return row[0] if row else 0.0


def get_win_rate() -> float:
    conn = get_db()
    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN exit_pnl > 0 THEN 1 ELSE 0 END), 0) as wins,
            COUNT(*) as total
        FROM option_trades WHERE status='CLOSED'
    """).fetchone()
    conn.close()
    if row and row[1] > 0:
        return row[0] / row[1] * 100
    return 0.0


def count_today_trades() -> int:
    today = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) FROM option_trades WHERE entry_time LIKE ?", (f"{today}%",)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def _row_to_trade(row) -> OptionTrade:
    return OptionTrade(
        trade_id=row["trade_id"],
        underlying=row["underlying"],
        expiry=row["expiry"],
        strike=row["strike"],
        ce_security_id=row["ce_security_id"],
        pe_security_id=row["pe_security_id"],
        entry_time=row["entry_time"],
        entry_ce_premium=row["entry_ce_premium"],
        entry_pe_premium=row["entry_pe_premium"],
        entry_credit=row["entry_credit"],
        quantity=row["quantity"],
        lot_size=row["lot_size"],
        margin=row["margin"],
        sl_amount=row["sl_amount"],
        exit_time=row["exit_time"],
        exit_ce_premium=row["exit_ce_premium"],
        exit_pe_premium=row["exit_pe_premium"],
        exit_pnl=row["exit_pnl"],
        status=row["status"],
        exit_reason=row["exit_reason"],
        pcr=row["pcr"],
        selection_reason=row["selection_reason"],
        spot_at_entry=row["spot_at_entry"],
    )
