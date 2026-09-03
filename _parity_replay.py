"""PARITY REPLAY HARNESS — drive the REAL live TradingEngine (indicators, HTF
engine, BaseDEMAStrategy, PaperExecutionEngine, OrderManager, PositionManager,
PNL/account, RiskEngine, MarketStatus, TradeLedger) over the same five 5-minute
days (2026-08-24..28) and same contract CSVs the backtest reference consumes.

Run A  (controlled parity):  bars built keep_partial=True (partial 23:00 1H
  bucket INCLUDED = reference KEEP-ALL resample), NO startup warmup
  (indicator/HTF history = exactly the 5-day window), tick_signal_processing
  False (bar-crossing model), slippage 0.  Under these conditions every shared
  input to both sides is IDENTICAL, so any remaining divergence isolates logic/
  mapping gaps besides the ONE intentional difference (live fills the breakout
  at the signal-bar trigger level; reference fills at the crossing bar's OPEN).

Run B  (production-faithful): bars built with the production COMPLETE-WINDOW
  rule, startup _warmup_from_rest() fetch (7 calendar days back from a simulated
  2026-08-24 09:00 IST start -> trading days 08-17..08-21), tick_signal_processing
  True, slippage_ticks 1.  This is what a live restart on 08-24 would compute.

Outputs (written into _BACKTEST_VS_LIVE_AUDIT/):
  DATA_PARITY_REPORT.csv  INDICATOR_PARITY_REPORT.csv  SIGNAL_PARITY_REPORT.csv
  TRADE_PARITY_REPORT.csv FINANCIAL_PARITY_REPORT.csv  LIVE_* per-run captures.

The only substitute is the network layer: a ParityReplayDataAdapter serves the
CSV 5m rows (epoch format used by Dhan REST) for the REST warmup fetch, and a
mock WS stands in for the Dhan socket — the same seams full_simulator/_audit_5day use.
"""
from __future__ import annotations

import datetime as _dtm
import json
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from full_simulator import (  # noqa: E402
    LIVE_INSTRUMENTS, _TF_RANK, ist, build_bars, ReplayDataAdapter, teardown,
)

OUT_DIR = ROOT / "_BACKTEST_VS_LIVE_AUDIT"
REF_DIR = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\parity_ref")
RUN_ROOT = Path(r"C:\Users\pc\AppData\Local\Temp\opencode\parity_replay")

WINDOW = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]
WINDOW_START = date(2026, 8, 24)

