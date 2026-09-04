"""
LIVE RUNTIME VERIFICATION V2 — Clean-Room Architecture
=====================================================
Version: 2.0.0
Run ID: Generated per invocation (not reused)

This test suite is COMPLETELY INDEPENDENT of all prior test files.
It does NOT import from, extend, copy, or depend on any old test module.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# ─── SUITE IDENTITY ─────────────────────────────────────────────────────────
SUITE_VERSION = "2.0.0"
RUN_ID = f"rtv2_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
PYTHON_VERSION = sys.version
STARTUP_TS = time.time()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_ROOT / "tests" / "live_runtime_v2" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── ANTI-CONTAMINATION GUARD ───────────────────────────────────────────────
_BLOCKED_PREFIXES = ("test_master_parity", "test_fresh", "test_crash_api",
                     "test_comprehensive", "test_full_", "test_forensic_",
                     "test_financial", "test_edge", "test_deep_",
                     "test_whole_project", "test_medium_items",
                     "test_per_strategy", "test_reversal_",
                     "test_rest_trading", "test_reconciliation_linkage",
                     "test_price_sentinel", "test_websocket_robustness",
                     "test_ledger_open", "test_audit_", "test_analytics_",
                     "test_backtest_vs_live", "test_db_path")


def _check_no_old_contamination():
    """Verify this module was not loaded alongside old test modules."""
    loaded = set(sys.modules.keys())
    violations = [m for m in loaded if any(m.startswith(p) for p in _BLOCKED_PREFIXES)]
    if violations:
        raise RuntimeError(
            f"CLEANROOM VIOLATION: old test modules detected: {violations[:5]}..."
        )


# Run at import time
_check_no_old_contamination()


# ─── PYTEST MARKERS ─────────────────────────────────────────────────────────
def pytest_configure(config):
    """Register custom markers for this suite."""
    markers = [
        "phase0: Clean-room environment setup",
        "phase1: Architecture discovery",
        "phase2: Runtime boot test",
        "phase3: Market data input verification",
        "phase4: Indicator exact trace",
        "phase5: HTF mapping / lookahead",
        "phase6: Strategy decision verification",
        "phase7: Signal -> execution verification",
        "phase8: Order state machine",
        "phase9: Position state machine",
        "phase10: Stop loss / exit verification",
        "phase11: Trade creation / closure",
        "phase12: Database real write/read",
        "phase13: Transaction atomicity / crash",
        "phase14: Restart / recovery",
        "phase15: Duplicate / idempotency",
        "phase16: Out-of-order / stale data",
        "phase17: Instrument isolation",
        "phase18: Strategy isolation",
        "phase19: 5-day end-to-end replay",
        "phase20: API verification",
        "phase21: Frontend data lineage",
        "phase22: Real-time update",
        "phase23: Session / market-close",
        "phase24: Auth / token / reconnect",
        "phase25: Paper vs real broker boundary",
        "phase26: False-positive test detection",
        "phase27: Live observability",
        "phase28: Performance / race conditions",
        "phase29: Security / data integrity",
        "phase30: Final cross-layer reconciliation",
    ]
    for m in markers:
        config.addinivalue_line("markers", m)


# ─── EVIDENCE COLLECTION ────────────────────────────────────────────────────
class EvidenceCollector:
    """Collect machine-readable test evidence."""

    def __init__(self):
        self.entries: list[dict] = []
        self._start = time.time()

    def record(self, phase: str, test: str, result: str, data: dict):
        self.entries.append({
            "run_id": RUN_ID,
            "timestamp": time.time(),
            "elapsed": time.time() - self._start,
            "phase": phase,
            "test": test,
            "result": result,
            "data": data,
        })

    def save(self, name: str):
        path = REPORT_DIR / f"{name}_{RUN_ID}.jsonl"
        with open(path, "w") as f:
            for e in self.entries:
                f.write(json.dumps(e, default=str) + "\n")
        return path


_evidence = EvidenceCollector()


def get_evidence():
    return _evidence


# ─── SHARED FIXTURES ───────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def run_id():
    return RUN_ID


@pytest.fixture(scope="session")
def evidence():
    return _evidence


@pytest.fixture(scope="session")
def project_root():
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def suite_identity():
    """Return the suite identity record."""
    return {
        "suite_version": SUITE_VERSION,
        "run_id": RUN_ID,
        "python_version": PYTHON_VERSION,
        "platform": platform.platform(),
        "project_root": str(PROJECT_ROOT),
        "startup_ts": STARTUP_TS,
    }


@pytest.fixture(scope="session", autouse=True)
def _save_identity(suite_identity):
    """Persist suite identity at start."""
    path = REPORT_DIR / f"identity_{RUN_ID}.json"
    with open(path, "w") as f:
        json.dump(suite_identity, f, indent=2, default=str)
