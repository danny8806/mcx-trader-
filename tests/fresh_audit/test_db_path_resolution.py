"""Regression test for cwd-independent DB-path resolution (the "new DB" risk).

The engine's analytics.event_store / trade_ledger and fill_dedup derive their
SQLite paths from the config's (relative) `system.db_path`.  When those are
anchored only to the process cwd, launching a second entrypoint from a different
directory silently forks a brand-new trading.db / analytics.db -- exactly the
"splits state across a new DB" failure mode.

This guards `Config.resolve_path`: relative paths must resolve against the
PROJECT ROOT (config/..), never the cwd, so every consumer converges on the one
canonical data store no matter where the process starts.
"""
from __future__ import annotations

import os
from pathlib import Path

from config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestDBPathResolution:
    def test_resolve_path_anchors_relative_to_project_root_not_cwd(self):
        Config.load()
        rel = "data/db/trading.db"
        expected = (PROJECT_ROOT / rel).resolve()
        assert Config.resolve_path(rel) == str(expected)

    def test_resolve_path_is_absolute_even_when_cwd_is_elsewhere(self):
        other = PROJECT_ROOT.parent / "some-other-workdir"
        os.makedirs(other, exist_ok=True)
        try:
            old = os.getcwd()
            os.chdir(other)
            try:
                got = Config.resolve_path("data/db/trading.db")
                assert Path(got).is_absolute()
                assert got == str((PROJECT_ROOT / "data/db/trading.db").resolve())
            finally:
                os.chdir(old)
        finally:
            try:
                os.rmdir(other)
            except OSError:
                pass

    def test_config_db_path_resolves_to_project_root_data_dir(self):
        Config.load()
        got = Config.resolve_path(Config.get("system.db_path"))
        assert got == str((PROJECT_ROOT / "data" / "db" / "trading.db").resolve())

    def test_engine_analytics_path_is_project_root_relative(self):
        # Mirrors trading_engine.__init__: analytics.db lives beside trading.db
        Config.load()
        db_root = Path(Config.resolve_path(Config.get("system.db_path"))).parent
        analytics = db_root / "analytics.db"
        assert analytics == (PROJECT_ROOT / "data" / "db" / "analytics.db").resolve()

    def test_resolve_path_keeps_absolute_paths_untouched(self):
        abs_path = (PROJECT_ROOT / "data" / "db" / "trading.db").resolve()
        assert Config.resolve_path(str(abs_path)) == str(abs_path)