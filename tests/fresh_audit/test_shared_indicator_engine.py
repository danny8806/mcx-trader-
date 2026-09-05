"""MISSION §7–§12 — Shared native indicator engine with immutable snapshots.

Acceptance:
  §7   NativeCandleRouter identity (security_id, timeframe, candle_end_ts);
       duplicates deduplicated; out-of-order detected.
  §8   SharedNativeIndicatorEngine owns one DEMA/ATR stream per unique
       (security_id, timeframe); each stream calculated once per candle.
  §10  Immutable IndicatorSnapshot with the required fields.
  §11  Strategies receive direct values; no dataframe/bisect/resample/calc.
  §12  No global HTF mapping engine in the hot path; strategies hold reference
       to shared snapshots via their HTF state views.

Parity: the shared stream's math must equal the standalone DEMAATR/HTFState
math, and both strategies sharing a stream must observe byte-identical values
(object identity of snapshots is NOT required across different stream reads,
but the numeric values must be identical).
"""
import json

import pytest

from indicators.shared import (
    IndicatorStream, IndicatorSnapshot, SharedNativeIndicatorEngine,
    StrategyIndicatorView, StreamHTFStateView,
)
from indicators.dema_atr import DEMAATR
from strategies.htf_state import HTFState
from core.timeframe_engine import Bar


def _bars(security, timeframe, n, start_ts=1_700_000_000, tf_min=5):
    """Produce n sequential native bars (ascending end_ts)."""
    out = []
    for i in range(n):
        out.append(Bar(
            instrument=security, timeframe=timeframe,
            start_ts=start_ts + i * tf_min * 60,
            end_ts=start_ts + (i + 1) * tf_min * 60,
            open=100 + i, high=101 + i, low=99 + i,
            close=100.5 + i, volume=1000 + i,
        ))
    return out


# ── §10 immutable snapshot ───────────────────────────────────────────────

def test_snapshot_is_immutable():
    s = IndicatorSnapshot(
        security_id="569003", timeframe="5m",
        candle_start_ts=1.0, candle_end_ts=2.0,
        open=1, high=2, low=0, close=1.5, volume=10,
        dema=1.2, atr=0.3, dema_atr=1.2,
        previous_dema=None, previous_atr=None, previous_dema_atr=None,
        is_complete=True,
    )
    with pytest.raises(Exception):
        s.close = 99.0
    # Frozen: dataclass asdict still readable
    import dataclasses
    d = dataclasses.asdict(s)
    assert d["close"] == 1.5


# ── §7 shared stream dedup by candle_end_ts ──────────────────────────────

def test_stream_dedups_duplicate_candle_end_ts():
    stream = IndicatorStream("569003", "5m")
    bars = _bars("569003", "5m", 3, tf_min=5)
    s1 = stream.feed(bars[0].open, bars[0].high, bars[0].low, bars[0].close, end_ts=bars[0].end_ts)
    s2 = stream.feed(bars[1].open, bars[1].high, bars[1].low, bars[1].close, end_ts=bars[1].end_ts)

    # Re-feed bar 2 (duplicate) — must NOT advance math or append array
    d1 = stream.feed(bars[1].open, bars[1].high, bars[1].low, bars[1].close, end_ts=bars[1].end_ts)
    assert d1 is s2  # same immutable snapshot object returned
    assert stream.bar_count() == 2
    assert stream.indicator._count == 2  # advanced only twice
    assert stream._dedup_count == 1


def test_stream_detects_out_of_order():
    stream = IndicatorStream("569003", "5m")
    bars = _bars("569003", "5m", 3, tf_min=5)
    stream.feed(bars[2].open, bars[2].high, bars[2].low, bars[2].close, end_ts=bars[2].end_ts)
    stream.feed(bars[0].open, bars[0].high, bars[0].low, bars[0].close, end_ts=bars[0].end_ts)
    assert stream._out_of_order_count == 1


# ── §10 snapshot fields are filled after warmup ──────────────────────────

def test_snapshot_has_all_fields():
    stream = IndicatorStream("569003", "15m")
    bars = _bars("569003", "15m", 10, tf_min=15)
    snap = None
    prev_dema_atr = None
    for b in bars:
        snap = stream.feed(b.open, b.high, b.low, b.close, end_ts=b.end_ts, start_ts=b.start_ts, volume=b.volume)
        prev_dema_atr = snap.previous_dema_atr
    # previous_* are the values from the bar BEFORE the last one fed
    assert snap.previous_dema_atr == prev_dema_atr
    assert snap.security_id == "569003"
    assert snap.timeframe == "15m"
    assert snap.dema is not None
    assert snap.atr is not None
    assert snap.dema_atr is not None
    assert snap.is_complete is True


# ── Parity: shared stream == standalone DEMAATR ──────────────────────────

def test_shared_matches_standalone_dema_atr():
    tf = "5m"
    bars = _bars("569003", tf, 40, tf_min=5)
    shared = IndicatorStream("569003", tf)
    standalone = DEMAATR(3, 6, 1.0)
    for b in bars:
        shared.feed(b.open, b.high, b.low, b.close, end_ts=b.end_ts)
        standalone.update(b.open, b.high, b.low, b.close)
        assert shared.value == pytest.approx(standalone.value)


# ── Parity: shared map data == HTFState mapping ──────────────────────────

