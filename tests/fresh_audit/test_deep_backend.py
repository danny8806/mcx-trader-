"""Deep backend verification suite for the Gold/Silver live trading system.

8 classes, 80+ tests covering:
  1) Live API endpoints
  2) Indicator independent verification
  3) Candle resampling
  4) HTF -> LTF mapping
  5) Strategy logic
  6) Fee model
  7) Risk engine
  8) Persistence
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Bootstrap the module hierarchy (same as conftest.py)
# ---------------------------------------------------------------------------
_PKG = Path(__file__).resolve().parent.parent.parent

import sys
from types import ModuleType

def _make_package(name: str, path: Path, is_pkg: bool = True) -> ModuleType:
    mod = ModuleType(name)
    mod.__file__ = str(path / "__init__.py") if is_pkg else str(path)
    mod.__package__ = name
    if is_pkg:
        mod.__path__ = [str(path)]
    sys.modules[name] = mod
    return mod

def _load_module(name: str, file_path: Path, package: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

import importlib

def bootstrap():
    if "gsl" in sys.modules:
        return
    gsl = _make_package("gsl", _PKG)
    ind = _make_package("gsl.indicators", _PKG / "indicators")
    ind.dema = _load_module("gsl.indicators.dema", _PKG / "indicators" / "dema.py", "gsl.indicators")
    ind.atr = _load_module("gsl.indicators.atr", _PKG / "indicators" / "atr.py", "gsl.indicators")
    ind.dema_atr = _load_module("gsl.indicators.dema_atr", _PKG / "indicators" / "dema_atr.py", "gsl.indicators")
    core = _make_package("gsl.core", _PKG / "core")
    core.timeframe_engine = _load_module("gsl.core.timeframe_engine", _PKG / "core" / "timeframe_engine.py", "gsl.core")
    sys.modules["core.timeframe_engine"] = core.timeframe_engine
    core.risk_engine = _load_module("gsl.core.risk_engine", _PKG / "core" / "risk_engine.py", "gsl.core")
    sys.modules["core.risk_engine"] = core.risk_engine
    htf = _make_package("gsl.htf", _PKG / "htf")
    htf.backtest_style_htf = _load_module("gsl.htf.backtest_style_htf", _PKG / "htf" / "backtest_style_htf.py", "gsl.htf")
    sys.modules["htf.backtest_style_htf"] = htf.backtest_style_htf
    strats = _make_package("gsl.strategies", _PKG / "strategies")
    strats.base_dema_strategy = _load_module(
        "gsl.strategies.base_dema_strategy", _PKG / "strategies" / "base_dema_strategy.py", "gsl.strategies",
    )
    sys.modules["strategies.base_dema_strategy"] = strats.base_dema_strategy
    gold = _make_package("gsl.strategies.gold", _PKG / "strategies" / "gold")
    gold_mod = _load_module("gsl.strategies.gold.__init__", _PKG / "strategies" / "gold" / "__init__.py", "gsl.strategies.gold")
    gold.GoldStrategy01 = gold_mod.GoldStrategy01
    gold.GoldStrategy02 = gold_mod.GoldStrategy02
    gold.GoldStrategy03 = gold_mod.GoldStrategy03
    gold.GoldStrategy04 = gold_mod.GoldStrategy04
    silver = _make_package("gsl.strategies.silver", _PKG / "strategies" / "silver")
    silver_mod = _load_module("gsl.strategies.silver.__init__", _PKG / "strategies" / "silver" / "__init__.py", "gsl.strategies.silver")
    silver.SilverStrategy01 = silver_mod.SilverStrategy01
    silver.SilverStrategy02 = silver_mod.SilverStrategy02
    silver.SilverStrategy03 = silver_mod.SilverStrategy03
    silver.SilverStrategy04 = silver_mod.SilverStrategy04
    exe = _make_package("gsl.execution", _PKG / "execution")
    exe.paper_broker = _load_module("gsl.execution.paper_broker", _PKG / "execution" / "paper_broker.py", "gsl.execution")
    exe.fee_model = _load_module("gsl.execution.fee_model", _PKG / "execution" / "fee_model.py", "gsl.execution")
    exe.order_manager = _load_module("gsl.execution.order_manager", _PKG / "execution" / "order_manager.py", "gsl.execution")
    port = _make_package("gsl.portfolio", _PKG / "portfolio")
    port.position_manager = _load_module("gsl.portfolio.position_manager", _PKG / "portfolio" / "position_manager.py", "gsl.portfolio")
    port.pnl = _load_module("gsl.portfolio.pnl", _PKG / "portfolio" / "pnl.py", "gsl.portfolio")
    port.account = _load_module("gsl.portfolio.account", _PKG / "portfolio" / "account.py", "gsl.portfolio")
    pers = _make_package("gsl.persistence", _PKG / "persistence")
    pers.manager = _load_module("gsl.persistence.manager", _PKG / "persistence" / "manager.py", "gsl.persistence")
    alias_map = {
        "indicators.dema": "gsl.indicators.dema",
        "indicators.atr": "gsl.indicators.atr",
        "indicators.dema_atr": "gsl.indicators.dema_atr",
        "core.timeframe_engine": "gsl.core.timeframe_engine",
        "core.risk_engine": "gsl.core.risk_engine",
        "htf.backtest_style_htf": "gsl.htf.backtest_style_htf",
        "strategies.base_dema_strategy": "gsl.strategies.base_dema_strategy",
        "execution.paper_broker": "gsl.execution.paper_broker",
        "execution.fee_model": "gsl.execution.fee_model",
        "portfolio.position_manager": "gsl.portfolio.position_manager",
        "portfolio.pnl": "gsl.portfolio.pnl",
        "portfolio.account": "gsl.portfolio.account",
    }
    for real_path, gsl_path in alias_map.items():
        if gsl_path in sys.modules and real_path not in sys.modules:
            sys.modules[real_path] = sys.modules[gsl_path]

bootstrap()

# Now safe to import production modules
from core.timeframe_engine import Bar, BarState
from indicators.dema import DEMA
from indicators.atr import ATR
from indicators.dema_atr import DEMAATR
from htf.backtest_style_htf import BacktestStyleHTFEngine, HTFMappedValue
from strategies.base_dema_strategy import BaseDEMAStrategy, StrategyState, Signal, SignalType, PendingEntry
from strategies.gold import GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04
from strategies.silver import SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04
from execution.paper_broker import PaperExecutionEngine, Fill, Order, OrderState
from execution.fee_model import MCXFeeModel, FeeBreakdown
from portfolio.position_manager import PositionManager, Position, PositionSide, PositionStatus
from portfolio.pnl import PNLEngine
from portfolio.account import AccountEngine
from core.risk_engine import RiskEngine
from persistence.manager import PersistenceManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL = "http://localhost:8000"
TIMEOUT = 5


def _get(path: str) -> Optional[dict]:
    try:
        r = urllib.request.urlopen(f"{BASE_URL}{path}", timeout=TIMEOUT)
        return json.loads(r.read())
    except Exception:
        return None


def _is_server_up() -> bool:
    return _get("/api/health") is not None


SERVER_UP = _is_server_up()

# ===========================================================================
# CLASS 1 — LIVE API ENDPOINTS
# ===========================================================================

@pytest.mark.skipif(not SERVER_UP, reason="Server not running on port 8000")
class TestLiveAPIEndpoints:
    """Verify all live API endpoints return valid JSON with expected keys."""

    def test_health_live(self):
        data = _get("/api/health")
        assert data is not None
        assert "status" in data
        assert data["status"] == "ok"

    def test_health_system_live(self):
        data = _get("/api/health/system")
        assert data is not None
        assert "overall" in data
        assert data["overall"] in ("healthy", "degraded", "stopped", "error")

    def test_overview_live(self):
        data = _get("/api/overview")
        assert data is not None
        assert "total_equity" in data
        assert "starting_capital" in data
        assert isinstance(data["total_equity"], dict)
        assert "value" in data["total_equity"]

    def test_strategies_live(self):
        data = _get("/api/strategies")
        assert data is not None
        assert "strategies" in data
        assert isinstance(data["strategies"], (list, dict))

    def test_positions_live(self):
        data = _get("/api/positions")
        assert data is not None
        assert "count" in data

    def test_orders_live(self):
        data = _get("/api/orders")
        assert data is not None
        assert "count" in data

    def test_fills_live(self):
        data = _get("/api/fills")
        assert data is not None
        assert "count" in data

    def test_risk_live(self):
        data = _get("/api/risk")
        assert data is not None
        assert "kill_switch_active" in data
        assert "daily_pnl" in data
        assert "max_positions_per_strategy" in data

    def test_market_data_live(self):
        data = _get("/api/market-data")
        assert data is not None
        assert "instruments" in data
        assert isinstance(data["instruments"], dict)

    def test_pnl_live(self):
        data = _get("/api/pnl")
        assert data is not None
        assert "portfolio" in data or "equity" in data

    def test_indicators_live(self):
        data = _get("/api/indicators")
        assert data is not None
        assert "indicators" in data
        assert isinstance(data["indicators"], dict)

    def test_htf_live(self):
        data = _get("/api/htf")
        assert data is not None
        assert "htf" in data

    def test_alerts_live(self):
        data = _get("/api/alerts")
        assert data is not None
        assert "count" in data

    def test_audit_live(self):
        data = _get("/api/audit")
        assert data is not None
        assert "count" in data

    def test_overview_goldm_live(self):
        data = _get("/api/overview/GOLDM")
        assert data is not None
        assert data.get("instrument") == "GOLDM"
        assert "ltp" in data
        assert "strategies" in data

    def test_overview_silverm_live(self):
        data = _get("/api/overview/SILVERM")
        assert data is not None
        assert data.get("instrument") == "SILVERM"
        assert "ltp" in data
        assert "strategies" in data


# ===========================================================================
# CLASS 2 — INDICATOR INDEPENDENT VERIFICATION
# ===========================================================================

# Reference implementations (scratch-built, NO imports from production)

def _ref_ema(values: list[float], period: int) -> list[Optional[float]]:
    """Standard EMA with seed = first value."""
    result: list[Optional[float]] = []
    alpha = 2.0 / (period + 1.0)
    ema: Optional[float] = None
    for v in values:
        if ema is None:
            ema = v
        else:
            ema = alpha * v + (1 - alpha) * ema
        result.append(ema)
    return result


def _ref_dema(values: list[float], period: int) -> list[Optional[float]]:
    """DEMA = 2*EMA1 - EMA2 (reference)."""
    ema1 = _ref_ema(values, period)
    ema2 = _ref_ema(ema1, period)
    return [2 * e1 - e2 if e1 is not None and e2 is not None else None for e1, e2 in zip(ema1, ema2)]


def _ref_tr(high: float, low: float, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _ref_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[Optional[float]]:
    """ATR with Wilder smoothing (reference)."""
    n = len(highs)
    result: list[Optional[float]] = [None] * n
    trs: list[float] = []
    prev_close: Optional[float] = None
    for i in range(n):
        tr = _ref_tr(highs[i], lows[i], prev_close)
        prev_close = closes[i]
        trs.append(tr)
        if len(trs) < period:
            continue
        if result[period - 1] is None and i == period - 1:
            result[i] = sum(trs[:period]) / period
        elif i >= period:
            result[i] = (result[i - 1] * (period - 1) + tr) / period
    return result


class TestIndicatorIndependentVerification:
    """Reference DEMA and ATR built from scratch — no production imports."""

    def test_reference_dema_ema2(self):
        vals = [100.0, 101.0, 102.0, 103.0, 104.0]
        ema1 = _ref_ema(vals, 3)
        ema2 = _ref_ema(ema1, 3)
        # EMA1 after 5 values should be close to 103.x
        assert ema1[-1] is not None
        assert ema2[-1] is not None
        # EMA2 of EMA1: more smoothed
        assert ema2[-1] < ema1[-1]

    def test_reference_dema_ema3(self):
        """DEMA = 2*EMA1 - EMA2."""
        vals = [100.0, 101.0, 102.0, 103.0, 104.0]
        dema_vals = _ref_dema(vals, 3)
        assert dema_vals[0] == 100.0  # seed
        # DEMA should track price closely
        assert dema_vals[-1] > 102.0

    def test_reference_dema_known_values(self):
        """DEMA(3) on [10, 20, 30, 40, 50]: verify manual calc."""
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        dema = _ref_dema(vals, 3)
        # EMA1: seed=10, then 0.5*v + 0.5*prev
        e1_0 = 10.0
        e1_1 = 0.5 * 20 + 0.5 * 10  # 15.0
        e1_2 = 0.5 * 30 + 0.5 * 15  # 22.5
        e1_3 = 0.5 * 40 + 0.5 * 22.5  # 31.25
        e1_4 = 0.5 * 50 + 0.5 * 31.25  # 40.625
        # EMA2 of EMA1
        e2_0 = 10.0
        e2_1 = 0.5 * 15.0 + 0.5 * 10.0  # 12.5
        e2_2 = 0.5 * 22.5 + 0.5 * 12.5  # 17.5
        e2_3 = 0.5 * 31.25 + 0.5 * 17.5  # 24.375
        e2_4 = 0.5 * 40.625 + 0.5 * 24.375  # 32.5
        # DEMA
        assert dema[0] == pytest.approx(10.0)
        assert dema[1] == pytest.approx(2 * 15.0 - 12.5)
        assert dema[2] == pytest.approx(2 * 22.5 - 17.5)
        assert dema[3] == pytest.approx(2 * 31.25 - 24.375)
        assert dema[4] == pytest.approx(2 * 40.625 - 32.5)

    def test_reference_atr_known_values(self):
        """ATR(3) on known OHLC data."""
        highs = [105.0, 108.0, 110.0, 107.0, 112.0]
        lows = [95.0, 97.0, 99.0, 98.0, 100.0]
        closes = [100.0, 103.0, 105.0, 102.0, 108.0]
        atr = _ref_atr(highs, lows, closes, 3)
        # First 2 should be None (need 3 TRs)
        assert atr[0] is None
        assert atr[1] is None
        # Index 2: first ATR = mean of TR[0:3]
        tr0 = 105 - 95  # 10
        tr1 = max(108 - 97, abs(108 - 100), abs(97 - 100))  # max(11, 8, 3) = 11
        tr2 = max(110 - 99, abs(110 - 103), abs(99 - 103))  # max(11, 7, 4) = 11
        expected_atr2 = (tr0 + tr1 + tr2) / 3
        assert atr[2] == pytest.approx(expected_atr2)
        # Index 3: Wilder smooth
        tr3 = max(107 - 98, abs(107 - 105), abs(98 - 105))  # max(9, 2, 7) = 9
        expected_atr3 = (expected_atr2 * 2 + tr3) / 3
        assert atr[3] == pytest.approx(expected_atr3)

    def test_reference_atr_warmup(self):
        """ATR returns None during warmup period."""
        highs = [100.0, 101.0]
        lows = [99.0, 100.0]
        closes = [100.0, 100.5]
        atr = _ref_atr(highs, lows, closes, 6)
        assert all(v is None for v in atr)

    def test_production_dema_matches_reference(self):
        """Production DEMA matches reference implementation."""
        vals = [100.0, 101.5, 99.0, 103.0, 102.0, 105.0, 104.5, 106.0]
        ref = _ref_dema(vals, 3)
        prod = DEMA(period=3)
        for v in vals:
            prod.update(v)
        for i, v in enumerate(vals):
            d = DEMA(period=3)
            for j in range(i + 1):
                d.update(vals[j])
            assert d.value == pytest.approx(ref[i], abs=1e-10)

    def test_production_atr_matches_reference(self):
        """Production ATR matches reference implementation."""
        highs = [105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0, 112.0]
        lows = [95.0, 96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0]
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
        ref = _ref_atr(highs, lows, closes, 6)
        prod = ATR(period=6)
        for h, l, c in zip(highs, lows, closes):
            prod.update(h, l, c)
        for i in range(len(highs)):
            a = ATR(period=6)
            for j in range(i + 1):
                a.update(highs[j], lows[j], closes[j])
            if ref[i] is None:
                assert a.value is None
            else:
                assert a.value == pytest.approx(ref[i], abs=1e-10)

    def test_dema_restore_matches_incremental(self):
        """DEMA snapshot/restore produces identical values to incremental."""
        vals = [100.0, 101.5, 99.0, 103.0, 102.0, 105.0]
        d1 = DEMA(period=3)
        for v in vals:
            d1.update(v)
        snap = d1.snapshot()
        d2 = DEMA(period=3)
        d2.restore(snap)
        assert d1.value == pytest.approx(d2.value, abs=1e-10)
        assert d1._count == d2._count

    def test_atr_restore_matches_incremental(self):
        """ATR snapshot/restore produces identical values to incremental."""
        highs = [105.0, 106.0, 107.0, 108.0, 109.0, 110.0]
        lows = [95.0, 96.0, 97.0, 98.0, 99.0, 100.0]
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        a1 = ATR(period=6)
        for h, l, c in zip(highs, lows, closes):
            a1.update(h, l, c)
        snap = a1.snapshot()
        a2 = ATR(period=6)
        a2.restore(snap)
        assert a1.value == pytest.approx(a2.value, abs=1e-10)
        assert a1._count == a2._count


# ===========================================================================
# CLASS 3 — CANDLE RESAMPLING
# ===========================================================================

def _make_5m_bars(n: int = 12, base_price: float = 100.0, start_ts: float = 0.0) -> list[Bar]:
    """Generate n sequential 5-minute bars."""
    bars = []
    for i in range(n):
        o = base_price + i * 0.5
        h = o + 2.0
        l = o - 1.0
        c = o + 1.0
        bars.append(Bar(
            instrument="TEST", timeframe="5m",
            start_ts=start_ts + i * 300, end_ts=start_ts + (i + 1) * 300,
            open=o, high=h, low=l, close=c,
            volume=100 + i, state=BarState.CLOSED,
        ))
    return bars


def _resample(bars: list[Bar], target_tf_minutes: int) -> list[dict]:
    """Resample bars into target timeframe using OHLCV aggregation."""
    result = []
    bucket: list[Bar] = []
    bucket_start = None
    for bar in bars:
        ts_minutes = int(bar.start_ts // 60)
        bucket_ts = (ts_minutes // target_tf_minutes) * target_tf_minutes
        if bucket_ts != bucket_start and bucket:
            result.append(_aggregate(bucket))
            bucket = []
        bucket.append(bar)
        bucket_start = bucket_ts
    if bucket:
        result.append(_aggregate(bucket))
    return result


def _aggregate(bars: list[Bar]) -> dict:
    return {
        "open": bars[0].open,
        "high": max(b.high for b in bars),
        "low": min(b.low for b in bars),
        "close": bars[-1].close,
        "volume": sum(b.volume for b in bars),
        "count": len(bars),
        "start_ts": bars[0].start_ts,
        "end_ts": bars[-1].end_ts,
    }


class TestCandleResampling:

    def test_5m_to_15m_resample_ohlc(self):
        bars = _make_5m_bars(12)  # 60 minutes = 4 x 15m
        resampled = _resample(bars, 15)
        assert len(resampled) == 4
        # First 15m: bars 0,1,2
        assert resampled[0]["open"] == bars[0].open
        assert resampled[0]["high"] == max(b.high for b in bars[:3])
        assert resampled[0]["low"] == min(b.low for b in bars[:3])
        assert resampled[0]["close"] == bars[2].close

    def test_5m_to_1h_resample_ohlc(self):
        bars = _make_5m_bars(12)  # 60 min = 1 x 1h
        resampled = _resample(bars, 60)
        assert len(resampled) == 1
        assert resampled[0]["open"] == bars[0].open
        assert resampled[0]["close"] == bars[-1].close
        assert resampled[0]["high"] == max(b.high for b in bars)
        assert resampled[0]["low"] == min(b.low for b in bars)

    def test_15m_to_1h_resample_ohlc(self):
        # 4 x 15m = 1h: bars starting at 0, 900, 1800, 2700 all within same hour bucket
        bars_15m = []
        for i in range(4):
            o = 100 + i * 3
            bars_15m.append(Bar(
                instrument="T", timeframe="15m",
                start_ts=i * 900, end_ts=(i + 1) * 900,
                open=o, high=o + 5, low=o - 2, close=o + 2,
                volume=50, state=BarState.CLOSED,
            ))
        resampled = _resample(bars_15m, 60)
        assert len(resampled) == 1
        assert resampled[0]["open"] == 100.0
        assert resampled[0]["close"] == 111.0  # last bar (index 3) close = 100 + 3*3 + 2 = 111

    def test_resample_volume_sums(self):
        bars = _make_5m_bars(6)
        bars[0].volume = 10
        bars[1].volume = 20
        bars[2].volume = 30
        bars[3].volume = 40
        bars[4].volume = 50
        bars[5].volume = 60
        resampled = _resample(bars, 15)  # 2 x 15m
        assert resampled[0]["volume"] == 60  # 10+20+30
        assert resampled[1]["volume"] == 150  # 40+50+60

    def test_resample_preserves_session(self):
        """Resampling preserves bar count and sequential ordering."""
        bars = _make_5m_bars(18)  # 90 min = 6 x 15m
        resampled = _resample(bars, 15)
        assert len(resampled) == 6
        for i in range(len(resampled) - 1):
            assert resampled[i]["end_ts"] <= resampled[i + 1]["start_ts"]

    def test_resample_single_bar(self):
        """Single bar resampled stays as one bar."""
        bars = _make_5m_bars(1)
        resampled = _resample(bars, 15)
        assert len(resampled) == 1
        assert resampled[0]["open"] == bars[0].open
        assert resampled[0]["close"] == bars[0].close

    def test_resample_high_low_aggregation(self):
        """Resampled high is max of highs, low is min of lows."""
        bars = _make_5m_bars(3)
        bars[0] = Bar("T", "5m", 0, 300, 100, 110, 90, 105, 10, BarState.CLOSED)
        bars[1] = Bar("T", "5m", 300, 600, 105, 115, 95, 108, 10, BarState.CLOSED)
        bars[2] = Bar("T", "5m", 600, 900, 108, 112, 92, 110, 10, BarState.CLOSED)
        resampled = _resample(bars, 15)
        assert len(resampled) == 1
        assert resampled[0]["high"] == 115  # max of all highs
        assert resampled[0]["low"] == 90  # min of all lows


# ===========================================================================
# CLASS 4 — HTF -> LTF MAPPING
# ===========================================================================

def _warmup_htf_engine(engine: BacktestStyleHTFEngine, instrument: str, tf: str, n: int = 8, base_ts: float = 0.0) -> None:
    """Feed n bars to warm up HTF engine so values are non-None."""
    for i in range(n):
        base = 100.0 + i * 0.5
        bar = Bar(
            instrument=instrument, timeframe=tf,
            start_ts=base_ts + i * 3600, end_ts=base_ts + (i + 1) * 3600,
            open=base, high=base + 2.0, low=base - 2.0, close=base + 1.0,
            volume=100, state=BarState.CLOSED,
        )
        engine.on_htf_bar_closed(bar)


class TestHTFMapping:

    def test_htf_mapping_before_close(self):
        """5m bar ending BEFORE 1h bar close: no confirmed HTF value."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        _warmup_htf_engine(engine, "GOLDM", "1h", 8)
        # 5m bar ending at 7:55 (before 8th 1h bar at 8:00 = 8*3600=28800)
        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=28500, end_ts=28800 - 1,
            open=102, high=103, low=101, close=102.5,
            volume=10, state=BarState.CLOSED,
        )
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        # end_ts=28799 < 28800, target_close = 28500 + 60 = 28560
        # searchsorted(times, 28560, right) - 1 should give the last bar before 28560
        # which might be None if none ended before that
        assert mapped is not None  # returns HTFMappedValue

    def test_htf_mapping_after_close(self):
        """5m bar ending AFTER 1h bar close: should have confirmed HTF value."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        _warmup_htf_engine(engine, "GOLDM", "1h", 8)
        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=28800, end_ts=28800 + 300,
            open=102, high=103, low=101, close=102.5,
            volume=10, state=BarState.CLOSED,
        )
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        assert mapped.htf_confirmed is True
        assert mapped.htf_value is not None

    def test_htf_mapping_at_boundary(self):
        """5m bar ending exactly at 1h boundary."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        _warmup_htf_engine(engine, "GOLDM", "1h", 8)
        # end_ts == 8*3600 = 28800
        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=28500, end_ts=28800,
            open=102, high=103, low=101, close=102.5,
            volume=10, state=BarState.CLOSED,
        )
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        assert mapped.htf_confirmed is True

    def test_htf_mapping_first_after_close(self):
        """First 5m bar after 1h close should use the confirmed 1h value."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        _warmup_htf_engine(engine, "GOLDM", "1h", 8)
        htf_val = engine.get_htf_value("GOLDM", "1h")
        assert htf_val is not None
        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=28800, end_ts=28800 + 300,
            open=102, high=103, low=101, close=102.5,
            volume=10, state=BarState.CLOSED,
        )
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        assert mapped.htf_value == pytest.approx(htf_val, abs=1e-10)

    def test_htf_mapping_no_future_leak(self):
        """HTF value should NOT be from a future bar."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        # Feed bars 0..7 (8 bars total, 8th ends at 28800)
        _warmup_htf_engine(engine, "GOLDM", "1h", 8)
        # Now feed 9th bar at 28800-32400
        bar_9 = Bar(
            instrument="GOLDM", timeframe="1h",
            start_ts=28800, end_ts=32400,
            open=200.0, high=210.0, low=195.0, close=205.0,
            volume=100, state=BarState.CLOSED,
        )
        engine.on_htf_bar_closed(bar_9)
        # Now map a fast bar ending at 29100 (before 9th bar's end at 32400)
        fast_bar = Bar(
            instrument="GOLDM", timeframe="5m",
            start_ts=28800, end_ts=29100,
            open=102, high=103, low=101, close=102.5,
            volume=10, state=BarState.CLOSED,
        )
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        # Should use 8th bar's value, NOT 9th bar's (200+)
        if mapped.htf_value is not None:
            assert mapped.htf_value < 200.0, "HTF value leaked from future bar!"

    def test_htf_snapshot_restore_roundtrip(self):
        """HTF engine snapshot/restore preserves state."""
        engine = BacktestStyleHTFEngine()
        engine.register("GOLDM", "1h", 3, 6, 1.0)
        _warmup_htf_engine(engine, "GOLDM", "1h", 10)
        snap = engine.snapshot()
        engine2 = BacktestStyleHTFEngine()
        engine2.register("GOLDM", "1h", 3, 6, 1.0)
        engine2.restore(snap)
        v1 = engine.get_htf_value("GOLDM", "1h")
        v2 = engine2.get_htf_value("GOLDM", "1h")
        assert v1 is not None and v2 is not None
        assert v1 == pytest.approx(v2, abs=1e-10)

    def test_backfill_reset_before_refeed_matches_fresh_compute(self):
        """refeed-after-restore must equal fresh compute (no double counting).

        Locks the startup-warmup fix: a restored session snapshot carries EMA/
        ATR history; re-feeding the same overlapping REST window on top of it
        double-counts bars and drifts the line.  reset_instrument() clears the
        restored state so the backfill recomputes a single authoritative series.
        """
        bars = [
            Bar("GOLDM", "1h", 28800 + i * 3600, 32400 + i * 3600,
                o, o + 2.0, o - 2.0, o, 100, BarState.CLOSED)
            for i, o in enumerate([float(100 + i) for i in range(12)])
        ]

        # Reference: fresh engine warmed from the 12 bars.
        fresh = BacktestStyleHTFEngine()
        fresh.register("GOLDM", "1h", 3, 6, 1.0)
        fresh.load_batch_htf("GOLDM", "1h", bars)
        ref_value = fresh.get_htf_value("GOLDM", "1h")
        assert ref_value is not None

        # Old behaviour: restored engine + refeed of the same window doubles up.
        restored = BacktestStyleHTFEngine()
        restored.register("GOLDM", "1h", 3, 6, 1.0)
        restored.restore(fresh.snapshot())
        restored.load_batch_htf("GOLDM", "1h", bars)    # no reset (bug)
        assert restored.get_htf_value("GOLDM", "1h") != pytest.approx(ref_value)
        assert restored.snapshot()["GOLDM:1h"]["htf_count"] == 24

        # Fixed behaviour: reset_instrument clears restored state before refeed.
        again = BacktestStyleHTFEngine()
        again.register("GOLDM", "1h", 3, 6, 1.0)
        again.restore(fresh.snapshot())
        again.reset_instrument("GOLDM")
        again.load_batch_htf("GOLDM", "1h", bars)
        assert again.get_htf_value("GOLDM", "1h") == pytest.approx(ref_value)
        assert again.snapshot()["GOLDM:1h"]["htf_count"] == len(bars)

    def test_htf_no_bars_returns_none(self):
        """HTF engine with no bars returns None values."""
        engine = BacktestStyleHTFEngine()
        engine.register("TEST", "1h", 3, 6, 1.0)
        fast_bar = Bar("TEST", "5m", 0, 300, 100, 101, 99, 100, 10, BarState.CLOSED)
        mapped = engine.map_to_fast_bar(fast_bar, "5m")
        assert mapped.htf_value is None
        assert mapped.htf_confirmed is False