CSV_PATHS = {
    "GOLDM":   Path(r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\gold\GOLDM_04Sep2026_5m.csv"),
    "SILVERM": Path(r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx\silver\SILVERM_30Nov2026_5m.csv"),
}


# ═══════════════════════════════════════════════════════════════
# Data loading (CSV -> Dhan-style epoch row tuples)
# ═══════════════════════════════════════════════════════════════
def load_rows(csv_path: Path) -> list:
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    s = df["datetime"].dt.tz_localize("Asia/Kolkata")
    epoch0 = pd.Timestamp("1970-01-01", tz="UTC")
    secs = (s - epoch0).dt.total_seconds().astype("int64")
    return [
        (int(e), float(o), float(h), float(l), float(c), int(v))
        for e, o, h, l, c, v in zip(secs, df["open"], df["high"], df["low"], df["close"], df["volume"])
    ]


class ParityReplayDataAdapter(ReplayDataAdapter):
    STORE: dict = {}
    CAP_BEFORE: Optional[date] = None

    def fetch_historical_candles(self, name, tf, from_date, to_date):
        from_date = from_date.date() if hasattr(from_date, "date") else from_date
        to_date = to_date.date() if hasattr(to_date, "date") else to_date
        rows = [r for r in self.STORE.get(name, [])
                if from_date <= ist(r[0]).date() <= to_date]
        if self.CAP_BEFORE is not None:
            rows = [r for r in rows if ist(r[0]).date() < self.CAP_BEFORE]
        return sorted(rows, key=lambda r: r[0])


class _FakeDatetime(_dtm.datetime):
    FIXED = _dtm.datetime(2026, 8, 24, 9, 0, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls.FIXED.replace(tzinfo=tz)
        return cls.FIXED


# ═══════════════════════════════════════════════════════════════
# Config (only gold_02 + silver_01; all other live settings preserved)
# ═══════════════════════════════════════════════════════════════
def make_config(root: Path, slippage_ticks: int, keep_partial: bool) -> Path:
    strategies = {
        "gold_02": {"instrument": "GOLDM", "fast_timeframe": "15m",
                    "mid_timeframe": "15m", "htf_timeframe": "1h",
                    "quantity": 1, "capital": 300000, "enabled": True},
        "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
                      "mid_timeframe": "15m", "htf_timeframe": "1h",
                      "quantity": 1, "capital": 300000, "enabled": True},
    }
    data = {
        "system": {"name": "ParityReplay", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake", "token_file": str(root / "data" / "db" / "dhan_token.json"),
                 "pin": "", "totp_secret": ""},
        "instruments": LIVE_INSTRUMENTS,
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": strategies,
        "paper_execution": {"slippage_ticks": slippage_ticks, "latency_ms": 1,
                            "partial_fill_probability": 0.0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001, "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 600000.0, "starting_capital_per_strategy": 300000.0,
                    "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    (root / "data" / "db").mkdir(parents=True, exist_ok=True)
    cfg = root / "settings.json"
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg


def build_engine(cfg_path: Path):
    import trading_engine as te
    te.DhanDataAdapter = ParityReplayDataAdapter

    from persistence.manager import PersistenceManager
    from analytics.schema import init_analytics_db
    from core.trade_close import TradeCloseManager

    root = cfg_path.parent
    (root / "data" / "db").mkdir(parents=True, exist_ok=True)
    init_analytics_db(str(root / "data" / "db" / "analytics.db"))
    persistence = PersistenceManager(
        state_path=str(root / "data" / "db" / "system_state.json"),
        db_path=str(root / "data" / "db" / "trading.db"),
    )
    engine = te.TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        global_account=engine.account_engine,
        risk_engine=engine.risk_engine,
        persistence=persistence,
        event_store=engine.event_store,
        telegram=engine.telegram,
        event_callback=engine._event_callback,
        trade_ledger=engine.trade_ledger,
    )
    return engine, persistence


def _fast_strategy(engine, bar):
    for strat in engine.strategies.values():
        if strat.instrument == bar.instrument and strat.fast_timeframe == bar.timeframe:
            return strat
    return None


def build_stream(rows_by_inst, keep_partial: bool):
    all_bars = []
    for name in ("GOLDM", "SILVERM"):
        b5, b15, b1h = build_bars(name, rows_by_inst[name], keep_partial=keep_partial)
        all_bars.extend(b15 + b1h)
    by_day: dict = {}
    for bar in all_bars:
        by_day.setdefault(ist(bar.end_ts).date(), []).append(bar)
    for d in by_day:
        by_day[d].sort(key=lambda b: (b.end_ts, _TF_RANK[b.timeframe]))
    return by_day


# ═══════════════════════════════════════════════════════════════
# Replay driver with per-bar capture
# ═══════════════════════════════════════════════════════════════
def run_replay(engine, stream_by_day, tick_processing: bool):
    from core.market_status import MarketState, EngineStatus
    engine._running = True
    engine.market_status.set_engine_status(EngineStatus.READY)
    engine.market_status.force_state(MarketState.LIVE_TRADING)
    engine.market_status.set_engine_status(EngineStatus.TRADING)
    ws = engine.data_adapter.ws
    capture = {"rows": [], "events": [], "errors": []}

    seq = []
    for day in sorted(stream_by_day):
        seq.extend(stream_by_day[day])

    # pre-length of strategy event buffers
    pre_lens = {sid: len(s._events) for sid, s in engine.strategies.items()}
    prev_close = {}

    for bar in seq:
        engine.market_status.force_state(MarketState.LIVE_TRADING)
        ws.connected = True
        ws._last_tick_time = time.time()

        strat = _fast_strategy(engine, bar)
        if strat is not None:
            engine.execution_engine.update_price(bar.instrument, bar.close)
        engine._on_bar_closed(bar)
        engine._on_tick({"instrument": bar.instrument, "ltp": bar.close,
                         "event_timestamp": bar.end_ts})

        if strat is not None:
            events = strat._events[pre_lens[strat.strategy_id]:]
            pre_lens[strat.strategy_id] = len(strat._events)
            h1 = engine.htf_engine.map_to_fast_bar(bar, "1h")
            m15 = engine.htf_engine.map_mid_to_fast_bar(bar, "15m")
            fast_line = engine.indicators[f"{bar.instrument}:15m"].value
            pc = prev_close.get(bar.instrument)
            prev_close[bar.instrument] = bar.close
            capture["rows"].append({
                "instrument": bar.instrument,
                "bucket_start": ist(bar.start_ts).strftime("%Y-%m-%d %H:%M"),
                "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                "live_fast15": fast_line,
                "live_h1": h1.htf_value,
                "live_h1_prev": h1.prev_htf_value,
                "live_mid15": m15.htf_value,
                "strat_prev_htf": getattr(strat, "prev_htf", None),
                "strat_store_htf": getattr(strat, "_prev_htf_value", None),
                "strat_prev_close": getattr(strat, "_prev_fast_close", None),
                "bars_processed": getattr(strat, "_bars_processed", None),
                "prev_close": pc,
                "state": strat.state.value,
                "pos_side": strat.position_side,
                "pending_side": strat.pending_entry.side if strat.pending_entry else "",
                "pending_trig": strat.pending_entry.trigger_price if strat.pending_entry else "",
                "stop": strat.stop_price,
                "stop_flt": json.dumps([f"{e.get('event_type')}:{e.get('side','')}:{e.get('price','')}:{e.get('reason','')}"
                                        for e in events]),
            })
            capture["events"].extend(
                {"instrument": bar.instrument, "bucket_start": ist(bar.start_ts).strftime("%Y-%m-%d %H:%M"),
                 "strategy": strat.strategy_id, **e} for e in events)
    engine.market_status.force_state(MarketState.AFTER_MARKET)
    return capture


# ═══════════════════════════════════════════════════════════════
# Reports
# ═══════════════════════════════════════════════════════════════
def _f(x):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:g}"


def _ft(ts):
    return ist(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def compare(reftag, run_id, rows_by_inst, capture, closed_trades):
    ref_lines = {n: pd.read_csv(REF_DIR / f"REFERENCE_LINES_{n}.csv") for n in ("GOLDM", "SILVERM")}
    ref_trades = pd.read_csv(REF_DIR / "REFERENCE_TRADES.csv")

    live_df = pd.DataFrame(capture["rows"])
    data_rows, ind_rows, sig_rows = [], [], []
    for inst in ("GOLDM", "SILVERM"):
        rl = ref_lines[inst].set_index("bucket_start")
        lv = live_df[live_df["instrument"] == inst].set_index("bucket_start")
        if rl.empty or lv.empty:
            continue
        r1, r2, c1 = rl.first_valid_index(), lv.first_valid_index(), 0
        for k in rl.index:
            if k not in lv.index:
                c1 += 1
        # Warm-up/availability gate diagnostics from per-bar captures
        bp = lv["bars_processed"].to_numpy(float)
        started = ""
        missed_pre_h1 = 0
        if (bp > 0).any():
            first_done = lv.index[int(np.argmax(bp > 0))]
            started = first_done
            pre = lv.loc[:first_done, "live_h1"]
            missed_pre_h1 = int(np.isfinite(pre.to_numpy(float)).sum()) if len(pre) else 0
        data_rows.append({
            "run": run_id, "instrument": inst, "ref_base_bars": len(rl),
            "live_fast_bars": len(lv), "missing_live": c1,
            "live_first_processed": started,
            "live_processed_bars": int(np.sum(bp > 0)),
            "ref_h1_finite": int(np.isfinite(rl["h1"].to_numpy(float)).sum()),
            "live_h1_finite": int(np.isfinite(lv["live_h1"].to_numpy(float)).sum()),
        })
        # shared keys
        keys = [k for k in rl.index if k in lv.index]
        rh15 = rl.loc[keys, "h15"].to_numpy(float)
        lh15 = lv.loc[keys, "live_fast15"].to_numpy(float)
        rh1 = rl.loc[keys, "h1"].to_numpy(float)
        lh1 = lv.loc[keys, "live_h1"].to_numpy(float)
        rm15 = rl.loc[keys, "h15"].to_numpy(float)   # reference 15m line == mid (equal-time)
        lm15 = lv.loc[keys, "live_mid15"].to_numpy(float)
        both15 = np.isfinite(rh15) & np.isfinite(lh15)
        both1 = np.isfinite(rh1) & np.isfinite(lh1)
        ind_rows.append({
            "run": run_id, "instrument": inst,
            "shared_bars": len(keys),
            "h15_exact_equal": int(np.sum(np.isclose(rh15, lh15, rtol=0, atol=1e-3))),
            "h15_max_abs_diff": f"{np.max(np.abs(rh15[both15] - lh15[both15])):.6g}" if both15.any() else "NA",
            "h1_exact_equal": int(np.sum(np.isclose(rh1, lh1, rtol=0, atol=1e-3))),
            "h1_max_abs_diff": f"{np.max(np.abs(rh1[both1] - lh1[both1])):.6g}" if both1.any() else "NA",
            "m15_exact_equal": int(np.sum(np.isclose(rm15, lm15, rtol=0, atol=1e-3))),
            "m15_max_abs_diff": f"{np.max(np.abs(rm15[both15] - lm15[both15])):.6g}" if both15.any() else "NA",
            "h1_diff_bars_over_0_01": int(np.sum(np.abs(rh1[both1] - lh1[both1]) > 0.01)) if both1.any() else 0,
            "h15_diff_bars_over_0_01": int(np.sum(np.abs(rh15[both15] - lh15[both15]) > 0.01)) if both15.any() else 0,
        })
        # signal parity: reference flag vs live pending armed / event
        ev = pd.DataFrame(capture["events"]).pipe(lambda d: d[d["instrument"] == inst]) if capture["events"] else pd.DataFrame()
        for k in keys:
            rb = bool(rl.loc[k, "buy"]); rs = bool(rl.loc[k, "sell"])
            le = ev[ev["bucket_start"] == k]
            live_armed = ""
            if len(le):
                for _, e in le.iterrows():
                    if e.get("event_type") in ("PENDING_ENTRY_CREATED", "REVERSAL_SIGNAL"):
                        live_armed = e.get("side", "") + ("-REV" if e.get("event_type") == "REVERSAL_SIGNAL" else "")
            rsig = "LONG" if rb else ("SHORT" if rs else "")
            ls = live_armed.replace("-REV", "")
            mm = "-"
            if rsig or live_armed:
                mm = "MATCH" if rsig == ls else "DIFF"
            sig_rows.append({
                "run": run_id, "instrument": inst, "bucket_start": k,
                "close": _f(rl.loc[k, "close"]), "h1": _f(rl.loc[k, "h1"]),
                "h15": _f(rl.loc[k, "h15"]),
                "ref_signal": rsig,
                "live_signal": live_armed,
                "match": mm,
            })
    # trade parity / financial parity
    # Build live trade episodes from the strategy EVENT stream (bar-bucket
    # anchored).  Ledger first_fill_time/last_exit_fill_time are WALL-CLOCK
    # time.time() stamps in the live engine, so they must NOT be used to align
    # trades with the reference (which is bar-bucket anchored).
    episodes = {}
    ev_df = pd.DataFrame(capture["events"]) if capture["events"] else pd.DataFrame()
    for inst in ("GOLDM", "SILVERM"):
        episodes[inst] = []
        if ev_df.empty:
            continue
        sub = ev_df[ev_df["instrument"] == inst]
        for _, e in sub.iterrows():
            et = e.get("event_type")
            if et == "ENTRY_EXECUTED":
                episodes[inst].append({
                    "side": e.get("side", ""),
                    "entry_bucket": e.get("bucket_start", ""),
                    "entry_price": float(e.get("price") or 0),
                    "exit_bucket": "", "exit_price": 0.0,
                    "exit_reason": "", "net": 0.0,
                })
            elif et == "POSITION_CLOSED" and episodes[inst]:
                ep = episodes[inst][-1]
                ep["exit_bucket"] = e.get("bucket_start", "")
                ep["exit_price"] = float(e.get("exit_price") or 0)
                ep["exit_reason"] = str(e.get("reason", ""))
                ep["net"] = ep["exit_price"] - ep["entry_price"] if ep["side"] == "LONG" \
                    else ep["entry_price"] - ep["exit_price"]

    closed = sorted(closed_trades, key=lambda t: (t.instrument, t.first_fill_time or 0))
    ledger_by_inst = {}
    for inst in ("GOLDM", "SILVERM"):
        ledger_by_inst[inst] = [t for t in closed if t.instrument == inst]
    trade_rows = []
    rl = ref_trades.copy()
    for inst in ("GOLDM", "SILVERM"):
        reft = rl[rl["instrument"] == inst]
        liv = [ep for ep in episodes[inst] if ep["exit_bucket"]]
        # align each episode (in event order) to the ledger closed trade
        # (same per-instrument order: each ENTRY_EXECUTED opens the position
        # that the next POSITION_CLOSED of that instrument closes).
        leds = ledger_by_inst[inst]
        # align by side + entry bar-bucket
        ref_used = set(); live_used = set()
        for ie, ep in enumerate(liv):
            live_entry = ep["entry_price"]
            live_exit = ep["exit_price"]
            ledsi = leds[ie] if ie < len(leds) else None
            live_net = float(ledsi.net_pnl) if ledsi is not None else float("nan")
            live_gross = float(ledsi.gross_pnl) if ledsi is not None else float("nan")
            live_fees = float(ledsi.fees) if ledsi is not None else float("nan")
            hit = None
            for ri, rt in reft.iterrows():
                if ri in ref_used:
                    continue
                if rt["side"] == ep["side"] and str(rt["entry_time"]) == ep["entry_bucket"]:
                    hit = (ri, rt); break
            if hit:
                ref_used.add(hit[0]); live_used.add(ie)
                rt = hit[1]
                ref_entry = float(rt["entry_price"]); ref_exit = float(rt["exit_price"])
                ref_net = float(rt["pnl"]); ref_gross = float(rt["gross_pnl"]); ref_fees = float(rt["charges"])
                trade_rows.append({
                    "run": run_id, "instrument": inst,
                    "side": ep["side"], "entry_time": ep["entry_bucket"],
                    "ref_entry": f"{ref_entry:g}", "live_entry": f"{live_entry:g}",
                    "entry_diff": f"{ref_entry - live_entry:g}",
                    "ref_trigger": f"{float(rt['trigger']):g}",
                    "live_fill_is_trigger": int(abs(live_entry - float(rt['trigger'])) < 1e-6),
                    "exit_time": str(rt["exit_time"]), "live_exit_time": ep["exit_bucket"],
                    "ref_exit": f"{ref_exit:g}", "live_exit": f"{live_exit:g}",
                    "exit_diff": f"{ref_exit - live_exit:g}",
                    "ref_reason": str(rt["exit_reason"]), "live_reason": ep["exit_reason"],
                    "ref_net": f"{ref_net:.2f}", "live_net": f"{live_net:.2f}",
                    "ref_gross": f"{ref_gross:.2f}", "live_gross": f"{live_gross:.2f}",
                    "ref_fees": f"{ref_fees:.2f}", "live_fees": f"{live_fees:.2f}",
                })
            else:
                live_used.add(ie)
                trade_rows.append({
                    "run": run_id, "instrument": inst, "side": ep["side"],
                    "entry_time": ep["entry_bucket"], "ref_entry": "-",
                    "live_entry": f"{live_entry:g}", "entry_diff": "-",
                    "ref_trigger": "-", "live_fill_is_trigger": 0,
                    "exit_time": "-", "live_exit_time": ep["exit_bucket"],
                    "ref_exit": "-", "live_exit": f"{live_exit:g}", "exit_diff": "-",
                    "ref_reason": "-", "live_reason": ep["exit_reason"],
                    "ref_net": "-", "live_net": f"{live_net:.2f}",
                    "ref_gross": "-", "live_gross": f"{live_gross:.2f}",
                    "ref_fees": "-", "live_fees": f"{live_fees:.2f}",
                })
        for ri, rt in reft.iterrows():
            if ri not in ref_used:
                trade_rows.append({
                    "run": run_id, "instrument": inst,
                    "side": rt["side"], "entry_time": str(rt["entry_time"]),
                    "ref_entry": f"{float(rt['entry_price']):g}", "live_entry": "-",
                    "entry_diff": "-", "ref_trigger": f"{float(rt['trigger']):g}", "live_fill_is_trigger": 0,
                    "exit_time": str(rt["exit_time"]), "live_exit_time": "-",
                    "ref_exit": f"{float(rt['exit_price']):g}", "live_exit": "-", "exit_diff": "-",
                    "ref_reason": str(rt["exit_reason"]), "live_reason": "-",
                    "ref_net": f"{float(rt['pnl']):.2f}", "live_net": "-",
                    "ref_gross": f"{float(rt['gross_pnl']):.2f}", "live_gross": "-",
                    "ref_fees": f"{float(rt['charges']):.2f}", "live_fees": "-",
                })
    # financial aggregates
    fin_rows = []
    for inst in ("GOLDM", "SILVERM"):
        reft = rl[rl["instrument"] == inst]
        liv = [t for t in closed if t.instrument == inst]
        fin_rows.append({
            "run": run_id, "instrument": inst,
            "ref_trades": len(reft), "live_trades": len(liv),
            "ref_net_sum": round(float(reft["pnl"].astype(float).sum()), 2),
            "live_net_sum": round(sum(t.net_pnl or 0 for t in liv), 2),
            "ref_gross_sum": round(float(reft["gross_pnl"].astype(float).sum()), 2),
            "live_gross_sum": round(sum(t.gross_pnl or 0 for t in liv), 2),
            "ref_fees_sum": round(float(reft["charges"].astype(float).sum()), 2),
            "live_fees_sum": round(sum(t.fees or 0 for t in liv), 2),
            "live_slippage_cost_sum": round(sum(t.slippage_cost or 0 for t in liv), 2),
        })
    pd.DataFrame(data_rows).to_csv(OUT_DIR / "DATA_PARITY_REPORT.csv", index=False, mode="a",
                                   header=not (OUT_DIR / "DATA_PARITY_REPORT.csv").exists())
    pd.DataFrame(ind_rows).to_csv(OUT_DIR / "INDICATOR_PARITY_REPORT.csv", index=False, mode="a",
                                  header=not (OUT_DIR / "INDICATOR_PARITY_REPORT.csv").exists())
    pd.DataFrame(sig_rows).to_csv(OUT_DIR / "SIGNAL_PARITY_REPORT.csv", index=False, mode="a",
                                  header=not (OUT_DIR / "SIGNAL_PARITY_REPORT.csv").exists())
    pd.DataFrame(trade_rows).to_csv(OUT_DIR / "TRADE_PARITY_REPORT.csv", index=False, mode="a",
                                    header=not (OUT_DIR / "TRADE_PARITY_REPORT.csv").exists())
    pd.DataFrame(fin_rows).to_csv(OUT_DIR / "FINANCIAL_PARITY_REPORT.csv", index=False, mode="a",
                                  header=not (OUT_DIR / "FINANCIAL_PARITY_REPORT.csv").exists())
    print(f"[{run_id}] data rows={len(data_rows)} ind rows={len(ind_rows)} sig rows={len(sig_rows)} "
          f"trade rows={len(trade_rows)} fin rows={len(fin_rows)}")


def main():
    import trading_engine as te

    if RUN_ROOT.exists():
        shutil.rmtree(RUN_ROOT)

    # Remove stale combined parity reports so a re-run cannot append duplicates
    # (reports are written with mode='a').
    for f in ("DATA_PARITY_REPORT.csv", "INDICATOR_PARITY_REPORT.csv",
              "SIGNAL_PARITY_REPORT.csv", "TRADE_PARITY_REPORT.csv",
              "FINANCIAL_PARITY_REPORT.csv"):
        (OUT_DIR / f).unlink(missing_ok=True)

    rows_by_inst = {
        n: load_rows(CSV_PATHS[n]) for n in ("GOLDM", "SILVERM")
    }
    window_rows = {
        n: [r for r in rows if ist(r[0]).strftime("%Y-%m-%d") in WINDOW]
        for n, rows in rows_by_inst.items()
    }

    for run_id, (slippage, keep_partial, warmup, tick_proc) in {
        "A_controlled":       (0, True,  False, False),
        "B_production":       (1, False, True,  True),
        # C = option-2 fix applied: backtest-aligned bars (keep_partial -> D2
        # resolved) + production execution realism (slippage + tick). Reference
        # signals must now MATCH 1:1 (the warmup-side D3 fix is verified by
        # _step6_warmup_alignment_check.py against _warmup_from_rest).
        "C_matched":          (1, True,  False, True),
    }.items():
        root = RUN_ROOT / run_id
        cfg = make_config(root, slippage, keep_partial)
        te.DhanDataAdapter = ParityReplayDataAdapter
        ParityReplayDataAdapter.STORE = rows_by_inst
        ParityReplayDataAdapter.CAP_BEFORE = None if not warmup else WINDOW_START

        engine, persistence = build_engine(cfg)
        engine.tick_signal_processing = tick_proc

        if warmup:
            _real = _dtm.datetime
            _dtm.datetime = _FakeDatetime
            try:
                engine._warmup_from_rest()
            finally:
                _dtm.datetime = _real
            print(f"[{run_id}] warmup done. 1h engine bars=",
                  [len(s.end_times) for s in engine.htf_engine._engines.values()])

        stream = build_stream(window_rows, keep_partial=keep_partial)
        print(f"[{run_id}] stream days={sorted(stream)} bars={sum(len(v) for v in stream.values())}")
        capture = run_replay(engine, stream, tick_proc)

        # persist per-run captures
        pd.DataFrame(capture["rows"]).to_csv(OUT_DIR / f"LIVE_ROWS_{run_id}.csv", index=False)
        pd.DataFrame(capture["events"]).to_csv(OUT_DIR / f"LIVE_EVENTS_{run_id}.csv", index=False)

        closed = engine.trade_ledger.get_closed_trades()
        pd.DataFrame([{
            "strategy": t.strategy_id, "instrument": t.instrument, "side": t.side,
            "entry_time": _ft(t.first_fill_time),
            "entry_price": t.average_entry_price,
            "exit_time": _ft(t.last_exit_fill_time),
            "exit_price": t.average_exit_price, "exit_reason": t.exit_reason,
            "gross": t.gross_pnl, "fees": t.fees, "slippage_cost": t.slippage_cost,
            "net": t.net_pnl,
        } for t in closed]).to_csv(OUT_DIR / f"LIVE_TRADES_{run_id}.csv", index=False)
        print(f"[{run_id}] closed trades={len(closed)}")

        compare(run_id, run_id, rows_by_inst, capture, closed)
        teardown(engine, persistence)

    print("=== PARITY REPLAY COMPLETE ===")


if __name__ == "__main__":
    main()