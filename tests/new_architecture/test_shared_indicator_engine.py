"""Shared native indicator engine — one stream per unique (sec, tf) (Phase 6)."""
import time

from ._harness import SIDS


def test_exactly_six_streams(engine):
    assert engine.indicator_engine.stream_count == 6
    expected = {"569003:5m", "569003:15m", "569003:1h",
                "483080:5m", "483080:15m", "483080:1h"}
    got = set(engine.indicator_engine.stats().get("stream_keys", []))
    assert got == expected


def test_shared_15m_single_computation(engine):
    """All strategies share the GOLDM 15m stream by (sec,tf) keying."""
    streams = {sid: engine.strategies[sid]._shared_streams for sid in SIDS}
    # gold_01(mid=15m) and gold_02(fast=15m) share the single GOLDM 15m stream
    assert streams["gold_01"]["mid"] is streams["gold_02"]["fast"]
    # the two 15m strategies share the same GOLDM 15m line with gold_01
    assert streams["silver_01"]["mid"] is streams["silver_02"]["mid"]
    # GOLDM 5m stream is distinct from GOLDM 15m stream
    assert streams["gold_01"]["fast"] is not streams["gold_01"]["mid"]
    # both gold strategies share the single GOLDM 1h stream
    assert streams["gold_01"]["slow"] is streams["gold_02"]["slow"]
    # silver's fast/mid are silver's own (SILVERM) streams, not gold's
    assert streams["gold_01"]["mid"] is not streams["silver_01"]["mid"]
    assert streams["gold_01"]["fast"] is not streams["silver_01"]["fast"]


def test_calculation_happens_once_per_stream(engine):
    """Feeding one bar to the shared GOLDM 15m stream updates one object."""
    s = engine.strategies["gold_01"]._shared_streams["mid"]
    before = s.bar_count()
    t0 = time.time()
    s.feed(100.0, 101.0, 99.0, 100.0, end_ts=t0)
    assert s.bar_count() == before + 1
    # both strategies now see the same single indicator value
    assert engine.strategies["gold_01"].mid_indicator.value == \
           engine.strategies["gold_02"].fast_indicator.value

def test_indicator_stream_keys():
    from indicators.shared import SharedNativeIndicatorEngine
    eng = SharedNativeIndicatorEngine(dema_period=3, atr_period=6)
    s1 = eng.get_or_create("569003", "5m")
    s2 = eng.get_or_create("569003", "5m")
    assert s1 is s2, "same (sec,tf) must reuse the same stream"
    s3 = eng.get_or_create("569003", "15m")
    assert s3 is not s1
    s4 = eng.get_or_create("483080", "5m")
    assert s4 is not s1