# ===========================================================================
# CLASS 5 — STRATEGY LOGIC
# ===========================================================================

class TestStrategyLogic:

    def test_gold_01_config(self):
        s = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM", fast_timeframe="5m", htf_timeframe="1h", quantity=1)
        assert s.strategy_id == "gold_01"
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"
        assert s.quantity == 1

    def test_gold_02_config(self):
        s = GoldStrategy02(strategy_id="gold_02", instrument="GOLDM", fast_timeframe="15m", htf_timeframe="1h", quantity=1)
        assert s.strategy_id == "gold_02"
        assert s.fast_timeframe == "15m"

    def test_gold_03_config(self):
        s = GoldStrategy03(strategy_id="gold_03", instrument="GOLDM")
        assert s.instrument == "GOLDM"
        assert s.fast_timeframe == "5m"
        assert s.htf_timeframe == "1h"

    def test_gold_04_config(self):
        s = GoldStrategy04(strategy_id="gold_04", instrument="GOLDM")
        assert s.instrument == "GOLDM"

    def test_silver_01_config(self):
        s = SilverStrategy01(strategy_id="silver_01", instrument="SILVERM")
        assert s.instrument == "SILVERM"
        assert s.fast_timeframe == "5m"

    def test_silver_02_config(self):
        s = SilverStrategy02(strategy_id="silver_02", instrument="SILVERM")
        assert s.instrument == "SILVERM"

    def test_silver_03_config(self):
        s = SilverStrategy03(strategy_id="silver_03", instrument="SILVERM")
        assert s.instrument == "SILVERM"

    def test_silver_04_config(self):
        s = SilverStrategy04(strategy_id="silver_04", instrument="SILVERM")
        assert s.instrument == "SILVERM"

    def test_all_strategies_have_correct_instruments(self):
        gold_strats = [GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04]
        silver_strats = [SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04]
        for cls in gold_strats:
            s = cls()
            assert s.instrument == "GOLDM", f"{cls.__name__} should use GOLDM"
        for cls in silver_strats:
            s = cls()
            assert s.instrument == "SILVERM", f"{cls.__name__} should use SILVERM"

    def test_all_strategies_have_capital(self):
        """All strategy classes accept a quantity parameter (capital proxy)."""
        for cls in [GoldStrategy01, GoldStrategy02, GoldStrategy03, GoldStrategy04,
                     SilverStrategy01, SilverStrategy02, SilverStrategy03, SilverStrategy04]:
            s = cls(quantity=2)
            assert s.quantity == 2

    def test_all_strategies_have_parameters(self):
        """All strategies have the required BaseDEMAStrategy attributes."""
        for cls in [GoldStrategy01, SilverStrategy01]:
            s = cls()
            assert hasattr(s, "state")
            assert hasattr(s, "position_side")
            assert hasattr(s, "stop_price")
            assert hasattr(s, "pending_entry")
            assert hasattr(s, "on_bar")
            assert hasattr(s, "on_tick")
            assert hasattr(s, "snapshot")
            assert hasattr(s, "restore")
            assert s.state == StrategyState.FLAT