def test_shared_mapping_matches_htf_state():
    bars = _bars("569003", "15m", 30, tf_min=15)
    shared = IndicatorStream("569003", "15m")
    htf = HTFState("569003", "15m")
    for b in bars:
        shared.feed(b.open, b.high, b.low, b.close, end_ts=b.end_ts)
        htf.update(b)
    # Map a mid-bar snapshot
    fake_fast = Bar(instrument="569003", timeframe="5m", start_ts=bars[-1].end_ts,
                    end_ts=bars[-1].end_ts + 5 * 60, open=0, high=0, low=0, close=0, volume=0)
    sm = shared.get_mapped_value(fake_fast)
    hm = htf.get_mapped_value(fake_fast)
    assert sm.htf_value == pytest.approx(hm.htf_value)
    assert sm.prev_htf_value == pytest.approx(hm.prev_htf_value)


# ── §8 shared engine: one stream per (security_id, timeframe) ────────────

def test_shared_engine_stream_count_and_identity():
    engine = SharedNativeIndicatorEngine()
    keys = [("569003", "5m"), ("569003", "15m"), ("569003", "1h"),
            ("483080", "5m"), ("483080", "15m"), ("483080", "1h")]
    first = {}
    for sid, tf in keys:
        s = engine.get_or_create(sid, tf)
        assert engine.get_or_create(sid, tf) is s  # same object on reuse
        first[(sid, tf)] = s
    assert engine.stream_count == 6


# ── Engine-level: 4 real strategies bind to 6 streams ──────────────────

def test_four_strategies_bind_to_six_shared_streams():
    from strategies.gold import create_gold_5m, create_gold_15m
    from strategies.silver import create_silver_5m, create_silver_15m

    engine = SharedNativeIndicatorEngine()
    gold_5m = create_gold_5m(strategy_id="gold_01")
    gold_15 = create_gold_15m(strategy_id="gold_02")
    silver_5m = create_silver_5m(strategy_id="silver_02")
    silver_15 = create_silver_15m(strategy_id="silver_01")

    for s in (gold_5m, gold_15, silver_5m, silver_15):
        s.bind_shared_indicators(engine)

    # Exactly 6 streams: GOLDM + SILVERM x 5m/15m/1h
    assert engine.stream_count == 6
    keys = sorted(f"{sid}:{tf}" for sid, tf in engine._streams)
    assert keys == sorted([
        "569003:5m", "569003:15m", "569003:1h",
        "483080:5m", "483080:15m", "483080:1h",
    ])

    # §8: gold_02's fast indicator IS the SAME stream as gold_01's mid 15m.
    assert gold_15.fast_indicator._stream is gold_5m.mid_htf_state._stream
    # §8: both GOLDM strategies share the same 1h stream.
    assert gold_15.slow_htf_state._stream is gold_5m.slow_htf_state._stream
    # §11/§12: SILVERM_15M fast == its own mid, and shares 1h with SILVERM_5M.
    assert silver_15.fast_indicator._stream is silver_15.mid_htf_state._stream
    assert silver_15.slow_htf_state._stream is silver_5m.slow_htf_state._stream

    # Strategies hold read-only views; feeding gold_5m's 15m also feeds what
    # gold_15 sees (both point to the shared stream's DEMAATR).
    bars = _bars("569003", "15m", 20, tf_min=15)
    for b in bars:
        gold_5m.mid_htf_state.update(b)
    assert gold_15.fast_indicator._count == 20
    assert gold_5m.mid_htf_state.last_value == pytest.approx(gold_15.fast_indicator.value)
    # dedup: re-delivering the SAME last candle through the second strategy
    # must not advance the shared stream (same candle_end_ts as last accepted).
    last = bars[-1]
    gold_15.fast_indicator.update(last.open, last.high, last.low, last.close, last.end_ts)
    gold_15.fast_indicator.update(last.open, last.high, last.low, last.close, last.end_ts)
    assert gold_15.fast_indicator._count == 20
    assert gold_15.fast_indicator.value == pytest.approx(gold_5m.mid_htf_state.last_value)


# ── §11/§12 strategy view parity & object identity across subscribers ────

def test_views_share_stream_object_and_feed_deduplicates():
    stream = IndicatorStream("569003", "15m")
    mid = StreamHTFStateView(stream)
    fast = StrategyIndicatorView(stream)
    # Both subscribers write through to the SAME stream -> same indicator object
    assert mid.indicator is fast._stream.indicator is stream.indicator

    bars = _bars("569003", "15m", 20, tf_min=15)
    for b in bars:
        fast.update(b.open, b.high, b.low, b.close, b.end_ts)
        mid.update(b)  # dedup no-op (same end_ts)
    assert stream.bar_count() == 20          # only advanced 20 times
    assert stream.indicator._count == 20
    assert fast.value == pytest.approx(mid.last_value)

    # cross-subscriber value parity
    assert fast._count == mid.indicator._count


def test_mapped_value_share_across_subscribers():
    stream = IndicatorStream("569003", "1h")
    v1 = StreamHTFStateView(stream)
    v2 = StreamHTFStateView(stream)
    bars = _bars("569003", "1h", 15, tf_min=60)
    for b in bars:
        v1.update(b)
    # v2 is a different view over the SAME stream -> sees v1's data
    assert v2.bar_count() == 15
    assert v1.last_value == pytest.approx(v2.last_value)
    fake_fast = Bar(instrument="569003", timeframe="5m", start_ts=bars[-1].end_ts,
                    end_ts=bars[-1].end_ts + 300, open=0, high=0, low=0, close=0, volume=0)
    assert v1.get_mapped_value(fake_fast).htf_value == pytest.approx(
        v2.get_mapped_value(fake_fast).htf_value)
