"""REPLAY vs BACKTEST (aggregated-HTFT) parity over the SAME REAL data.

The authoritative reference backtest consumes 5m and RESAMPLES 15m/1h
(full_simulator.build_bars == CandleFetcher aggregation path). The master
directive requires the replay to consume NATIVE 15m/1h bars (no per-strategy
resampling). This harness runs the SAME production pipeline (same engine,
same shared indicators, same strategies) over the same real 09-02..09-04
window in BOTH modes:

  native      -> HistoricalDhanReplaySource (native 5/15/60 rows)
  aggregated  -> 5m native rows, 15m/1h via full_simulator.build_bars
                  (the exact backtest aggregation)

and compares signal/trade output. Any difference isolates the HTF aggregation
source (the intended difference) from strategy math (must be zero impact).
Outputs: replay_output/historical_replay/backtest_parity/parity_report.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.dhan_historical_replay import (
    REPLAY_START, HistoricalDhanReplaySource, build_engine, load_snapshot_bars,
    warmup, chronological, SNAPSHOT_DIR,
)
from tools.replay_determinism_test import stable_checksum
from tools.replay_live_architecture import (
    fresh_workdir, run_replay, collect,
)

import datetime
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

OUT = ROOT / "replay_output" / "historical_replay" / "backtest_parity"


class AggregatedHtfSource(HistoricalDhanReplaySource):
    """Same 5m native rows, but 15m/60 restored from CandleFetcher aggregation
    of the 5m rows (the reference backtest's HTF construction)."""

    def __init__(self, *a, **kw):
        # build 5m-only rows first, then synthesize 15/60 via aggregation.
        super().__init__(*a, **kw)
        raw = self.__dict__["_rows"]
        from full_simulator import build_bars

        for instrument in ("GOLDM", "SILVERM"):
            rows5 = raw[(instrument, "5")]
            b5, b15, b1h = build_bars(instrument, rows5)
            raw[(instrument, "15")] = [b for b in b15]  # aggregated native 15m
            raw[(instrument, "60")] = [b for b in b1h]


def run_once(tag: str, source_cls) -> dict:
    import trading_engine as te
    te.DhanDataAdapter = source_cls
    workdir = fresh_workdir(OUT / "work" / tag)
    # build_engine sets te.DhanDataAdapter = HistoricalDhanReplaySource; use it
    # then override via a wrapper class through monkeypatching before build.
    import tools.dhan_historical_replay as dh
    _orig = dh.HistoricalDhanReplaySource
    dh.HistoricalDhanReplaySource = source_cls
    try:
        engine, persistence, _ = build_engine(workdir)
        wu = warmup(engine, REPLAY_START)
        from tools.replay_live_architecture import install_crossover_loggers
        install_crossover_loggers(engine)
        out = {}
        bars = load_snapshot_bars()
        stream = chronological(bars)
        replay = [b for b in stream if b["end_ts"] > REPLAY_START.timestamp()]
        run_replay(engine, persistence, replay, out)
        out.update(collect(engine, persistence, workdir))
        out["warmup"] = wu
        return out
    finally:
        dh.HistoricalDhanReplaySource = _orig
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

    native = run_once("native", HistoricalDhanReplaySource)
    aggregated = run_once("aggregated", AggregatedHtfSource)

    report: dict = {}
    for name in ("signals", "trades", "orders", "fills", "positions", "evaluation_stream"):
        report[name] = {
            "count_native": len(native.get(name, [])),
            "count_aggregated": len(aggregated.get(name, [])),
            "checksum_native": stable_checksum(native.get(name, [])),
            "checksum_aggregated": stable_checksum(aggregated.get(name, [])),
            "identical": stable_checksum(native.get(name, [])) == stable_checksum(aggregated.get(name, [])),
        }
        if not report[name]["identical"]:
            (OUT / f"diff_{name}_native.json").write_text(
                json.dumps(native.get(name, []), indent=1, default=str), encoding="utf-8")
            (OUT / f"diff_{name}_aggregated.json").write_text(
                json.dumps(aggregated.get(name, []), indent=1, default=str), encoding="utf-8")

    # strategy-by-strategy signal counts
    report["strategies"] = {
        sid: {"native": native.get("strategies", {}).get(sid, {}).get("signals_count"),
              "aggregated": aggregated.get("strategies", {}).get(sid, {}).get("signals_count")}
        for sid in ("gold_01", "gold_02", "silver_01", "silver_02")
    }

    # classify diffs: count of signal timestamps differing
    def sig_times(out):
        return sorted(t["timestamp"] for t in out.get("signals", []))

    st_n, st_a = sig_times(native), sig_times(aggregated)
    report["signal_timestamp_sets"] = {
        "n_native": len(st_n), "n_aggregated": len(st_a),
        "n_common": len(set(st_n) & set(st_a)),
        "only_native": len(set(st_n) - set(st_a)),
        "only_aggregated": len(set(st_a) - set(st_n)),
    }

    # trades: common (strategy, entry price+time, exit_reason)
    def trade_keys(out):
        return sorted((t["strategy_id"], t.get("entry_timestamp"), t.get("entry_price"),
                       t.get("exit_reason")) for t in out.get("trades", []))
    tk_n, tk_a = trade_keys(native), trade_keys(aggregated)
    report["trade_key_comparison"] = {
        "n_native": len(tk_n), "n_aggregated": len(tk_a),
        "n_common": len(set(tk_n) & set(tk_a)),
        "only_native": len(set(tk_n) - set(tk_a)),
        "only_aggregated": len(set(tk_a) - set(tk_n)),
    }

    report["warmup_fetch_calls_equal"] = native.get("warmup", {}).get("fetch_calls") == aggregated.get("warmup", {}).get("fetch_calls")

    # The expectation: native vs aggregated HTF may differ ONLY because
    # aggregated 15m/1h differ from native 15m/1h at session edges.
    summary = {
        "test": "replay_vs_backtest_aggregation_parity",
        "window": "2026-09-02 09:00 IST .. last closed candle",
        "native_mode": "native Dhan 5/15/60 rows (replay directive)",
        "aggregated_mode": "reference backtest aggregation of native 5m (full_simulator.build_bars)",
        "report": report,
    }
    (OUT / "parity_report.json").write_text(json.dumps(summary, indent=2, default=str),
                                            encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())