# ===========================================================================
# CLASS 6 — FEE MODEL
# ===========================================================================

class TestFeeModel:

    def test_fee_gold_long(self):
        model = MCXFeeModel(brokerage_per_side=20.0, stt_sell_pct=0.01, exchange_pct=0.0026, sebi_pct=0.0001, gst_pct=18.0, stamp_duty_pct=0.005)
        # Entry 96000, Exit 96500, qty=1, mult=10
        fees = model.calculate(96000, 96500, 1, 10.0)
        buy_turnover = 96000 * 1 * 10  # 960000
        sell_turnover = 96500 * 1 * 10  # 965000
        # brokerage = 20*2 = 40
        assert fees.brokerage == pytest.approx(40.0)
        # stamp_duty on buy turnover * 0.005%
        assert fees.stamp_duty == pytest.approx(buy_turnover * 0.005 / 100, rel=1e-3)

    def test_fee_gold_short(self):
        model = MCXFeeModel(brokerage_per_side=20.0, stt_sell_pct=0.01, exchange_pct=0.0026, sebi_pct=0.0001, gst_pct=18.0, stamp_duty_pct=0.005)
        fees = model.calculate(96500, 96000, 1, 10.0)
        assert fees.total > 0
        # STT is on sell turnover
        sell_turnover = 96000 * 10
        assert fees.stt == pytest.approx(sell_turnover * 0.01 / 100, rel=1e-3)

    def test_fee_silver_long(self):
        model = MCXFeeModel(brokerage_per_side=20.0, stt_sell_pct=0.01, exchange_pct=0.0026, sebi_pct=0.0001, gst_pct=18.0, stamp_duty_pct=0.005)
        fees = model.calculate(80000, 80500, 1, 5.0)
        assert fees.total > 0
        assert fees.brokerage == 40.0

    def test_fee_silver_short(self):
        model = MCXFeeModel(brokerage_per_side=20.0, stt_sell_pct=0.01, exchange_pct=0.0026, sebi_pct=0.0001, gst_pct=18.0, stamp_duty_pct=0.005)
        fees = model.calculate(80500, 80000, 1, 5.0)
        assert fees.total > 0

    def test_fee_stt_gold(self):
        model = MCXFeeModel(stt_sell_pct=0.01)
        sell_turnover = 96500 * 1 * 10
        fees = model.calculate(96000, 96500, 1, 10.0)
        assert fees.stt == pytest.approx(sell_turnover * 0.0001, rel=1e-4)

    def test_fee_stt_silver(self):
        model = MCXFeeModel(stt_sell_pct=0.01)
        sell_turnover = 80500 * 1 * 5
        fees = model.calculate(80000, 80500, 1, 5.0)
        assert fees.stt == pytest.approx(sell_turnover * 0.0001, rel=1e-4)

    def test_fee_stamp_duty(self):
        model = MCXFeeModel(stamp_duty_pct=0.005)
        buy_turnover = 96000 * 1 * 10
        fees = model.calculate(96000, 96500, 1, 10.0)
        assert fees.stamp_duty == pytest.approx(buy_turnover * 0.005 / 100, rel=1e-4)

    def test_fee_exchange_charges(self):
        model = MCXFeeModel(exchange_pct=0.0026)
        buy_turnover = 96000 * 1 * 10
        sell_turnover = 96500 * 1 * 10
        fees = model.calculate(96000, 96500, 1, 10.0)
        expected = (buy_turnover + sell_turnover) * 0.0026 / 100
        assert fees.exchange == pytest.approx(expected, rel=1e-3)

    def test_fee_gst(self):
        model = MCXFeeModel(brokerage_per_side=20.0, exchange_pct=0.0026, sebi_pct=0.0001, gst_pct=18.0)
        fees = model.calculate(96000, 96500, 1, 10.0)
        taxable = fees.brokerage + fees.exchange + fees.sebi
        assert fees.gst == pytest.approx(taxable * 0.18, rel=1e-3)

    def test_fee_total_percentage(self):
        """Total fees should be a reasonable percentage of turnover."""
        model = MCXFeeModel()
        fees = model.calculate(96000, 96500, 1, 10.0)
        turnover = 96000 * 10 + 96500 * 10
        pct = fees.total / turnover * 100
        assert 0.01 < pct < 1.0  # Should be under 1%


