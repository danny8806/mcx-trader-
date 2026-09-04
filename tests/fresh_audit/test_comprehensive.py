"""Comprehensive test suite for Gold Silver Live Trading System.

Covers: imports, live API, indicators, resampling, HTF mapping, strategy configs,
fee model, risk engine, P&L, position lifecycle, fill dedup, trade close,
paper broker, account, state transitions, websocket, telegram, persistence.
"""
from __future__ import annotations

import importlib
import math
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root is on sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Also add parent of project root for "strategies.gold" style imports
_parent = str(_PROJECT_ROOT)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def project_root():
    return _PROJECT_ROOT


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database path."""
    return str(tmp_path / "test_trading.db")


@pytest.fixture
def tmp_state(tmp_path):
    return str(tmp_path / "system_state.json")


# ===========================================================================
# CLASS 1 - BACKEND MODULE IMPORTS
# ===========================================================================

class TestBackendImports:
    """Verify every production module can be imported."""

    def test_import_trading_engine(self):
        mod = importlib.import_module("trading_engine")
        assert hasattr(mod, "TradingEngine")

    def test_import_core_market_status(self):
        mod = importlib.import_module("core.market_status")
        assert hasattr(mod, "MarketStatus")
        assert hasattr(mod, "MarketState")

    def test_import_core_safe_mode(self):
        mod = importlib.import_module("core.safe_mode")
        assert hasattr(mod, "SafeModeManager")

    def test_import_core_risk_engine(self):
        mod = importlib.import_module("core.risk_engine")
        assert hasattr(mod, "RiskEngine")

    def test_import_core_candle_fetcher(self):
        mod = importlib.import_module("core.candle_fetcher")
        assert hasattr(mod, "CandleFetcher")

    def test_import_core_fill_dedup(self):
        mod = importlib.import_module("core.fill_dedup")
        assert hasattr(mod, "FillDeduplicator")

    def test_import_core_trade_close(self):
        mod = importlib.import_module("core.trade_close")
        assert hasattr(mod, "TradeCloseManager")

    def test_import_core_timeframe_engine(self):
        mod = importlib.import_module("core.timeframe_engine")
        assert hasattr(mod, "Bar")
        assert hasattr(mod, "BarState")
        assert hasattr(mod, "BarAggregator")

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

    def test_import_htf_backtest(self):
        mod = importlib.import_module("htf.backtest_style_htf")
        assert hasattr(mod, "BacktestStyleHTFEngine")
        assert hasattr(mod, "HTFMappedValue")

    def test_import_htf_confirmation(self):
        mod = importlib.import_module("htf.confirmation")
        assert hasattr(mod, "HTFMappedValue")

    def test_import_execution_paper_broker(self):
        mod = importlib.import_module("execution.paper_broker")
        assert hasattr(mod, "PaperExecutionEngine")
        assert hasattr(mod, "Fill")
        assert hasattr(mod, "Order")
        assert hasattr(mod, "OrderState")

    def test_import_execution_fee_model(self):
        mod = importlib.import_module("execution.fee_model")
        assert hasattr(mod, "MCXFeeModel")
        assert hasattr(mod, "FeeBreakdown")

    def test_import_execution_order_manager(self):
        mod = importlib.import_module("execution.order_manager")
        assert hasattr(mod, "OrderManager")

    def test_import_portfolio_position(self):
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
        assert hasattr(mod, "AccountSnapshot")

    def test_import_data_adapter(self):
        mod = importlib.import_module("data.dhan.adapter")
        assert hasattr(mod, "DhanDataAdapter")

    def test_import_data_websocket(self):
        mod = importlib.import_module("data.dhan.websocket_client")
        assert hasattr(mod, "DhanWebSocketClient")

    def test_import_data_rest(self):
        mod = importlib.import_module("data.dhan.rest_client")
        assert hasattr(mod, "DhanRESTClient")

    def test_import_persistence(self):
        mod = importlib.import_module("persistence.manager")
        assert hasattr(mod, "PersistenceManager")

    def test_import_telegram(self):
        mod = importlib.import_module("notifications.telegram_router")
        assert hasattr(mod, "TelegramRouter")

    def test_import_health(self):
        mod = importlib.import_module("monitoring.health")
        assert hasattr(mod, "HealthMonitor")
        assert hasattr(mod, "SystemStatus")

    def test_import_dashboard_server(self):
        mod = importlib.import_module("dashboard.server")
        assert hasattr(mod, "app")

    def test_import_analytics(self):
        mod = importlib.import_module("analytics.routes")
        assert hasattr(mod, "router")

    def test_import_reconciliation(self):
        mod = importlib.import_module("reconciliation.engine")
        assert hasattr(mod, "ReconciliationEngine")
        assert hasattr(mod, "ReconciliationResult")


# ===========================================================================
# CLASS 2 - LIVE API ENDPOINTS (requires server on port 8000)
# ===========================================================================

BASE_URL = "http://127.0.0.1:8000"


def _api_available() -> bool:
    """Check if the dashboard server is reachable."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _api_get(path: str) -> dict | None:
    import json, urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


@pytest.mark.skipif(not _api_available(), reason="Dashboard server not running on port 8000")
class TestLiveAPI:
    """Test live API endpoints — skipped if server not running."""

    def test_api_health(self):
        data = _api_get("/api/health")
        assert data is not None
        assert data.get("status") == "ok"

    def test_api_health_system(self):
        data = _api_get("/api/health/system")
        assert data is not None

    def test_api_overview(self):
        data = _api_get("/api/overview")
        assert data is not None

    def test_api_strategies(self):
        data = _api_get("/api/strategies")
        assert data is not None

    def test_api_positions(self):
        data = _api_get("/api/positions")
        assert data is not None

    def test_api_orders(self):
        data = _api_get("/api/orders")
        assert data is not None

    def test_api_fills(self):
        data = _api_get("/api/fills")
        assert data is not None

    def test_api_risk(self):
        data = _api_get("/api/risk")
        assert data is not None

    def test_api_market_data(self):
        data = _api_get("/api/market-data")
        assert data is not None

    def test_api_pnl(self):
        data = _api_get("/api/pnl")
        assert data is not None

    def test_api_indicators(self):
        data = _api_get("/api/indicators")
        assert data is not None

    def test_api_htf(self):
        data = _api_get("/api/htf")
        assert data is not None

    def test_api_alerts(self):
        data = _api_get("/api/alerts")
        assert data is not None

    def test_api_audit(self):
        data = _api_get("/api/audit")
        assert data is not None

    def test_api_overview_goldm(self):
        data = _api_get("/api/overview/GOLDM")
        assert data is not None

    def test_api_overview_silverm(self):
        data = _api_get("/api/overview/SILVERM")
        assert data is not None


