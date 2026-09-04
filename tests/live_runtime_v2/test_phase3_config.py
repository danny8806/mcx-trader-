"""PHASE 3 — CONFIGURATION VALIDATION"""
from __future__ import annotations

import json
import os

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestConfigValidation:
    """Phase 3: settings.json is correct, complete, and safe."""

    def _load_cfg(self):
        from config import Config
        return Config.load()

    def test_config_loads(self):
        """settings.json exists and is valid JSON."""
        path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "settings.json")
        assert os.path.exists(path), f"Config missing: {path}"
        with open(path) as f:
            cfg = json.load(f)
        assert isinstance(cfg, dict)
        get_evidence().record("phase3", "config_loads", "PASS", {"keys": list(cfg.keys())})

    def test_all_four_strategies_present(self):
        """Config has exactly gold_01, gold_02, silver_01, silver_02."""
        cfg = self._load_cfg()
        strats = cfg.get("strategies", {})
        expected = {"gold_01", "gold_02", "silver_01", "silver_02"}
        assert set(strats.keys()) == expected, f"Strategies mismatch: {set(strats.keys())} vs {expected}"

    def test_silver_01_fast_timeframe_is_15m(self):
        """silver_01 fast_timeframe must be 15m (not 5m — was BUG #2)."""
        cfg = self._load_cfg()
        s = cfg.get("strategies", {}).get("silver_01", {})
        assert s.get("fast_timeframe") == "15m", \
            f"silver_01 fast_timeframe is {s.get('fast_timeframe')}, expected 15m"

    def test_paper_mode_enforced(self):
        """system.environment must be 'paper'."""
        cfg = self._load_cfg()
        env = cfg.get("system", {}).get("environment", "")
        assert env == "paper", f"environment is '{env}', expected 'paper'"

    def test_instruments_have_multiplier(self):
        """Both GOLDM and SILVERM have positive multiplier."""
        cfg = self._load_cfg()
        instruments = cfg.get("instruments", {})
        for name, inst in instruments.items():
            mult = inst.get("multiplier", 0)
            assert mult > 0, f"{name} multiplier is {mult}"

    def test_risk_limits_present(self):
        """Risk section has required fields."""
        cfg = self._load_cfg()
        risk = cfg.get("risk", {})
        assert "max_daily_loss" in risk
        assert "kill_switch_enabled" in risk
        assert "margin_per_trade_pct" in risk

    def test_no_real_execution_config(self):
        """No live execution keys present in config."""
        cfg = self._load_cfg()
        dhan = cfg.get("dhan", {})
        client_id = dhan.get("client_id", "")
        # Env var placeholder is fine, actual value is not
        assert client_id.startswith("${") or client_id == "", \
            f"dhan.client_id looks like real credential: {client_id[:4]}..."
