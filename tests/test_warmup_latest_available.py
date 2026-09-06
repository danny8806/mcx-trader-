"""Finite focused tests: Dhan REST latest-available warmup/backfill, MCX.

Covers the Dhan-direct-source contract:
  - requested end is a fetch boundary, not a market-close rule;
  - weekend/Sunday/Saturday/Monday-pre-open use the latest available Dhan
    trading candle (no synthetic weekend candles, no "missing data" failure);
  - mid-session restart: the forming candle never enters completed state;
  - the final Dhan candle completes the same identity exactly once;
  - native 5m/15m/1H are preserved (no resampling); the 23:00 1H survives;
  - four strategies consume the same verified shared native streams;
  - no indicator contamination; dedup never freezes a forming candle.

All scenario dates are derived relative to the run date so the suite stays
inside the engine's fetch window on any machine.
"""
from __future__ import annotations

import copy
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

import trading_engine as te
from events.types import CandleEvent
from indicators.dema_atr import DEMAATR

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
REAL = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))

_TODAY = datetime.now(IST).date()


def iso(d: datetime.date) -> str:
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _last_weekdays(n: int) -> list:
    out = []
    d = _TODAY - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


WEEKDAYS = _last_weekdays(3)


def ist_epoch(datestr: str, hhmm: str) -> int:
    dt = datetime.strptime(f"{datestr} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return int(dt.timestamp())


def make_bar(s: float, base: float) -> list:
    return [float(s), base + 1.0, base + 3.0, base - 2.0, base, 25.0]


def day(tf: str, date: str, last_start: str = "23:25", base: float = 100000.0) -> list:
    step = {"5": 5, "15": 15, "60": 60}[tf]
    starts = []
    t = ist_epoch(date, "09:00")
    last = ist_epoch(date, last_start)
    while t <= last:
        starts.append(t)
        t += step * 60
    return [make_bar(float(s), base) for s in starts]


class FakeAdapter:
    def __init__(self):
        self.scenario: dict = {}
        self.ws = None
        self.on_status = None
        self._live_ltp = {}
        self.rest = types.SimpleNamespace(stats={"ok": 0, "empty": 0, "retry": 0})
        self._instruments = {}

    def register_instruments(self, instruments: dict) -> None:
        self._instruments = instruments

    def fetch_historical_candles(self, symbol, tf_id, from_date, to_date):
        return list(self.scenario.get(symbol, {}).get(tf_id, []))

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass


def build_engine(tmp_path, scenario: dict):
    cfg = copy.deepcopy(REAL)
    cfg["system"]["db_path"] = str(tmp_path / "trading.db")
    cfg["system"]["state_path"] = str(tmp_path / "system_state.json")
    cfg["dhan"].update({
        "client_id": "TEST",
        "token_file": str(tmp_path / "dhan_token.json"),
        "pin": "", "totp_secret": "", "access_token": "",
        "ws_url": "wss://fake", "rest_base": "https://fake",
    })
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(cfg), encoding="utf-8")
    fake = FakeAdapter()
    fake.scenario = scenario

    def _factory(**kwargs):
        return fake

    with mock.patch.object(te, "DhanDataAdapter", _factory):
        engine = te.TradingEngine(config_path=str(sp))
    engine.data_adapter = fake
    return engine, fake


def gold_three_days() -> dict:
    d_a, d_b, d_c = (iso(d) for d in WEEKDAYS)
    return {
        "5": day("5", d_a) + day("5", d_b) + day("5", d_c),
        "15": day("15", d_a) + day("15", d_b) + day("15", d_c),
        "60": (day("60", d_a, "23:00") + day("60", d_b, "23:00")
               + day("60", d_c, "23:00")),
    }


def fed_opens(stream) -> list:
    tf60 = 60 * {"5m": 5, "15m": 15, "1h": 60}[stream.timeframe]
    return [float(e - tf60) for e in stream._end_times]


def dema_reference(series, dp=3, ap=6):
    ind = DEMAATR(dp, ap, 1.0)
    vals = []
    for c in series:
        ind.update(c[1], c[2], c[3], c[4])
        vals.append(ind.value)
    return ind, vals


