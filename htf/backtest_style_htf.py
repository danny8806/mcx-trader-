"""Backtest-style HTF engine — EXACT mapping logic as the backtest.

Uses:
- BarAggregator for 1H bar creation (verified 100% match)
- Incremental DEMAATR for DEMA-ATR (verified 100% identical to batch pandas ewm)
- np.searchsorted() for mapping (EXACT same as backtest dema_mtf.py)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from core.timeframe_engine import Bar
from htf.confirmation import HTFMappedValue
from indicators.dema_atr import DEMAATR


class BacktestStyleHTFEngine:
    """HTF engine using EXACT backtest mapping logic.

    Mapping: bisect_right(end_times, fast_bar.end_ts) - 1
    This is mathematically identical to backtest's:
        np.searchsorted(src_avail, target_close, side="right") - 1

    DEMA-ATR: incremental DEMAATR (verified 100% identical to batch pandas ewm).
    """

    def __init__(self):
        self._engines: dict[str, _HTFInstrumentState] = {}

    def register(
        self,
        instrument: str,
        htf_timeframe: str,
        dema_period: int = 3,
        atr_period: int = 6,
        atr_factor: float = 1.0,
        session_open: str = "09:00",
    ) -> None:
        key = f"{instrument}:{htf_timeframe}"
        self._engines[key] = _HTFInstrumentState(
            instrument=instrument,
            htf_timeframe=htf_timeframe,
            indicator=DEMAATR(dema_period, atr_period, atr_factor),
        )

    def on_htf_bar_closed(self, bar: Bar) -> None:
        """Feed a closed 1H/15m bar. Updates indicator + stores for mapping."""
        key = f"{bar.instrument}:{bar.timeframe}"
        state = self._engines.get(key)
        if state is None:
            return

        # Update DEMA-ATR indicator (incremental, verified identical to batch)
        state.indicator.update(bar.open, bar.high, bar.low, bar.close)

        # Store EVERY bar's end time (even when DEMA-ATR is NaN during warmup)
        # This matches backtest's src_avail which includes ALL 1H bars
        value = state.indicator.value
        state.end_times.append(bar.end_ts)
        state.values.append(value)  # None during warmup, float after
        # Track last known values for get_htf_value/get_prev_htf_value
        if value is not None:
            state.prev_value = state.last_value
            state.last_value = value

    def map_to_fast_bar(
        self,
        fast_bar: Bar,
        fast_timeframe: str,
    ) -> HTFMappedValue:
        """Map 1H DEMA-ATR to 5m bar using EXACT backtest searchsorted logic."""
        return self._map_htf_to_fast(fast_bar, "1h")

    def map_mid_to_fast_bar(
        self,
        fast_bar: Bar,
        fast_timeframe: str,
    ) -> HTFMappedValue:
        """Map 15m DEMA-ATR to 5m bar using EXACT backtest searchsorted logic."""
        return self._map_htf_to_fast(fast_bar, "15m")

    def _map_htf_to_fast(
        self,
        fast_bar: Bar,
        htf_timeframe: str,
    ) -> HTFMappedValue:
        """Map any HTF DEMA-ATR to a fast bar using searchsorted.

        EXACT backtest logic (dema_mtf.py:94-98):
            base_min = int(round(pos.min()))   # = 5 for MCX 5m data
            target_close = bar_start + base_min  # bar_start + 5min
            idx = searchsorted(src_avail, target_close, "right") - 1

        Here we reproduce that: compute target_close as bar_start + base_min
        where base_min matches the fast bar spacing (5 minutes for 5m MCX data).
        """
        import bisect

        state = self._engines.get(f"{fast_bar.instrument}:{htf_timeframe}")
        if state is None:
            return HTFMappedValue(None, None, False, None)

        if not state.end_times:
            return HTFMappedValue(None, None, False, None)

        # A higher-timeframe value is usable only after the fast bar itself has
        # closed.  The previous implementation hard-coded a 5-minute offset,
        # causing every configured 15m strategy to map stale HTF values.
        target_close = fast_bar.end_ts

        # EXACT backtest logic: searchsorted(src_avail, target_close, side="right") - 1
        idx = bisect.bisect_right(state.end_times, target_close) - 1

        if idx < 0:
            return HTFMappedValue(None, None, False, None)

        htf_value = state.values[idx]
        prev_htf_value = state.values[idx - 1] if idx > 0 else None

        # Only return confirmed values (non-None = after warmup)
        if htf_value is None:
            return HTFMappedValue(None, None, False, None)

        return HTFMappedValue(
            htf_value=htf_value,
            prev_htf_value=prev_htf_value,
            htf_confirmed=True,
            htf_source_timestamp=state.end_times[idx],
        )

    def get_htf_value(self, instrument: str, timeframe: str) -> Optional[float]:
        state = self._engines.get(f"{instrument}:{timeframe}")
        return state.last_value if state else None

    def get_prev_htf_value(self, instrument: str, timeframe: str) -> Optional[float]:
        state = self._engines.get(f"{instrument}:{timeframe}")
        return state.prev_value if state else None

    def load_batch_htf(
        self,
        instrument: str,
        htf_timeframe: str,
        bars: list[Bar],
    ) -> None:
        """Pre-populate HTF engine with historical bars (startup backfill).

        Feeds a batch of closed bars to the engine, building up DEMA-ATR
        state identically to incremental processing. Call this BEFORE
        switching to incremental mode via on_htf_bar_closed().

        Args:
            instrument: e.g. 'GOLDM', 'SILVERM'
            htf_timeframe: '1h' or '15m'
            bars: list of closed Bar objects in chronological order
        """
        for bar in bars:
            self.on_htf_bar_closed(bar)

    def snapshot(self) -> dict:
        result = {}
        for key, state in self._engines.items():
            ind_snap = state.indicator.snapshot() if state.indicator else {}
            result[key] = {
                'instrument': state.instrument,
                'htf_timeframe': state.htf_timeframe,
                'htf_count': len(state.end_times),
                'last_value': state.last_value,
                'last_confirmed_value': state.last_value,
                'prev_confirmed_value': state.prev_value,
                'source_timestamp': state.end_times[-1] if state.end_times else None,
                'indicator': ind_snap,
                'end_times': state.end_times[-100:] if state.end_times else [],
                'values': state.values[-100:] if state.values else [],
            }
        return result

    def restore(self, data: dict) -> None:
        for key, state_data in data.items():
            if key not in self._engines:
                continue
            state = self._engines[key]
            state.last_value = state_data.get('last_value')
            state.prev_value = state_data.get('prev_confirmed_value')
            state.end_times = state_data.get('end_times', [])
            state.values = state_data.get('values', [])
            if state.indicator and state_data.get('indicator'):
                state.indicator.restore(state_data['indicator'])


class _HTFInstrumentState:
    def __init__(self, instrument, htf_timeframe, indicator):
        self.instrument = instrument
        self.htf_timeframe = htf_timeframe
        self.indicator = indicator
        self.end_times: list[float] = []
        self.values: list[float] = []
        self.last_value: Optional[float] = None
        self.prev_value: Optional[float] = None
