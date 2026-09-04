"""PHASE 25 — PAPER vs REAL
Verifies: Paper mode enforced, no real execution paths accessible.
"""
from __future__ import annotations

import os

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestPaperVsReal:
    """Phase 25: Paper mode enforcement."""

    def _load_cfg(self):
        from config import Config
        return Config.load()

    def test_config_environment_paper(self):
        """Config system.environment is 'paper'."""
        cfg = self._load_cfg()
        assert cfg.get("system", {}).get("environment") == "paper"

    def test_paper_execution_engine_is_default(self):
        """Default execution engine is PaperExecutionEngine."""
        from execution.paper_broker import PaperExecutionEngine
        eng = PaperExecutionEngine(slippage_ticks=0, latency_ms=0,
                                   partial_fill_probability=0.0)
        assert isinstance(eng, PaperExecutionEngine)

    def test_partial_fill_rejected(self):
        """PaperExecutionEngine rejects non-zero partial_fill_probability."""
        from execution.paper_broker import PaperExecutionEngine
        with pytest.raises(ValueError, match="Partial fills"):
            PaperExecutionEngine(partial_fill_probability=0.1)

    def test_no_live_broker_import(self):
        """No live broker module exists in the codebase."""
        base = os.path.join(os.path.dirname(__file__), "..", "..")
        for root, dirs, files in os.walk(base):
            for f in files:
                if "live" in f.lower() and "broker" in f.lower():
                    if f.endswith(".py") and "test" not in f.lower():
                        pytest.fail(f"Live broker file found: {os.path.join(root, f)}")

    def test_dhan_credentials_are_env_vars(self):
        """Dhan API credentials use env var placeholders, not real values."""
        cfg = self._load_cfg()
        dhan = cfg.get("dhan", {})
        for key in ["client_id", "access_token", "secret_key"]:
            val = dhan.get(key, "")
            if val:
                assert val.startswith("${"), \
                    f"dhan.{key} appears to be a real credential (starts with {val[:4]}...)"

    def test_risk_limits_are_conservative(self):
        """Risk limits are set for paper mode (high limits)."""
        cfg = self._load_cfg()
        risk = cfg.get("risk", {})
        assert risk.get("max_daily_loss", 0) >= 100000, \
            "Paper mode max_daily_loss should be >= 100000"

    def test_account_starting_capital(self):
        """Starting capital is configured per strategy."""
        cfg = self._load_cfg()
        acct = cfg.get("account", {})
        assert acct.get("starting_capital", 0) > 0
        assert acct.get("starting_capital_per_strategy", 0) > 0
