"""REST candle fetcher - fetches completed candles from Dhan REST API when they close.

Architecture:
  WebSocket: ONLY for LTP (live price, order fills, P&L)
  REST API: Fetch actual OHLCV candles when they close
  
Flow:
  1. Timer checks every minute if any candle should have closed
  2. When 5m candle closes at :05, :10, :15, etc. → fetch from REST
  3. Create Bar object from REST data
  4. Feed to indicators and HTF engine
  5. Check for signals

Session-aware:
  - Only fetches candles during MARKET_OPEN / LIVE_TRADING states
  - Skips fetching during PRE_MARKET, MARKET_CLOSE, AFTER_MARKET, OVERNIGHT
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

IST = timezone(timedelta(hours=5, minutes=30))

# Timeframe minutes
TIMEFRAME_MINUTES = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
}


class CandleFetcher:
    """Fetches completed candles from REST API when they close.
    
    Does NOT form candles from ticks. Uses actual exchange OHLCV data.
    """
    
    def __init__(
        self,
        data_adapter,
        instruments: dict,
        on_candle_closed: Callable,
        session_open: str = "09:00",
        session_close: str = "23:30",
        market_status=None,
    ):
        self.data_adapter = data_adapter
        self.instruments = instruments
        self.on_candle_closed = on_candle_closed
        self.session_open = session_open
        self.session_close = session_close
        self.market_status = market_status
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_fetched: dict[str, float] = {}  # key -> last_fetch_timestamp
        
    def start(self):
        """Start the candle fetcher thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="candle-fetcher"
        )
        self._thread.start()
        print("[CandleFetcher] Started", flush=True)
        
    def stop(self):
        """Stop the candle fetcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[CandleFetcher] Stopped", flush=True)
        
    def _run(self):
        """Main loop: check every 30 seconds if any candle closed."""
        while self._running:
            try:
                # Skip fetching if market is not in active trading state
                if self.market_status and not self.market_status.should_fetch_candles:
                    time.sleep(30)
                    continue
                self._check_and_fetch()
            except Exception as e:
                print(f"[CandleFetcher] Error: {e}", flush=True)
            time.sleep(30)  # Check every 30 seconds
            
    def _check_and_fetch(self):
        """Check if any candle should have closed and fetch it."""
        now = datetime.now(IST)
        
        for name, cfg in self.instruments.items():
            # Check 5m candles
            self._check_timeframe(name, cfg, "5m", now)
            # Check 15m candles
            self._check_timeframe(name, cfg, "15m", now)
            # Check 1H candles
            self._check_timeframe(name, cfg, "1h", now)
            
    def _check_timeframe(self, name: str, cfg: dict, timeframe: str, now: datetime):
        """Check if a candle of this timeframe just closed and fetch it."""
        tf_minutes = TIMEFRAME_MINUTES.get(timeframe, 5)
        
        # Calculate when the last candle should have closed
        hour, minute = map(int, self.session_open.split(":"))
        session_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if now < session_start:
            return  # Market not open yet

        # End-of-session guard: don't fetch candles after session close
        close_h, close_m = map(int, self.session_close.split(":"))
        session_end = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        if now >= session_end:
            return  # Market closed, no more candles to fetch
            
        minutes_since_open = (now - session_start).total_seconds() / 60
        
        # Which candle just closed?
        # The last closed candle ends at: session_start + (minutes_since_open // tf_minutes) * tf_minutes
        last_close_minutes = (int(minutes_since_open) // tf_minutes) * tf_minutes
        last_close_time = session_start + timedelta(minutes=last_close_minutes)
        
        # Don't fetch the current forming candle (it hasn't closed yet)
        if last_close_time >= now:
            return
            
        # Create key for dedup
        key = f"{name}:{timeframe}:{last_close_time.timestamp()}"
        if key in self._last_fetched:
            return  # Already fetched this candle
            
        # Don't fetch candles older than 2 candles (avoid re-fetching old data)
        if (now - last_close_time).total_seconds() > tf_minutes * 60 * 2:
            return
            
        # Fetch the candle from REST API
        self._fetch_candle(name, cfg, timeframe, last_close_time, key)
        
    def _fetch_candle(self, name: str, cfg: dict, timeframe: str, candle_time: datetime, key: str):
        """Fetch a single candle from REST API."""
        try:
            # Calculate from_date and to_date (same day)
            from_date = candle_time.date()
            to_date = candle_time.date()
            
            # Fetch candles from REST
            candles = self.data_adapter.fetch_historical_candles(
                name, "5", from_date, to_date,
            )
            
            if not candles:
                return
                
            # Find the candle that matches our time
            target_ts = int(candle_time.timestamp())
            tf_minutes = TIMEFRAME_MINUTES[timeframe]
            
            # For 5m, find exact match
            if timeframe == "5m":
                for candle in candles:
                    candle_ts = candle[0]
                    if abs(candle_ts - target_ts) < 300:  # Within 5 minutes
                        bar = self._create_bar(name, timeframe, candle, candle_time, tf_minutes)
                        if bar:
                            self._last_fetched[key] = time.time()
                            self.on_candle_closed(bar)
                            print(f"[CandleFetcher] {name} {timeframe} closed: {candle_time.strftime('%H:%M')} O={bar.open:.0f} H={bar.high:.0f} L={bar.low:.0f} C={bar.close:.0f}", flush=True)
                        return
                        
            # For 15m/1H, we need to aggregate multiple 5m candles
            else:
                # Find all 5m candles in this timeframe window
                window_start = candle_time.timestamp()
                window_end = window_start + tf_minutes * 60
                
                window_candles = []
                for candle in candles:
                    candle_ts = candle[0]
                    if window_start <= candle_ts < window_end:
                        window_candles.append(candle)
                        
                if window_candles:
                    bar = self._aggregate_candles(name, timeframe, window_candles, candle_time, tf_minutes)
                    if bar:
                        self._last_fetched[key] = time.time()
                        self.on_candle_closed(bar)
                        print(f"[CandleFetcher] {name} {timeframe} closed: {candle_time.strftime('%H:%M')} O={bar.open:.0f} H={bar.high:.0f} L={bar.low:.0f} C={bar.close:.0f}", flush=True)
                        
        except Exception as e:
            print(f"[CandleFetcher] Error fetching {name} {timeframe}: {e}", flush=True)
            
    def _create_bar(self, name: str, timeframe: str, candle: list, candle_time: datetime, tf_minutes: int):
        """Create a Bar object from REST candle data."""
        from core.timeframe_engine import Bar, BarState
        
        try:
            timestamp, open_p, high, low, close, volume = candle
            
            # Convert to IST if needed
            bar_dt = candle_time
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=IST)
            start_ts = bar_dt.timestamp()
            
            return Bar(
                instrument=name,
                timeframe=timeframe,
                start_ts=start_ts,
                end_ts=start_ts + tf_minutes * 60,
                open=float(open_p),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=int(volume),
                state=BarState.CLOSED,
            )
        except Exception as e:
            print(f"[CandleFetcher] Error creating bar: {e}", flush=True)
            return None
            
    def _aggregate_candles(self, name: str, timeframe: str, candles: list, candle_time: datetime, tf_minutes: int):
        """Aggregate multiple 5m candles into a higher timeframe candle."""
        from core.timeframe_engine import Bar, BarState
        
        try:
            if not candles:
                return None
                
            # Aggregate OHLCV
            open_p = float(candles[0][1])  # First candle's open
            high = max(float(c[2]) for c in candles)  # Highest high
            low = min(float(c[3]) for c in candles)  # Lowest low
            close_p = float(candles[-1][4])  # Last candle's close
            volume = sum(int(c[5]) for c in candles)  # Sum of volumes
            
            bar_dt = candle_time
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=IST)
            start_ts = bar_dt.timestamp()
            
            return Bar(
                instrument=name,
                timeframe=timeframe,
                start_ts=start_ts,
                end_ts=start_ts + tf_minutes * 60,
                open=open_p,
                high=high,
                low=low,
                close=close_p,
                volume=volume,
                state=BarState.CLOSED,
            )
        except Exception as e:
            print(f"[CandleFetcher] Error aggregating: {e}", flush=True)
            return None
