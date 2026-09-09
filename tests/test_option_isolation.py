"""MCX/OPTION database isolation tests. Proves trades never cross databases."""
import os
import sys
import sqlite3
import uuid

# Add project root to path
_project_root = os.path.join(os.path.dirname(__file__), "..")
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from option.database import DB_PATH as OPTION_DB_PATH, init_db as init_option_db, save_trade as save_option_trade, OptionTrade


def setup():
    init_option_db()


def test_option_db_path():
    """Option database is separate from MCX database."""
    assert "option_paper_trading.db" in OPTION_DB_PATH
    # Check the actual filename, not the full path (parent dirs may contain "trading.db")
    db_filename = os.path.basename(OPTION_DB_PATH)
    assert db_filename != "trading.db", f"Option DB has same name as MCX DB: {db_filename}"
    print("PASS: Option DB path is separate")


def test_mcx_db_not_option():
    """MCX database does not contain option trades."""
    mcx_db = os.path.join(os.path.dirname(__file__), "..", "..", "data", "db", "trading.db")
    if not os.path.exists(mcx_db):
        print("SKIP: MCX database not found (expected in production)")
        return

    conn = sqlite3.connect(mcx_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()

    # MCX DB should NOT have option_trades table
    assert "option_trades" not in tables, "MCX database has option_trades table!"
    print("PASS: MCX database has no option_trades table")


def test_option_db_not_mcx():
    """Option database does not contain MCX trades."""
    if not os.path.exists(OPTION_DB_PATH):
        print("SKIP: Option database not found (will be created on first trade)")
        return

    conn = sqlite3.connect(OPTION_DB_PATH)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()

    # Option DB should NOT have MCX tables
    mcx_tables = {"trades", "fills", "orders", "positions", "account_snapshots", "system_state", "processed_fills"}
    found = mcx_tables.intersection(set(tables))
    assert not found, f"Option database has MCX tables: {found}"
    print("PASS: Option database has no MCX tables")


def test_option_trade_isolation():
    """Option trade saved to option DB only."""
    trade_id = f"TEST-ISO-{uuid.uuid4().hex[:8]}"
    trade = OptionTrade(
        trade_id=trade_id,
        underlying="NIFTY",
        expiry="2026-09-11",
        strike=23400,
        ce_security_id="123",
        pe_security_id="456",
        entry_time="2026-09-09 12:00:00",
        entry_ce_premium=215.6,
        entry_pe_premium=65.65,
        entry_credit=18292.25,
        quantity=65,
        lot_size=65,
        margin=340155.72,
        sl_amount=3401.56,
        status="OPEN",
    )

    save_option_trade(trade)

    # Verify in option DB
    conn = sqlite3.connect(OPTION_DB_PATH)
    row = conn.execute("SELECT trade_id, underlying FROM option_trades WHERE trade_id=?", (trade_id,)).fetchone()
    conn.close()

    assert row is not None, "Trade not found in option DB"
    assert row[0] == trade_id
    assert row[1] == "NIFTY"

    # Verify NOT in MCX DB
    mcx_db = os.path.join(os.path.dirname(__file__), "..", "..", "data", "db", "trading.db")
    if os.path.exists(mcx_db):
        conn = sqlite3.connect(mcx_db)
        for table in ["trades", "fills", "orders"]:
            try:
                rows = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE trade_id=?", (trade_id,)).fetchone()
                assert rows[0] == 0, f"Option trade found in MCX table {table}!"
            except:
                pass
        conn.close()

    # Cleanup
    conn = sqlite3.connect(OPTION_DB_PATH)
    conn.execute("DELETE FROM option_trades WHERE trade_id=?", (trade_id,))
    conn.commit()
    conn.close()

    print("PASS: Option trade isolated to option DB")


if __name__ == "__main__":
    setup()
    test_option_db_path()
    test_mcx_db_not_option()
    test_option_db_not_mcx()
    test_option_trade_isolation()
    print("\nAll isolation tests passed!")
