"""Market session lifecycle manager.

Authoritative market status for the entire trading system.

State machine:
  OVERNIGHT → PRE_MARKET → MARKET_OPEN → LIVE_TRADING → MARKET_CLOSE → AFTER_MARKET → OVERNIGHT

Additional override states:
  SAFE_MODE — entered on reconciliation failure or uncertain state
  HALTED — manual emergency stop

Source: IST clock + configured session_open/session_close
Update frequency: checked on every access (state property)
Timezone: IST (UTC+5:30)
Session: MCX 09:00-23:30
Weekends: PRE_MARKET/MARKET_OPEN/etc never trigger on Sat/Sun (minutes Since Monday used)
Holidays: Not handled automatically (exchange calendar not available)
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Callable, List

IST = timezone(timedelta(hours=5, minutes=30))


class MarketState(Enum):
    """Market session states."""
    OVERNIGHT = "overnight"          # Idle. Server on. No market activity.
    PRE_MARKET = "pre_market"        # Warmup window (session_open - 5min to session_open)
    MARKET_OPEN = "market_open"      # Session just opened (first 1 minute)
    LIVE_TRADING = "live_trading"    # Active trading hours
    MARKET_CLOSE = "market_close"    # Closing window (session_close - 5min to session_close)
    AFTER_MARKET = "after_market"    # Post-close wind-down (session_close to +30min)
    SAFE_MODE = "safe_mode"          # Entered on critical error
    HALTED = "halted"                # Manual emergency stop


class DataStatus(Enum):
    """Market data freshness status."""
    CONNECTED = "connected"          # Receiving live ticks
    STALE = "stale"                  # Connected but no recent ticks
    DISCONNECTED = "disconnected"    # WebSocket not connected
    NO_DATA = "no_data"              # Never received any tick


class EngineStatus(Enum):
    """Trading engine operational status."""
    INITIALIZING = "initializing"
    RESTORING = "restoring"
    WARMING_UP = "warming_up"
    RECONCILING = "reconciling"
    READY = "ready"
    TRADING = "trading"
    SAFE_MODE = "safe_mode"
    HALTED = "halted"
    STOPPED = "stopped"


class MarketStatus:
    """Authoritative market session state.
    
    Every subsystem reads state from here. Never infer market status
    from WebSocket connection or tick timestamps alone.
    """

    def __init__(
        self,
        session_open: str = "09:00",
        session_close: str = "23:30",
        pre_market_minutes: int = 5,
        close_minutes: int = 5,
    ):
        self.session_open = session_open
        self.session_close = session_close
        self.pre_market_minutes = pre_market_minutes
        self.close_minutes = close_minutes
        self._lock = threading.RLock()

        self._market_state = MarketState.OVERNIGHT
        self._engine_status = EngineStatus.INITIALIZING
        self._data_status = DataStatus.NO_DATA
        self._last_transition: Optional[datetime] = None
        self._last_tick_time: float = 0.0
        self._stale_threshold: float = 60.0  # seconds
        self._session_date: Optional[str] = None
        self._warmup_done_today: bool = False
        self._reconcile_done_today: bool = False
        self._force_state_override: Optional[MarketState] = None
        self._on_transition_callbacks: List[Callable] = []

    # --- Properties ---

    @property
    def state(self) -> MarketState:
        with self._lock:
            if self._force_state_override:
                return self._force_state_override
            self._check_transition()
            return self._market_state

    @property
    def engine_status(self) -> EngineStatus:
        with self._lock:
            return self._engine_status

    @property
    def data_status(self) -> DataStatus:
        with self._lock:
            return self._data_status

    @property
    def is_trading_allowed(self) -> bool:
        return (self.state == MarketState.LIVE_TRADING
                and self._engine_status == EngineStatus.TRADING
                and self._data_status == DataStatus.CONNECTED)

    @property
    def is_session_active(self) -> bool:
        return self.state in (
            MarketState.PRE_MARKET,
            MarketState.MARKET_OPEN,
            MarketState.LIVE_TRADING,
            MarketState.MARKET_CLOSE,
        )

    @property
    def should_fetch_candles(self) -> bool:
        return self.state in (MarketState.MARKET_OPEN, MarketState.LIVE_TRADING)

    @property
    def should_warmup(self) -> bool:
        return (self.state in (MarketState.PRE_MARKET, MarketState.MARKET_OPEN, MarketState.LIVE_TRADING)
                and not self._warmup_done_today)

    @property
    def should_reconcile(self) -> bool:
        return not self._reconcile_done_today

    @property
    def is_safe(self) -> bool:
        return self._engine_status in (EngineStatus.SAFE_MODE, EngineStatus.HALTED)

    # --- State transitions ---

    def set_engine_status(self, status: EngineStatus) -> None:
        with self._lock:
            old = self._engine_status
            self._engine_status = status
            if old != status:
                print(f"[Market] Engine: {old.value} -> {status.value}", flush=True)

    def update_data_status(self, connected: bool, last_tick_time: float = 0.0) -> None:
        """Update data status from WebSocket state."""
        with self._lock:
            self._last_tick_time = last_tick_time
            now = time.time()
            if not connected:
                self._data_status = DataStatus.DISCONNECTED
            elif last_tick_time == 0:
                self._data_status = DataStatus.NO_DATA
            elif (now - last_tick_time) > self._stale_threshold:
                self._data_status = DataStatus.STALE
            else:
                self._data_status = DataStatus.CONNECTED

    def mark_warmup_done(self) -> None:
        with self._lock:
            self._warmup_done_today = True

    def mark_reconcile_done(self) -> None:
        with self._lock:
            self._reconcile_done_today = True

    def enter_safe_mode(self, reason: str = "") -> None:
        with self._lock:
            self._force_state_override = MarketState.SAFE_MODE
            self._engine_status = EngineStatus.SAFE_MODE
            print(f"[Market] ENTERED SAFE MODE: {reason}", flush=True)

    def exit_safe_mode(self) -> None:
        with self._lock:
            self._force_state_override = None
            self._engine_status = EngineStatus.READY
            print("[Market] Exited safe mode", flush=True)

    def halt(self) -> None:
        with self._lock:
            self._force_state_override = MarketState.HALTED
            self._engine_status = EngineStatus.HALTED
            print("[Market] HALTED", flush=True)

    def on_transition(self, callback: Callable) -> None:
        self._on_transition_callbacks.append(callback)

    # --- Persistence ---

    def snapshot(self) -> dict:
        with self._lock:
            current_state = self._force_state_override or self._market_state
            return {
                "market_state": current_state.value,
                "force_state_override": self._force_state_override.value if self._force_state_override else None,
                "engine_status": self._engine_status.value,
                "data_status": self._data_status.value,
                "last_transition": self._last_transition.isoformat() if self._last_transition else None,
                "session_date": self._session_date,
                "warmup_done_today": self._warmup_done_today,
                "reconcile_done_today": self._reconcile_done_today,
                "session_open": self.session_open,
                "session_close": self.session_close,
            }

    def restore(self, data: dict) -> None:
        with self._lock:
            if data.get("session_date") != self._current_date():
                # New day — reset daily flags
                self._warmup_done_today = False
                self._reconcile_done_today = False
            else:
                self._warmup_done_today = data.get("warmup_done_today", False)
                self._reconcile_done_today = data.get("reconcile_done_today", False)
                # Restore state from same session
                ms = data.get("market_state")
                if ms:
                    self._market_state = MarketState(ms)
                es = data.get("engine_status")
                if es:
                    self._engine_status = EngineStatus(es)
                ds = data.get("data_status")
                if ds:
                    self._data_status = DataStatus(ds)
                # Restore the explicit force-override (e.g. SAFE_MODE) so the
                # same safety guard survives a same-day restart; otherwise
                # state() falls back to the time-based transition and the
                # override is silently lost.
                fso = data.get("force_state_override")
                if fso:
                    self._force_state_override = MarketState(fso)
                else:
                    self._force_state_override = None
            self._session_date = data.get("session_date")

    def force_state(self, state: MarketState) -> None:
        with self._lock:
            self._force_state_override = state
            self._last_transition = self._now()

    # --- Internal ---

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _current_date(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def _current_weekday(self) -> int:
        return self._now().weekday()  # 0=Mon, 6=Sun

    def _parse_time(self, time_str: str) -> tuple[int, int]:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])

    def _check_transition(self) -> None:
        now = self._now()
        current_date = now.strftime("%Y-%m-%d")
        weekday = now.weekday()

        # Reset daily flags on new day
        if current_date != self._session_date:
            self._warmup_done_today = False
            self._reconcile_done_today = False
            self._session_date = current_date

        # Weekend: stay OVERNIGHT
        if weekday >= 5:
            target = MarketState.OVERNIGHT
        else:
            hour, minute = now.hour, now.minute
            current_minutes = hour * 60 + minute

            open_h, open_m = self._parse_time(self.session_open)
            close_h, close_m = self._parse_time(self.session_close)
            open_minutes = open_h * 60 + open_m
            close_minutes_val = close_h * 60 + close_m
            pre_market_start = open_minutes - self.pre_market_minutes
            close_start = close_minutes_val - self.close_minutes

            if current_minutes < pre_market_start:
                target = MarketState.OVERNIGHT
            elif current_minutes < open_minutes:
                target = MarketState.PRE_MARKET
            elif current_minutes < open_minutes + 1:
                target = MarketState.MARKET_OPEN
            elif current_minutes < close_start:
                target = MarketState.LIVE_TRADING
            elif current_minutes < close_minutes_val:
                target = MarketState.MARKET_CLOSE
            elif current_minutes < close_minutes_val + 30:
                target = MarketState.AFTER_MARKET
            else:
                target = MarketState.OVERNIGHT

        if target != self._market_state:
            old = self._market_state
            self._market_state = target
            self._last_transition = now
            print(f"[Market] {old.value} -> {target.value} at {now.strftime('%H:%M:%S')}", flush=True)
            for cb in self._on_transition_callbacks:
                try:
                    cb(old, target)
                except Exception:
                    pass

    def __repr__(self) -> str:
        return f"MarketStatus({self.state.value}, engine={self._engine_status.value}, data={self._data_status.value})"
