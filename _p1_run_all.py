"""PHASE-1 / PART 50 — RUN ALL: parallel orchestration of every _p1_* gate.

Runs each _p1_* test in its own subprocess (isolated runs, isolated DBs) and
writes PHASE1_RUN_SUMMARY.csv; aggregate exit code is non-zero if any gate fails.

Phase-1 gates:
  warmup      -> WARMUP_VALIDATION_REPORT.csv
  resample    -> RESAMPLING_VALIDATION_REPORT.csv
  parity      -> INDICATOR_PARITY_REPORT.csv + MAPPING_PARITY_REPORT.csv
  continuity  -> FIVE_DAY_CONTINUITY_REPORT.csv
  restart     -> RESTART_PARITY_REPORT.csv
  regression  -> REGRESSION_REPORT.csv
  lifecycle   -> GOLD_VALIDATION_REPORT.csv + SILVER_VALIDATION_REPORT.csv
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _p1_lib as L

GATES = [
    ("warmup", "_p1_warmup_test.py"),
    ("resample", "_p1_resample_test.py"),
    ("parity", "_p1_parity_test.py"),
    ("continuity", "_p1_continuity_test.py"),
    ("restart", "_p1_restart_test.py"),
    ("regression", "_p1_regression_test.py"),
    ("lifecycle", "_p1_lifecycle_test.py"),
    ("map4", "_p1_map4_test.py"),
    ("signals", "_p1_signal4_test.py"),
]

SUMMARY = L.AUDIT_DIR / "PHASE1_RUN_SUMMARY.csv"
if SUMMARY.exists():
    SUMMARY.unlink()

results = []
for name, script in GATES:
    print(f"\n===== RUN {name} ({script}) =====", flush=True)
    p = subprocess.run([sys.executable, script], capture_output=True, text=True)
    tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
    ok = p.returncode == 0 and "ALL PASSED" in (p.stdout or "")
    results.append({"gate": name, "script": script, "ok": ok,
                    "exit": p.returncode, "tail": tail.replace("\n", " | ")[:300]})
    print(f"---- {name}: {'PASS' if ok else 'FAIL'} (exit {p.returncode}) ----", flush=True)

rows_out = [{"gate": r["gate"], "script": r["script"], "pass": "PASS" if r["ok"] else "FAIL",
             "exit_code": r["exit"]} for r in results]
L.append_rows(SUMMARY, rows_out)

failed = [r for r in results if not r["ok"]]
print("\n=== PHASE-1 RUN SUMMARY ===")
for r in results:
    print(f"  {r['gate']:<12s} {'PASS' if r['ok'] else 'FAIL'}")
print(f"\nTOTAL: {len(results) - len(failed)}/{len(results)} gates passed")
print(f"SUMMARY -> {SUMMARY}")
for r in failed:
    print(f"\n--- {r['gate']} FAILED tail ---\n{r['tail']}")
sys.exit(1 if failed else 0)