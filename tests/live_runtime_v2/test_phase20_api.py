"""PHASE 20 — API / DASHBOARD
Verifies: Dashboard routes exist and basic API structure.
"""
from __future__ import annotations

import os
import json

import pytest

from . import RUN_ID, SUITE_VERSION, get_evidence


class TestAPIStructure:
    """Phase 20: Dashboard API verification."""

    def _load_cfg(self):
        from config import Config
        return Config.load()

    def test_dashboard_module_exists(self):
        """dashboard/ module exists with run.py."""
        base = os.path.join(os.path.dirname(__file__), "..", "..")
        assert os.path.exists(os.path.join(base, "dashboard", "run.py"))

    def test_routes_directory_exists(self):
        """dashboard/routes/ directory exists."""
        base = os.path.join(os.path.dirname(__file__), "..", "..")
        routes_dir = os.path.join(base, "dashboard", "routes")
        assert os.path.isdir(routes_dir), f"Routes dir missing: {routes_dir}"

    def test_api_key_config_key_exists(self):
        """Dashboard section has api_key config key."""
        cfg = self._load_cfg()
        dashboard = cfg.get("dashboard", {})
        assert "api_key" in dashboard, "dashboard.api_key must exist in config"

    def test_telegram_config_section_exists(self):
        """Telegram section exists with required keys."""
        cfg = self._load_cfg()
        tg = cfg.get("telegram", {})
        assert "bot_token" in tg, "telegram.bot_token must exist"
        assert "chat_id" in tg, "telegram.chat_id must exist"
        assert "enabled" in tg, "telegram.enabled must exist"

    def test_all_strategies_enabled(self):
        """All 4 strategies are enabled in config."""
        cfg = self._load_cfg()
        strats = cfg.get("strategies", {})
        for sid, s in strats.items():
            assert s.get("enabled") is True, \
                f"Strategy {sid} is not enabled"

    def test_instruments_config_complete(self):
        """Both GOLDM and SILVERM have all required fields."""
        cfg = self._load_cfg()
        instruments = cfg.get("instruments", {})
        for name, inst in instruments.items():
            assert inst.get("symbol"), f"{name} missing symbol"
            assert inst.get("multiplier", 0) > 0
            assert inst.get("security_id"), f"{name} missing security_id"
