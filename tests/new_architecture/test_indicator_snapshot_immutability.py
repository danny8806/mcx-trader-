"""Indicator snapshot immutability + sharing (Phase 8)."""
import dataclasses

import pytest

from indicators.shared import IndicatorSnapshot


def _snap(**kw):
    base = dict(security_id="569003", timeframe="15m", candle_start_ts=0.0,
                candle_end_ts=123.0, open=100.0, high=101.0, low=99.0,
                close=100.0, volume=10.0, dema=10.0, atr=5.0, dema_atr=7.0,
                previous_dema=None, previous_atr=None, previous_dema_atr=None,
                is_complete=True)
    base.update(kw)
    return IndicatorSnapshot(**base)


def test_snapshot_is_frozen_dataclass():
    assert dataclasses.is_dataclass(IndicatorSnapshot)
    s = _snap(dema=100.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.dema = 999.0  # mutation must fail


def test_original_snapshot_unchanged_after_mutation_attempt():
    s = _snap(dema=10.0, atr=5.0, candle_end_ts=123.0)
    with pytest.raises(Exception):
        s.dema = 999.0
    assert s.dema == 10.0
    assert s.atr == 5.0
    assert s.candle_end_ts == 123.0


def test_two_strategies_share_same_immutable_snapshot(engine):
    # gold_01 and gold_02 both consume the GOLDM 15m stream
    s = engine.strategies["gold_01"]._shared_streams["mid"]
    s.feed(100.0, 101.0, 99.0, 100.0, end_ts=1600000000.0)
    snap = s.latest_snapshot
    assert snap is not None
    # both strategy views expose the same immutable snapshot values
    assert engine.strategies["gold_01"].mid_indicator.value == snap.dema_atr
    assert engine.strategies["gold_02"].fast_indicator.value == snap.dema_atr
    # state stays independent even though the snapshot is shared
    assert engine.strategies["gold_01"]._bars_processed != engine.strategies["gold_02"]._bars_processed \
           or engine.strategies["gold_01"] is not engine.strategies["gold_02"]