"""Base DEMA-ATR strategy framework for Gold/Silver live trading."""
from __future__ import annotations

import time
from typing import Any, Optional

from strategies.types import (
    SignalType, StrategyState, Signal, PendingEntry, StrategyInput,
)
from core.timeframe_engine import Bar
from htf.backtest_style_htf import HTFMappedValue


class BaseDEMAStrategy:
    """Base DEMA-ATR crossover strategy.
    
    Implements:
    - Crossover signal detection
    - Pending breakout entry
    - Stop loss management
    - Reversal logic
    
    Signal logic (from existing system):
    - LONG: close > htf_dema_atr AND previous_close <= previous_htf_dema_atr
    - SHORT: close < htf_dema_atr AND previous_close >= previous_htf_dema_atr
    """

    def __init__(
        self,
        strategy_id: str,
        instrument: str,
        fast_timeframe: str,
        htf_timeframe: str,
        quantity: int = 1,
        long_compare: str = ">",
        short_compare: str = "<",
        pending_timeout_bars: int = 50,
    ):
        self.strategy_id = strategy_id
        self.instrument = instrument
        self.fast_timeframe = fast_timeframe
        self.htf_timeframe = htf_timeframe
        self.quantity = quantity
        self.long_compare = long_compare
        self.short_compare = short_compare
        self.pending_timeout_bars = pending_timeout_bars

        # State machine
        self.state = StrategyState.FLAT
        self.position_side: Optional[str] = None
        self.stop_price: Optional[float] = None
        self.pending_entry: Optional[PendingEntry] = None
        self.just_entered: bool = False

        # Indicator tracking
        self._prev_fast_close: Optional[float] = None
        self._prev_htf_value: Optional[float] = None
        self._prev_mid_value: Optional[float] = None
        self._prev_fast_high: Optional[float] = None
        self._prev_fast_low: Optional[float] = None
        self._bars_processed: int = 0

        # Audit trail
        self._signals: list[Signal] = []
        self._events: list[dict] = []

    def on_bar(
        self,
        bar: Bar,
        htf_mapped: HTFMappedValue,
        fast_dema_atr: float,
        mid_mapped: Optional[HTFMappedValue] = None,
    ) -> Optional[Signal]:
        """Process a closed fast timeframe bar.
        
        Args:
            bar: Closed fast timeframe bar
            htf_mapped: Mapped HTF DEMA-ATR value (1H signal line)
            fast_dema_atr: Current fast DEMA-ATR value
            mid_mapped: Mapped mid TF DEMA-ATR value (15m confirmation line)
            
        Returns:
            Signal if trade decision made, None otherwise
        """
        self._bars_processed += 1

        # Clear just_entered flag from previous tick-triggered entry
        self.just_entered = False

        # Extract values
        close = bar.close
        high = bar.high
        low = bar.low
        prev_close = self._prev_fast_close or close
        prev_high = self._prev_fast_high or high
        prev_low = self._prev_fast_low or low
        htf_val = htf_mapped.htf_value
        prev_htf_val = self._prev_htf_value
        # 15m confirmation line
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

        # 1. Execute pending entry if triggered (exclusive - return immediately)
        if self.pending_entry is not None:
            # Check timeout: expire pending entries that haven't triggered
            if self.pending_entry.bars_pending >= self.pending_timeout_bars:
                self._emit("PENDING_ENTRY_EXPIRED",
                           side=self.pending_entry.side,
                           bars_pending=self.pending_entry.bars_pending)
                self.pending_entry = None
                self.state = StrategyState.FLAT
                self.position_side = None
                return None
            self.pending_entry.bars_pending += 1
            signal = self._check_pending_entry(bar)
            if signal is not None:
                self.just_entered = False
                return signal

        # 2. Check stop loss (skip if just entered, stop exits don't generate new signals)
        if (self.position_side is not None
                and self.stop_price is not None
                and not self.just_entered):
            stop_signal = self._check_stop_loss(bar)
            if stop_signal is not None:
                self.just_entered = False
                return stop_signal

        # 3. Detect new signals (only if flat or reversal)
        if self.state == StrategyState.FLAT:
            signal = self._detect_signal(
                close, prev_close, htf_val, prev_htf_val, high, low, bar.start_ts,
                mid_val, prev_mid_val, prev_high, prev_low,
            )
        elif self.position_side == "SHORT" and self._check_long_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            signal = self._create_reversal_signal("LONG", close, high, low, bar.start_ts, prev_high, prev_low)
        elif self.position_side == "LONG" and self._check_short_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            signal = self._create_reversal_signal("SHORT", close, high, low, bar.start_ts, prev_high, prev_low)

        self.just_entered = False
        return signal

    def _check_long_cross(
        self, close: float, prev_close: float,
        htf_val: float, prev_htf_val: float,
        mid_val: Optional[float] = None, prev_mid_val: Optional[float] = None,
    ) -> bool:
        """Check for long crossover signal.

        Buy = close crosses ABOVE 1H line AND 15m line is not strongly above 1H line.
        Tolerance: 15m can be up to 1.5% above 1H (accounts for DEMA drift between TFs
        caused by different data ranges in live vs backtest).
        """
        cross = close > htf_val and prev_close <= prev_htf_val
        if not cross:
            return False
        # Confirmation: 15m line must be strictly below 1H line for LONG
        # (matches backtest exactly: h15 < h1)
        if mid_val is not None and htf_val is not None:
            if mid_val >= htf_val:
                return False
        return True

    def _check_short_cross(
        self, close: float, prev_close: float,
        htf_val: float, prev_htf_val: float,
        mid_val: Optional[float] = None, prev_mid_val: Optional[float] = None,
    ) -> bool:
        """Check for short crossover signal.

        Sell = close crosses BELOW 1H line AND 15m line is above 1H line.
        Strict filter: 15m MUST be above 1H for SHORT confirmation.
        (15m below 1H = bullish trend, contradicting a SHORT signal)
        """
        cross = close < htf_val and prev_close >= prev_htf_val
        if not cross:
            return False
        # Confirmation: 15m line must be above 1H line for bearish confirmation
        if mid_val is not None and htf_val is not None:
            if mid_val <= htf_val:
                return False
        return True

    def _detect_signal(
        self,
        close: float,
        prev_close: float,
        htf_val: float,
        prev_htf_val: float,
        high: float,
        low: float,
        timestamp: float,
        mid_val: Optional[float] = None,
        prev_mid_val: Optional[float] = None,
        prev_high: Optional[float] = None,
        prev_low: Optional[float] = None,
    ) -> Optional[Signal]:
        """Detect new entry signal."""
        if self._check_long_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            return self._create_pending_signal("LONG", close, high, low, timestamp, prev_high, prev_low)
        elif self._check_short_cross(close, prev_close, htf_val, prev_htf_val, mid_val, prev_mid_val):
            return self._create_pending_signal("SHORT", close, high, low, timestamp, prev_high, prev_low)
        return None

    def _create_pending_signal(
        self, side: str, close: float, high: float, low: float, timestamp: float,
        prev_high: Optional[float] = None, prev_low: Optional[float] = None,
    ) -> Signal:
        """Create a pending entry signal."""
        if side == "LONG":
            trigger = high
            sl_high = prev_high if prev_high is not None else high
            sl_low = prev_low if prev_low is not None else low
            stop = min(low, sl_low)
        else:
            trigger = low
            sl_high = prev_high if prev_high is not None else high
            sl_low = prev_low if prev_low is not None else low
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

        self.pending_entry = PendingEntry(
            signal=signal,
            trigger_price=trigger,
            side=side,
            created_at=time.time(),
        )
        self.state = StrategyState.PENDING_LONG if side == "LONG" else StrategyState.PENDING_SHORT

        self._emit("PENDING_ENTRY_CREATED", side=side, trigger=trigger, stop=stop)
        return signal

    def _create_reversal_signal(
        self, side: str, close: float, high: float, low: float, timestamp: float,
        prev_high: Optional[float] = None, prev_low: Optional[float] = None,
    ) -> Signal:
        """Create a reversal signal. Closes existing position + creates pending entry."""
        if side == "LONG":
            trigger = high
            sl_high = prev_high if prev_high is not None else high
            sl_low = prev_low if prev_low is not None else low
            stop = min(low, sl_low)
        else:
            trigger = low
            sl_high = prev_high if prev_high is not None else high
            sl_low = prev_low if prev_low is not None else low
            stop = max(high, sl_high)

        signal = Signal(
            signal_type=SignalType.REVERSAL,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=timestamp,
            trigger_price=trigger,
            stop_price=stop,
            quantity=self.quantity,
            metadata={"exit_reason": f"{side.lower()}_reversal"},
        )

        # Close old position before creating pending entry
        self._close_position(f"{side.lower()}_reversal", close, timestamp)

        self.pending_entry = PendingEntry(
            signal=signal,
            trigger_price=trigger,
            side=side,
            created_at=time.time(),
        )
        self.state = StrategyState.PENDING_LONG if side == "LONG" else StrategyState.PENDING_SHORT

        self._emit("REVERSAL_SIGNAL", side=side, trigger=trigger, stop=stop)
        return signal

    def _check_pending_entry(self, bar: Bar) -> Optional[Signal]:
        """Check if pending entry is triggered by bar."""
        if self.pending_entry is None:
            return None

        pen = self.pending_entry
        triggered = False

        if pen.side == "LONG" and bar.high > pen.trigger_price:
            triggered = True
        elif pen.side == "SHORT" and bar.low < pen.trigger_price:
            triggered = True

        if triggered:
            # Execute entry at bar open (next-bar execution model)
            signal = Signal(
                signal_type=SignalType.LONG if pen.side == "LONG" else SignalType.SHORT,
                instrument=self.instrument,
                strategy_id=self.strategy_id,
                timestamp=bar.start_ts,
                trigger_price=bar.open,
                stop_price=pen.signal.stop_price,
                quantity=self.quantity,
                metadata={"entry_price": bar.open, "executed": True},
            )
            self.position_side = pen.side
            self.stop_price = pen.signal.stop_price
            self.just_entered = True
            self.state = StrategyState.LONG_POSITION if pen.side == "LONG" else StrategyState.SHORT_POSITION
            self.pending_entry = None

            self._emit("ENTRY_EXECUTED", side=pen.side, price=bar.open, stop=self.stop_price)
            return signal

        return None

    def _check_stop_loss(self, bar: Bar) -> Optional[Signal]:
        """Check if stop loss is hit. Returns exit Signal if stopped out, else None."""
        if self.position_side == "LONG" and bar.low <= self.stop_price:
            exit_signal = self._create_exit_signal("stop_loss_hit", self.stop_price, bar.start_ts)
            self._close_position("stop_loss_hit", self.stop_price, bar.start_ts)
            return exit_signal
        elif self.position_side == "SHORT" and bar.high >= self.stop_price:
            exit_signal = self._create_exit_signal("stop_loss_hit", self.stop_price, bar.start_ts)
            self._close_position("stop_loss_hit", self.stop_price, bar.start_ts)
            return exit_signal
        return None

    def _create_exit_signal(self, reason: str, exit_price: float, timestamp: float) -> Signal:
        """Create an exit signal for stop-loss or other exits."""
        signal_type = SignalType.SHORT if self.position_side == "LONG" else SignalType.LONG
        return Signal(
            signal_type=signal_type,
            instrument=self.instrument,
            strategy_id=self.strategy_id,
            timestamp=timestamp,
            trigger_price=exit_price,
            stop_price=0.0,
            quantity=self.quantity,
            metadata={"exit_reason": reason, "exit": True, "source": "stop_loss"},
        )

    def _close_position(self, reason: str, exit_price: float, timestamp: float) -> None:
        """Close current position."""
        self._emit("POSITION_CLOSED", reason=reason, exit_price=exit_price)
        self.position_side = None
        self.stop_price = None
        self.pending_entry = None
        self.state = StrategyState.FLAT

    def on_tick(self, ltp: float, timestamp: float) -> Optional[Signal]:
        """Process real-time tick for pending trigger check and stop loss monitoring.
        
        Used for live pending trigger monitoring instead of waiting
        for next bar close. Also checks stop loss on every tick.
        """
        if self.just_entered:
            return None

        if self.position_side is not None and self.stop_price is not None:
            if self.position_side == "LONG" and ltp <= self.stop_price:
                exit_signal = self._create_exit_signal("stop_loss_hit", ltp, timestamp)
                self._close_position("stop_loss_hit", ltp, timestamp)
                return exit_signal
            elif self.position_side == "SHORT" and ltp >= self.stop_price:
                exit_signal = self._create_exit_signal("stop_loss_hit", ltp, timestamp)
                self._close_position("stop_loss_hit", ltp, timestamp)
                return exit_signal

        if self.pending_entry is None:
            return None

        pen = self.pending_entry
        triggered = False

        if pen.side == "LONG" and ltp > pen.trigger_price:
            triggered = True
        elif pen.side == "SHORT" and ltp < pen.trigger_price:
            triggered = True

        if triggered:
            signal = Signal(
                signal_type=SignalType.LONG if pen.side == "LONG" else SignalType.SHORT,
                instrument=self.instrument,
                strategy_id=self.strategy_id,
                timestamp=timestamp,
                trigger_price=ltp,
                stop_price=pen.signal.stop_price,
                quantity=self.quantity,
                metadata={"entry_price": ltp, "executed": True, "source": "tick"},
            )
            self.position_side = pen.side
            self.stop_price = pen.signal.stop_price
            self.just_entered = True
            self.state = StrategyState.LONG_POSITION if pen.side == "LONG" else StrategyState.SHORT_POSITION
            self.pending_entry = None

            self._emit("ENTRY_TRIGGERED", side=pen.side, price=ltp, stop=self.stop_price)
            return signal

        return None

    def _emit(self, event_type: str, **kwargs) -> None:
        """Emit event for audit trail."""
        self._events.append({
            "event_type": event_type,
            "strategy_id": self.strategy_id,
            **kwargs,
        })

    @property
    def is_flat(self) -> bool:
        return self.position_side is None

    @property
    def has_position(self) -> bool:
        return self.position_side is not None

    def snapshot(self) -> dict:
        """Get strategy state for persistence."""
        return {
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "state": self.state.value,
            "position_side": self.position_side,
            "stop_price": self.stop_price,
            "bars_processed": self._bars_processed,
            "pending_entry": {
                "side": self.pending_entry.side,
                "trigger_price": self.pending_entry.trigger_price,
                "bars_pending": self.pending_entry.bars_pending,
            } if self.pending_entry else None,
            "prev_fast_close": self._prev_fast_close,
            "prev_htf_value": self._prev_htf_value,
            "prev_mid_value": self._prev_mid_value,
        }

    def restore(self, data: dict) -> None:
        """Restore strategy state from persistence."""
        self.state = StrategyState(data.get("state", "flat"))
        self.position_side = data.get("position_side")
        self.stop_price = data.get("stop_price")
        self._bars_processed = data.get("bars_processed", 0)
        self._prev_fast_close = data.get("prev_fast_close")
        self._prev_htf_value = data.get("prev_htf_value")
        self._prev_mid_value = data.get("prev_mid_value")
        if data.get("pending_entry"):
            pe = data["pending_entry"]
            self.pending_entry = PendingEntry(
                signal=Signal(
                    signal_type=SignalType.LONG if pe["side"] == "LONG" else SignalType.SHORT,
                    instrument=self.instrument,
                    strategy_id=self.strategy_id,
                    timestamp=0,
                    trigger_price=pe["trigger_price"],
                    stop_price=0,
                    quantity=self.quantity,
                ),
                trigger_price=pe["trigger_price"],
                side=pe["side"],
                created_at=time.time(),
                bars_pending=pe.get("bars_pending", 0),
            )
