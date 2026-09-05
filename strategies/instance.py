"""StrategyInstance — one completely independent strategy with own indicators, state, and HTF tracking.

Each of the four strategies (GOLDM_5M, GOLDM_15M, SILVERM_5M, SILVERM_15M)
gets its own StrategyInstance. No shared mutable state between instances.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from events.types import CandleEvent
from core.timeframe_engine import Bar
from htf.confirmation import HTFMappedValue
from indicators.dema_atr import DEMAATR
from strategies.htf_state import HTFState
from strategies.types import (
    SignalType, StrategyState, Signal, PendingEntry,
)
from indicators.shared import (
    IndicatorStream, StrategyIndicatorView, StreamHTFStateView,
)

log = logging.getLogger(__name__)

# Parameters — must match backtest exactly
DEMA_PERIOD = 3
ATR_PERIOD = 6
ATR_FACTOR = 1.0


class StrategyInstance:
    """One completely independent strategy instance.

    Owns:
        - strategy_id, instrument, security_id
        - fast_indicator (DEMAATR for primary timeframe)
        - mid_indicator (DEMAATR for 15m confirmation)
        - slow_indicator (DEMAATR for 1H signal line)
        - mid_htf_state (HTFState for 15m)
        - slow_htf_state (HTFState for 1H)
        - all strategy state (FLAT/LONG/SHORT, stop, pending, etc.)
        - previous values (prev_close, prev_htf, prev_mid)

    Does NOT own:
        - CandleFetcher
        - EventBus
        - ExecutionEngine
        - PositionManager
        - TradeLifecycleManager
    """

    def __init__(
        self,
        strategy_id: str,
        instrument: str,
        security_id: str,
        fast_timeframe: str,
        mid_timeframe: str = "15m",
        htf_timeframe: str = "1h",
        quantity: int = 1,
        pending_timeout_bars: int = 50,
        capital: float = 300_000.0,
        multiplier: float = 10.0,
    ):
        self.strategy_id = strategy_id
        self.instrument = instrument
        self.security_id = security_id
        self.fast_timeframe = fast_timeframe
        self.mid_timeframe = mid_timeframe
        self.htf_timeframe = htf_timeframe
        self.quantity = quantity
        self.pending_timeout_bars = pending_timeout_bars
        self.capital = capital
        self.multiplier = multiplier

        # ── Subscriptions ──
        self.subscriptions = list(dict.fromkeys([
            f"{instrument}:{fast_timeframe}",
            f"{instrument}:{mid_timeframe}",
            f"{instrument}:{htf_timeframe}",
        ]))

        # ── Own indicators (per-strategy, NOT shared) ──
        self.fast_indicator = DEMAATR(DEMA_PERIOD, ATR_PERIOD, ATR_FACTOR)
        self.mid_indicator = DEMAATR(DEMA_PERIOD, ATR_PERIOD, ATR_FACTOR)
        self.slow_indicator = DEMAATR(DEMA_PERIOD, ATR_PERIOD, ATR_FACTOR)

        # ── Own HTF state (per-strategy, NOT shared) ──
        self.mid_htf_state = HTFState(instrument, mid_timeframe, DEMA_PERIOD, ATR_PERIOD, ATR_FACTOR)
        self.slow_htf_state = HTFState(instrument, htf_timeframe, DEMA_PERIOD, ATR_PERIOD, ATR_FACTOR)

        # ── Strategy state machine ──
        self.state = StrategyState.FLAT
        self.position_side: Optional[str] = None
        self.stop_price: Optional[float] = None
        self.pending_entry: Optional[PendingEntry] = None
        self.just_entered: bool = False
        self.last_exit_reason: Optional[str] = None

        # ── Deferred reversal exit ──
        self.pending_exit_at_open: bool = False
        self.pending_exit_reason: Optional[str] = None
        self.pending_exit_bar_start: Optional[float] = None

        # ── Same-bar stop ──
        self.same_bar_stop: Optional[float] = None

        # ── Indicator tracking (previous values) ──
        self._prev_fast_close: Optional[float] = None
        self._prev_htf_value: Optional[float] = None
        self._prev_mid_value: Optional[float] = None
        self._prev_fast_high: Optional[float] = None
        self._prev_fast_low: Optional[float] = None
        self._bars_processed: int = 0

        # ── Current trade reference ──
        self.current_trade_id: Optional[str] = None

        # ── Shared indicator binding (set by bind_shared_indicators) ──
        self._shared_indicators_bound: bool = False
        self._shared_streams: dict = {}

        # ── Audit trail ──
        self._signals: list[Signal] = []
        self._events: list[dict] = []

        log.info("[StrategyInstance] %s initialized: %s %s/%s/%s",
                 strategy_id, instrument, fast_timeframe, mid_timeframe, htf_timeframe)

    # ═══════════════════════════════════════════════════════════════════════
    # SHARED INDICATOR BINDING (mission §7–§12)
    # ═══════════════════════════════════════════════════════════════════════

    def bind_shared_indicators(self, engine) -> None:
        """Bind this strategy's indicator slots to shared IndicatorStreams.

        Minimal-bind: replaces the strategy's self-owned DEMAATR / HTFState
        objects with thin views over the SharedNativeIndicatorEngine's streams,
        keyed by (security_id, timeframe). The strategy evaluation hot path
        (on_bar) is NOT changed — it keeps calling .update / .get_mapped_value
        / .value exactly as before, only the underlying storage is now shared
        so each (security_id, timeframe) DEMA-ATR is calculated once.

        The previous fast/mid/slow splitting on a single stream collapses to
        one stream per timeframe: fast == mid for anything whose fast timeframe
        equals a shared 15m stream, and the slow 1H stream serves the 1H line.
        """
        mid = engine.get_or_create(self.security_id, self.mid_timeframe)
        slow = engine.get_or_create(self.security_id, self.htf_timeframe)

        # fast indicator stream: the strategy's primary timeframe. For a 5m
        # strategy this is its own 5m stream; for a 15m strategy it is the same
        # 15m stream it shares with the 5m strategy's mid timeframe.
        fast = engine.get_or_create(self.security_id, self.fast_timeframe)

        self.fast_indicator = StrategyIndicatorView(fast)
        self.mid_indicator = StrategyIndicatorView(mid)
        self.slow_indicator = StrategyIndicatorView(slow)
        self.mid_htf_state = StreamHTFStateView(mid)
        self.slow_htf_state = StreamHTFStateView(slow)

        self._shared_streams = {
            "fast": fast,
            "mid": mid,
            "slow": slow,
        }
        self._shared_indicators_bound = True

    # ═══════════════════════════════════════════════════════════════════════
    # EVENT HANDLERS — called by EventBus routing
    # ═══════════════════════════════════════════════════════════════════════

    def on_candle(self, event: CandleEvent) -> Optional[Signal]:
        """Route incoming candle to correct handler based on timeframe.

        This is the primary entry point for candle events.
        """
        if event.timeframe == self.fast_timeframe:
            return self._on_fast_candle(event)
        elif event.timeframe == self.mid_timeframe:
            self._on_mid_htf_candle(event)
            return None
        elif event.timeframe == self.htf_timeframe:
            self._on_slow_htf_candle(event)
            return None
        return None

    def _on_fast_candle(self, event: CandleEvent) -> Optional[Signal]:
        """Process fast candle: update indicator, map HTF, check signals.

        This is the hot path — must be minimal.
        """
        bar = Bar(
            instrument=event.instrument,
            timeframe=event.timeframe,
            start_ts=event.start_ts,
            end_ts=event.end_ts,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=int(event.volume),
        )

        # 1. Update own fast indicator (idempotent by candle_end_ts)
        self.fast_indicator.update(bar.open, bar.high, bar.low, bar.close, bar.end_ts)
        fast_dema_atr = self.fast_indicator.value

        # 1b. For strategies where fast == mid, the fast bar IS the mid bar —
        # keep the mid HTF state live so 15m confirmation never goes stale.
        if self.fast_timeframe == self.mid_timeframe:
            self.mid_htf_state.update(bar)

        # 2. Map HTF values from OWN state (not global engine)
        slow_mapped = self.slow_htf_state.get_mapped_value(bar)
        mid_mapped = self.mid_htf_state.get_mapped_value(bar)

        # 3. Run strategy evaluation
        return self.on_bar(bar, slow_mapped, fast_dema_atr, mid_mapped)

    def _on_mid_htf_candle(self, event: CandleEvent) -> None:
        """Update own mid HTF state (15m)."""
        bar = Bar(
            instrument=event.instrument,
            timeframe=event.timeframe,
            start_ts=event.start_ts,
            end_ts=event.end_ts,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=int(event.volume),
        )
        self.mid_htf_state.update(bar)

    def _on_slow_htf_candle(self, event: CandleEvent) -> None:
        """Update own slow HTF state (1H)."""
        bar = Bar(
            instrument=event.instrument,
            timeframe=event.timeframe,
            start_ts=event.start_ts,
            end_ts=event.end_ts,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=int(event.volume),
        )
        self.slow_htf_state.update(bar)

    # ═══════════════════════════════════════════════════════════════════════
    # STRATEGY EVALUATION — identical to BaseDEMAStrategy.on_bar()
    # ═══════════════════════════════════════════════════════════════════════

    def on_bar(
        self,
        bar: Bar,
        htf_mapped: HTFMappedValue,
        fast_dema_atr: Optional[float],
        mid_mapped: Optional[HTFMappedValue] = None,
    ) -> Optional[Signal]:
        """Process a fast timeframe bar. Identical logic to BaseDEMAStrategy.on_bar().

        This is the core strategy evaluation. Called ONLY on fast TF close.
        """
        if self.enabled is False:
            return None

        self._bars_processed += 1
        self.just_entered = False

        close = bar.close
        high = bar.high
        low = bar.low
        prev_close = self._prev_fast_close or close
        prev_high = self._prev_fast_high or high
        prev_low = self._prev_fast_low or low
        htf_val = htf_mapped.htf_value
        prev_htf_val = self._prev_htf_value
        mid_val = mid_mapped.htf_value if mid_mapped else None
        prev_mid_val = self._prev_mid_value

        # Store for next bar
        self._prev_fast_close = close
        self._prev_htf_value = htf_val
        self._prev_mid_value = mid_val
        self._prev_fast_high = high
        self._prev_fast_low = low

        # Skip if no HTF value available
        if htf_val is None or prev_htf_val is None:
            return None

        signal = None

        # 1. Execute pending entry if triggered
        if self.pending_entry is not None:
            if self.pending_entry.bars_pending >= self.pending_timeout_bars:
                self.pending_entry = None
                self.state = StrategyState.FLAT
                self.position_side = None
                return None
            self.pending_entry.bars_pending += 1
            signal = self._check_pending_entry(bar)
            if signal is not None:
                self.just_entered = False
                if self.stop_price is not None:
                    if (self.position_side == "LONG" and bar.low <= self.stop_price) or (
                            self.position_side == "SHORT" and bar.high >= self.stop_price):
                        self.same_bar_stop = bar.close
                        self.last_exit_reason = "stop_loss_hit"
                return signal

        # 2. Check stop loss
        if (self.position_side is not None
                and self.stop_price is not None
                and not self.just_entered):
            stop_signal = self._check_stop_loss(bar)
            if stop_signal is not None:
                self.just_entered = False
                # Re-arm signal detection on the SAME bar so a clean flip/flat
                # after a stop-out can immediately re-enter (matches backtest).
                self._detect_signal(
                    close, prev_close, htf_val, prev_htf_val, high, low, bar.start_ts,
                    mid_val, prev_mid_val, prev_high, prev_low, fast_dema_atr,
                )
                return stop_signal

        # 3. Detect new signals
        if self.state in (StrategyState.FLAT,
                          StrategyState.PENDING_LONG, StrategyState.PENDING_SHORT):
            signal = self._detect_signal(
                close, prev_close, htf_val, prev_htf_val, high, low, bar.start_ts,
                mid_val, prev_mid_val, prev_high, prev_low, fast_dema_atr,
            )
        elif self.position_side == "SHORT" and self._check_long_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            signal = self._create_reversal_signal("LONG", close, high, low, bar.start_ts, prev_high, prev_low,
                                                  htf_val=htf_val, mid_val=mid_val, fast_dema_atr=fast_dema_atr)
        elif self.position_side == "LONG" and self._check_short_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            signal = self._create_reversal_signal("SHORT", close, high, low, bar.start_ts, prev_high, prev_low,
                                                  htf_val=htf_val, mid_val=mid_val, fast_dema_atr=fast_dema_atr)

        self.just_entered = False
        return signal

    # ═══════════════════════════════════════════════════════════════════════
    # CROSSOVER DETECTION — identical to BaseDEMAStrategy
    # ═══════════════════════════════════════════════════════════════════════

    def _check_long_cross(
        self, close: float, prev_close: float,
        htf_val: float, prev_htf_val: float,
        mid_val: Optional[float] = None, prev_mid_val: Optional[float] = None,
    ) -> bool:
        """Long crossover: close crosses ABOVE 1H line AND 15m below 1H."""
        cross = close > htf_val and prev_close <= htf_val
        if not cross:
            return False
        if mid_val is not None and htf_val is not None:
            if mid_val >= htf_val:
                return False
        return True

    def _check_short_cross(
        self, close: float, prev_close: float,
        htf_val: float, prev_htf_val: float,
        mid_val: Optional[float] = None, prev_mid_val: Optional[float] = None,
    ) -> bool:
        """Short crossover: close crosses BELOW 1H line AND 15m above 1H."""
        cross = close < htf_val and prev_close >= htf_val
        if not cross:
            return False
        if mid_val is not None and htf_val is not None:
            if mid_val <= htf_val:
                return False
        return True

    # ═══════════════════════════════════════════════════════════════════════
    # SIGNAL CREATION — identical to BaseDEMAStrategy
    # ═══════════════════════════════════════════════════════════════════════

    def _detect_signal(
        self, close, prev_close, htf_val, prev_htf_val, high, low, timestamp,
        mid_val=None, prev_mid_val=None, prev_high=None, prev_low=None,
        fast_dema_atr=None,
    ) -> Optional[Signal]:
        """Detect new entry signal. Arms a pending breakout entry."""
        if self._check_long_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            return self._create_pending_signal("LONG", close, high, low, timestamp, prev_high, prev_low,
                                               htf_val=htf_val, mid_val=mid_val, fast_dema_atr=fast_dema_atr)
        elif self._check_short_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            return self._create_pending_signal("SHORT", close, high, low, timestamp, prev_high, prev_low,
                                               htf_val=htf_val, mid_val=mid_val, fast_dema_atr=fast_dema_atr)
        return None

    def _create_pending_signal(
        self, side, close, high, low, timestamp,
        prev_high=None, prev_low=None,
        htf_val=None, mid_val=None, fast_dema_atr=None,
    ) -> Signal:
        """Create a pending entry signal (breakout trigger)."""
        if side == "LONG":
            trigger = high
            sl_low = prev_low if prev_low is not None else low
            stop = min(low, sl_low)
        else:
            trigger = low
            sl_high = prev_high if prev_high is not None else high
            stop = max(high, sl_high)

        signal = Signal(
            signal_type=SignalType.LONG if side == "LONG" else SignalType.SHORT,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=timestamp,
            trigger_price=trigger,
            stop_price=stop,
            quantity=self.quantity,
        )
        signal.metadata = {
            # A signal is durable evidence, but it is not a trade.  The engine
            # persists this state and waits for the breakout before it creates
            # a lifecycle/trade id.
            "pending": True,
            "triggered": False,
            "entry_price": close,
            "htf_value": htf_val,
            "mid_value": mid_val,
            "fast_dema_atr": fast_dema_atr,
            "trigger_level": trigger,
        }

        self.pending_entry = PendingEntry(
            signal=signal,
            trigger_price=trigger,
            side=side,
        )
        self.state = StrategyState.PENDING_LONG if side == "LONG" else StrategyState.PENDING_SHORT
        self._signals.append(signal)
        return signal

    def _create_reversal_signal(
        self, side, close, high, low, timestamp,
        prev_high=None, prev_low=None,
        htf_val=None, mid_val=None, fast_dema_atr=None,
    ) -> Optional[Signal]:
        """Arm a reversal: exit at the NEXT BAR OPEN, then re-enter the
        opposite side via the breakout trigger.

        Mirrors the reference backtest flow: the crossing bar only arms a
        pending breakout for the opposite side and schedules the held
        position's exit at the next fast bar's open. No order is placed now
        (returns None), matching BaseDEMAStrategy.on_bar().
        """
        if side == "LONG":
            trigger = high
            sl_low = prev_low if prev_low is not None else low
            stop = min(low, sl_low)
        else:
            trigger = low
            sl_high = prev_high if prev_high is not None else high
            stop = max(high, sl_high)

        signal = Signal(
            signal_type=SignalType.LONG if side == "LONG" else SignalType.SHORT,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=timestamp,
            trigger_price=trigger,
            stop_price=stop,
            quantity=self.quantity,
        )
        signal.metadata = {
            "entry_price": close,
            "htf_value": htf_val,
            "mid_value": mid_val,
            "fast_dema_atr": fast_dema_atr,
            "trigger_level": trigger,
            "is_reversal": True,
        }

        # Arm the opposite-side breakout entry (fired by _check_pending_entry
        # on the next bars).
        self.pending_entry = PendingEntry(
            signal=signal,
            trigger_price=trigger,
            side=side,
        )
        self.state = StrategyState.PENDING_LONG if side == "LONG" else StrategyState.PENDING_SHORT

        # Schedule the held position's exit at the next fast bar's OPEN.
        self.pending_exit_at_open = True
        self.pending_exit_reason = f"{side.lower()}_reversal"
        self.pending_exit_bar_start = timestamp

        # Mark the exit pending (position/stop stay live until the engine
        # consumes the deferred exit at the next open).
        self._close_position(f"{side.lower()}_reversal")
        self._signals.append(signal)
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # PENDING ENTRY + STOP LOSS — identical to BaseDEMAStrategy
    # ═══════════════════════════════════════════════════════════════════════

    def _check_pending_entry(self, bar: Bar) -> Optional[Signal]:
        """Check if pending breakout entry is triggered by this bar."""
        pen = self.pending_entry
        if pen is None:
            return None

        triggered = False
        if pen.side == "LONG" and bar.high > pen.trigger_price:
            triggered = True
        elif pen.side == "SHORT" and bar.low < pen.trigger_price:
            triggered = True

        if not triggered:
            return None

        # Fill at trigger price
        fill_px = pen.trigger_price
        self.position_side = pen.side
        self.stop_price = pen.signal.stop_price
        self.just_entered = True
        self.state = StrategyState.LONG_POSITION if pen.side == "LONG" else StrategyState.SHORT_POSITION
        self.pending_entry = None

        if pen.signal.metadata is None:
            pen.signal.metadata = {}
        pen.signal.metadata["pending"] = False
        pen.signal.metadata["triggered"] = True

        return pen.signal

    def _check_stop_loss(self, bar: Bar) -> Optional[Signal]:
        """Check if stop loss is hit."""
        if self.position_side is None or self.stop_price is None:
            return None

        hit = False
        if self.position_side == "LONG" and bar.low <= self.stop_price:
            hit = True
        elif self.position_side == "SHORT" and bar.high >= self.stop_price:
            hit = True

        if not hit:
            return None

        exit_signal = Signal(
            signal_type=SignalType.SHORT if self.position_side == "LONG" else SignalType.LONG,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=bar.start_ts,
            trigger_price=bar.close,
            stop_price=self.stop_price,
            quantity=self.quantity,
        )
        exit_signal.metadata = {
            "exit": True,
            "exit_reason": "stop_loss_hit",
            "exit_price": bar.close,
            "position_side": self.position_side,
        }

        self._close_position("stop_loss_hit")
        self._signals.append(exit_signal)
        return exit_signal

    def _close_position(self, reason: str) -> None:
        """Mark exit as pending; engine clears state after fill."""
        self.last_exit_reason = reason
        self.state = StrategyState.EXIT_ORDER_SUBMITTED

    def _consume_same_bar_stop(self, bar: Bar) -> Optional[Signal]:
        """Handle same-bar stop: entry AND stop-loss on one candle."""
        if self.same_bar_stop is None:
            return None

        exit_signal = Signal(
            signal_type=SignalType.SHORT if self.position_side == "LONG" else SignalType.LONG,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=(bar.start_ts or 0.0) + 0.25,
            trigger_price=self.same_bar_stop,
            stop_price=self.stop_price,
            quantity=self.quantity,
        )
        exit_signal.metadata = {
            "exit": True,
            "exit_reason": "stop_loss_hit",
            "exit_price": self.same_bar_stop,
            "same_bar": True,
        }

        self._close_position("stop_loss_hit")
        self.same_bar_stop = None
        self._signals.append(exit_signal)
        return exit_signal

    # ═══════════════════════════════════════════════════════════════════════
    # TICK HANDLER — for live LTP processing
    # ═══════════════════════════════════════════════════════════════════════

    def on_tick(self, ltp: float, timestamp: float) -> Optional[Signal]:
        """Process LTP tick. Only checks pending triggers and stop loss.

        Must NOT recalculate indicators or run full strategy logic.
        """
        if not self.enabled:
            return None

        if self.just_entered:
            return None

        if ltp <= 0:
            return None

        if self.pending_exit_at_open:
            return None

        # Check stop loss on tick
        if (self.position_side is not None
                and self.stop_price is not None
                and not self.just_entered):
            if self.position_side == "LONG" and ltp <= self.stop_price:
                return self._tick_stop_loss(ltp, timestamp)
            elif self.position_side == "SHORT" and ltp >= self.stop_price:
                return self._tick_stop_loss(ltp, timestamp)

        # Check pending entry trigger on tick
        if self.pending_entry is not None:
            pen = self.pending_entry
            if pen.side == "LONG" and ltp >= pen.trigger_price:
                return self._tick_entry_trigger(pen, ltp, timestamp)
            elif pen.side == "SHORT" and ltp <= pen.trigger_price:
                return self._tick_entry_trigger(pen, ltp, timestamp)

        return None

    def _tick_stop_loss(self, ltp: float, timestamp: float) -> Optional[Signal]:
        """Execute stop loss from tick."""
        exit_signal = Signal(
            signal_type=SignalType.SHORT if self.position_side == "LONG" else SignalType.LONG,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=timestamp,
            trigger_price=ltp,
            stop_price=self.stop_price,
            quantity=self.quantity,
        )
        exit_signal.metadata = {
            "exit": True,
            "exit_reason": "stop_loss_hit",
            "exit_price": ltp,
            "source": "tick",
        }
        self._close_position("stop_loss_hit")
        self._signals.append(exit_signal)
        return exit_signal

    def _tick_entry_trigger(self, pen: PendingEntry, ltp: float, timestamp: float) -> Optional[Signal]:
        """Execute pending entry from tick."""
        fill_px = pen.trigger_price
        self.position_side = pen.side
        self.stop_price = pen.signal.stop_price
        self.just_entered = True
        self.state = StrategyState.LONG_POSITION if pen.side == "LONG" else StrategyState.SHORT_POSITION
        self.pending_entry = None
        if pen.signal.metadata is None:
            pen.signal.metadata = {}
        pen.signal.metadata["pending"] = False
        pen.signal.metadata["triggered"] = True
        return pen.signal

    # ═══════════════════════════════════════════════════════════════════════
    # WARMUP — per-strategy indicator warmup
    # ═══════════════════════════════════════════════════════════════════════

    def warmup_indicator(self, bar: Bar) -> None:
        """Warm up fast indicator from a historical bar."""
        self.fast_indicator.update(bar.open, bar.high, bar.low, bar.close, bar.end_ts)

    def warmup_htf(self, bar: Bar) -> None:
        """Warm up HTF state from a historical bar."""
        tf_min = self._tf_to_minutes(bar.timeframe)
        if tf_min == self._tf_to_minutes(self.mid_timeframe):
            self.mid_htf_state.update(bar)
        elif tf_min == self._tf_to_minutes(self.htf_timeframe):
            self.slow_htf_state.update(bar)

    def warmup_indicator_htf(self, bar: Bar) -> None:
        """Warm up HTF indicator (not state) from historical bar."""
        tf_min = self._tf_to_minutes(bar.timeframe)
        if tf_min == self._tf_to_minutes(self.mid_timeframe):
            self.mid_indicator.update(bar.open, bar.high, bar.low, bar.close, bar.end_ts)
        elif tf_min == self._tf_to_minutes(self.htf_timeframe):
            self.slow_indicator.update(bar.open, bar.high, bar.low, bar.close, bar.end_ts)

    def reset(self) -> None:
        """Reset all strategy state. Used before warmup."""
        self.state = StrategyState.FLAT
        self.position_side = None
        self.stop_price = None
        self.pending_entry = None
        self.pending_exit_at_open = False
        self.same_bar_stop = None
        self._prev_fast_close = None
        self._prev_htf_value = None
        self._prev_mid_value = None
        self._prev_fast_high = None
        self._prev_fast_low = None
        self.current_trade_id = None
        # §9: bound strategies MUST NOT mutate shared indicator state — the
        # shared streams belong to SharedNativeIndicatorEngine and are used by
        # every strategy subscribed to the same (security_id, timeframe).
        if not getattr(self, "_shared_indicators_bound", False):
            self.fast_indicator.reset()
            self.mid_indicator.reset()
            self.slow_indicator.reset()
            self.mid_htf_state.reset()
            self.slow_htf_state.reset()

    @staticmethod
    def _tf_to_minutes(tf: str) -> int:
        if not tf:
            return 5
        unit = tf[-1].lower()
        try:
            n = int(tf[:-1])
        except ValueError:
            return 5
        if unit == "h":
            return n * 60
        if unit == "m":
            return n
        return 5

    # ═══════════════════════════════════════════════════════════════════════
    # PROPERTIES + SNAPSHOTS
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def enabled(self) -> bool:
        return True

    @property
    def is_flat(self) -> bool:
        return self.position_side is None and self.pending_entry is None

    @property
    def has_position(self) -> bool:
        return self.position_side is not None

    @property
    def has_pending(self) -> bool:
        return self.pending_entry is not None

    def snapshot(self) -> dict:
        """Return diagnostic snapshot."""
        return {
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "fast_timeframe": self.fast_timeframe,
            "state": self.state.value,
            "position_side": self.position_side,
            "stop_price": self.stop_price,
            "has_pending": self.has_pending,
            "bars_processed": self._bars_processed,
            "signals_generated": len(self._signals),
            "fast_indicator_count": self.fast_indicator._count,
            "mid_htf_bars": self.mid_htf_state.bar_count(),
            "slow_htf_bars": self.slow_htf_state.bar_count(),
            "slow_htf_value": self.slow_htf_state.last_value,
            "mid_htf_value": self.mid_htf_state.last_value,
            "prev_fast_close": self._prev_fast_close,
            "prev_htf_value": self._prev_htf_value,
            "prev_mid_value": self._prev_mid_value,
            "last_exit_reason": self.last_exit_reason,
            "just_entered": self.just_entered,
            "pending_exit_at_open": self.pending_exit_at_open,
            "pending_exit_reason": self.pending_exit_reason,
            "pending_exit_bar_start": self.pending_exit_bar_start,
            "pending_entry": ((self.pending_entry.signal.signal_id,
                               self.pending_entry.trigger_price,
                               self.pending_entry.side,
                               self.pending_entry.bars_pending)
                              if self.pending_entry else None),
            "same_bar_stop": self.same_bar_stop,
            "current_trade_id": self.current_trade_id,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore strategy state from a snapshot dict."""
        state_value = snapshot.get("state", "flat")
        try:
            self.state = StrategyState(state_value)
        except (ValueError, KeyError):
            self.state = StrategyState.FLAT
        self.position_side = snapshot.get("position_side")
        self.stop_price = snapshot.get("stop_price")
        self._bars_processed = snapshot.get("bars_processed", 0)
        self._prev_fast_close = snapshot.get("prev_fast_close")
        self._prev_htf_value = snapshot.get("prev_htf_value")
        self._prev_mid_value = snapshot.get("prev_mid_value")
        self.last_exit_reason = snapshot.get("last_exit_reason")
        self.just_entered = bool(snapshot.get("just_entered", False))
        self.pending_exit_at_open = bool(snapshot.get("pending_exit_at_open", False))
        self.pending_exit_reason = snapshot.get("pending_exit_reason")
        self.pending_exit_bar_start = snapshot.get("pending_exit_bar_start")
        self.same_bar_stop = snapshot.get("same_bar_stop")
        self.current_trade_id = snapshot.get("current_trade_id")

        pending_entry = snapshot.get("pending_entry")
        if pending_entry:
            signal_id, trigger, side, bars = pending_entry
            signal = self._signals[-1] if self._signals else None
            self.pending_entry = PendingEntry(
                signal=signal or Signal(
                    signal_type=SignalType.LONG if side == "LONG" else SignalType.SHORT,
                    instrument=self.instrument, strategy_id=self.strategy_id,
                    timestamp=0.0, trigger_price=trigger,
                    stop_price=self.stop_price or 0.0, quantity=self.quantity,
                ),
                trigger_price=trigger, side=side,
            )
            if self.pending_entry.signal.signal_id != signal_id and signal is not None:
                self.pending_entry.signal.signal_id = signal_id
            self.pending_entry.bars_pending = bars
        else:
            self.pending_entry = None