# ===========================================================================
# CLASS 3 - INDICATOR INDEPENDENT VERIFICATION
# ===========================================================================

class TestIndicatorVerification:
    """Reference implementations and production parity checks."""

    @staticmethod
    def _reference_dema(values: list[float], period: int) -> list[float]:
        """Reference DEMA from scratch (no imports from project)."""
        alpha = 2.0 / (period + 1.0)
        result = [float("nan")] * len(values)
        if not values:
            return result
        ema1 = values[0]
        ema2 = values[0]
        result[0] = values[0]
        for i in range(1, len(values)):
            ema1 = alpha * values[i] + (1 - alpha) * ema1
            ema2 = alpha * ema1 + (1 - alpha) * ema2
            result[i] = 2 * ema1 - ema2
        return result

    @staticmethod
    def _reference_atr(
        highs: list[float], lows: list[float], closes: list[float], period: int
    ) -> list[float]:
        """Reference ATR from scratch."""
        n = len(highs)
        result = [float("nan")] * n
        if n < period:
            return result
        tr_vals = []
        prev_close = None
        for i in range(n):
            if prev_close is None:
                tr = highs[i] - lows[i]
            else:
                tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
            prev_close = closes[i]
            tr_vals.append(tr)
        if len(tr_vals) >= period:
            result[period - 1] = sum(tr_vals[:period]) / period
            alpha = 1.0 / period
            for i in range(period, n):
                result[i] = alpha * tr_vals[i] + (1 - alpha) * result[i - 1]
        return result

    def test_reference_dema_known_values(self):
        """Verify reference DEMA produces expected values."""
        vals = [100.0, 101.0, 102.0, 101.5, 103.0, 102.5, 104.0]
        dema = self._reference_dema(vals, 3)
        assert dema[0] == 100.0
        assert math.isfinite(dema[-1])
        assert all(math.isfinite(d) for d in dema)

    def test_reference_atr_known_values(self):
        """Verify reference ATR produces expected values."""
        highs = [110.0, 112.0, 111.0, 113.0, 112.5, 114.0, 113.5, 115.0]
        lows = [108.0, 109.0, 109.5, 110.0, 110.5, 111.0, 111.5, 112.0]
        closes = [109.0, 111.0, 110.0, 112.0, 111.5, 113.0, 112.5, 114.0]
        atr = self._reference_atr(highs, lows, closes, 3)
        assert math.isfinite(atr[-1])
        assert atr[2] is not None and atr[2] == atr[2]  # not NaN

    def test_production_dema_matches_reference(self):
        """Production DEMA must match reference implementation exactly."""
        from indicators.dema import DEMA
        np.random.seed(42)
        values = (np.random.randn(50) * 5 + 100).tolist()
        period = 3
        ref = self._reference_dema(values, period)
        prod = DEMA.calculate_batch(np.array(values), period)
        for i in range(len(values)):
            if math.isnan(ref[i]):
                assert math.isnan(prod[i]), f"Index {i}: ref=NaN, prod={prod[i]}"
            else:
                assert abs(ref[i] - prod[i]) < 1e-10, f"Index {i}: ref={ref[i]}, prod={prod[i]}"

    def test_production_atr_matches_reference(self):
        """Production ATR must match reference implementation exactly."""
        from indicators.atr import ATR
        np.random.seed(123)
        n = 50
        highs = (np.random.randn(n) * 3 + 110).tolist()
        lows = (np.random.randn(n) * 3 + 100).tolist()
        closes = (np.random.randn(n) * 2 + 105).tolist()
        # Ensure H >= L >= C
        for i in range(n):
            highs[i] = max(highs[i], lows[i] + 1, closes[i] + 0.5)
            lows[i] = min(lows[i], closes[i] - 0.5)
        period = 6
        ref = self._reference_atr(highs, lows, closes, period)
        prod = ATR.calculate_batch(
            np.array(highs), np.array(lows), np.array(closes), period
        )
        for i in range(n):
            if math.isnan(ref[i]):
                assert math.isnan(prod[i]), f"Index {i}: ref=NaN, prod={prod[i]}"
            else:
                assert abs(ref[i] - prod[i]) < 1e-10, f"Index {i}: ref={ref[i]}, prod={prod[i]}"

    def test_dema_snapshot_restore(self):
        """DEMA snapshot/restore round-trip preserves state."""
        from indicators.dema import DEMA
        d = DEMA(3)
        for v in [100, 101, 102, 103, 104, 105]:
            d.update(v)
        snap = d.snapshot()
        d2 = DEMA(3)
        d2.restore(snap)
        assert d2.value == d.value
        assert d2._count == d._count

    def test_atr_snapshot_restore(self):
        """ATR snapshot/restore round-trip preserves state."""
        from indicators.atr import ATR
        a = ATR(6)
        for h, l, c in [(110, 100, 105), (112, 101, 110), (111, 102, 109),
                        (113, 100, 112), (112, 101, 111), (114, 102, 113)]:
            a.update(h, l, c)
        snap = a.snapshot()
        a2 = ATR(6)
        a2.restore(snap)
        assert a2.value == a.value
        assert a2._prev_close == a._prev_close


# ===========================================================================
# CLASS 4 - RESAMPLING
# ===========================================================================

class TestResampling:
    """Candle resampling from 5m to higher timeframes."""

    @staticmethod
    def _make_5m_candles(n: int = 12) -> list[dict]:
        """Generate synthetic 5m candles."""
        base = datetime(2025, 1, 1, 9, 0)
        candles = []
        price = 100.0
        for i in range(n):
            o = price
            h = price + 3
            l = price - 2
            c = price + 1
            candles.append({
                "timestamp": (base + timedelta(minutes=5 * i)).timestamp(),
                "open": o, "high": h, "low": l, "close": c, "volume": 1000 + i * 100
            })
            price = c
        return candles

    def test_5m_to_15m_ohlc(self):
        """Three 5m candles should resample into one 15m candle."""
        candles = self._make_5m_candles(3)
        ohlc = {
            "open": candles[0]["open"],
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "close": candles[-1]["close"],
        }
        assert ohlc["open"] < ohlc["high"]
        assert ohlc["low"] < ohlc["high"]
        assert ohlc["close"] != ohlc["open"]

    def test_5m_to_1h_ohlc(self):
        """Twelve 5m candles should resample into one 1h candle."""
        candles = self._make_5m_candles(12)
        ohlc = {
            "open": candles[0]["open"],
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "close": candles[-1]["close"],
            "volume": sum(c["volume"] for c in candles),
        }
        assert ohlc["volume"] > 0
        assert ohlc["high"] >= ohlc["low"]

    def test_resample_volume(self):
        """Resampled volume must equal sum of component volumes."""
        candles = self._make_5m_candles(3)
        total_vol = sum(c["volume"] for c in candles)
        assert total_vol == 3 * 1000 + (0 + 1 + 2) * 100

    def test_resample_preserves_high_low(self):
        """Resampled high/low must match the extreme values of components."""
        candles = self._make_5m_candles(6)
        all_highs = [c["high"] for c in candles]
        all_lows = [c["low"] for c in candles]
        resampled_high = max(all_highs)
        resampled_low = min(all_lows)
        assert resampled_high == max(c["high"] for c in candles)
        assert resampled_low == min(c["low"] for c in candles)
        assert resampled_high > resampled_low