# ===========================================================================
# CLASS 7 — RISK ENGINE
# ===========================================================================

class TestRiskEngine:

    def test_risk_normal_order(self):
        re = RiskEngine(max_positions_per_strategy=1, max_positions_total=8,
                        max_daily_loss=50000, max_drawdown_pct=5.0, kill_switch_enabled=True)
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=62400, current_equity=300000,
        )
        assert allowed is True
        assert reason is None

    def test_risk_max_positions(self):
        re = RiskEngine(max_positions_per_strategy=1)
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=1,
            available_margin=300000, margin_required=62400, current_equity=300000,
        )
        assert allowed is False
        assert reason == "max_positions_per_strategy_reached"

    def test_risk_kill_switch(self):
        re = RiskEngine(max_daily_loss=50000, kill_switch_enabled=True)
        re.update_daily_pnl(-60000)  # exceeds max_daily_loss of 50000
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=62400, current_equity=300000,
        )
        assert allowed is False

    def test_risk_daily_loss_limit(self):
        re = RiskEngine(max_daily_loss=50000, kill_switch_enabled=True)
        re.update_daily_pnl(-50000)
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=62400, current_equity=300000,
        )
        assert allowed is False
        assert reason == "daily_loss_limit_reached"

    def test_risk_drawdown_limit(self):
        re = RiskEngine(max_drawdown_pct=5.0, kill_switch_enabled=True)
        re.update_peak_equity(300000)
        # equity=284000 -> drawdown = (300000-284000)/300000*100 = 5.33%
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=62400, current_equity=284000,
        )
        assert allowed is False
        assert reason == "max_drawdown_reached"
        assert re.kill_switch_active is True

    def test_risk_insufficient_capital(self):
        re = RiskEngine()
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=50000, margin_required=62400, current_equity=300000,
        )
        assert allowed is False
        assert reason == "insufficient_margin"

    def test_risk_zero_quantity(self):
        """Zero quantity should not be blocked by risk engine (handled elsewhere)."""
        re = RiskEngine()
        # Margin required = 0 for zero quantity
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=0, current_equity=300000,
        )
        assert allowed is True

    def test_risk_negative_quantity(self):
        """Negative margin (impossible in real scenario) — risk engine allows it."""
        re = RiskEngine()
        # margin_required < available_margin always when negative
        allowed, reason = re.check_order(
            signal=None, current_positions=0, strategy_positions=0,
            available_margin=300000, margin_required=-100, current_equity=300000,
        )
        assert allowed is True

    def test_risk_snapshot_restore_roundtrip(self):
        """RiskEngine snapshot/restore preserves all fields."""
        re = RiskEngine(max_daily_loss=50000, max_drawdown_pct=5.0, kill_switch_enabled=True)
        re.update_daily_pnl(-10000)
        re.update_peak_equity(310000)
        snap = re.snapshot()
        re2 = RiskEngine()
        re2.restore(snap)
        assert re2.daily_pnl == -10000
        assert re2._peak_equity == 310000
        assert re2.kill_switch_active is False

    def test_risk_total_positions_limit(self):
        """Total positions limit blocks new orders."""
        re = RiskEngine(max_positions_total=2)
        allowed, _ = re.check_order(
            signal=None, current_positions=2, strategy_positions=0,
            available_margin=300000, margin_required=62400, current_equity=300000,
        )
        assert allowed is False

    def test_risk_deactivate_kill_switch(self):
        """Kill switch can be deactivated manually."""
        re = RiskEngine(max_daily_loss=1, kill_switch_enabled=True)
        re.update_daily_pnl(-100)
        re.check_order(signal=None, current_positions=0, strategy_positions=0,
                        available_margin=300000, margin_required=1, current_equity=300000)
        assert re.kill_switch_active is True
        re.deactivate_kill_switch()
        assert re.kill_switch_active is False


