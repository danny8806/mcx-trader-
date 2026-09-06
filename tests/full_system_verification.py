"""Full-system verification — finite entrypoint.

Proves the entire MCX-TRADER architecture end-to-end against REAL Dhan
historical data and a fresh full-stack boot:

  Section A  historical replay oracle  (read-only, ground truth from the
             REAL Dhan 2026-08-03..09-04 snapshot; DB counts + file
             checksums vs the recorded manifest)
  Section B  full-stack live boot       (fresh deterministic engine seed ->
             real uvicorn server -> HTTP + WS + UI battery, 92 checks)
  Section C  regression gate            (full pytest suite; 0 failures)

Writes test_results.json (test_id/category/description/start_time/end_time/
status/expected/actual/evidence/failure_classification).

Exit code 0 == VERIFIED; non-zero == NOT VERIFIED.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPLAY_DB = ROOT / "replay_output" / "historical_replay" / "work" / "data" / "db" / "trading.db"
SNAPSHOT_DIR = ROOT / "replay_output" / "dhan_snapshot"
MANIFEST = ROOT / "replay_output" / "replay_manifest.json"
OUT = ROOT / "test_results.json"

# Ground truths established by the verified deterministic replay (§66-67).
ORACLE = {
    "signals": 40,
    "orders": 44,
    "fills": 44,
    "positions": 23,
    "positions_open": 2,
    "positions_closed": 21,
    "trades": 23,
    "trades_closed": 21,
    "trades_open": 2,
    "reversals": 7,
    "sl_trades": 14,
    "net_realized_inr": 20921.06,
}


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def dt() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    results: list[dict] = []
    failed_any = False

    def record(tid, category, description, ok, expected, actual, evidence=""):
        nonlocal failed_any
        results.append({
            "test_id": tid,
            "category": category,
            "description": description,
            "start_time": start_t,
            "end_time": dt(),
            "status": "passed" if ok else "failed",
            "expected": str(expected),
            "actual": str(actual),
            "evidence": str(evidence),
            "failure_classification": "" if ok else evidence.split("|")[0].strip(),
        })
        if not ok:
            failed_any = True
        print(f"  [{'PASS' if ok else 'FAIL'}] {tid}: {description}")

    start_t = dt()

    # ── Section A: historical replay oracle ──────────────────────────────
    print("=" * 78)
    print("Section A — historical replay oracle (REAL Dhan data)")
    print("=" * 78)
    if not REPLAY_DB.exists():
        record("A.restore.oracle", "replay_oracle", "replay trading.db present",
               False, f"{REPLAY_DB} exists", "missing", "missing replay DB artifact")
        print(json.dumps(results, indent=2))
        print("\nVERDICT: NOT VERIFIED")
        return 1

    conn = sqlite3.connect(str(REPLAY_DB))
    conn.row_factory = sqlite3.Row
    def q(sql):
        return conn.execute(sql).fetchall()

    got = {
        "signals": len(q("SELECT DISTINCT signal_id FROM signals")),
        "orders": len(q("SELECT DISTINCT order_id FROM orders")),
        "fills": len(q("SELECT DISTINCT fill_id FROM fills")),
        "positions": len(q("SELECT position_id FROM positions")),
        "positions_open": len(q("SELECT position_id FROM positions WHERE status='open'")),
        "positions_closed": len(q("SELECT position_id FROM positions WHERE status='closed'")),
        "trades": len(q("SELECT trade_id FROM trades")),
        "trades_closed": len(q("SELECT trade_id FROM trades WHERE status='CLOSED'")),
        "trades_open": len(q("SELECT trade_id FROM trades WHERE status='OPEN'")),
        "reversals": len(q("SELECT DISTINCT trade_id FROM trades WHERE exit_reason IN ('long_reversal','short_reversal')")),
        "sl_trades": len(q("SELECT trade_id FROM trades WHERE exit_reason='STOP_LOSS'")),
    }
    conn.close()
    net = None
    try:
        conn2 = sqlite3.connect(str(REPLAY_DB))
        net = float(conn2.execute("SELECT SUM(net_pnl) FROM trades WHERE status='CLOSED'").fetchone()[0])
        conn2.close()
    except Exception:
        pass
    got["net_realized_inr"] = round(net, 2) if net is not None else None

    for key, expected in ORACLE.items():
        actual = got[key]
        ok = actual is not None and (abs(actual - expected) < 0.01 if isinstance(expected, float) else actual == expected)
        record(f"A.oracle.{key}", "replay_oracle",
               f"replay DB metric {key} == {expected}",
               ok, expected, actual, "trading.db derived")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    exp_checksums = manifest.get("checksums", {})
    mism = []
    for fname, exp_hash in exp_checksums.items():
        fpath = SNAPSHOT_DIR / fname
        if not fpath.exists():
            mism.append(f"{fname}: missing")
            continue
        cur = sha256(fpath)
        if cur != exp_hash:
            mism.append(f"{fname}: {cur} != {exp_hash}")
    ok = not mism and len(exp_checksums) == 12
    record("A.oracle.checksums", "replay_oracle",
           "all 12 Dhan snapshot files match the recorded manifest checksums",
           ok, "checksums_replay_manifest", "mismatches=[%s]" % ("; ".join(mism) if mism else "none"),
           "sha256 over dhan_snapshot files")

    # ── Section B: full-stack live boot (fresh) ──────────────────────────
    print("=" * 78)
    print("Section B — full-stack live boot (HTTP + WS + UI battery)")
    print("=" * 78)
    import _fullstack_check as fc
    engine, persistence, _root = fc.seed_engine()
    ref = fc.ref_closed(engine)
    try:
        persistence.save_state(engine.snapshot())
    except Exception as e:
        print(f"[Embed] save_state failed: {e}", file=sys.stderr, flush=True)
    server = fc.boot_server(engine, persistence)
    try:
        asyncio.run(fc.main_checks(engine, ref))
    finally:
        server.should_exit = True
        time.sleep(1)
    for name, passed, detail in fc.CHECKS:
        record(f"B.{name}", "fullstack_live", name, passed, "pass", "pass" if passed else f"FAIL detail: {detail}",
               detail)

    # ── Section C: regression gate ───────────────────────────────────────
    print("=" * 78)
    print("Section C — regression gate (full pytest suite)")
    print("=" * 78)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"],
            capture_output=True, text=True, timeout=2400, cwd=str(ROOT),
        )
        tail = (proc.stdout or "") + (proc.stderr or "")
        print(tail[-3000:])
        import re
        m = re.search(r"(\d+) passed", tail)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", tail)
        failed = int(m.group(1)) if m else (0 if proc.returncode == 0 else None)
        m = re.search(r"(\d+) skipped", tail)
        skipped = int(m.group(1)) if m else 0
        ok = proc.returncode == 0 and failed in (0,) and passed > 0
        record("C.regression.suite", "regression",
               "full pytest suite: 0 failures", ok,
               "0 failed", f"{passed} passed, {skipped} skipped, {failed} failed",
               f"exit={proc.returncode} | {tail[-1500:].strip()}")
    except subprocess.TimeoutExpired as e:
        record("C.regression.suite", "regression", "full pytest suite: 0 failures",
               False, "0 failed", "timeout", "pytest timed out after 2400s")

    # ── write test_results.json ──────────────────────────────────────────
    summary = {
        "generated_at": dt(),
        "repository": str(ROOT),
        "replay_range": "2026-08-03T09:00:00+05:30 .. 2026-09-04T23:25:00+05:30 (REAL Dhan REST intraday)",
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "verdict": "VERIFIED" if not failed_any else "NOT VERIFIED",
        "results": results,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  wrote {OUT}")

    print("=" * 78)
    print(f"FINAL VERDICT: {'VERIFIED' if not failed_any else 'NOT VERIFIED'}")
    print(f"  {summary['passed']}/{summary['total']} checks passed")
    print("=" * 78)
    return 0 if not failed_any else 1


if __name__ == "__main__":
    sys.exit(main())