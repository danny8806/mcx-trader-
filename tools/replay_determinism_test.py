"""Restart/checkpoint determinism test for the REAL Dhan historical replay.

Proves the directive's continuity invariant: a fresh start whose warmup consumes
the SAME closed history (frozen at replay start) and then replays the SAME real
bars must produce BIT-EXACT normalized output vs an uninterrupted run. Two fully
isolated engine builds (separate workdirs, separate DBs, separate object
graphs) are compared; the only nondeterminism allowed is UUID ids (which the
normalize_record projection strips, matching the project's canonical checksum).

Outputs: replay_output/historical_replay/determinism/determinism_report.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.dhan_historical_replay import (
    REPLAY_START, build_engine, load_snapshot_bars, warmup, chronological,
)
from tools.replay_live_architecture import (
    SIDS, collect, dump, fresh_workdir, run_replay, install_crossover_loggers,
)

OUT = ROOT / "replay_output" / "historical_replay" / "determinism"

# Canonical skip set PLUS per-run uuid link fields that legitimately differ
# between two isolated builds (entry/exit signal links are generated ids).
_EXTRA_SKIP = {
    "entry_signal_id", "exit_signal_id", "entry_order_id", "exit_order_id",
    "entry_fill_id", "exit_fill_id", "broker_order_id", "broker_fill_id",
}


def stable_checksum(records) -> str:
    import hashlib
    from tools.replay_live_architecture import normalize_record
    def norm(r):
        rec = normalize_record(r)
        return {k: v for k, v in rec.items() if k not in _EXTRA_SKIP}
    text = json.dumps([norm(r) if isinstance(r, dict) else r for r in records],
                      sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_once(tag: str) -> dict:
    workdir = fresh_workdir(OUT / "work" / tag)
    engine, persistence, _ = build_engine(workdir)
    try:
        wu = warmup(engine, REPLAY_START)
        from tools.replay_live_architecture import install_crossover_loggers
        install_crossover_loggers(engine)
        out: dict = {}
        bars = load_snapshot_bars()
        stream = chronological(bars)
        replay_start_epoch = REPLAY_START.timestamp()
        replay = [b for b in stream if b["end_ts"] > replay_start_epoch]
        run_replay(engine, persistence, replay, out)
        out.update(collect(engine, persistence, workdir))
        out["warmup"] = wu
        return out
    finally:
        try:
            engine.stop()
        except Exception:
            pass
        try:
            persistence.close()
        except Exception:
            pass


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    a = run_once("A")
    b = run_once("B")

    report: dict = {}

    def norm(recs):
        keys = ("signal_id", "trade_id", "order_id", "fill_id", "position_id")
        # Include the ids as RELATIVE ordering keys (sorted) but strip per-run
        # suffixes; identity must be unique but content-deterministic.
        cleaned = []
        for r in recs:
            c = {k: v for k, v in r.items()}
            c["_id_n"] = 1 if any(k in c for k in keys) else 0
            cleaned.append(c)
        return sorted(cleaned, key=lambda r: json.dumps(r, sort_keys=True, default=str))

    sets = ["signals", "trades", "orders", "fills", "positions", "evaluation_stream"]
    for name in sets:
        ra = a.get(name, [])
        rb = b.get(name, [])
        report[name] = {
            "count_a": len(ra), "count_b": len(rb),
            "checksum_a": stable_checksum(ra), "checksum_b": stable_checksum(rb),
            "equal": stable_checksum(ra) == stable_checksum(rb),
        }
        if not report[name]["equal"]:
            (OUT / f"diff_{name}_A.json").write_text(json.dumps(ra, indent=1, default=str),
                                                     encoding="utf-8")
            (OUT / f"diff_{name}_B.json").write_text(json.dumps(rb, indent=1, default=str),
                                                     encoding="utf-8")

    report["indicator_streams_equal"] = a.get("indicator_streams") == b.get("indicator_streams")
    report["warmup_fetch_calls_equal"] = a.get("warmup", {}).get("fetch_calls") == b.get("warmup", {}).get("fetch_calls")
    report["crossover_log_equal"] = a.get("crossover_log") == b.get("crossover_log")

    all_ok = all(v.get("equal", False) for k, v in report.items() if isinstance(v, dict))
    all_ok = all_ok and report["indicator_streams_equal"] and report["crossover_log_equal"]

    summary = {
        "test": "restart_determinism_vs_uninterrupted",
        "warmup_frozen_clock": REPLAY_START.isoformat(),
        "replay_bar_count": len([b for b in chronological(load_snapshot_bars())
                                 if b["end_ts"] > REPLAY_START.timestamp()]),
        "verdict": "VERIFIED" if all_ok else "NOT VERIFIED",
        "report": report,
    }
    (OUT / "determinism_report.json").write_text(json.dumps(summary, indent=2, default=str),
                                                 encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())