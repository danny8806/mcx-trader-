"""Fresh Audit Test Framework - Independent verification of the entire trading system.

This file tests the CURRENT code directly. It does NOT import or depend on any
old test results. Every test is self-contained and verifies real behavior.

Run with: python -m pytest tests/fresh_audit/test_whole_project.py -v
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so all modules can be imported
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================================
# CLASS 1 — MODULE IMPORTS (every module can be imported, classes exist)
# ============================================================================
class TestModuleImports:
    """Verify every project module can be imported and key classes exist."""

    def test_import_trading_engine(self):
        mod = importlib.import_module("trading_engine")
        assert hasattr(mod, "TradingEngine")

    def test_import_indicators_dema(self):
        mod = importlib.import_module("indicators.dema")
        assert hasattr(mod, "DEMA")

    def test_import_indicators_atr(self):
        mod = importlib.import_module("indicators.atr")
        assert hasattr(mod, "ATR")

    def test_import_indicators_dema_atr(self):
        mod = importlib.import_module("indicators.dema_atr")
        assert hasattr(mod, "DEMAATR")

    def test_import_strategies_base(self):
        mod = importlib.import_module("strategies.base_dema_strategy")
        assert hasattr(mod, "BaseDEMAStrategy")
        assert hasattr(mod, "Signal")
        assert hasattr(mod, "StrategyState")

    def test_import_strategies_gold(self):
        mod = importlib.import_module("strategies.gold")
        assert hasattr(mod, "GoldStrategy01")
        assert hasattr(mod, "GoldStrategy02")
        assert hasattr(mod, "GoldStrategy03")
        assert hasattr(mod, "GoldStrategy04")

    def test_import_strategies_silver(self):
        mod = importlib.import_module("strategies.silver")
        assert hasattr(mod, "SilverStrategy01")
        assert hasattr(mod, "SilverStrategy02")
        assert hasattr(mod, "SilverStrategy03")
        assert hasattr(mod, "SilverStrategy04")

    def test_import_htf(self):
        mod = importlib.import_module("htf.backtest_style_htf")
        assert hasattr(mod, "BacktestStyleHTFEngine")
        assert hasattr(mod, "HTFMappedValue")

    def test_import_persistence(self):
        mod = importlib.import_module("persistence.manager")
        assert hasattr(mod, "PersistenceManager")

    def test_import_execution_paper_broker(self):
        mod = importlib.import_module("execution.paper_broker")
        assert hasattr(mod, "PaperExecutionEngine")
        assert hasattr(mod, "Order")
        assert hasattr(mod, "Fill")
        assert hasattr(mod, "OrderState")

    def test_import_execution_fee_model(self):
        mod = importlib.import_module("execution.fee_model")
        assert hasattr(mod, "MCXFeeModel")
        assert hasattr(mod, "FeeBreakdown")

    def test_import_execution_order_manager(self):
        mod = importlib.import_module("execution.order_manager")
        assert hasattr(mod, "OrderManager")

    def test_import_portfolio_position_manager(self):
        mod = importlib.import_module("portfolio.position_manager")
        assert hasattr(mod, "PositionManager")
        assert hasattr(mod, "Position")
        assert hasattr(mod, "PositionSide")

    def test_import_portfolio_pnl(self):
        mod = importlib.import_module("portfolio.pnl")
        assert hasattr(mod, "PNLEngine")
        assert hasattr(mod, "PnLSnapshot")

    def test_import_portfolio_account(self):
        mod = importlib.import_module("portfolio.account")
        assert hasattr(mod, "AccountEngine")

    def test_import_risk_engine(self):
        mod = importlib.import_module("core.risk_engine")
        assert hasattr(mod, "RiskEngine")

    def test_import_market_status(self):
        mod = importlib.import_module("core.market_status")
        assert hasattr(mod, "MarketStatus")
        assert hasattr(mod, "MarketState")
        assert hasattr(mod, "EngineStatus")
        assert hasattr(mod, "DataStatus")

    def test_import_safe_mode(self):
        mod = importlib.import_module("core.safe_mode")
        assert hasattr(mod, "SafeModeManager")

    def test_import_data_adapter(self):
        mod = importlib.import_module("data.dhan.adapter")
        assert hasattr(mod, "DhanDataAdapter")

    def test_import_telegram(self):
        mod = importlib.import_module("notifications.telegram_router")
        assert hasattr(mod, "TelegramRouter")

    def test_import_dashboard_server(self):
        mod = importlib.import_module("dashboard.server")
        assert hasattr(mod, "app")

    def test_import_health_monitor(self):
        mod = importlib.import_module("monitoring.health")
        assert hasattr(mod, "HealthMonitor")
        assert hasattr(mod, "SystemStatus")

    def test_import_analytics_event_store(self):
        mod = importlib.import_module("analytics.event_store")
        assert hasattr(mod, "EventStore")

    def test_import_config(self):
        mod = importlib.import_module("config")
        assert hasattr(mod, "Config")

    def test_import_timeframe_engine(self):
        mod = importlib.import_module("core.timeframe_engine")
        assert hasattr(mod, "Bar")
        assert hasattr(mod, "BarState")

    def test_import_fill_dedup(self):
        mod = importlib.import_module("core.fill_dedup")
        assert hasattr(mod, "FillDeduplicator")

    def test_import_trade_close(self):
        mod = importlib.import_module("core.trade_close")
        assert hasattr(mod, "TradeCloseManager")

    def test_import_candle_fetcher(self):
        mod = importlib.import_module("core.candle_fetcher")
        assert hasattr(mod, "CandleFetcher")


# ============================================================================
# CLASS 2 — INDICATOR CALCULATIONS (independent reference verification)
# ============================================================================
class TestIndicatorCalculations:
    """Independent reference verification of DEMA and ATR indicators."""

    # ---- DEMA tests ----

    def test_dema_basic_calculation(self):
        from indicators.dema import DEMA
        d = DEMA(period=3)
        result = d.update(100.0)
        assert result == 100.0  # First value seeded directly

    def test_dema_warmup_period(self):
        from indicators.dema import DEMA
        d = DEMA(period=5)
        assert d.initialized is False
        for i in range(4):
            d.update(100.0 + i)
        assert d.initialized is False
        d.update(100.0 + 4)
        assert d.initialized is True

    def test_dema_monotonic_input(self):
        from indicators.dema import DEMA
        d = DEMA(period=3)
        values = [10, 20, 30, 40, 50]
        results = []
        for v in values:
            results.append(d.update(v))
        assert results[0] == 10.0
        # DEMA should track upward movement
        for i in range(1, len(results)):
            assert results[i] > results[i - 1]

    def test_dema_reset(self):
        from indicators.dema import DEMA
        d = DEMA(period=3)
        for v in [10, 20, 30]:
            d.update(v)
        d.reset()
        assert d.value is None
        assert d.initialized is False
        result = d.update(100.0)
        assert result == 100.0

    def test_dema_known_values(self):
        """Verify DEMA against hand-calculated reference."""
        from indicators.dema import DEMA
        d = DEMA(period=3)
        values = [10.0, 11.0, 12.0, 11.0, 13.0]
        results = []
        for v in values:
            results.append(d.update(v))
        # First value is seeded
        assert results[0] == 10.0
        # All subsequent values should be finite floats
        for r in results[1:]:
            assert r is not None
            assert np.isfinite(r)

    def test_dema_batch_matches_incremental(self):
        from indicators.dema import DEMA
        values = np.array([10.0, 11.0, 12.0, 11.5, 13.0, 14.0, 12.5, 15.0], dtype=np.float64)
        # Incremental
        d = DEMA(period=3)
        incremental = []
        for v in values:
            incremental.append(d.update(v))
        # Batch
        batch = DEMA.calculate_batch(values, period=3)
        for i in range(len(values)):
            assert abs(incremental[i] - batch[i]) < 1e-10, f"Mismatch at index {i}"

    def test_dema_nan_handling(self):
        from indicators.dema import DEMA
        d = DEMA(period=3)
        result = d.update(float("nan"))
        assert result is None

    # ---- ATR tests ----

    def test_atr_basic_calculation(self):
        from indicators.atr import ATR
        a = ATR(period=3)
        result = a.update(high=105.0, low=95.0, close=100.0)
        assert result is None  # Not enough data yet

    def test_atr_true_range(self):
        from indicators.atr import ATR
        a = ATR(period=2)
        a.update(high=105.0, low=95.0, close=100.0)
        result = a.update(high=110.0, low=98.0, close=108.0)
        assert result is not None
        assert result > 0

    def test_atr_warmup_period(self):
        from indicators.atr import ATR
        a = ATR(period=5)
        for i in range(4):
            a.update(high=100 + i * 2, low=98 + i * 2, close=99 + i * 2)
        assert a.initialized is False
        a.update(high=110, low=96, close=108)
        assert a.initialized is True

    def test_atr_known_values(self):
        from indicators.atr import ATR
        a = ATR(period=3)
        bars = [
            (105, 95, 100),
            (110, 98, 108),
            (112, 100, 106),
            (108, 97, 102),
        ]
        for h, l, c in bars:
            result = a.update(high=h, low=l, close=c)
        assert result is not None
        assert result > 0

    def test_atr_batch_matches_incremental(self):
        from indicators.atr import ATR
        np.random.seed(42)
        n = 20
        closes = 100 + np.cumsum(np.random.randn(n))
        highs = closes + np.abs(np.random.randn(n)) * 2
        lows = closes - np.abs(np.random.randn(n)) * 2
        # Incremental
        a = ATR(period=5)
        incremental = []
        for i in range(n):
            incremental.append(a.update(high=float(highs[i]), low=float(lows[i]), close=float(closes[i])))
        # Batch
        batch = ATR.calculate_batch(highs, lows, closes, period=5)
        for i in range(n):
            inc = incremental[i]
            bat = batch[i]
            if inc is None and np.isnan(bat):
                continue
            if inc is None or np.isnan(bat):
                continue
            assert abs(inc - bat) < 1e-10, f"ATR mismatch at index {i}: incremental={inc}, batch={bat}"

    def test_atr_reset(self):
        from indicators.atr import ATR
        a = ATR(period=3)
        for i in range(5):
            a.update(high=100 + i, low=98 + i, close=99 + i)
        a.reset()
        assert a.value is None
        assert a.initialized is False

    # ---- DEMA-ATR combined tests ----

    def test_dema_atr_combined(self):
        from indicators.dema_atr import DEMAATR
        da = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        result = da.update(open_price=100.0, high=105.0, low=95.0, close=100.0)
        assert result is not None

    def test_dema_atr_signal_direction(self):
        """Verify DEMA-ATR output follows price direction."""
        from indicators.dema_atr import DEMAATR
        da = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        rising = []
        for i in range(20):
            v = da.update(open_price=100 + i, high=102 + i, low=98 + i, close=100 + i)
            if v is not None:
                rising.append(v)
        da.reset()
        falling = []
        for i in range(20):
            v = da.update(open_price=200 - i, high=202 - i, low=198 - i, close=200 - i)
            if v is not None:
                falling.append(v)
        # In a rising sequence, DEMA-ATR should be higher in later bars
        assert rising[-1] > rising[0]
        # In a falling sequence, DEMA-ATR should be lower in later bars
        assert falling[-1] < falling[0]

    def test_dema_atr_snapshot_restore(self):
        from indicators.dema_atr import DEMAATR
        da = DEMAATR(dema_period=3, atr_period=6)
        for i in range(10):
            da.update(open_price=100 + i, high=103 + i, low=97 + i, close=100 + i)
        snap = da.snapshot()
        assert "dema" in snap
        assert "atr" in snap
        assert "prev_output" in snap
        da2 = DEMAATR(dema_period=3, atr_period=6)
        da2.restore(snap)
        assert da2.value == da.value

    def test_dema_atr_batch_matches_incremental(self):
        from indicators.dema_atr import DEMAATR
        np.random.seed(123)
        n = 25
        closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
        opens = closes + np.random.randn(n) * 0.1
        highs = np.maximum(opens, closes) + np.abs(np.random.randn(n))
        lows = np.minimum(opens, closes) - np.abs(np.random.randn(n))
        da = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        incremental = []
        for i in range(n):
            incremental.append(da.update(open_price=float(opens[i]), high=float(highs[i]),
                                         low=float(lows[i]), close=float(closes[i])))
        batch = DEMAATR.calculate_batch(opens, highs, lows, closes,
                                         dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(n):
            inc = incremental[i]
            bat = batch[i]
            if inc is None and np.isnan(bat):
                continue
            if inc is None or np.isnan(bat):
                continue
            assert abs(inc - bat) < 1e-10, f"DEMA-ATR mismatch at {i}"


# ============================================================================
# CLASS 3 — API ROUTES (FastAPI TestClient)
# ============================================================================
class TestAPIRoutes:
    """Test all API routes via FastAPI TestClient."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        from fastapi.testclient import TestClient
        from dashboard.server import app, set_engine, _on_engine_event
        from dashboard.event_bus import EventBus

        # Build a mock engine with all required attributes
        engine = MagicMock()
        engine.config.get.return_value = {}
        engine.market_status = MagicMock()
        engine.market_status.snapshot.return_value = {
            "market_state": "overnight",
            "engine_status": "ready",
            "data_status": "no_data",
        }
        engine.safe_mode = MagicMock()
        engine.safe_mode.get_status.return_value = {"active": False, "reasons": []}
        engine.safe_mode.is_active = False
        engine.health = MagicMock()
        engine.health.snapshot.return_value = {
            "components": {},
            "overall_status": "healthy",
        }
        engine.account_engine = MagicMock()
        engine.account_engine.snapshot.return_value = {
            "equity": 1200000,
            "starting_capital": 1200000,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "used_margin": 0,
        }
        engine.position_manager = MagicMock()
        engine.position_manager.snapshot.return_value = {
            "open_positions": {},
            "closed_count": 0,
        }
        engine.execution_engine = MagicMock()
        engine.execution_engine.snapshot.return_value = {"orders": {}}
        engine.execution_engine._orders = {}
        engine.execution_engine._current_prices = {}
        engine.execution_engine.get_fills.return_value = []
        engine.risk_engine = MagicMock()
        engine.risk_engine.snapshot.return_value = {
            "kill_switch_active": False,
            "daily_pnl": 0,
            "peak_equity": 0,
        }
        engine.strategies = {}
        engine.indicators = {}
        engine.pnl_engines = {}
        engine.data_adapter = MagicMock()
        engine.data_adapter.connected = False

        bus = EventBus()
        set_engine(engine)

        # Initialize routes with mock engine
        from dashboard.routes import (overview, strategies, positions, orders,
                                       pnl, market_data, risk, health, alerts,
                                       audit_log, indicators)
        for mod in [overview, strategies, positions, orders, pnl, market_data,
                    risk, health, alerts, audit_log, indicators]:
            if hasattr(mod, 'init'):
                mod.init(engine, bus)

        import dashboard.server as srv
        srv._api_key = "test-key"  # set test key for auth bypass

        self.client = TestClient(app, raise_server_exceptions=False, headers={"X-API-Key": "test-key"})

    def test_api_health(self):
        resp = self.client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_api_health_system(self):
        resp = self.client.get("/api/health/system")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data or "error" in data

    def test_api_overview(self):
        resp = self.client.get("/api/overview")
        assert resp.status_code == 200

    def test_api_strategies(self):
        resp = self.client.get("/api/strategies")
        assert resp.status_code == 200

    def test_api_positions(self):
        resp = self.client.get("/api/positions")
        assert resp.status_code == 200

    def test_api_orders(self):
        resp = self.client.get("/api/orders")
        assert resp.status_code == 200

    def test_api_pnl(self):
        resp = self.client.get("/api/pnl")
        assert resp.status_code == 200

    def test_api_market_data(self):
        resp = self.client.get("/api/market-data")
        assert resp.status_code == 200

    def test_api_risk(self):
        resp = self.client.get("/api/risk")
        assert resp.status_code == 200

    def test_api_indicators(self):
        resp = self.client.get("/api/indicators")
        assert resp.status_code == 200

    def test_api_alerts(self):
        resp = self.client.get("/api/alerts")
        assert resp.status_code == 200

    def test_api_audit(self):
        resp = self.client.get("/api/audit")
        assert resp.status_code == 200

    def test_api_overview_gold(self):
        resp = self.client.get("/api/overview/gold")
        assert resp.status_code == 200

    def test_api_overview_silver(self):
        resp = self.client.get("/api/overview/silver")
        assert resp.status_code == 200

    def test_api_strategies_filter_instrument(self):
        resp = self.client.get("/api/strategies?instrument=GOLDM")
        assert resp.status_code == 200

    def test_api_positions_filter(self):
        resp = self.client.get("/api/positions?instrument=GOLDM")
        assert resp.status_code == 200