def test_normal_day_reaches_last_closed_and_rejects_post_close_echo(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    echo = make_bar(float(ist_epoch(date, "23:30")), 100000.0)
    scen = {"GOLDM": {
        "5": day("5", date) + [echo],
        "15": day("15", date),
        "60": day("60", date, "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)

    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    assert fast.bar_count() == 174
    assert fast._end_times[-1] == ist_epoch(date, "23:30")
    assert fast._end_times[-1] - fast._end_times[-2] == 300
    wm = engine._warmup_watermark["gold_01_5"]
    assert wm["latest_completed_open_ts"] == ist_epoch(date, "23:25")
    assert wm["latest_completed_end_ts"] == ist_epoch(date, "23:30")
    assert wm["source"] == "DHAN_REST"

    fore = engine._warmup_forensics["gold_01_5"]
    assert fore["dhan_return_count"] == 175
    assert fore["rejected_candle_count"] == 1
    assert fore["rejected"][0]["reason"] == "OUTSIDE_SUPPORTED_SESSION"
    assert fore["forming_candle_count"] == 0
    assert fore["source"] == "DHAN_REST"

    mid = engine.strategies["gold_01"]._shared_streams["mid"]
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    assert mid.bar_count() == 58
    assert mid._end_times[-1] == ist_epoch(date, "23:30")
    assert slow.bar_count() == 15
    assert slow._end_times[-1] == ist_epoch(date, "23:00") + 3600


def test_three_day_native_grid_preserved_5m_15m_1h(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    scen = {"GOLDM": gold_three_days(), "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    mid = engine.strategies["gold_01"]._shared_streams["mid"]
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    assert fast.bar_count() == 174 * 3
    assert mid.bar_count() == 58 * 3
    assert slow.bar_count() == 15 * 3
    assert slow._end_times[-1] == ist_epoch(date, "23:00") + 3600


@pytest.mark.parametrize("offset_days,hhmm", [(1, "12:00"), (2, "15:30"), (3, "08:30")])
def test_weekend_and_preopen_use_latest_available_friday_candle(tmp_path, offset_days, hhmm):
    last = WEEKDAYS[-1]
    later = last + timedelta(days=offset_days)
    now = ist_epoch(iso(later), hhmm)
    scen = {"GOLDM": {
        "5": day("5", iso(last)),
        "15": day("15", iso(last)),
        "60": day("60", iso(last), "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    assert fast.bar_count() == 174
    opens = fed_opens(fast)
    assert all(datetime.fromtimestamp(o, tz=IST).date().isoformat() == iso(last)
               for o in opens)
    wm = engine._warmup_watermark["gold_01_5"]
    assert wm["latest_completed_open_ts"] == ist_epoch(iso(last), "23:25")
    assert wm["latest_completed_end_ts"] == ist_epoch(iso(last), "23:30")
    fore = engine._warmup_forensics["gold_01_5"]
    assert fore["forming_candle_count"] == 0
    assert fore["rejected_candle_count"] == 0
    assert fore["last_completed_candle"] == f"{iso(last)}T23:25:00+05:30"
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    assert slow.bar_count() == 15
    assert ist_epoch(iso(last), "23:00") in fed_opens(slow)


def test_mid_session_restart_forming_candle_rejected(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "10:37")
    forming5 = make_bar(float(ist_epoch(date, "10:35")), 100000.0)
    scen = {"GOLDM": {
        "5": day("5", date, "10:30") + [forming5],
        "15": day("15", date, "10:30"),
        "60": day("60", date, "10:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)

    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    assert fast.bar_count() == 19
    assert fast._end_times[-1] == ist_epoch(date, "10:35")
    opens = fed_opens(fast)
    assert ist_epoch(date, "10:35") not in opens
    assert ist_epoch(date, "10:30") in opens

    fore = engine._warmup_forensics["gold_01_5"]
    assert fore["forming_candle_count"] == 1
    assert fore["rejected_candle_count"] == 1
    assert fore["rejected"][0]["reason"] == "FORMING_CANDLE"
    wm = engine._warmup_watermark["gold_01_5"]
    assert wm["latest_completed_open_ts"] == ist_epoch(date, "10:30")

    mid = engine.strategies["gold_01"]._shared_streams["mid"]
    assert mid.bar_count() == 6
    assert mid._end_times[-1] == ist_epoch(date, "10:30")
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    assert slow.bar_count() == 1


def test_final_candle_replacement_no_double_count_no_freeze(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "10:41")
    completed = day("5", date, "10:35")
    scen = {"GOLDM": {
        "5": completed,
        "15": day("15", date, "10:30"),
        "60": day("60", date, "10:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)

    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    assert fast.bar_count() == 20
    wm = engine._warmup_watermark["gold_01_5"]
    assert wm["latest_completed_open_ts"] == ist_epoch(date, "10:35")
    assert wm["latest_completed_end_ts"] == ist_epoch(date, "10:40")

    ref, ref_vals = dema_reference(completed)
    assert abs(fast.dema_value - ref.dema_value) < 1e-6
    fed_cnt = fast._count
    dedup_before = fast._dedup_count

    engine._warmup_from_rest(now_epoch=now)
    assert fast.bar_count() == 20
    assert fast._count == fed_cnt
    assert fast._dedup_count == dedup_before + 20
    assert abs(fast.dema_value - ref.dema_value) < 1e-6


def test_duplicate_rest_fetch_deduped(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    base = day("5", date)
    dup = base + [base[0], base[10]]
    scen = {"GOLDM": {
        "5": dup,
        "15": day("15", date),
        "60": day("60", date, "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    assert fast.bar_count() == 174
    assert fast._count == 174
    fore = engine._warmup_forensics["gold_01_5"]
    assert fore["duplicate_count"] == 2


def test_repeated_restart_idempotent_watermark_and_dema(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    scen = {"GOLDM": {
        "5": day("5", date),
        "15": day("15", date),
        "60": day("60", date, "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    fast = engine.strategies["gold_01"]._shared_streams["fast"]
    wm1 = engine._warmup_watermark["gold_01_5"]
    d1 = fast.dema_value
    for _ in range(2):
        engine._warmup_from_rest(now_epoch=now)
        assert engine._warmup_watermark["gold_01_5"] == wm1
        assert fast.dema_value == d1
        assert fast.bar_count() == 174


def test_1h_2300_completes_at_session_close_not_grid_end(tmp_path):
    date = iso(WEEKDAYS[-1])
    scen = {"GOLDM": {
        "5": day("5", date),
        "15": day("15", date),
        "60": day("60", date, "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=ist_epoch(date, "23:20"))
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    assert slow.bar_count() == 14
    assert ist_epoch(date, "23:00") not in fed_opens(slow)
    engine._warmup_from_rest(now_epoch=ist_epoch(date, "23:35"))
    assert slow.bar_count() == 15
    assert ist_epoch(date, "23:00") in fed_opens(slow)


def test_native_1h_2300_survives_session_boundary(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    scen = {"GOLDM": {
        "5": day("5", date),
        "15": day("15", date),
        "60": day("60", date, "23:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    slow = engine.strategies["gold_01"]._shared_streams["slow"]
    opens = fed_opens(slow)
    assert ist_epoch(date, "23:00") in opens
    assert min(opens) == ist_epoch(date, "09:00")
    assert max(opens) == ist_epoch(date, "23:00")


def test_four_strategies_share_same_native_streams(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "23:45")
    scen = {"GOLDM": gold_three_days(), "SILVERM": gold_three_days()}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    s = engine.strategies
    assert s["gold_02"]._shared_streams["fast"] is s["gold_01"]._shared_streams["mid"]
    assert s["gold_02"]._shared_streams["slow"] is s["gold_01"]._shared_streams["slow"]
    assert s["silver_01"]._shared_streams["fast"] is s["silver_02"]._shared_streams["mid"]
    assert s["silver_01"]._shared_streams["slow"] is s["silver_02"]._shared_streams["slow"]
    assert s["gold_01"]._shared_streams["fast"].bar_count() == 174 * 3
    assert s["gold_02"]._shared_streams["fast"].bar_count() == 58 * 3
    assert s["silver_01"]._shared_streams["fast"].bar_count() == 58 * 3
    assert s["silver_02"]._shared_streams["fast"].bar_count() == 174 * 3
    assert s["gold_01"]._shared_streams["mid"].dema_value == \
        s["gold_02"]._shared_streams["fast"].dema_value
    assert s["silver_01"]._shared_streams["fast"].dema_value == \
        s["silver_02"]._shared_streams["mid"].dema_value


def test_no_partial_candle_contamination_after_warmup(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "10:37")
    partial_then_final = day("5", date, "10:35")
    forming5 = make_bar(float(ist_epoch(date, "10:35")), 100000.0)
    scen = {"GOLDM": {
        "5": day("5", date, "10:30") + [forming5],
        "15": day("15", date, "10:30"),
        "60": day("60", date, "10:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    fast = engine.strategies["gold_01"]._shared_streams["fast"]

    ref19, _ = dema_reference(day("5", date, "10:30"))
    assert abs(fast.dema_value - ref19.dema_value) < 1e-6

    view = engine.strategies["gold_01"].fast_indicator
    final_bar = partial_then_final[-1]
    view.update(final_bar[1], final_bar[2], final_bar[3], final_bar[4],
                end_ts=float(final_bar[0]) + 300)
    ref20, _ = dema_reference(partial_then_final)
    assert abs(fast.dema_value - ref20.dema_value) < 1e-6
    assert fast.bar_count() == 20
    assert fast._dedup_count == 0


def test_signal_parity_invariant_after_warmup(tmp_path):
    date = iso(WEEKDAYS[-1])
    now = ist_epoch(date, "18:00")
    seq = day("5", date, "17:55", base=100000.0)
    scen = {"GOLDM": {
        "5": seq,
        "15": day("15", date, "17:45"),
        "60": day("60", date, "17:00"),
    }, "SILVERM": {}}
    engine, _ = build_engine(tmp_path, scen)
    engine._warmup_from_rest(now_epoch=now)
    strat = engine.strategies["gold_01"]
    assert strat._prev_fast_close is None
    assert strat._bars_processed == 0
    ref, _ = dema_reference(seq)
    fast = strat._shared_streams["fast"]
    assert abs(fast.dema_value - ref.dema_value) < 1e-6

    next_bar = make_bar(float(seq[-1][0]) + 300, 100005.0)
    ev = CandleEvent(
        instrument="GOLDM", timeframe="5m",
        start_ts=float(next_bar[0]), end_ts=float(next_bar[0]) + 300,
        open=next_bar[1], high=next_bar[2], low=next_bar[3], close=next_bar[4],
        volume=next_bar[5],
    )
    signal = strat.on_candle(ev)
    assert strat._bars_processed == 1
    assert strat._prev_fast_close == float(next_bar[4])
    assert abs(fast.dema_value - dema_reference(seq + [next_bar])[0].dema_value) < 1e-6
    if signal is not None:
        assert signal.side in ("LONG", "SHORT")
    signal2 = strat.on_candle(ev)
    assert signal2 is None
    assert fast._dedup_count == 1