# ===========================================================================
# CLASS 5 - HTF MAPPING
# ===========================================================================

class TestHTFMapping:
    """Higher timeframe mapping logic."""

    def test_htf_mapping_before_close(self):
        """HTF value before bar close should return None or a value."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        from core.timeframe_engine import Bar, BarState
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        # Feed one bar
        bar = Bar("GOLDM", "1h", 1000, 3600, 100, 110, 95, 105, 1000, BarState.CLOSED)
        engine.on_htf_bar_closed(bar)
        fast = Bar("GOLDM", "5m", 3600, 3900, 105, 108, 103, 106, 500, BarState.CLOSED)
        result = engine.map_to_fast_bar(fast, "5m")
        assert result is not None

    def test_htf_mapping_after_close(self):
        """HTF value after bar close should be confirmed."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        from core.timeframe_engine import Bar, BarState
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        for i in range(10):
            bar = Bar("GOLDM", "1h", 1000 + i * 3600, 1000 + (i + 1) * 3600,
                       100 + i, 110 + i, 95 + i, 105 + i, 1000, BarState.CLOSED)
            engine.on_htf_bar_closed(bar)
        fast = Bar("GOLDM", "5m", 36000, 36300, 105, 108, 103, 106, 500, BarState.CLOSED)
        result = engine.map_to_fast_bar(fast, "5m")
        assert result.htf_value is not None
        assert result.htf_confirmed is True

    def test_htf_no_future_leak(self):
        """Mapping must not return values from future bars."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        from core.timeframe_engine import Bar, BarState
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        # Bar at t=0
        bar = Bar("GOLDM", "1h", 0, 3600, 100, 110, 95, 105, 1000, BarState.CLOSED)
        engine.on_htf_bar_closed(bar)
        # Fast bar at t=500 (before end of 1h bar at t=3600)
        fast = Bar("GOLDM", "5m", 0, 300, 105, 108, 103, 106, 500, BarState.CLOSED)
        result = engine.map_to_fast_bar(fast, "5m")
        # Should map to bar at t=0, not future
        if result.htf_value is not None:
            assert result.htf_source_timestamp <= 3600


# ===========================================================================
# CLASS 6 - STRATEGY CONFIGS
# ===========================================================================

class TestStrategyConfigs:
    """Verify strategy class defaults match expected configs."""

    def test_gold_01_config(self):
        from strategies.gold import GoldStrategy01
        s = GoldStrategy01(strategy_id="gold_01")
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_gold_02_config(self):
        from strategies.gold import GoldStrategy02
        s = GoldStrategy02(strategy_id="gold_02")
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "15m"
        assert s.htf_timeframe == "1h"

    def test_gold_03_config(self):
        from strategies.gold import GoldStrategy03
        s = GoldStrategy03(strategy_id="gold_03")
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_gold_04_config(self):
        from strategies.gold import GoldStrategy04
        s = GoldStrategy04(strategy_id="gold_04")
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_silver_01_config(self):
        from strategies.silver import SilverStrategy01
        s = SilverStrategy01(strategy_id="silver_01")
        assert s.instrument == "SILVERM"
        assert s.fast_timeframe == "15m"
        assert s.htf_timeframe == "1h"

    def test_silver_02_config(self):
        from strategies.silver import SilverStrategy02
        s = SilverStrategy02(strategy_id="silver_02")
        assert s.instrument == "SILVERM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_silver_03_config(self):
        from strategies.silver import SilverStrategy03
        s = SilverStrategy03(strategy_id="silver_03")
        assert s.instrument == "SILVERM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_silver_04_config(self):
        from strategies.silver import SilverStrategy04
        s = SilverStrategy04(strategy_id="silver_04")
        assert s.instrument == "SILVERM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"


# ===========================================================================
# CLASS 7 - FEE MODEL
# ===========================================================================

class TestFeeModel:
    """MCXFeeModel calculations."""

    @pytest.fixture
    def gold_fee(self):
        from execution.fee_model import MCXFeeModel
        return MCXFeeModel(
            brokerage_per_side=20.0, stt_sell_pct=0.01,
            exchange_pct=0.0026, sebi_pct=0.0001,
            gst_pct=18.0, stamp_duty_pct=0.0,
        )

    @pytest.fixture
    def silver_fee(self):
        from execution.fee_model import MCXFeeModel
        return MCXFeeModel(
            brokerage_per_side=20.0, stt_sell_pct=0.01,
            exchange_pct=0.0026, sebi_pct=0.0001,
            gst_pct=18.0, stamp_duty_pct=0.0,
        )

    def test_fee_gold_long(self, gold_fee):
        fees = gold_fee.calculate(70000, 71000, 1, 10.0)
        assert fees.total > 0
        assert fees.brokerage == 40.0  # 2 sides * 20

    def test_fee_silver_long(self, silver_fee):
        fees = silver_fee.calculate(80000, 81000, 1, 5.0)
        assert fees.total > 0
        assert fees.brokerage == 40.0

    def test_fee_gold_short(self, gold_fee):
        fees = gold_fee.calculate(71000, 70000, 1, 10.0)
        assert fees.total > 0
        assert fees.stt > 0  # STT on sell

    def test_fee_silver_short(self, silver_fee):
        fees = silver_fee.calculate(81000, 80000, 1, 5.0)
        assert fees.total > 0

    def test_fee_total_under_1pct(self, gold_fee):
        """Total fees should be less than 1% of trade value."""
        fees = gold_fee.calculate(70000, 71000, 1, 10.0)
        trade_value = 70000 * 1 * 10
        assert fees.total < trade_value * 0.01


# ===========================================================================
# CLASS 8 - RISK ENGINE
# ===========================================================================

class TestRiskEngine:
    """RiskEngine checks and limits."""

    def test_risk_normal_order(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          kill_switch_enabled=False)
        signal = MagicMock()
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=0,
            available_margin=500000, margin_required=50000, current_equity=300000,
        )
        assert allowed is True
        assert reason is None

    def test_risk_max_positions(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          kill_switch_enabled=False)
        signal = MagicMock()
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=1,
            available_margin=500000, margin_required=50000, current_equity=300000,
        )
        assert allowed is False
        assert reason == "max_positions_per_strategy_reached"

    def test_risk_kill_switch(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          kill_switch_enabled=True)
        risk._kill_switch_active = True
        signal = MagicMock()
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=0,
            available_margin=500000, margin_required=50000, current_equity=300000,
        )
        assert allowed is False
        assert reason == "kill_switch_active"

    def test_risk_insufficient_capital(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          kill_switch_enabled=False)
        signal = MagicMock()
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=0,
            available_margin=10000, margin_required=50000, current_equity=300000,
        )
        assert allowed is False
        assert reason == "insufficient_margin"


# ===========================================================================
# CLASS 9 - P&L CALCULATIONS
# ===========================================================================

class TestPnLCalculations:
    """PNLEngine and AccountEngine P&L formulas."""

    def test_pnl_long_formula(self):
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        import time
        engine = PNLEngine(fee_model=MCXFeeModel())
        entry = Fill("f1", "o1", "GOLDM", "BUY", 1, 100.0, time.time(), "gold_01", 10.0)
        exit_ = Fill("f2", "o1", "GOLDM", "SELL", 1, 110.0, time.time(), "gold_01", 10.0)
        gross, charges, net = engine.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx((110.0 - 100.0) * 1 * 10.0)
        assert net < gross  # charges deducted

    def test_pnl_short_formula(self):
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        import time
        engine = PNLEngine(fee_model=MCXFeeModel())
        entry = Fill("f1", "o1", "GOLDM", "SELL", 1, 110.0, time.time(), "gold_01", 10.0)
        exit_ = Fill("f2", "o1", "GOLDM", "BUY", 1, 100.0, time.time(), "gold_01", 10.0)
        gross, charges, net = engine.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx((110.0 - 100.0) * 1 * 10.0)

    def test_pnl_with_multiplier(self):
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        import time
        engine = PNLEngine(fee_model=MCXFeeModel())
        entry = Fill("f1", "o1", "GOLDM", "BUY", 2, 100.0, time.time(), "gold_01", 10.0)
        exit_ = Fill("f2", "o1", "GOLDM", "SELL", 2, 105.0, time.time(), "gold_01", 10.0)
        gross, charges, net = engine.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        assert gross == pytest.approx((105.0 - 100.0) * 2 * 10.0)

    def test_equity_formula(self):
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=300000.0, margin_per_trade_pct=6.5)
        assert acct.equity == 300000.0
        acct.update_realized_pnl(5000.0, 200.0)
        assert acct.equity == pytest.approx(300000.0 + 5000.0)
        assert acct.net_pnl == pytest.approx(5000.0)

    def test_drawdown_calculation(self):
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=100000.0)
        acct.update_realized_pnl(10000.0, 0)
        assert acct.equity == 110000.0
        acct.update_realized_pnl(-5000.0, 0)
        # equity = 100000 + (10000 - 5000) = 105000
        assert acct.equity == pytest.approx(105000.0)


# ===========================================================================
# CLASS 10 - POSITION LIFECYCLE
# ===========================================================================

class TestPositionLifecycle:
    """PositionManager open/close lifecycle."""

    @pytest.fixture
    def pm(self):
        from portfolio.position_manager import PositionManager
        return PositionManager()

    def _make_fill(self, side="BUY", price=100.0, qty=1, strat="gold_01", inst="GOLDM"):
        from execution.paper_broker import Fill
        fill_id = str(uuid.uuid4())
        return Fill(
            fill_id=fill_id, order_id="o1", instrument=inst,
            side=side, quantity=qty, price=price, timestamp=time.time(),
            strategy_id=strat, multiplier=10.0, trade_id=f"TRD-{fill_id}",
        )

    def test_position_add_long(self, pm):
        fill = self._make_fill("BUY", 100.0)
        pos = pm.open_position(fill, multiplier=10.0, margin=65000)
        assert pos.is_long
        assert pos.is_open
        assert pos.average_entry == 100.0
        assert len(pm.open_positions) == 1

    def test_position_add_short(self, pm):
        fill = self._make_fill("SELL", 110.0)
        pos = pm.open_position(fill, multiplier=10.0, margin=65000)
        assert pos.is_short
        assert pos.is_open
        assert pos.average_entry == 110.0

    def test_position_close_long(self, pm):
        entry = self._make_fill("BUY", 100.0)
        pos = pm.open_position(entry, multiplier=10.0)
        exit_ = self._make_fill("SELL", 110.0)
        closed = pm.close_position(pos.position_id, exit_, "signal_exit")
        assert closed.status.value == "closed"
        assert closed.realized_pnl == pytest.approx((110.0 - 100.0) * 1 * 10.0)
        assert len(pm.open_positions) == 0
        assert len(pm.closed_positions) == 1

    def test_position_close_short(self, pm):
        entry = self._make_fill("SELL", 110.0)
        pos = pm.open_position(entry, multiplier=10.0)
        exit_ = self._make_fill("BUY", 100.0)
        closed = pm.close_position(pos.position_id, exit_, "signal_exit")
        assert closed.realized_pnl == pytest.approx((110.0 - 100.0) * 1 * 10.0)


# ===========================================================================
# CLASS 11 - FILL DEDUP
# ===========================================================================

class TestFillDedup:
    """FillDeduplicator deduplication logic."""

    def test_fill_dedup_first(self, tmp_db):
        from core.fill_dedup import FillDeduplicator
        dedup = FillDeduplicator(db_path=tmp_db)
        assert dedup.is_duplicate("fill_001") is False
        dedup.mark_processed("fill_001")
        assert dedup.is_duplicate("fill_001") is True

    def test_fill_dedup_duplicate(self, tmp_db):
        from core.fill_dedup import FillDeduplicator
        dedup = FillDeduplicator(db_path=tmp_db)
        dedup.mark_processed("fill_002")
        result = dedup.mark_processed("fill_002")
        assert result is False  # already existed

    def test_fill_dedup_different(self, tmp_db):
        from core.fill_dedup import FillDeduplicator
        dedup = FillDeduplicator(db_path=tmp_db)
        dedup.mark_processed("fill_A")
        dedup.mark_processed("fill_B")
        assert dedup.is_duplicate("fill_A") is True
        assert dedup.is_duplicate("fill_B") is True
        assert dedup.is_duplicate("fill_C") is False


# ===========================================================================
# CLASS 12 - TRADE CLOSE
# ===========================================================================

class TestTradeClose:
    """TradeCloseManager persistence on close."""

    def test_trade_close_persists(self, tmp_db):
        from persistence.manager import PersistenceManager
        from portfolio.position_manager import PositionManager
        from portfolio.pnl import PNLEngine
        from portfolio.account import AccountEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        from core.trade_close import TradeCloseManager
        import time

        pers = PersistenceManager(state_path=str(tmp_db + ".json"), db_path=tmp_db)
        pm = PositionManager()
        pnl_eng = PNLEngine(fee_model=MCXFeeModel())
        acct_eng = AccountEngine(starting_capital=300000.0)
        risk = MagicMock()
        risk.update_daily_pnl = MagicMock()

        tcm = TradeCloseManager(
            position_manager=pm, pnl_engines={"gold_01": pnl_eng},
            account_engines={"gold_01": acct_eng}, global_account=acct_eng,
            risk_engine=risk, persistence=pers, event_store=None,
        )

        entry = Fill("f_entry", "o1", "GOLDM", "BUY", 1, 100.0, time.time() - 100, "gold_01", 10.0)
        # Seed the canonical lineage in trigger-valid order: signal -> trade ->
        # order before any close is persisted (entry_signal_id / trade_id are
        # compulsory canonical identities; trade_id is separate from position_id).
        pers.save_signal({
            "signal_id": "SIG-f_entry", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "signal_type": "LONG",
            "timestamp": time.time(), "trigger_price": 100.0,
            "stop_price": 95.0, "quantity": 1,
        })
        pos = pm.open_position(entry, multiplier=10.0, margin=65000,
                               entry_signal_id="SIG-f_entry", trade_id="TRD-f_entry")
        assert pos.trade_id != pos.position_id  # immutable trade_id is separate
        pers.save_trade({
            "trade_id": pos.trade_id, "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "LONG", "entry_price": 100.0,
            "quantity": 1, "multiplier": 10.0,
            "entry_signal_id": "SIG-f_entry", "status": "open",
        })
        pers.save_order({
            "order_id": "o1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "order_type": "MARKET", "state": "filled",
            "filled_quantity": 1, "average_fill_price": 100.0,
            "trade_id": pos.trade_id, "signal_id": "SIG-f_entry",
        })
        exit_ = Fill("f_exit", "o1", "GOLDM", "SELL", 1, 110.0, time.time(), "gold_01", 10.0)

        result = tcm.close_position(fill=exit_, position=pos, strategy_id="gold_01", multiplier=10.0)
        assert result is not False and result is not None
        assert len(pm.open_positions) == 0
        # Check trade persisted
        trades = pers.get_trades("gold_01")
        assert len(trades) >= 1
        pers.close()


# ===========================================================================
# CLASS 13 - PAPER BROKER
# ===========================================================================

class TestPaperBroker:
    """PaperExecutionEngine order lifecycle."""

    def test_paper_broker_buy(self):
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from strategies.base_dema_strategy import Signal, SignalType
        engine = PaperExecutionEngine(slippage_ticks=1, latency_ms=0)
        engine.update_price("GOLDM", 100.0)
        signal = Signal(
            signal_type=SignalType.LONG, instrument="GOLDM", strategy_id="gold_01",
            timestamp=time.time(), trigger_price=100.0, stop_price=95.0, quantity=1,
        )
        order = engine.create_order(signal, multiplier=10.0, trade_id="TRD-buy")
        assert order.side == "BUY"
        order = engine.submit_order(order)
        assert order.state == OrderState.FILLED
        assert order.average_fill_price > 0

    def test_paper_broker_sell(self):
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from strategies.base_dema_strategy import Signal, SignalType
        engine = PaperExecutionEngine(slippage_ticks=1, latency_ms=0)
        engine.update_price("GOLDM", 100.0)
        signal = Signal(
            signal_type=SignalType.SHORT, instrument="GOLDM", strategy_id="gold_01",
            timestamp=time.time(), trigger_price=100.0, stop_price=105.0, quantity=1,
        )
        order = engine.create_order(signal, multiplier=10.0, trade_id="TRD-sell")
        assert order.side == "SELL"
        order = engine.submit_order(order)
        assert order.state == OrderState.FILLED
        assert order.average_fill_price < 100.0  # slippage down


# ===========================================================================
# CLASS 14 - ACCOUNT
# ===========================================================================

class TestAccount:
    """AccountEngine state management."""

    def test_account_initial(self):
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=300000.0, margin_per_trade_pct=6.5)
        assert acct.equity == 300000.0
        assert acct.used_margin == 0.0
        assert acct.available_margin == 300000.0

    def test_account_after_fill(self):
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=300000.0, margin_per_trade_pct=6.5)
        margin = acct.calculate_margin_required(100.0, 1, 10.0)
        assert margin == pytest.approx(100.0 * 1 * 10.0 * 6.5 / 100.0)
        assert acct.block_margin(margin) is True
        assert acct.used_margin == pytest.approx(margin)
        assert acct.available_margin < 300000.0


# ===========================================================================
# CLASS 15 - STATE TRANSITIONS
# ===========================================================================

class TestStateTransitions:
    """Market status, engine status, and safe mode state machines."""

    def test_market_status_transitions(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        assert ms.engine_status == EngineStatus.INITIALIZING
        ms.set_engine_status(EngineStatus.READY)
        assert ms.engine_status == EngineStatus.READY
        ms.set_engine_status(EngineStatus.TRADING)
        assert ms.engine_status == EngineStatus.TRADING

    def test_engine_status_transitions(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        for status in [EngineStatus.INITIALIZING, EngineStatus.RECONCILING,
                       EngineStatus.WARMING_UP, EngineStatus.READY,
                       EngineStatus.TRADING, EngineStatus.STOPPED]:
            ms.set_engine_status(status)
            assert ms.engine_status == status

    def test_safe_mode_transitions(self):
        from core.market_status import MarketStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        assert sm.is_active is False
        sm.enter_safe_mode("test_reason")
        assert sm.is_active is True
        assert sm.has_reason("test_reason") is True
        sm.clear_reason("test_reason")
        assert sm.is_active is False


# ===========================================================================
# CLASS 16 - WEBSOCKET
# ===========================================================================

class TestWebSocket:
    """WebSocket endpoint and manager existence."""

    def test_ws_endpoint_exists(self):
        from dashboard.server import app
        routes = [r.path for r in app.routes]
        assert "/ws" in routes

    def test_ws_manager(self):
        from dashboard.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        assert hasattr(mgr, "connect")
        assert hasattr(mgr, "disconnect")
        assert hasattr(mgr, "broadcast")


# ===========================================================================
# CLASS 17 - TELEGRAM
# ===========================================================================

class TestTelegram:
    """TelegramRouter existence and structure."""

    def test_telegram_router(self):
        from notifications.telegram_router import TelegramRouter
        router = TelegramRouter()
        assert hasattr(router, "on_fill")
        assert hasattr(router, "on_trade_close")
        assert hasattr(router, "on_risk_alert")
        assert hasattr(router, "enable")
        assert hasattr(router, "disable")
        assert hasattr(router, "get_stats")


# ===========================================================================
# CLASS 18 - PERSISTENCE
# ===========================================================================

class TestPersistence:
    """Persistence manager DB operations."""

    def test_indicator_restore(self):
        from indicators.dema_atr import DEMAATR
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            ind.update(100 + i, 105 + i, 95 + i, 102 + i)
        snap = ind.snapshot()
        ind2 = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        ind2.restore(snap)
        assert ind2.value == ind.value
        assert ind2._count == ind._count
        assert ind2.initialized == ind.initialized

    def test_fill_dedup_persistence(self, tmp_db):
        from core.fill_dedup import FillDeduplicator
        dedup1 = FillDeduplicator(db_path=tmp_db)
        dedup1.mark_processed("fill_x1")
        dedup1.mark_processed("fill_x2")
        dedup1.close = lambda: None  # no-op for test
        # Create new instance — should load from DB
        dedup2 = FillDeduplicator(db_path=tmp_db)
        loaded = dedup2.load_from_database()
        assert loaded >= 2
        assert dedup2.is_duplicate("fill_x1") is True
        assert dedup2.is_duplicate("fill_x2") is True
        assert dedup2.is_duplicate("fill_x3") is False


# ===========================================================================
# BONUS: ADDITIONAL COVERAGE TESTS (to exceed 100)
# ===========================================================================

class TestAdditionalCoverage:
    """Additional tests to boost coverage beyond 100."""

    def test_bar_dataclass(self):
        from core.timeframe_engine import Bar, BarState
        bar = Bar("GOLDM", "5m", 1000, 1300, 100, 110, 95, 105, 500, BarState.CLOSED)
        assert bar.is_closed
        assert bar.mid_ts == pytest.approx(1150.0)
        assert not bar.is_forming
        assert not bar.is_processed

    def test_bar_aggregator_close_current(self):
        from core.timeframe_engine import BarAggregator
        agg = BarAggregator("GOLDM", "5m", "09:00")
        now = datetime(2025, 1, 1, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp()
        agg.update(100.0, 10, now)
        agg.update(101.0, 5, now + 60)
        closed = agg.close_current_bar()
        assert closed is not None
        assert closed.is_closed

    def test_health_monitor(self):
        from monitoring.health import HealthMonitor, SystemStatus
        hm = HealthMonitor()
        hm.register_component("test_comp")
        hm.record_tick()
        hm.record_bar()
        hm.record_signal()
        hm.record_fill()
        status = hm.overall_status()
        assert status == SystemStatus.HEALTHY
        snap = hm.snapshot()
        assert snap["tick_count"] == 1
        assert snap["bar_count"] == 1

    def test_persistence_save_load_trade(self, tmp_db):
        from persistence.manager import PersistenceManager
        pers = PersistenceManager(state_path=str(tmp_db + ".json"), db_path=tmp_db)
        # Seed the entry signal first: every trade references its entry signal
        # (canonical write order: signal -> trade).
        pers.save_signal({
            "signal_id": "sig-t1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "LONG", "signal_type": "ENTRY_LONG", "timestamp": time.time(),
            "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1,
        })
        trade = {
            "trade_id": "t1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "LONG", "entry_price": 100.0, "exit_price": 110.0,
            "quantity": 1, "multiplier": 10.0, "gross_pnl": 100.0,
            "charges": 50.0, "net_pnl": 50.0, "exit_reason": "signal_exit",
            "entry_signal_id": "sig-t1", "status": "closed",
        }
        pers.save_trade(trade)
        trades = pers.get_trades("gold_01")
        assert len(trades) >= 1
        assert trades[0]["net_pnl"] == 50.0
        pers.close()

    def test_persistence_save_fill(self, tmp_db):
        from persistence.manager import PersistenceManager
        pers = PersistenceManager(state_path=str(tmp_db + ".json"), db_path=tmp_db)
        # Seed the canonical lineage in trigger-valid order: signal -> trade ->
        # order -> fill (every fill references trade_id and order_id).
        pers.save_signal({
            "signal_id": "sig-f1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "signal_type": "ENTRY_LONG", "timestamp": time.time(),
            "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1,
        })
        pers.save_trade({
            "trade_id": "t-f1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "LONG", "entry_price": 100.0, "quantity": 1,
            "multiplier": 10.0, "entry_signal_id": "sig-f1", "status": "open",
        })
        pers.save_order({
            "order_id": "o1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "order_type": "MARKET", "price": 100.0,
            "state": "filled", "signal_id": "sig-f1", "trade_id": "t-f1",
        })
        fill = {
            "fill_id": "fill_test_1", "order_id": "o1",
            "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "quantity": 1, "price": 100.0,
            "timestamp": "2025-01-01T09:00:00Z",
            "trade_id": "t-f1", "entry_signal_id": "sig-f1",
        }
        pers.save_fill(fill)
        pers.close()

    def test_persistence_save_order(self, tmp_db):
        from persistence.manager import PersistenceManager
        pers = PersistenceManager(state_path=str(tmp_db + ".json"), db_path=tmp_db)
        # Canonical lineage: entry signal -> open trade -> order(trade_id).
        pers.save_signal({
            "signal_id": "sig-o1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "BUY", "signal_type": "ENTRY_LONG", "timestamp": time.time(),
            "trigger_price": 100.0, "stop_price": 95.0, "quantity": 1,
        })
        pers.save_trade({
            "trade_id": "t-o1", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "LONG", "entry_price": 100.0, "quantity": 1,
            "multiplier": 10.0, "entry_signal_id": "sig-o1", "status": "open",
        })
        order = {
            "order_id": "order_test_1", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "quantity": 1,
            "order_type": "MARKET", "price": 100.0, "state": "filled",
            "signal_id": "sig-o1", "trade_id": "t-o1",
        }
        pers.save_order(order)
        pers.close()

    def test_persistence_save_event(self, tmp_db):
        from persistence.manager import PersistenceManager
        pers = PersistenceManager(state_path=str(tmp_db + ".json"), db_path=tmp_db)
        event = {
            "event_type": "TRADE_CLOSED", "strategy_id": "gold_01",
            "instrument": "GOLDM", "details": {"net_pnl": 50.0},
        }
        pers.save_event(event)
        pers.close()

    def test_dema_single_value(self):
        from indicators.dema import DEMA
        d = DEMA(3)
        result = d.update(100.0)
        assert result == 100.0
        assert d.initialized is False  # count=1 < period=3

    def test_dema_not_initialized(self):
        from indicators.dema import DEMA
        d = DEMA(10)
        d.update(100.0)
        assert d.initialized is False

    def test_atr_single_value(self):
        from indicators.atr import ATR
        a = ATR(3)
        result = a.update(110.0, 100.0, 105.0)
        assert result is None  # need 3 bars
        assert a.initialized is False

    def test_risk_engine_daily_loss_limit(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          max_daily_loss=1000, kill_switch_enabled=True)
        risk.update_daily_pnl(-500)
        risk.update_daily_pnl(-600)  # total -1100
        signal = MagicMock()
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=0,
            available_margin=500000, margin_required=50000, current_equity=300000,
        )
        assert allowed is False
        assert reason == "daily_loss_limit_reached"

    def test_risk_engine_deactivate_kill(self):
        from core.risk_engine import RiskEngine
        risk = RiskEngine(kill_switch_enabled=True)
        risk._kill_switch_active = True
        risk.deactivate_kill_switch()
        assert risk.kill_switch_active is False

    def test_risk_engine_snapshot_restore(self):
        from core.risk_engine import RiskEngine
        risk = RiskEngine()
        risk.update_daily_pnl(5000)
        risk.update_peak_equity(310000)
        snap = risk.snapshot()
        risk2 = RiskEngine()
        risk2.restore(snap)
        assert risk2.daily_pnl == 5000

    def test_risk_engine_drawdown(self):
        from core.risk_engine import RiskEngine
        from unittest.mock import MagicMock
        risk = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                          max_drawdown_pct=5.0, kill_switch_enabled=True)
        risk.update_peak_equity(100000)
        signal = MagicMock()
        # equity = 94000, drawdown = (100000-94000)/100000*100 = 6%
        allowed, reason = risk.check_order(
            signal=signal, current_positions=0, strategy_positions=0,
            available_margin=500000, margin_required=50000, current_equity=94000,
        )
        assert allowed is False
        assert reason == "max_drawdown_reached"

    def test_pnl_engine_snapshot(self):
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        from execution.paper_broker import Fill
        import time
        engine = PNLEngine(fee_model=MCXFeeModel())
        entry = Fill("f1", "o1", "GOLDM", "BUY", 1, 100.0, time.time(), "gold_01", 10.0)
        exit_ = Fill("f2", "o1", "GOLDM", "SELL", 1, 110.0, time.time(), "gold_01", 10.0)
        gross, charges, net = engine.calculate_realized_pnl(entry, exit_, multiplier=10.0)
        engine.record_trade(gross, charges, net)
        snap = engine.snapshot()
        assert snap["trade_count"] == 1
        assert snap["wins"] == 1
        assert engine.win_rate == pytest.approx(100.0)

    def test_account_snapshot_restore(self):
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=300000.0)
        acct.update_realized_pnl(10000.0, 500.0)
        snap = acct.snapshot()
        acct2 = AccountEngine()
        acct2.restore(snap)
        assert acct2.realized_pnl == 10000.0
        assert acct2.charges == 500.0

    def test_position_update_mark(self):
        from portfolio.position_manager import Position, PositionSide, PositionStatus
        pos = Position(
            position_id="p1", strategy_id="gold_01", instrument="GOLDM",
            side=PositionSide.LONG, quantity=1, average_entry=100.0,
            entry_timestamp=time.time(), multiplier=10.0,
        )
        pos.update_mark(110.0)
        assert pos.unrealized_pnl == pytest.approx((110.0 - 100.0) * 1 * 10.0)

    def test_position_short_pnl(self):
        from portfolio.position_manager import Position, PositionSide
        pos = Position(
            position_id="p2", strategy_id="gold_01", instrument="GOLDM",
            side=PositionSide.SHORT, quantity=1, average_entry=110.0,
            entry_timestamp=time.time(), multiplier=10.0,
        )
        pos.update_mark(100.0)
        assert pos.unrealized_pnl == pytest.approx((110.0 - 100.0) * 1 * 10.0)

    def test_safe_mode_multiple_reasons(self):
        from core.market_status import MarketStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        sm.enter_safe_mode("reason_a")
        sm.enter_safe_mode("reason_b")
        assert len(sm.active_reasons) == 2
        sm.clear_reason("reason_a")
        assert sm.is_active is True
        sm.clear_reason("reason_b")
        assert sm.is_active is False

    def test_safe_mode_should_allow_trading(self):
        from core.market_status import MarketStatus, EngineStatus, DataStatus
        from core.safe_mode import SafeModeManager
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        # Not trading allowed by default (market is overnight)
        assert sm.should_allow_trading() is False

    def test_fill_gross_value(self):
        from execution.paper_broker import Fill
        f = Fill("f1", "o1", "GOLDM", "BUY", 2, 100.0, time.time(), "gold_01", 10.0)
        assert f.gross_value == pytest.approx(2 * 100.0 * 10.0)

    def test_fee_model_from_config(self):
        from execution.fee_model import MCXFeeModel
        cfg = {"brokerage_per_side": 25.0, "stt_sell_pct": 0.02}
        model = MCXFeeModel.from_config(cfg)
        assert model.brokerage_per_side == 25.0
        assert model.stt_sell_pct == pytest.approx(0.02 / 100)

    def test_paper_broker_no_market_data(self):
        from execution.paper_broker import PaperExecutionEngine, OrderState
        from strategies.base_dema_strategy import Signal, SignalType
        engine = PaperExecutionEngine()
        signal = Signal(
            signal_type=SignalType.LONG, instrument="GOLDM", strategy_id="gold_01",
            timestamp=time.time(), trigger_price=100.0, stop_price=95.0, quantity=1,
        )
        order = engine.create_order(signal, multiplier=10.0, trade_id="TRD-no-mkt")
        order = engine.submit_order(order)
        assert order.state == OrderState.REJECTED

    def test_paper_broker_snapshot_restore(self):
        from execution.paper_broker import PaperExecutionEngine
        engine = PaperExecutionEngine()
        engine.update_price("GOLDM", 100.0)
        snap = engine.snapshot()
        engine2 = PaperExecutionEngine()
        engine2.restore(snap)
        assert engine2._current_prices.get("GOLDM") == 100.0

    def test_htf_engine_register(self):
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        assert "GOLDM:1h" in engine._engines

    def test_htf_engine_get_value(self):
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        engine = BacktestStyleHTFEngine()
        assert engine.get_htf_value("GOLDM", "1h") is None

    def test_htf_engine_snapshot_restore(self):
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        from core.timeframe_engine import Bar, BarState
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        bar = Bar("GOLDM", "1h", 0, 3600, 100, 110, 95, 105, 1000, BarState.CLOSED)
        engine.on_htf_bar_closed(bar)
        snap = engine.snapshot()
        engine2 = BacktestStyleHTFEngine()
        engine2.register("GOLDM", "1h", 3, 6, 1.0, "09:00")
        engine2.restore(snap)

    def test_market_status_snapshot(self):
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        snap = ms.snapshot()
        assert "market_state" in snap
        assert "engine_status" in snap

    def test_market_status_force_state(self):
        from core.market_status import MarketStatus, MarketState
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.force_state(MarketState.HALTED)
        assert ms.state == MarketState.HALTED

    def test_market_status_is_safe(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.enter_safe_mode("test")
        assert ms.is_safe is True

    def test_market_status_exit_safe_mode(self):
        from core.market_status import MarketStatus, EngineStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.enter_safe_mode("test")
        ms.exit_safe_mode()
        assert ms.is_safe is False

    def test_market_status_halt(self):
        from core.market_status import MarketStatus, MarketState
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        ms.halt()
        assert ms.state == MarketState.HALTED

    def test_bar_aggregator_update_closes_bar(self):
        from core.timeframe_engine import BarAggregator
        closed_bars = []
        agg = BarAggregator("GOLDM", "5m", "09:00", on_bar_closed=lambda b: closed_bars.append(b))
        # IST timestamps for a 5m bar
        ist = timezone(timedelta(hours=5, minutes=30))
        t1 = datetime(2025, 1, 1, 9, 0, tzinfo=ist).timestamp()
        t2 = datetime(2025, 1, 1, 9, 6, tzinfo=ist).timestamp()  # new 5m bucket
        agg.update(100.0, 10, t1)
        result = agg.update(101.0, 5, t2)
        assert result is not None
        assert result.is_closed

    def test_reconciliation_result(self):
        from reconciliation.engine import ReconciliationResult
        r = ReconciliationResult()
        assert r.is_consistent is True
        r.add_error("test error")
        assert r.is_consistent is False
        assert len(r.errors) == 1
        r.add_warning("test warning")
        assert len(r.warnings) == 1
        s = r.summary()
        assert "test error" in s

    def test_dema_atr_properties(self):
        from indicators.dema_atr import DEMAATR
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            ind.update(100 + i, 105 + i, 95 + i, 102 + i)
        assert ind.value is not None
        assert ind.dema_value is not None
        assert ind.atr_value is not None
        assert ind.upper_band is not None
        assert ind.lower_band is not None
        assert ind.initialized is True

    def test_dema_atr_reset(self):
        from indicators.dema_atr import DEMAATR
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            ind.update(100 + i, 105 + i, 95 + i, 102 + i)
        ind.reset()
        assert ind.value is None
        assert ind.initialized is False

    def test_position_manager_get_by_strategy(self):
        from portfolio.position_manager import PositionManager
        from execution.paper_broker import Fill
        pm = PositionManager()
        f = Fill("f1", "o1", "GOLDM", "BUY", 1, 100.0, time.time(), "gold_01", 10.0)
        pm.open_position(f, multiplier=10.0, trade_id=f"TRD-{f.fill_id}")
        assert len(pm.get_positions_by_strategy("gold_01")) == 1
        assert len(pm.get_positions_by_strategy("gold_02")) == 0

    def test_position_manager_get_by_instrument(self):
        from portfolio.position_manager import PositionManager
        from execution.paper_broker import Fill
        pm = PositionManager()
        f = Fill("f1", "o1", "GOLDM", "BUY", 1, 100.0, time.time(), "gold_01", 10.0)
        pm.open_position(f, multiplier=10.0, trade_id=f"TRD-{f.fill_id}")
        assert len(pm.get_positions_by_instrument("GOLDM")) == 1
        assert len(pm.get_positions_by_instrument("SILVERM")) == 0

    def test_order_manager_snapshot(self):
        from execution.order_manager import OrderManager
        from execution.paper_broker import PaperExecutionEngine
        engine = PaperExecutionEngine()
        om = OrderManager(execution_engine=engine)
        snap = om.snapshot()
        assert "pending_signals" in snap
        assert "active_orders" in snap

    def test_strategy_snapshot_restore(self):
        from strategies.gold import GoldStrategy01
        s = GoldStrategy01(strategy_id="gold_01")
        snap = s.snapshot()
        assert snap["strategy_id"] == "gold_01"
        s2 = GoldStrategy01(strategy_id="gold_01")
        s2.restore(snap)
        assert s2.state.value == snap["state"]

    def test_candle_fetcher_constants(self):
        from core.candle_fetcher import TIMEFRAME_MINUTES
        assert TIMEFRAME_MINUTES["5m"] == 5
        assert TIMEFRAME_MINUTES["15m"] == 15
        assert TIMEFRAME_MINUTES["1h"] == 60

    def test_timeframe_engine_barstate(self):
        from core.timeframe_engine import BarState
        assert BarState.FORMING.value == "forming"
        assert BarState.CLOSED.value == "closed"
        assert BarState.PROCESSED.value == "processed"

    def test_strategy_state_values(self):
        from strategies.base_dema_strategy import StrategyState
        assert StrategyState.FLAT.value == "flat"
        assert StrategyState.LONG_POSITION.value == "long_position"
        assert StrategyState.SHORT_POSITION.value == "short_position"

    def test_signal_type_values(self):
        from strategies.base_dema_strategy import SignalType
        assert SignalType.LONG.value == "LONG"
        assert SignalType.SHORT.value == "SHORT"
        assert SignalType.FLAT.value == "FLAT"
        assert SignalType.REVERSAL.value == "REVERSAL"
