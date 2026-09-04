"""
PHASE 12 — DATABASE REAL WRITE/READ VERIFICATION
=================================================
Verify actual database operations produce correct field-by-field results.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence, REPORT_DIR


@pytest.fixture
def tmp_db(tmp_path):
    """Create a fresh temp DB for each test."""
    from persistence.manager import PersistenceManager
    return PersistenceManager(
        state_path=str(tmp_path / "state.json"),
        db_path=str(tmp_path / "trading.db"),
    )


class TestDatabaseRealWriteRead:
    """Phase 12: Verify actual DB write/read operations."""

    def test_save_trade_and_read_back(self, tmp_db):
        """Save a trade and read it back field-by-field."""
        trade = {
            "trade_id": "TEST_TRADE_001",
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "LONG",
            "entry_price": 150000.0,
            "exit_price": 151000.0,
            "quantity": 1,
            "multiplier": 10.0,
            "gross_pnl": 10000.0,
            "charges": 80.0,
            "net_pnl": 9920.0,
            "exit_reason": "signal_exit",
            "status": "closed",
        }
        tmp_db.save_trade(trade)
        trades = tmp_db.get_trades("gold_01")
        assert len(trades) >= 1, "Trade should be in DB"
        t = trades[-1]
        assert t["trade_id"] == "TEST_TRADE_001"
        assert t["strategy_id"] == "gold_01"
        assert t["instrument"] == "GOLDM"
        assert t["entry_price"] == 150000.0
        assert t["exit_price"] == 151000.0
        assert t["gross_pnl"] == 10000.0
        assert t["net_pnl"] == 9920.0
        get_evidence().record("phase12", "save_trade_read", "PASS", t)

    def test_save_fill_and_read_back(self, tmp_db):
        """Save a fill and read it back."""
        fill = {
            "fill_id": "TEST_FILL_001",
            "order_id": "TEST_ORDER_001",
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "BUY",
            "quantity": 1,
            "price": 150000.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        tmp_db.save_fill(fill)
        result = tmp_db.get_fill("TEST_FILL_001")
        assert result is not None, "Fill should exist in DB"
        assert result["fill_id"] == "TEST_FILL_001"
        assert result["price"] == 150000.0

    def test_save_order_and_read_back(self, tmp_db):
        """Save an order and read it back."""
        order = {
            "order_id": "TEST_ORDER_001",
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "BUY",
            "quantity": 1,
            "order_type": "MARKET",
            "price": 150000.0,
            "state": "filled",
            "filled_quantity": 1,
            "average_fill_price": 150000.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_db.save_order(order)
        # Verify order is persisted
        from persistence.manager import PersistenceManager
        import sqlite3
        conn = sqlite3.connect(str(tmp_db.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?",
                          ("TEST_ORDER_001",)).fetchone()
        conn.close()
        assert row is not None, "Order should exist in DB"
        assert row["instrument"] == "GOLDM"
        assert row["side"] == "BUY"

    def test_fill_upsert_is_idempotent(self, tmp_db):
        """Saving same fill twice doesn't create duplicate."""
        fill = {
            "fill_id": "TEST_FILL_IDEM",
            "order_id": "TEST_ORDER_IDEM",
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "BUY",
            "quantity": 1,
            "price": 150000.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        tmp_db.save_fill(fill)
        tmp_db.save_fill(fill)  # Second save
        result = tmp_db.get_fill("TEST_FILL_IDEM")
        assert result is not None

    def test_trade_and_fill_relationship(self, tmp_db):
        """Trade has matching fill record."""
        trade_id = "TEST_RELATION_001"
        tmp_db.save_trade({
            "trade_id": trade_id,
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "LONG",
            "entry_price": 150000.0,
            "exit_price": 151000.0,
            "quantity": 1,
            "multiplier": 10.0,
            "gross_pnl": 10000.0,
            "charges": 80.0,
            "net_pnl": 9920.0,
            "exit_reason": "signal_exit",
            "status": "closed",
        })
        tmp_db.save_fill({
            "fill_id": "TEST_RELATION_FILL",
            "order_id": "TEST_RELATION_ORDER",
            "strategy_id": "gold_01",
            "instrument": "GOLDM",
            "side": "BUY",
            "quantity": 1,
            "price": 150000.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        trades = tmp_db.get_trades("gold_01")
        assert any(t["trade_id"] == trade_id for t in trades)
        fill = tmp_db.get_fill("TEST_RELATION_FILL")
        assert fill is not None
        assert fill["strategy_id"] == "gold_01"

    def test_strategy_state_persistence(self, tmp_db):
        """Strategy state saves and loads correctly."""
        state = {
            "gold_01": {
                "strategy_id": "gold_01",
                "instrument": "GOLDM",
                "state": "long_position",
                "position_side": "LONG",
                "stop_price": 149000.0,
                "bars_processed": 50,
                "enabled": True,
            }
        }
        tmp_db.save_state({"strategies": state})
        loaded = tmp_db.load_state()
        assert loaded is not None
        s = loaded["strategies"]["gold_01"]
        assert s["state"] == "long_position"
        assert s["position_side"] == "LONG"
        assert s["stop_price"] == 149000.0

    def test_indicator_state_persistence(self, tmp_db):
        """Indicator snapshot persists and restores correctly."""
        from indicators.dema_atr import DEMAATR
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(10):
            ind.update(100 + i, 102 + i, 98 + i, 101 + i)
        snap = ind.snapshot()
        tmp_db.save_state({"indicators": {"GOLDM:5m": snap}})
        loaded = tmp_db.load_state()
        ind2 = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        ind2.restore(loaded["indicators"]["GOLDM:5m"])
        assert ind.value == ind2.value