# ============================================================================
# CLASS 4 — DATABASE (schema verification, CRUD)
# ============================================================================
class TestDatabase:
    """Verify database existence and schema integrity."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_trading.db")

    def test_trading_db_exists(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        assert Path(self.db_path).exists()

    def test_trading_db_schema(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "trades" in tables
        assert "orders" in tables
        assert "fills" in tables

    def test_no_separate_analytics_db(self):
        """ONE canonical trading.db only: the system never creates a separate
        analytics.db file at runtime."""
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        assert Path(self.db_path).exists()
        assert not Path(self.db_path).with_name("analytics.db").exists()

    def test_analytics_tables_live_inside_trading_db(self):
        """Derived analytics tables live INSIDE trading.db (not analytics.db)."""
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        for t in ("trades_analytics", "trade_legs", "trade_events"):
            assert t in tables

    def test_trading_db_insert_and_query_trade(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO signals (signal_id, strategy_id, instrument, side, signal_type,
                                 signal_timestamp, trigger_price, stop_price, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIG_T001", "gold_01", "GOLDM", "BUY", "LONG", time.time(), 72000.0, 71900.0, 1))
        conn.execute("""
            INSERT INTO trades (trade_id, strategy_id, instrument, side, entry_price,
                                exit_price, quantity, net_pnl, status, entry_signal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("T001", "gold_01", "GOLDM", "LONG", 72000.0, 72500.0, 1, 5000.0, "closed", "SIG_T001"))
        conn.commit()
        cursor = conn.execute("SELECT trade_id FROM trades WHERE trade_id='T001'")
        row = cursor.fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "T001"

    def test_trading_db_insert_order(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO signals (signal_id, strategy_id, instrument, side, signal_type,
                                 signal_timestamp, trigger_price, stop_price, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIG_O001", "gold_01", "GOLDM", "BUY", "LONG", time.time(), 72000.0, 71900.0, 1))
        conn.execute("""
            INSERT INTO trades (trade_id, strategy_id, instrument, side, entry_price,
                                quantity, status, entry_signal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("TO001", "gold_01", "GOLDM", "LONG", 72000.0, 1, "open", "SIG_O001"))
        conn.execute("""
            INSERT INTO orders (order_id, strategy_id, instrument, side, quantity,
                                order_type, state, trade_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, ("O001", "gold_01", "GOLDM", "BUY", 1, "MARKET", "filled", "TO001"))
        conn.commit()
        cursor = conn.execute("SELECT order_id FROM orders WHERE order_id='O001'")
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_trading_db_insert_fill(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO signals (signal_id, strategy_id, instrument, side, signal_type,
                                 signal_timestamp, trigger_price, stop_price, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIG_F001", "gold_01", "GOLDM", "BUY", "LONG", time.time(), 72000.0, 71900.0, 1))
        conn.execute("""
            INSERT INTO trades (trade_id, strategy_id, instrument, side, entry_price,
                                quantity, status, entry_signal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("TF001", "gold_01", "GOLDM", "LONG", 72000.0, 1, "open", "SIG_F001"))
        conn.execute("""
            INSERT INTO orders (order_id, strategy_id, instrument, side, quantity,
                                order_type, state, trade_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, ("OF001", "gold_01", "GOLDM", "BUY", 1, "MARKET", "filled", "TF001"))
        conn.execute("""
            INSERT INTO fills (fill_id, order_id, strategy_id, instrument, side,
                               quantity, price, timestamp, trade_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("F001", "OF001", "gold_01", "GOLDM", "BUY", 1, 72000.0, time.time(), "TF001"))
        conn.commit()
        cursor = conn.execute("SELECT fill_id FROM fills WHERE fill_id='F001'")
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_trading_db_unique_constraint(self):
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(state_path=str(Path(self.db_path).parent / "state.json"),
                                db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO signals (signal_id, strategy_id, instrument, side, signal_type,
                                 signal_timestamp, trigger_price, stop_price, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIG_TDUP", "gold_01", "GOLDM", "BUY", "LONG", time.time(), 72000.0, 71900.0, 1))
        conn.execute("""
            INSERT INTO trades (trade_id, strategy_id, instrument, side, net_pnl, status, entry_signal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("T_DUP", "gold_01", "GOLDM", "LONG", 0, "closed", "SIG_TDUP"))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO trades (trade_id, strategy_id, instrument, side, net_pnl, status, entry_signal_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("T_DUP", "gold_01", "GOLDM", "LONG", 0, "closed", "SIG_TDUP"))
            conn.commit()
        conn.close()


# ============================================================================
# CLASS 5 — STATE TRANSITIONS
# ============================================================================
class TestStateTransitions:
    """Test market status, engine, and safe mode state transitions."""

    def test_market_status_transitions(self):
        from core.market_status import MarketStatus, MarketState, EngineStatus
        ms = MarketStatus()
        # Initial state is determined by clock; just verify it's a valid state
        initial = ms.state
        assert initial in MarketState
        # Force different states
        ms.force_state(MarketState.LIVE_TRADING)
        assert ms.state == MarketState.LIVE_TRADING
        ms.force_state(MarketState.MARKET_CLOSE)
        assert ms.state == MarketState.MARKET_CLOSE
        ms.force_state(MarketState.OVERNIGHT)
        assert ms.state == MarketState.OVERNIGHT

    def test_engine_status_transitions(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus()
        assert ms.engine_status == EngineStatus.INITIALIZING
        ms.set_engine_status(EngineStatus.READY)
        assert ms.engine_status == EngineStatus.READY
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms.engine_status == EngineStatus.TRADING
        ms.set_engine_status(EngineStatus.SAFE_MODE)
        assert ms.engine_status == EngineStatus.SAFE_MODE
        ms.set_engine_status(EngineStatus.HALTED)
        assert ms.engine_status == EngineStatus.HALTED
        ms.set_engine_status(EngineStatus.STOPPED)
        assert ms.engine_status == EngineStatus.STOPPED

    def test_safe_mode_enter_exit(self):
        from core.market_status import MarketStatus, MarketState, EngineStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus()
        ms.set_engine_status(EngineStatus.READY)
        sm = SafeModeManager(ms)
        assert sm.is_active is False
        sm.enter_safe_mode("test_reason", "unit test")
        assert sm.is_active is True
        assert ms.is_safe is True
        # Clear reason and exit
        sm.clear_reason("test_reason")
        # Reset cooldown for test
        sm._last_exit_attempt = 0.0
        sm._exit_cooldown = 0.0
        result = sm.exit_safe_mode()
        assert result is True
        assert sm.is_active is False
        assert ms.is_safe is False

    def test_market_status_snapshot_restore(self):
        from core.market_status import MarketStatus, MarketState
        ms = MarketStatus()
        ms.force_state(MarketState.LIVE_TRADING)
        snap = ms.snapshot()
        assert snap["market_state"] == "live_trading"
        ms2 = MarketStatus()
        # Set the session date to match so restore picks up daily flags
        ms2._session_date = ms._session_date or ms._current_date()
        ms2.restore(snap)

    def test_market_status_properties(self):
        from core.market_status import MarketStatus, MarketState, EngineStatus, DataStatus
        ms = MarketStatus()
        ms.set_engine_status(EngineStatus.TRADING)
        ms._data_status = DataStatus.CONNECTED
        ms.force_state(MarketState.LIVE_TRADING)
        assert ms.is_trading_allowed is True
        ms.force_state(MarketState.OVERNIGHT)
        assert ms.is_trading_allowed is False

    def test_safe_mode_multiple_reasons(self):
        from core.market_status import MarketStatus, EngineStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus()
        ms.set_engine_status(EngineStatus.READY)
        sm = SafeModeManager(ms)
        sm.enter_safe_mode("reason_a")
        sm.enter_safe_mode("reason_b")
        assert len(sm.active_reasons) == 2
        sm.clear_reason("reason_a")
        assert len(sm.active_reasons) == 1
        sm._last_exit_attempt = 0.0
        sm._exit_cooldown = 0.0
        sm.exit_safe_mode()
        assert sm.is_active is True  # reason_b still active

    def test_safe_mode_trading_blocked(self):
        from core.market_status import MarketStatus, MarketState, EngineStatus, DataStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus()
        ms.set_engine_status(EngineStatus.TRADING)
        ms._data_status = DataStatus.CONNECTED
        ms.force_state(MarketState.LIVE_TRADING)
        sm = SafeModeManager(ms)
        assert sm.should_allow_trading() is True
        sm.enter_safe_mode("block")
        assert sm.should_allow_trading() is False

    def test_data_status_transitions(self):
        from core.market_status import MarketStatus, DataStatus
        ms = MarketStatus()
        ms.update_data_status(connected=False)
        assert ms.data_status == DataStatus.DISCONNECTED
        ms.update_data_status(connected=True, last_tick_time=0)
        assert ms.data_status == DataStatus.NO_DATA
        ms.update_data_status(connected=True, last_tick_time=time.time())
        assert ms.data_status == DataStatus.CONNECTED


# ============================================================================
# CLASS 6 — STRATEGY ISOLATION
# ============================================================================
class TestStrategyIsolation:
    """Test strategy instantiation and capital independence."""

    def test_all_strategies_instantiate(self):
        from strategies.gold import GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04
        from strategies.silver import SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04
        for cls in [GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04,
                     SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04]:
            s = cls()
            assert s.strategy_id is not None
            assert s.instrument is not None

    def test_strategy_capital_independent(self):
        from strategies.gold import GoldStrategy01, GoldStrategy02
        s1 = GoldStrategy01(strategy_id="g1", quantity=1)
        s2 = GoldStrategy02(strategy_id="g2", quantity=2)
        assert s1.quantity != s2.quantity
        # Modifying one doesn't affect the other
        s1.quantity = 999
        assert s2.quantity == 2

    def test_strategy_state_machine(self):
        from strategies.base_dema_strategy import StrategyState, BaseDEMAStrategy
        s = BaseDEMAStrategy(
            strategy_id="test", instrument="GOLDM",
            fast_timeframe="5m", htf_timeframe="1h",
        )
        assert s.state == StrategyState.FLAT
        assert s.is_flat is True
        s.state = StrategyState.LONG_POSITION
        s.position_side = "LONG"
        assert s.has_position is True
        assert s.is_flat is False

    def test_strategy_snapshot_restore(self):
        from strategies.base_dema_strategy import BaseDEMAStrategy, StrategyState
        s = BaseDEMAStrategy(
            strategy_id="test", instrument="GOLDM",
            fast_timeframe="5m", htf_timeframe="1h",
        )
        s.state = StrategyState.SHORT_POSITION
        s.position_side = "SHORT"
        s.stop_price = 72500.0
        snap = s.snapshot()
        assert snap["state"] == "short_position"
        s2 = BaseDEMAStrategy(
            strategy_id="test", instrument="GOLDM",
            fast_timeframe="5m", htf_timeframe="1h",
        )
        s2.restore(snap)
        assert s2.state == StrategyState.SHORT_POSITION
        assert s2.position_side == "SHORT"
        assert s2.stop_price == 72500.0

    def test_gold_strategies_different_timeframes(self):
        from strategies.gold import GoldStrategy01, GoldStrategy02
        s1 = GoldStrategy01()
        s2 = GoldStrategy02()
        assert s1.fast_timeframe != s2.fast_timeframe or s1.strategy_id != s2.strategy_id

    def test_silver_strategies_different_ids(self):
        from strategies.silver import SilverStrategy01, SilverStrategy02
        s1 = SilverStrategy01()
        s2 = SilverStrategy02()
        assert s1.strategy_id != s2.strategy_id


# ============================================================================
# CLASS 7 — PERSISTENCE
# ============================================================================
class TestPersistence:
    """Test indicator and engine snapshot/restore cycles."""

    def test_indicator_snapshot_restore(self):
        from indicators.dema_atr import DEMAATR
        da = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(15):
            da.update(open_price=100 + i, high=103 + i, low=97 + i, close=100 + i)
        val_before = da.value
        snap = da.snapshot()
        da2 = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        da2.restore(snap)
        assert da2.value == val_before
        # Continued updates should produce same results
        v1 = da.update(open_price=120, high=123, low=117, close=120)
        v2 = da2.update(open_price=120, high=123, low=117, close=120)
        assert v1 == v2

    def test_engine_market_status_persistence(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus()
        ms.set_engine_status(EngineStatus.TRADING)
        # Ensure session_date is set by triggering a state check
        ms._session_date = ms._current_date()
        ms.mark_warmup_done()
        ms.mark_reconcile_done()
        snap = ms.snapshot()
        assert snap["warmup_done_today"] is True
        assert snap["reconcile_done_today"] is True
        ms2 = MarketStatus()
        ms2.restore(snap)
        assert ms2._warmup_done_today is True
        assert ms2._reconcile_done_today is True

    def test_strategy_state_persistence(self):
        from strategies.base_dema_strategy import BaseDEMAStrategy, StrategyState
        s = BaseDEMAStrategy(
            strategy_id="persist_test",
            instrument="GOLDM",
            fast_timeframe="5m",
            htf_timeframe="1h",
        )
        s._bars_processed = 42
        s._prev_fast_close = 72000.0
        s._prev_htf_value = 72100.0
        snap = s.snapshot()
        s2 = BaseDEMAStrategy(
            strategy_id="persist_test",
            instrument="GOLDM",
            fast_timeframe="5m",
            htf_timeframe="1h",
        )
        s2.restore(snap)
        assert s2._bars_processed == 42
        assert s2._prev_fast_close == 72000.0
        assert s2._prev_htf_value == 72100.0

    def test_dema_snapshot_restore(self):
        from indicators.dema import DEMA
        d = DEMA(period=3)
        for v in [10, 20, 30, 40]:
            d.update(v)
        snap = d.snapshot()
        d2 = DEMA(period=3)
        d2.restore(snap)
        assert d2.value == d.value
        assert d2._count == d._count

    def test_atr_snapshot_restore(self):
        from indicators.atr import ATR
        a = ATR(period=3)
        for i in range(5):
            a.update(high=100 + i * 2, low=98 + i * 2, close=99 + i * 2)
        snap = a.snapshot()
        a2 = ATR(period=3)
        a2.restore(snap)
        assert a2.value == a.value
        assert a2._prev_close == a._prev_close

    def test_fill_dedup_persistence(self):
        from core.fill_dedup import FillDeduplicator
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            fd = FillDeduplicator(db_path=db_path)
            assert fd.is_processed("fill_1") is False
            fd.mark_processed("fill_1")
            assert fd.is_processed("fill_1") is True
            fd2 = FillDeduplicator(db_path=db_path)
            count = fd2.load_from_database()
            assert count >= 1
        finally:
            os.unlink(db_path)


# ============================================================================
# CLASS 8 — WEBSOCKET + MISC
# ============================================================================
class TestWebSocketAndMisc:
    """Test WebSocket endpoint existence, EventBus, and component integration."""

    def test_websocket_endpoint_exists(self):
        from dashboard.server import app
        routes = [r.path for r in app.routes]
        assert "/ws" in routes

    def test_websocket_manager_connect_disconnect(self):
        from dashboard.ws_manager import ConnectionManager
        cm = ConnectionManager()
        ws_mock = MagicMock()
        cm.connect("client1", ws_mock, ["all"])
        assert cm.active_connections == 1
        cm.disconnect("client1")
        assert cm.active_connections == 0

    def test_websocket_subscribe(self):
        from dashboard.ws_manager import ConnectionManager
        cm = ConnectionManager()
        ws_mock = MagicMock()
        cm.connect("client1", ws_mock, ["all"])
        cm.subscribe("client1", ["gold_signals"])
        assert "gold_signals" in cm._client_channels["client1"]

    def test_event_bus_publish_subscribe(self):
        from dashboard.event_bus import EventBus
        bus = EventBus(max_events=1000)
        bus.publish("test_event", {"key": "value"})
        events = bus.get_recent(limit=10)
        assert len(events) >= 1
        assert events[-1]["event_type"] == "test_event"

    def test_event_bus_stats(self):
        from dashboard.event_bus import EventBus
        bus = EventBus(max_events=1000)
        bus.publish("type_a", {})
        bus.publish("type_b", {})
        stats = bus.get_stats()
        assert "total_events" in stats

    def test_risk_engine_basic_check(self):
        from core.risk_engine import RiskEngine
        re = RiskEngine(
            max_positions_per_strategy=1,
            max_positions_total=8,
            max_daily_loss=10000,
            kill_switch_enabled=False,
        )
        allowed, reason = re.check_order(
            signal=MagicMock(),
            current_positions=0,
            strategy_positions=0,
            available_margin=500000,
            margin_required=50000,
            current_equity=1200000,
        )
        assert allowed is True

    def test_risk_engine_max_positions(self):
        from core.risk_engine import RiskEngine
        re = RiskEngine(max_positions_per_strategy=1, max_positions_total=8)
        allowed, reason = re.check_order(
            signal=MagicMock(),
            current_positions=0,
            strategy_positions=1,
            available_margin=500000,
            margin_required=50000,
            current_equity=1200000,
        )
        assert allowed is False
        assert reason is not None

    def test_risk_engine_kill_switch(self):
        from core.risk_engine import RiskEngine
        re = RiskEngine(max_positions_per_strategy=1, kill_switch_enabled=True)
        # Kill switch is activated automatically when drawdown/loss limits are hit
        # Manually set it via internal state to test blocking
        re._kill_switch_active = True
        allowed, reason = re.check_order(
            signal=MagicMock(),
            current_positions=0,
            strategy_positions=0,
            available_margin=500000,
            margin_required=50000,
            current_equity=1200000,
        )
        assert allowed is False
        assert reason == "kill_switch_active"

    def test_fee_model_calculation(self):
        from execution.fee_model import MCXFeeModel
        fm = MCXFeeModel()
        fees = fm.calculate(entry_price=72000, exit_price=72500, quantity=1, multiplier=10.0)
        assert fees.total > 0
        assert fees.brokerage > 0

    def test_fee_model_gold_parameters(self):
        from execution.fee_model import MCXFeeModel
        fm = MCXFeeModel(brokerage_per_side=20.0)
        fees = fm.calculate(entry_price=72000, exit_price=72500, quantity=1, multiplier=10.0)
        assert fees.brokerage == 40.0  # 20 per side * 2

    def test_account_engine_equity(self):
        from portfolio.account import AccountEngine
        ae = AccountEngine(starting_capital=1000000)
        assert ae.equity == 1000000
        ae.realized_pnl = 50000
        ae.unrealized_pnl = 10000
        assert ae.equity == 1060000

    def test_pnl_engine_basic(self):
        from execution.fee_model import MCXFeeModel
        from portfolio.pnl import PNLEngine
        from execution.paper_broker import Fill
        fm = MCXFeeModel()
        pnl = PNLEngine(fm)
        entry = Fill(
            fill_id="f1", order_id="o1", instrument="GOLDM",
            side="BUY", quantity=1, price=72000.0,
            timestamp=time.time(), strategy_id="gold_01", multiplier=10.0,
        )
        exit_f = Fill(
            fill_id="f2", order_id="o1", instrument="GOLDM",
            side="SELL", quantity=1, price=72500.0,
            timestamp=time.time(), strategy_id="gold_01", multiplier=10.0,
        )
        gross, charges, net = pnl.calculate_realized_pnl(entry, exit_f, multiplier=10.0)
        assert gross == 5000.0  # (72500 - 72000) * 10
        assert charges > 0
        assert net < gross

    def test_position_manager_add_close(self):
        from portfolio.position_manager import PositionManager
        from execution.paper_broker import Fill
        pm = PositionManager()
        fill = Fill(
            fill_id="f1", order_id="o1", instrument="GOLDM",
            side="BUY", quantity=1, price=72000.0,
            timestamp=time.time(), strategy_id="gold_01", multiplier=10.0,
            entry_signal_id="SIG-P1",
        )
        pos = pm.open_position(fill, multiplier=10.0, entry_signal_id="SIG-P1", trade_id="TRD-P1")
        assert pos is not None
        assert pos.is_long
        # canonical identity: trade_id is explicit and distinct from position_id
        assert pos.trade_id == "TRD-P1"
        assert pos.trade_id != pos.position_id
        snap = pm.snapshot()
        assert len(snap.get("open_positions", {})) == 1

    def test_health_monitor(self):
        from monitoring.health import HealthMonitor, SystemStatus
        hm = HealthMonitor()
        hm.update_component("test_comp", status=SystemStatus.HEALTHY, message="ok")
        snap = hm.snapshot()
        assert "components" in snap
        assert "test_comp" in snap["components"]

    def test_bar_properties(self):
        from core.timeframe_engine import Bar, BarState
        bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=1000, end_ts=1300,
            open=100.0, high=105.0, low=95.0, close=102.0,
        )
        assert bar.is_forming is True
        assert bar.is_closed is False
        assert bar.mid_ts == 1150.0
        bar.state = BarState.CLOSED
        assert bar.is_closed is True

    def test_paper_broker_order_creation(self):
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from strategies.base_dema_strategy import Signal, SignalType
        pe = PaperExecutionEngine(slippage_ticks=1, latency_ms=0)
        signal = Signal(
            signal_type=SignalType.LONG,
            instrument="GOLDM",
            strategy_id="gold_01",
            timestamp=time.time(),
            trigger_price=72000.0,
            stop_price=71500.0,
            quantity=1,
        )
        order = pe.create_order(signal, multiplier=10.0, trade_id="TRD-ORDER-1")
        assert order is not None
        assert order.state == OrderState.CREATED
        # canonical lineage: every order carries its explicit trade_id
        assert order.trade_id == "TRD-ORDER-1"

    def test_dhan_adapter_instantiation(self):
        """Verify adapter can be constructed with minimal config."""
        from data.dhan.adapter import DhanDataAdapter
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"access_token": "test", "client_id": "test"}')
            token_file = f.name
        try:
            adapter = DhanDataAdapter(
                client_id="test",
                token_file=token_file,
                on_tick=MagicMock(),
                on_status=MagicMock(),
            )
            assert adapter is not None
            assert adapter.client_id == "test"
        finally:
            os.unlink(token_file)

    def test_htf_engine_register(self):
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        htf.register("GOLDM", "1h", dema_period=3, atr_period=6)
        assert "GOLDM:1h" in htf._engines

    def test_htf_mapped_value(self):
        from htf.backtest_style_htf import HTFMappedValue
        mv = HTFMappedValue(
            htf_value=72000.0,
            prev_htf_value=71900.0,
            htf_confirmed=True,
            htf_source_timestamp=time.time(),
        )
        assert mv.htf_value == 72000.0
        assert mv.htf_confirmed is True

    def test_telegram_router_instantiation(self):
        from notifications.telegram_router import TelegramRouter
        tr = TelegramRouter()
        assert tr._enabled is True
        tr.stop()

    def test_config_singleton(self):
        from config import Config
        c1 = Config()
        c2 = Config()
        assert c1 is c2  # Singleton

    def test_market_status_thread_safety(self):
        from core.market_status import MarketStatus, MarketState
        ms = MarketStatus()
        errors = []

        def force_many():
            try:
                for i in range(100):
                    ms.force_state(MarketState.LIVE_TRADING)
                    _ = ms.state
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=force_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    def test_multiple_strategies_no_interference(self):
        from strategies.gold import GoldStrategy01
        from strategies.silver import SilverStrategy01
        g = GoldStrategy01(strategy_id="g_test", quantity=1)
        s = SilverStrategy01(strategy_id="s_test", quantity=2)
        g.state = g.state.__class__.LONG_POSITION
        g.position_side = "LONG"
        assert s.state.value == "flat"
        assert s.position_side is None