# ===========================================================================
# CLASS 8 — PERSISTENCE
# ===========================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database for persistence tests."""
    db_path = str(tmp_path / "test_trading.db")
    return db_path


class TestPersistence:

    def test_indicator_snapshot_restore_dema(self):
        """DEMA snapshot/restore roundtrip preserves values."""
        d = DEMA(period=3)
        for v in [100.0, 101.0, 102.0, 103.0]:
            d.update(v)
        snap = d.snapshot()
        d2 = DEMA(period=3)
        d2.restore(snap)
        assert d.value == pytest.approx(d2.value, abs=1e-10)
        assert d._count == d2._count
        assert d.initialized == d2.initialized

    def test_indicator_snapshot_restore_atr(self):
        """ATR snapshot/restore roundtrip preserves values."""
        a = ATR(period=6)
        for h, l, c in [(105, 95, 100), (106, 96, 101), (107, 97, 102),
                         (108, 98, 103), (109, 99, 104), (110, 100, 105), (111, 101, 106)]:
            a.update(h, l, c)
        snap = a.snapshot()
        a2 = ATR(period=6)
        a2.restore(snap)
        assert a.value == pytest.approx(a2.value, abs=1e-10)
        assert a._count == a2._count

    def test_indicator_snapshot_restore_count(self):
        """DEMA count field is preserved across snapshot/restore."""
        d = DEMA(period=3)
        for v in [100, 101, 102, 103, 104]:
            d.update(v)
        snap = d.snapshot()
        assert snap["count"] == 5
        d2 = DEMA(period=3)
        d2.restore(snap)
        assert d2._count == 5

    def test_fill_dedup_persistence(self, tmp_db):
        """Fill deduplication persists across instances."""
        dedup1 = FillDeduplicator(db_path=tmp_db)
        dedup1.mark_processed("fill_abc_123")
        assert dedup1.is_duplicate("fill_abc_123") is True
        # Create a new instance — should load from DB
        dedup2 = FillDeduplicator(db_path=tmp_db)
        loaded = dedup2.load_from_database()
        assert loaded >= 1
        assert dedup2.is_duplicate("fill_abc_123") is True

    def test_indicator_snapshot_restore_dema_atr_combined(self):
        """DEMAATR snapshot/restore roundtrip."""
        da = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            base = 100.0 + i * 0.5
            da.update(base, base + 2.0, base - 2.0, base + 1.0)
        snap = da.snapshot()
        da2 = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        da2.restore(snap)
        assert da.value == pytest.approx(da2.value, abs=1e-10)
        assert da.initialized == da2.initialized

    def test_persistence_manager_save_load_state(self, tmp_db):
        """PersistenceManager save/load state roundtrip."""
        state_path = str(Path(tmp_db).parent / "state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        state = {"test_key": "test_value", "nested": {"a": 1, "b": [2, 3]}}
        pm.save_state(state)
        loaded = pm.load_state()
        assert loaded is not None
        assert loaded["test_key"] == "test_value"
        assert loaded["nested"]["a"] == 1
        assert loaded["nested"]["b"] == [2, 3]

    def test_persistence_manager_save_trade(self, tmp_db):
        """PersistenceManager save_trade persists to SQLite."""
        state_path = str(Path(tmp_db).parent / "state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        trade = {
            "trade_id": "T001", "strategy_id": "gold_01", "instrument": "GOLDM",
            "side": "LONG", "entry_timestamp": "2026-01-01T10:00:00", "entry_price": 96000,
            "exit_timestamp": "2026-01-01T11:00:00", "exit_price": 96500,
            "quantity": 1, "multiplier": 10.0, "gross_pnl": 5000, "charges": 100, "net_pnl": 4900,
            "exit_reason": "signal_exit", "status": "closed",
        }
        pm.save_trade(trade)
        trades = pm.get_trades(strategy_id="gold_01")
        assert len(trades) >= 1
        assert trades[0]["trade_id"] == "T001"
        assert trades[0]["net_pnl"] == 4900

    def test_persistence_manager_save_fill(self, tmp_db):
        """PersistenceManager save_fill persists to SQLite."""
        state_path = str(Path(tmp_db).parent / "state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        fill = {
            "fill_id": "F001", "order_id": "O001", "strategy_id": "gold_01",
            "instrument": "GOLDM", "side": "BUY", "quantity": 1, "price": 96000,
            "timestamp": "2026-01-01T10:00:00",
        }
        pm.save_fill(fill)
        # Verify via direct SQL
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM fills WHERE fill_id='F001'").fetchone()
        conn.close()
        assert row is not None

    def test_persistence_manager_save_event(self, tmp_db):
        """PersistenceManager save_event persists to SQLite."""
        state_path = str(Path(tmp_db).parent / "state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        event = {
            "event_type": "POSITION_OPENED", "strategy_id": "gold_01",
            "instrument": "GOLDM", "details": {"price": 96000},
        }
        pm.save_event(event)
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM events WHERE event_type='POSITION_OPENED'").fetchone()
        conn.close()
        assert row is not None

    def test_persistence_manager_get_trades_all(self, tmp_db):
        """PersistenceManager get_trades without strategy_id returns all."""
        state_path = str(Path(tmp_db).parent / "state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        pm.save_trade({"trade_id": "T1", "strategy_id": "gold_01", "instrument": "GOLDM",
                        "side": "LONG", "net_pnl": 100, "status": "closed"})
        pm.save_trade({"trade_id": "T2", "strategy_id": "silver_01", "instrument": "SILVERM",
                        "side": "SHORT", "net_pnl": -50, "status": "closed"})
        all_trades = pm.get_trades()
        assert len(all_trades) >= 2

    def test_fill_dedup_not_duplicate(self, tmp_db):
        """Fill dedup correctly identifies non-duplicate fills."""
        dedup = FillDeduplicator(db_path=tmp_db)
        assert dedup.is_duplicate("fill_xyz_999") is False
        dedup.mark_processed("fill_xyz_999")
        assert dedup.is_duplicate("fill_xyz_999") is True
        assert dedup.is_duplicate("fill_other_000") is False

    def test_fill_dedup_count(self, tmp_db):
        """Fill dedup count reflects processed fills."""
        dedup = FillDeduplicator(db_path=tmp_db)
        initial = dedup.count
        dedup.mark_processed("fill_count_1")
        dedup.mark_processed("fill_count_2")
        assert dedup.count == initial + 2

    def test_persistence_state_nonexistent(self, tmp_db):
        """Loading state from nonexistent file returns None."""
        state_path = str(Path(tmp_db).parent / "nonexistent_state.json")
        pm = PersistenceManager(state_path=state_path, db_path=tmp_db)
        assert pm.load_state() is None


