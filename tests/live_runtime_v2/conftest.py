"""
TEST RUNNER — Collects all phase results and generates final report.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from . import RUN_ID, SUITE_VERSION, REPORT_DIR, get_evidence, PROJECT_ROOT


# ─── SHARED FIXTURES ───────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def run_id():
    return RUN_ID


@pytest.fixture(scope="session")
def evidence():
    return get_evidence()


@pytest.fixture(scope="session")
def project_root():
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def suite_identity():
    """Return the suite identity record."""
    return {
        "suite_version": SUITE_VERSION,
        "run_id": RUN_ID,
        "project_root": str(PROJECT_ROOT),
    }


def _generate_report():
    """Generate the final comprehensive report."""
    evidence = get_evidence()
    report = {
        "run_id": RUN_ID,
        "suite_version": SUITE_VERSION,
        "total_tests": len(evidence.entries),
        "phases": {},
        "verdict": "SYSTEM_VERIFIED",
        "failures": [],
        "unverified": [],
    }

    for entry in evidence.entries:
        phase = entry["phase"]
        if phase not in report["phases"]:
            report["phases"][phase] = {"pass": 0, "fail": 0, "unverified": 0, "tests": []}
        p = report["phases"][phase]
        result = entry["result"].upper()
        if result == "PASS":
            p["pass"] += 1
        elif result == "FAIL":
            p["fail"] += 1
            report["failures"].append(entry)
            report["verdict"] = "SYSTEM_NOT_VERIFIED"
        elif result == "UNVERIFIED":
            p["unverified"] += 1
            report["unverified"].append(entry)
        p["tests"].append({
            "test": entry["test"],
            "result": result,
        })

    path = REPORT_DIR / f"NEW_TEST_SUMMARY_{RUN_ID}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    evidence.save(f"NEW_RUNTIME_INPUT_OUTPUT_TRACE_{RUN_ID}")
    return report, path


@pytest.fixture(scope="session", autouse=True)
def _generate_final_report():
    """Generate final report after all tests run."""
    yield
    report, path = _generate_report()
    print(f"\n{'='*60}")
    print(f"FINAL VERDICT: {report['verdict']}")
    print(f"Total tests: {report['total_tests']}")
    print(f"Failures: {len(report['failures'])}")
    print(f"Unverified: {len(report['unverified'])}")
    print(f"Report: {path}")
    print(f"{'='*60}")
