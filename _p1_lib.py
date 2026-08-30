"""PHASE-1 REMEDIATION — shared test library.

Loads CURRENT production code.  Provides independent reference math, CSV-backed
REST adapters, engine builders, and report writers used by the new _p1_* tests.

Independent reference values are computed here (never copied from previous runs)
so a test fails whenever production behaviour is actually wrong.
"""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))

CSV_DIR = Path(r"C:\Users\pc\Desktop\nifty dema backtest\project\data_mcx")
CSVS = {
    "GOLDM": "gold/GOLDM_04Sep2026_5m.csv",
    "SILVERM": "silver/SILVERM_30Nov2026_5m.csv",
}
MULT = {"GOLDM": 10.0, "SILVERM": 5.0}
LAST5 = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]

AUDIT_DIR = ROOT / "_BACKTEST_VS_LIVE_AUDIT"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

from full_simulator import LIVE_INSTRUMENTS, LIVE_STRATEGIES, build_bars, ist  # noqa: E402


def ist_from_epoch(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)


def is_weekday_ist(d: date) -> bool:
    return d.weekday() < 5


def load_csv_rows(name: str, start: str | None = None, stop: str | None = None):
    """CSV -> Dhan REST row format [epoch_ist, o, h, l, c, v] filtered to [start, stop] dates (inclusive)."""
    out = []
    with open(CSV_DIR / CSVS[name], encoding="utf-8") as f:
        for r in csv.DictReader(f):
            dd = r["datetime"][:10]
            if start is not None and dd < start:
                continue
            if stop is not None and dd > stop:
                continue
            naive = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
            epoch = naive.replace(tzinfo=IST).timestamp()
            out.append([epoch, float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"]), float(r["volume"])])
    out.sort(key=lambda r: r[0])
    return out


class _MockWS:
    connected = True
    _stats = {"tick": 0}
    _instruments = {}
    _last_tick_time = 0.0

    def is_stale(self) -> bool:
        return False


class CSVFeedAdapter:
    """Drop-in for DhanDataAdapter backed by the real CSV data (offline REST).

    fetch_historical_candles honours the exact [from_date, to_date] window the
    engine requests, so warmup window-extension behaviour is exercised for real.
    """

    def __init__(self, client_id="", token_file="", pin="", totp_secret="",
                 on_tick=None, on_status=None, **kwargs):
        self.client_id = client_id
        self._on_tick = on_tick
        self._on_status = on_status
        self.ws = _MockWS()
        self.instruments = {}
        self.requests = []

    def register_instruments(self, instruments: dict) -> None:
        self.instruments = instruments

    def connect(self) -> None:
        self.ws.connected = True

    def disconnect(self) -> None:
        self.ws.connected = False

    def fetch_historical_candles(self, name, timeframe, from_date, to_date):
        if timeframe != "5":
            return []
        self.requests.append((str(from_date), str(to_date)))
        return load_csv_rows(name, str(from_date), str(to_date))


class HolidayAdapter:
    """Synthetic adapter that returns 5m rows only on weekdays minus a holiday
    set, within the requested window.  Used to prove the warmup session-count
    guarantee: heavy holiday/weekend clusters must not shrink the session set."""

    def __init__(self, holidays=set(), base=100000.0, **kwargs):
        self.holidays = set(
            date.fromisoformat(h) if isinstance(h, str) else h for h in holidays)
        self.base = base
        self.requests = []
        self.ws = _MockWS()
        self.instruments = {}

    def register_instruments(self, instruments: dict) -> None:
        self.instruments = instruments

    def connect(self) -> None:
        self.ws.connected = True

    def disconnect(self) -> None:
        self.ws.connected = False

    def _rows_for(self, from_date, to_date):
        rows = []
        day = from_date
        while day <= to_date:
            if is_weekday_ist(day) and day not in self.holidays:
                for hm in range(9 * 60, 23 * 60 + 25, 5):
                    naive = datetime(day.year, day.month, day.day, hm // 60, hm % 60)
                    ep = naive.replace(tzinfo=IST).timestamp()
                    p = self.base + 0.01 * hm
                    rows.append([ep, p, p + 1.0, p - 1.0, p, 1.0])
            day += timedelta(days=1)
        rows.sort(key=lambda r: r[0])
        return rows

    def fetch_historical_candles(self, name, timeframe, from_date, to_date):
        self.requests.append((str(from_date), str(to_date)))
        return self._rows_for(from_date, to_date)


def write_config(root: Path, warmup: dict | None = None, instruments=None,
                 strategies=None) -> Path:
    """Write an isolated engine config (mirrors config/settings.json)."""
    data = {
        "system": {"name": "Phase1Remediation", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake", "token_file": str(root / "data" / "db" / "dhan_token.json"),
                 "pin": "", "totp_secret": ""},
        "warmup": warmup if warmup is not None
        else {"last_trading_days": 5, "fetch_calendar_days": 14,
              "max_fetch_calendar_days": 62, "fetch_extend_step_days": 7,
              "keep_partial": True},
        "instruments": instruments if instruments is not None else LIVE_INSTRUMENTS,
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": strategies if strategies is not None else LIVE_STRATEGIES,
        "paper_execution": {"slippage_ticks": 1, "latency_ms": 1, "partial_fill_probability": 0.0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001, "gst_pct": 18.0,
                      "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001, "gst_pct": 18.0,
                        "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 1200000.0, "starting_capital_per_strategy": 300000.0,
                    "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg = root / "settings.json"
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg


def build_engine(cfg_path: Path, adapter_cls=None):
    """Real TradingEngine with the Dhan adapter substituted by an offline feed."""
    import trading_engine as te
    from persistence.manager import PersistenceManager
    from analytics.schema import init_analytics_db

    adapter_cls = adapter_cls or CSVFeedAdapter
    te.DhanDataAdapter = adapter_cls
    from config import Config

    (cfg_path.parent / "data" / "db").mkdir(parents=True, exist_ok=True)
    init_analytics_db(str(cfg_path.parent / "data" / "db" / "analytics.db"))

    persistence = PersistenceManager(
        state_path=str(cfg_path.parent / "data" / "db" / "system_state.json"),
        db_path=str(cfg_path.parent / "data" / "db" / "trading.db"))
    engine = te.TradingEngine(config_path=str(cfg_path))
    engine.set_persistence(persistence)
    return engine, persistence


def swap_adapter(engine, adapter) -> None:
    """Point a built engine at a different data adapter (post-init swap)."""
    engine.data_adapter = adapter
    if engine.candle_fetcher is not None:
        engine.candle_fetcher.data_adapter = adapter
    adapter._on_tick = engine._on_tick
    adapter._on_status = engine._on_status


def wire_trade_close(engine) -> None:
    from core.trade_close import TradeCloseManager
    engine._trade_close_manager = TradeCloseManager(
        position_manager=engine.position_manager,
        pnl_engines=engine.pnl_engines,
        account_engines=engine.account_engines,
        global_account=engine.account_engine,
        risk_engine=engine.risk_engine,
        persistence=engine._persistence,
        event_store=engine.event_store,
        telegram=engine.telegram,
        event_callback=engine._event_callback,
        trade_ledger=engine.trade_ledger,
    )


def fresh_run_root(label: str) -> Path:
    run = Path(r"C:\Users\pc\AppData\Local\Temp\opencode") / f"p1_{label}"
    if run.exists():
        shutil.rmtree(run)
    run.mkdir(parents=True, exist_ok=True)
    return run


def teardown(engine, persistence) -> None:
    try:
        engine.stop()
    except Exception:
        pass
    try:
        persistence.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# INDEPENDENT REFERENCE MATH (never production code as the reference)
# ═══════════════════════════════════════════════════════════════════
def ref_ema(values, period):
    """Independent EMA reference = recursive Wilder/multiplicative smoothing
    seeded at the first value (min_periods=1).  Mirrors the verified product
    (and backtest) DEMA warmup: the line is defined from bar 0."""
    s = pd.Series(values, dtype=float)
    return s.ewm(alpha=2.0 / (period + 1), adjust=False, min_periods=1).mean()


def ref_dema(values, period):
    e1 = ref_ema(values, period)
    e2 = ref_ema(e1, period)
    return 2.0 * e1 - e2


def ref_atr(highs, lows, closes, period):
    """Wilder ATR — independent reference."""
    hl = np.asarray(highs, dtype=float)
    lo = np.asarray(lows, dtype=float)
    cl = np.asarray(closes, dtype=float)
    prev_c = np.roll(cl, 1)
    prev_c[0] = cl[0]
    tr = np.maximum(hl - lo, np.maximum(np.abs(hl - prev_c), np.abs(lo - prev_c)))
    out = np.full(len(cl), np.nan)
    if len(cl) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(cl)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def ref_dema_atr(h, l, c_ser, period=3, atr_period=6, factor=1.0):
    """Independent DEMA-ATR with recursive band clamp (matches production DEMAATR)."""
    d = ref_dema(c_ser, period).to_numpy()
    a = ref_atr(h, l, c_ser, atr_period)
    out = np.full(len(c_ser), np.nan)
    prev = None
    for i in range(len(c_ser)):
        if np.isnan(d[i]):
            continue
        band = a[i] * factor if not np.isnan(a[i]) else np.nan
        cur = d[i] if prev is None else prev
        if not np.isnan(band):
            if d[i] - band > cur:
                cur = d[i] - band
            if d[i] + band < cur:
                cur = d[i] + band
        out[i] = cur
        prev = cur
    return out


def ref_mapping_index(end_times, target):
    """Independent mapping reference: searchsorted(..., side='right') - 1."""
    arr = np.sort(np.asarray(end_times, dtype=float))
    idx = np.searchsorted(arr, target, side="right") - 1
    return int(idx) if arr.size else -1


def ref_session_resample(df5: pd.DataFrame, tf_minutes: int, session_open: str = "09:00",
                         keep_partial: bool = False):
    """Independent session-aware resample for cross-checking the engine's HTF
    buckets.  keep_partial=False returns only full window buckets (matches the
    complete-window audit tools); keep_partial=True additionally retains
    end-of-session partial buckets (the 23:00 1H slot with 6/12 rows),
    matching the live warmup KEEP-ALL path and the backtest reference.
    Returns (df_htf, per_bucket_source_dates)."""
    d = df5.copy()
    dt = pd.to_datetime(d["datetime"])
    if dt.dt.tz is not None:
        dt = dt.dt.tz_localize(None)
    d["datetime"] = dt
    dates = dt.dt.date.astype(str)
    session_start = pd.to_datetime(dates + f" {session_open}")
    mins = ((dt - session_start).dt.total_seconds() // 60).astype(int)
    d["_bucket"] = session_start + pd.to_timedelta((mins // tf_minutes) * tf_minutes, unit="m")
    d["_src_date"] = dt.dt.date.astype(str)
    d = d[mins >= 0]
    if not keep_partial:
        counts = d.groupby("_bucket")["datetime"].transform("size")
        d = d[counts == tf_minutes // 5]
    out = d.groupby("_bucket", sort=True).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"}).reset_index()
    src_dates = d.groupby("_bucket")["_src_date"].agg(lambda v: sorted(set(v))).reset_index()
    return out, src_dates


def readonly_sql(db_path, query, *params):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [tuple(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def append_rows(path: Path, rows: list[dict]) -> None:
    """Append dict rows to a CSV report with auto header."""
    header = not path.exists()
    pd.DataFrame(rows).to_csv(path, index=False, mode="a", header=header)