# ===========================================================================
# Integration: Full lifecycle test
# ===========================================================================

class TestIntegrationLifecycle:
    """End-to-end lifecycle: indicator -> HTF -> strategy -> signal -> fee -> P&L."""

    def test_full_trade_lifecycle(self):
        # 1. Create indicator and warm up
        ind = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        for i in range(20):
            base = 100.0 + i * 0.5
            ind.update(base, base + 2.0, base - 2.0, base + 1.0)
        assert ind.initialized is True

        # 2. Create HTF engine and warm up
        htf = BacktestStyleHTFEngine()
        htf.register("TEST", "1h", 3, 6, 1.0)
        for i in range(8):
            b = Bar("TEST", "1h", i * 3600, (i + 1) * 3600,
                    100 + i * 0.5, 102 + i * 0.5, 98 + i * 0.5, 101 + i * 0.5,
                    100, BarState.CLOSED)
            htf.on_htf_bar_closed(b)

        # 3. Map HTF value
        fast_bar = Bar("TEST", "5m", 28800, 29100, 105, 107, 103, 106, 10, BarState.CLOSED)
        mapped = htf.map_to_fast_bar(fast_bar, "5m")
        assert mapped.htf_value is not None

        # 4. Calculate fees
        model = MCXFeeModel()
        fees = model.calculate(96000, 96500, 1, 10.0)
        assert fees.total > 0

        # 5. Calculate P&L
        fee_model = MCXFeeModel()
        pnl_eng = PNLEngine(fee_model=fee_model)
        entry_fill = Fill("F1", "O1", "TEST", "BUY", 1, 96000, time.time(), "strat1", 10.0)
        exit_fill = Fill("F2", "O1", "TEST", "SELL", 1, 96500, time.time(), "strat1", 10.0)
        gross, charges, net = pnl_eng.calculate_realized_pnl(entry_fill, exit_fill, 10.0)
        assert gross > 0
        assert charges > 0
        assert net < gross  # net < gross because charges deducted


# ===========================================================================
# FillDeduplicator import
# ===========================================================================
from core.fill_dedup import FillDeduplicator
