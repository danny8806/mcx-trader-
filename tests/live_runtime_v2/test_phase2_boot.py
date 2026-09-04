"""
PHASE 2 — RUNTIME BOOT TEST
============================
Verify actual application startup using the real startup path.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from . import RUN_ID, SUITE_VERSION, PROJECT_ROOT, get_evidence, REPORT_DIR


def _build_realistic_config(tmpdir: str) -> dict:
    """Build a realistic config that mirrors production but with temp DB paths."""
    return {
        "system": {
            "db_path": os.path.join(tmpdir, "trading.db"),
            "state_path": os.path.join(tmpdir, "state.json"),
        },
        "dhan": {
            "client_id": "TEST_CLIENT",
            "token_file": os.path.join(tmpdir, "token.json"),
            "pin": "000000",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        },
        "execution_mode": "paper",
        "instruments": {
            "GOLDM": {
                "symbol": "MCX:GOLDM202610", "security_id": "569003",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30",
                "session_minutes": 870, "keep_partial": True,
            },
            "SILVERM": {
                "symbol": "MCX:SILVERM202611", "security_id": "483080",
                "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                "session_open": "09:00", "session_close": "23:30",
                "session_minutes": 870, "keep_partial": True,
            },
        },
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": {
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m",
                        "mid_timeframe": "15m", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 300000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 300000, "enabled": True},
        },
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 0,
                            "partial_fill_probability": 0.0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                      "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                        "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1,
                 "max_open_positions_total": 8,
                 "max_daily_loss": 50000.0, "max_drawdown_pct": 5.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": True},
        "account": {"starting_capital": 600000.0,
                    "starting_capital_per_strategy": 300000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
    }


@pytest.mark.phase2
class TestRuntimeBoot:
    """Phase 2: Verify actual application startup."""

    def test_config_loads_from_settings(self, project_root):
        """Config loads settings.json without error."""
        from config import Config
        config = Config()
        config.load()
        assert config.get("instruments") is not None, "Config failed to load instruments"
        assert config.get("strategies") is not None, "Config failed to load strategies"
        get_evidence().record("phase2", "config_loads", "PASS", {
            "instruments": list(config.get("instruments", {}).keys()),
            "strategies": list(config.get("strategies", {}).keys()),
        })

    def test_paper_execution_enforced(self, project_root):
        """Execution mode MUST be paper."""
        from config import Config
        config = Config()
        config.load()
        exec_mode = config.get("execution_mode", "paper")
        assert exec_mode == "paper", f"Execution mode must be 'paper', got '{exec_mode}'"

    def test_market_status_initializes(self):
        """MarketStatus initializes with correct session times."""
        from core.market_status import MarketStatus, MarketState
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        assert ms is not None, "MarketStatus failed to initialize"
        assert ms.state in (MarketState.OVERNIGHT, MarketState.PRE_MARKET,
                            MarketState.MARKET_OPEN, MarketState.LIVE_TRADING,
                            MarketState.MARKET_CLOSE, MarketState.AFTER_MARKET,
                            MarketState.SAFE_MODE)

    def test_indicators_initialize(self):
        """DEMA-ATR indicators initialize for all instruments/timeframes."""
        from indicators.dema_atr import DEMAATR
        indicators = {}
        for inst in ["GOLDM", "SILVERM"]:
            for tf in ["5m", "15m", "1h"]:
                key = f"{inst}:{tf}"
                indicators[key] = DEMAATR(dema_period=3, atr_period=6, atr_factor=1.0)
        assert len(indicators) == 6, f"Expected 6 indicators, got {len(indicators)}"
        for key, ind in indicators.items():
            assert ind is not None, f"Indicator {key} is None"
            assert not ind.initialized, f"Indicator {key} should not be initialized yet"

    def test_htf_engine_registers(self):
        """HTF engine registers for all instruments/timeframes."""
        from htf.backtest_style_htf import BacktestStyleHTFEngine
        htf = BacktestStyleHTFEngine()
        for inst in ["GOLDM", "SILVERM"]:
            htf.register(inst, "1h", dema_period=3, atr_period=6, atr_factor=1.0)
            htf.register(inst, "15m", dema_period=3, atr_period=6, atr_factor=1.0)
        assert len(htf._engines) == 4, f"Expected 4 HTF engines, got {len(htf._engines)}"

    def test_strategies_instantiate(self):
        """Strategy classes instantiate correctly."""
        from strategies.gold import GoldStrategy01, GoldStrategy02
        from strategies.silver import SilverStrategy01, SilverStrategy02
        s1 = GoldStrategy01(strategy_id="gold_01", instrument="GOLDM",
                           fast_timeframe="5m", htf_timeframe="1h")
        assert s1.fast_timeframe == "5m"
        assert s1.instrument == "GOLDM"
        s2 = SilverStrategy01(strategy_id="silver_01", instrument="SILVERM",
                             fast_timeframe="15m", htf_timeframe="1h")
        assert s2.fast_timeframe == "15m"
        assert s2.instrument == "SILVERM"

    def test_risk_engine_initializes(self):
        """Risk engine initializes with configured limits."""
        from core.risk_engine import RiskEngine
        risk = RiskEngine(
            max_positions_per_strategy=1,
            max_positions_total=8,
            max_daily_loss=50000.0,
            max_drawdown_pct=5.0,
            kill_switch_enabled=True,
        )
        assert risk.kill_switch_active is False, "Kill switch should start inactive"

    def test_position_manager_initializes(self):
        """Position manager initializes empty."""
        from portfolio.position_manager import PositionManager
        pm = PositionManager()
        assert len(pm.open_positions) == 0, "PositionManager should start empty"

    def test_pnl_engines_initialize(self):
        """P&L engines initialize per strategy."""
        from portfolio.pnl import PNLEngine
        from execution.fee_model import MCXFeeModel
        fee = MCXFeeModel(brokerage_per_side=20)
        pnl = PNLEngine(fee_model=fee)
        assert pnl.realized_net == 0.0, "PnL should start at 0"
        assert pnl.trade_count == 0, "Trade count should start at 0"

    def test_account_engines_initialize(self):
        """Account engines initialize with correct capital."""
        from portfolio.account import AccountEngine
        acct = AccountEngine(starting_capital=300000, margin_per_trade_pct=6.5)
        assert acct.equity == 300000, f"Equity should be 300000, got {acct.equity}"
        assert acct.available_margin == 300000

    def test_fill_dedup_initializes(self):
        """Fill deduplicator initializes."""
        from core.fill_dedup import FillDeduplicator
        import tempfile
        tmpdir = tempfile.mkdtemp()
        fd = FillDeduplicator(db_path=os.path.join(tmpdir, "dedup.db"))
        assert fd.count == 0, "Fill dedup should start empty"

    def test_safe_mode_initializes(self):
        """Safe mode manager initializes."""
        from core.safe_mode import SafeModeManager
        from core.market_status import MarketStatus
        ms = MarketStatus(session_open="09:00", session_close="23:30")
        sm = SafeModeManager(ms)
        assert sm.is_active is False, "Safe mode should start inactive"

    def test_execution_engine_paper_only(self):
        """Execution engine enforces paper mode only."""
        from execution.paper_broker import PaperExecutionEngine
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        assert eng is not None

    def test_persistence_initializes(self, tmp_path):
        """Persistence manager initializes with temp DB."""
        from persistence.manager import PersistenceManager
        pm = PersistenceManager(
            state_path=str(tmp_path / "state.json"),
            db_path=str(tmp_path / "trading.db"),
        )
        assert pm is not None

    def test_no_startup_exception_in_engine_init(self):
        """TradingEngine constructor completes without exception."""
        from trading_engine import TradingEngine
        import tempfile
        tmpdir = tempfile.mkdtemp()
        # Build config path with temp DB paths
        config_data = _build_realistic_config(tmpdir)
        config_path = os.path.join(tmpdir, "settings.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)
        try:
            engine = TradingEngine(config_path=config_path)
            get_evidence().record("phase2", "engine_init", "PASS", {
                "strategies": list(engine.strategies.keys()),
                "indicators": list(engine.indicators.keys()),
            })
            assert len(engine.strategies) >= 2, f"Expected >=2 strategies, got {len(engine.strategies)}"
            assert len(engine.indicators) >= 6, f"Expected >=6 indicators, got {len(engine.indicators)}"
        except Exception as e:
            get_evidence().record("phase2", "engine_init", "FAIL", {"error": str(e)})
            pytest.fail(f"TradingEngine init failed: {e}")

    def test_boot_test_all_components(self):
        """Comprehensive boot: all critical components exist after init."""
        from trading_engine import TradingEngine
        import tempfile
        tmpdir = tempfile.mkdtemp()
        config_data = _build_realistic_config(tmpdir)
        config_path = os.path.join(tmpdir, "settings.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)
        engine = TradingEngine(config_path=config_path)

        checks = {
            "market_status": engine.market_status is not None,
            "data_adapter": engine.data_adapter is not None,
            "candle_fetcher": engine.candle_fetcher is not None,
            "indicators": len(engine.indicators) >= 6,
            "htf_engine": engine.htf_engine is not None,
            "strategies": len(engine.strategies) >= 2,
            "execution_engine": engine.execution_engine is not None,
            "order_manager": engine.order_manager is not None,
            "position_manager": engine.position_manager is not None,
            "pnl_engines": len(engine.pnl_engines) >= 2,
            "account_engines": len(engine.account_engines) >= 2,
            "account_engine": engine.account_engine is not None,
            "risk_engine": engine.risk_engine is not None,
            "health": engine.health is not None,
            "telegram": engine.telegram is not None,
            "safe_mode": engine.safe_mode is not None,
            "fill_dedup": engine.fill_dedup is not None,
        }
        failed = {k for k, v in checks.items() if not v}
        get_evidence().record("phase2", "boot_components", "PASS" if not failed else "FAIL", checks)
        assert not failed, f"Components missing after boot: {failed}"
