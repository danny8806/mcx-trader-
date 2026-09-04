"""
PHASE 1 — ARCHITECTURE DISCOVERY
================================
Dynamically inspect the repository and build an architecture manifest.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from . import RUN_ID, SUITE_VERSION, PROJECT_ROOT, get_evidence, REPORT_DIR


def _discover_entrypoints(root: Path) -> dict:
    """Find main entrypoints."""
    result = {}
    main_py = root / "main.py"
    if main_py.exists():
        result["main_py"] = {
            "path": str(main_py),
            "is_entrypoint": True,
            "size_bytes": main_py.stat().st_size,
        }
    run_py = root / "dashboard" / "run.py"
    if run_py.exists():
        result["dashboard_run_py"] = {
            "path": str(run_py),
            "is_entrypoint": True,
            "size_bytes": run_py.stat().st_size,
        }
    return result


def _discover_modules(root: Path) -> list[dict]:
    """Discover all Python modules in the project."""
    modules = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in str(py) or ".git" in str(py):
            continue
        rel = py.relative_to(root)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            tree = None
        classes = []
        functions = []
        imports = []
        if tree:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    classes.append({
                        "name": node.name,
                        "line": node.lineno,
                        "bases": [b.id if isinstance(b, ast.Name) else str(b) for b in node.bases],
                    })
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append({
                        "name": node.name,
                        "line": node.lineno,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                    })
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append({"module": alias.name, "line": node.lineno})
                elif isinstance(node, ast.ImportFrom):
                    imports.append({
                        "module": node.module or "",
                        "names": [a.name for a in node.names],
                        "line": node.lineno,
                    })
        modules.append({
            "path": str(rel),
            "classes": classes,
            "functions": functions,
            "imports": imports,
            "lines": tree.body[-1].end_lineno if tree and tree.body else 0,
        })
    return modules


def _discover_config(root: Path) -> dict:
    """Discover configuration files."""
    result = {}
    settings = root / "config" / "settings.json"
    if settings.exists():
        with open(settings) as f:
            data = json.load(f)
        # Redact secrets
        redacted = json.loads(json.dumps(data))
        for key in ("dhan", "telegram"):
            if key in redacted:
                for k, v in redacted[key].items():
                    if any(s in k.lower() for s in ("token", "secret", "password", "pin", "key")):
                        redacted[key][k] = "***REDACTED***"
        result["settings_json"] = redacted
    env = root / "mcx-trader.env"
    if env.exists():
        result["env_file"] = {"path": str(env)}
    return result


@pytest.mark.phase1
class TestArchitectureDiscovery:
    """Phase 1: Dynamically discover and document the architecture."""

    def test_discover_entrypoints(self, project_root):
        """Discover all entrypoints."""
        entrypoints = _discover_entrypoints(project_root)
        get_evidence().record("phase1", "discover_entrypoints", "PASS", entrypoints)
        assert len(entrypoints) >= 1, f"Expected at least 1 entrypoint, got {len(entrypoints)}"

    def test_discover_modules(self, project_root):
        """Discover all Python modules."""
        modules = _discover_modules(project_root)
        get_evidence().record("phase1", "discover_modules", "PASS", {"count": len(modules)})
        assert len(modules) >= 10, f"Expected at least 10 modules, got {len(modules)}"

    def test_discover_config(self, project_root):
        """Discover configuration."""
        config = _discover_config(project_root)
        get_evidence().record("phase1", "discover_config", "PASS", {"keys": list(config.keys())})
        assert "settings_json" in config, "settings.json not found"

    def test_manifest_generation(self, project_root, suite_identity):
        """Generate architecture manifest."""
        manifest = {
            "run_id": RUN_ID,
            "suite_version": SUITE_VERSION,
            "identity": suite_identity,
            "entrypoints": _discover_entrypoints(project_root),
            "module_count": len(_discover_modules(project_root)),
            "config": _discover_config(project_root),
            "generated_at": time.time(),
        }
        path = REPORT_DIR / f"NEW_ARCHITECTURE_MANIFEST_{RUN_ID}.json"
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)
        get_evidence().record("phase1", "manifest_generation", "PASS", {"path": str(path)})
        assert path.exists(), f"Manifest not created at {path}"

    def test_critical_classes_discovered(self, project_root):
        """Verify critical classes are discoverable via import."""
        critical = [
            "trading_engine.TradingEngine",
            "strategies.base_dema_strategy.BaseDEMAStrategy",
            "indicators.dema_atr.DEMAATR",
            "htf.backtest_style_htf.BacktestStyleHTFEngine",
            "core.market_status.MarketStatus",
            "core.risk_engine.RiskEngine",
            "portfolio.position_manager.PositionManager",
            "portfolio.pnl.PNLEngine",
            "portfolio.account.AccountEngine",
            "execution.paper_broker.PaperExecutionEngine",
            "execution.order_manager.OrderManager",
            "persistence.manager.PersistenceManager",
            "analytics.event_store.EventStore",
            "analytics.trade_ledger.TradeLedger",
        ]
        results = {}
        for cls_path in critical:
            parts = cls_path.rsplit(".", 1)
            module_name = parts[0]
            class_name = parts[1] if len(parts) > 1 else None
            try:
                mod = importlib.import_module(module_name)
                if class_name:
                    getattr(mod, class_name)
                results[cls_path] = "IMPORTED"
            except Exception as e:
                results[cls_path] = f"FAILED: {e}"
        failed = {k: v for k, v in results.items() if v != "IMPORTED"}
        get_evidence().record("phase1", "critical_classes", "PASS" if not failed else "FAIL", results)
        assert not failed, f"Failed to import: {failed}"
