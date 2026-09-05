# NAUTILUS-STYLE MULTI-STRATEGY ARCHITECTURE — IMPLEMENTATION PLAN

## CURRENT STATE (Audit Summary)

```
TradingEngine (single, 2306 lines)
├── self.indicators: dict["GOLDM:5m" → DEMAATR]      ← SHARED per instrument:tf
├── self.htf_engine: BacktestStyleHTFEngine            ← SHARED (single instance)
│   └── _engines: dict["GOLDM:1h" → _HTFInstrumentState] ← SHARED per instrument:tf
├── self.strategies: dict["gold_01" → BaseDEMAStrategy] ← per-strategy (isolated)
├── self.position_manager: PositionManager             ← SHARED (positions tagged)
├── self.account_engines: dict["gold_01" → AccountEngine] ← per-strategy (isolated)
├── self.pnl_engines: dict["gold_01" → PNLEngine]     ← per-strategy (isolated)
├── self.execution_engine: PaperExecutionEngine        ← SHARED
├── self._lifecycle: TradeLifecycleManager             ← SHARED (trades tagged)
└── self.candle_fetcher: CandleFetcher                 ← SHARED
```

**Problems:**
1. Indicators shared per instrument:tf (not per strategy)
2. HTF engine shared (not per strategy)
3. No event model (direct callback coupling)
4. No subscription mechanism
5. TradingEngine is monolithic (2306 lines, does everything)
6. No replay/backtest adapter support

---

## TARGET ARCHITECTURE

```
SHARED INFRASTRUCTURE
├── DhanClient (REST + WebSocket)
├── CandleFetcher (fetches native candles)
├── EventBus (distributes CandleEvent to subscribers)
├── ExecutionEngine (order routing)
├── Database (persistence)
├── API (FastAPI)
├── Dashboard
├── Telegram notifications
└── Logging / Monitoring

EVENT MODEL
├── CandleEvent (immutable: instrument, timeframe, bar_data)
├── TickEvent (immutable: instrument, ltp, timestamp)
└── SignalEvent (immutable: strategy_id, signal_data)

FOUR INDEPENDENT STRATEGY INSTANCES
├── GOLDM_5M StrategyInstance
│   ├── own DEMAATR(5m)           — fast indicator
│   ├── own DEMAATR(15m)          — mid HTF indicator
│   ├── own DEMAATR(1h)           — slow HTF indicator
│   ├── own HTFState(15m)         — latest 15m bar + value
│   ├── own HTFState(1h)          — latest 1h bar + value
│   ├── own StrategyState         — FLAT/LONG/SHORT
│   ├── own crossover state       — prev_close, prev_htf, etc.
│   ├── own pending entry         — trigger, SL, timeout
│   ├── own position tracking
│   └── owns its trades
│
├── GOLDM_15M StrategyInstance
│   ├── own DEMAATR(15m)          — fast indicator
│   ├── own DEMAATR(1h)           — HTF indicator
│   ├── own HTFState(1h)          — latest 1h bar + value
│   ├── own StrategyState
│   ├── own crossover state
│   ├── own pending entry
│   ├── own position tracking
│   └── owns its trades
│
├── SILVERM_5M StrategyInstance
│   ├── own DEMAATR(5m)
│   ├── own DEMAATR(15m)
│   ├── own DEMAATR(1h)
│   ├── own HTFState(15m)
│   ├── own HTFState(1h)
│   ├── own StrategyState
│   ├── own crossover state
│   ├── own pending entry
│   ├── own position tracking
│   └── owns its trades
│
└── SILVERM_15M StrategyInstance
    ├── own DEMAATR(15m)
    ├── own DEMAATR(1h)
    ├── own HTFState(1h)
    ├── own StrategyState
    ├── own crossover state
    ├── own pending entry
    ├── own position tracking
    └── owns its trades
```

---

## IMPLEMENTATION PLAN

### PHASE 1: Event Model + Data Layer

**New files to create:**

#### 1. `events/types.py` — Event definitions
```python
@dataclass(frozen=True)
class CandleEvent:
    instrument: str          # "GOLDM"
    timeframe: str           # "5m", "15m", "1h"
    bar: Bar                 # immutable bar data
    is_closed: bool          # True = completed candle

@dataclass(frozen=True)
class TickEvent:
    instrument: str
    ltp: float
    timestamp: float
```

#### 2. `events/bus.py` — Event bus with subscriptions
```python
class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}
    
    def subscribe(self, event_type: str, callback: Callable):
        """Register callback for event type."""
        ...
    
    def publish(self, event):
        """Route event to all matching subscribers."""
        ...
```

#### 3. `data/native_streams.py` — Native candle distribution
```python
class NativeCandleDistributor:
    """Receives raw bars from CandleFetcher, creates CandleEvents, publishes to EventBus."""
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
    
    def on_candle_closed(self, bar: Bar):
        event = CandleEvent(instrument=bar.instrument, timeframe=bar.timeframe, bar=bar, is_closed=True)
        self.event_bus.publish(f"candle:{bar.instrument}:{bar.timeframe}", event)
        self.event_bus.publish(f"candle:{bar.instrument}:*", event)  # wildcard
        self.event_bus.publish("candle:*", event)  # global
```

**Changes to existing files:**

#### 4. `trading_engine.py` — Replace direct callbacks with EventBus
- Remove `on_candle_closed=lambda bar: self._on_bar_closed(bar)`
- Add `self.event_bus = EventBus()`
- Add `self.candle_distributor = NativeCandleDistributor(self.event_bus)`
- Wire CandleFetcher → candle_distributor

---

### PHASE 2: Strategy Instance Model

**New files to create:**

#### 5. `strategies/instance.py` — Complete strategy instance
```python
class StrategyInstance:
    """One completely independent strategy with own indicators, state, and HTF tracking."""
    
    def __init__(self, strategy_id, instrument, fast_timeframe, htf_timeframe, mid_timeframe, ...):
        # Subscriptions
        self.subscriptions: list[str] = []  # e.g. ["GOLDM:5m", "GOLDM:15m", "GOLDM:1h"]
        
        # Own indicators (per-strategy, not shared)
        self.fast_indicator = DEMAATR(...)    # e.g. DEMAATR(3,6,1.0) for 5m
        self.mid_indicator = DEMAATR(...)     # e.g. DEMAATR(3,6,1.0) for 15m
        self.slow_indicator = DEMAATR(...)    # e.g. DEMAATR(3,6,1.0) for 1h
        
        # Own HTF state (per-strategy)
        self.mid_htf_state = HTFState(instrument, mid_timeframe)  # latest 15m
        self.slow_htf_state = HTFState(instrument, htf_timeframe) # latest 1h
        
        # Own strategy state
        self.state = StrategyState.FLAT
        self.position_side = None
        self.stop_price = None
        self.pending_entry = None
        self._prev_fast_close = None
        self._prev_htf_value = None
        self._prev_mid_value = None
        # ... all state from BaseDEMAStrategy
    
    def on_candle(self, event: CandleEvent):
        """Route incoming candle to correct handler."""
        if event.timeframe == self.fast_timeframe:
            self._on_fast_candle(event)
        elif event.timeframe == self.mid_timeframe:
            self._on_mid_htf_candle(event)
        elif event.timeframe == self.htf_timeframe:
            self._on_slow_htf_candle(event)
    
    def _on_fast_candle(self, event):
        """Process fast candle: update indicator, map HTF, check signals."""
        bar = event.bar
        self.fast_indicator.update(bar.open, bar.high, bar.low, bar.close)
        
        # Map HTF values from own state (not global engine)
        htf_mapped = self.slow_htf_state.get_mapped_value(bar)
        mid_mapped = self.mid_htf_state.get_mapped_value(bar)
        
        # Run crossover detection
        signal = self._detect_signal(bar, htf_mapped, mid_mapped)
        if signal:
            self._emit_signal(signal)
    
    def _on_mid_htf_candle(self, event):
        """Update own mid HTF state."""
        self.mid_htf_state.update(event.bar, self.mid_indicator)
    
    def _on_slow_htf_candle(self, event):
        """Update own slow HTF state."""
        self.slow_htf_state.update(event.bar, self.slow_indicator)
```

#### 6. `strategies/htf_state.py` — Per-strategy HTF tracking
```python
class HTFState:
    """Tracks latest completed HTF bar and its DEMA-ATR value for ONE strategy."""
    
    def __init__(self, instrument: str, timeframe: str):
        self.instrument = instrument
        self.timeframe = timeframe
        self._end_times: list[float] = []
        self._values: list[float] = []
        self.last_value: Optional[float] = None
        self.prev_value: Optional[float] = None
    
    def update(self, bar: Bar, indicator: DEMAATR):
        """Feed a closed HTF bar. Updates indicator + stores for mapping."""
        indicator.update(bar.open, bar.high, bar.low, bar.close)
        value = indicator.value
        self._end_times.append(bar.end_ts)
        self._values.append(value)
        if value is not None:
            self.prev_value = self.last_value
            self.last_value = value
    
    def get_mapped_value(self, fast_bar: Bar) -> HTFMappedValue:
        """Map own HTF DEMA-ATR to fast bar using bisect (EXACT backtest logic)."""
        # Same as BacktestStyleHTFEngine._map_htf_to_fast but per-strategy
        ...
    
    def reset(self):
        self._end_times.clear()
        self._values.clear()
        self.last_value = None
        self.prev_value = None
```

**Changes to existing files:**

#### 7. `strategies/base_dema_strategy.py` — Deprecate or keep as compatibility layer
- Keep for backward compatibility
- StrategyInstance delegates crossover logic to base methods
- OR refactor crossover logic into a pure function

#### 8. `strategies/gold/__init__.py` + `strategies/silver/__init__.py`
- Replace thin wrappers with StrategyInstance factory functions:
```python
def create_gold_5m(strategy_id, instrument, **kwargs):
    return StrategyInstance(
        strategy_id=strategy_id,
        instrument=instrument,
        fast_timeframe="5m",
        mid_timeframe="15m",
        htf_timeframe="1h",
        subscriptions=[f"{instrument}:5m", f"{instrument}:15m", f"{instrument}:1h"],
        **kwargs,
    )
```

---

### PHASE 3: TradingEngine Refactor

**Major changes to `trading_engine.py`:**

#### 9. Remove shared indicator dict
```python
# BEFORE:
self.indicators: dict[str, DEMAATR] = {}  # SHARED

# AFTER:
# Indicators live inside each StrategyInstance
# Remove _init_indicator_engines()
# Remove self.indicators entirely
```

#### 10. Remove shared HTF engine
```python
# BEFORE:
self.htf_engine = BacktestStyleHTFEngine()  # SHARED

# AFTER:
# HTF state lives inside each StrategyInstance
# Remove _init_htf_engine()
# Remove self.htf_engine entirely
```

#### 11. Replace _on_bar_closed with event-driven routing
```python
# BEFORE:
def _on_bar_closed(self, bar):
    # 1. Update shared indicators
    # 2. Update shared HTF engine
    # 3. Loop through strategies
    # 4. Map HTF values
    # 5. Call strat.on_bar()

# AFTER:
# CandleFetcher → CandleDistributor → EventBus → StrategyInstance.on_candle()
# Each strategy handles its own indicators, HTF state, and signals
```

#### 12. Warmup becomes per-strategy
```python
# BEFORE:
self._warmup_from_rest()  # warms shared indicators + shared HTF engine

# AFTER:
for strategy in self.strategies.values():
    strategy.warmup_from_rest(rest_client)  # each strategy warms its own indicators + HTF state
```

#### 13. Slim down TradingEngine to orchestrator
```python
class TradingEngine:
    """Orchestrator: wires infrastructure, routes events, manages lifecycle."""
    
    def __init__(self, config):
        # Infrastructure (shared)
        self.event_bus = EventBus()
        self.data_adapter = DhanDataAdapter(...)
        self.candle_fetcher = CandleFetcher(...)
        self.candle_distributor = NativeCandleDistributor(self.event_bus)
        self.execution_engine = PaperExecutionEngine(...)
        self.position_manager = PositionManager()
        self.lifecycle = TradeLifecycleManager(...)
        
        # Strategy instances (independent)
        self.strategies: dict[str, StrategyInstance] = {}
        self._init_strategies()
        
        # Wire events
        self._wire_events()
    
    def _wire_events(self):
        """Subscribe strategy instances to relevant candle events."""
        for strategy in self.strategies.values():
            for sub in strategy.subscriptions:
                self.event_bus.subscribe(f"candle:{sub}", strategy.on_candle)
            # Also subscribe to ticks for pending entries / stop loss
            self.event_bus.subscribe(f"tick:{strategy.instrument}", strategy.on_tick)
```

---

### PHASE 4: Backtest/Replay Compatibility

#### 14. `replay/adapter.py` — Historical data adapter
```python
class HistoricalDataAdapter:
    """Replays historical candles through the same EventBus."""
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
    
    def replay(self, csv_path: str, instrument: str, timeframes: list[str]):
        """Load CSVs, create CandleEvents, publish to EventBus."""
        for tf in timeframes:
            df = load_csv(...)
            for _, row in df.iterrows():
                bar = Bar(instrument=instrument, timeframe=tf, ...)
                event = CandleEvent(instrument=instrument, timeframe=tf, bar=bar, is_closed=True)
                self.event_bus.publish(f"candle:{instrument}:{tf}", event)
```

#### 15. Same strategy instances work for live and replay
```python
# Live:
engine = TradingEngine(config)
engine.start()  # starts CandleFetcher, WebSocket

# Backtest:
engine = TradingEngine(config)
replay = HistoricalDataAdapter(engine.event_bus)
replay.replay("data/GOLDM_5m.csv", "GOLDM", ["5m", "15m", "1h"])
# Strategy instances process same events, produce same signals
```

---

### PHASE 5: Signal + Trade Ownership

#### 16. Signal carries strategy_id (already exists)
```python
@dataclass
class Signal:
    strategy_id: str    # "gold_01"
    instrument: str     # "GOLDM"
    signal_type: SignalType
    ...
```

#### 17. Trade carries strategy_id (already exists)
```python
class TradeContext:
    strategy_id: str    # "gold_01"
    trade_id: str       # UUID
    ...
```

#### 18. No changes needed for execution/persistence — they already tag strategy_id

---

## FILE MANIFEST

### New files to create:
| File | Purpose |
|------|---------|
| `events/__init__.py` | Package |
| `events/types.py` | CandleEvent, TickEvent dataclasses |
| `events/bus.py` | EventBus with pub/sub |
| `data/native_streams.py` | NativeCandleDistributor |
| `strategies/instance.py` | StrategyInstance (complete isolated strategy) |
| `strategies/htf_state.py` | Per-strategy HTF state tracking |
| `replay/__init__.py` | Package |
| `replay/adapter.py` | HistoricalDataAdapter for backtest |

### Files to modify:
| File | Change |
|------|--------|
| `trading_engine.py` | Remove shared indicators/HTF engine, add EventBus, wire events |
| `strategies/gold/__init__.py` | Replace thin wrappers with StrategyInstance factories |
| `strategies/silver/__init__.py` | Replace thin wrappers with StrategyInstance factories |
| `config/settings.json` | Add mid_timeframe to strategy configs (already exists) |

### Files to deprecate (keep but unused):
| File | Reason |
|------|--------|
| `htf/backtest_style_htf.py` | Replaced by per-strategy HTFState |
| `strategies/base_dema_strategy.py` | Replaced by StrategyInstance |
| `indicators/dema_atr.py` | Still used, but instances owned by strategies |
| `indicators/dema.py` | Still used, but instances owned by strategies |
| `indicators/atr.py` | Still used, but instances owned by strategies |

### Files that stay unchanged:
| File | Reason |
|------|--------|
| `core/candle_fetcher.py` | Fetches candles, emits bars — no change needed |
| `core/timeframe_engine.py` | Bar dataclass — no change needed |
| `execution/` | Already strategy-aware |
| `persistence/` | Already strategy-aware |
| `lifecycle/` | Already strategy-aware |
| `portfolio/` | Already per-strategy |
| `analytics/` | Reads from DB, no change |
| `dashboard/` | Reads from DB, no change |
| `notifications/` | Receives signals, no change |

---

## IMPLEMENTATION ORDER

### Step 1: Create event model (events/)
- Create `events/types.py` with CandleEvent, TickEvent
- Create `events/bus.py` with EventBus
- Create `data/native_streams.py` with NativeCandleDistributor
- Unit test: publish candle event, verify subscriber receives it

### Step 2: Create HTFState (strategies/htf_state.py)
- Implement HTFState class with update() and get_mapped_value()
- Verify bisect logic matches BacktestStyleHTFEngine exactly
- Unit test: feed same bars, verify same mapped values

### Step 3: Create StrategyInstance (strategies/instance.py)
- Implement StrategyInstance with own indicators, HTF state, and crossover logic
- Port all crossover/SL/reversal logic from BaseDEMAStrategy
- Each instance has its own subscriptions
- Unit test: feed candle events, verify same signals as BaseDEMAStrategy

### Step 4: Wire EventBus into TradingEngine
- Replace direct CandleFetcher callback with EventBus
- Remove shared indicators dict
- Remove shared HTF engine
- Subscribe StrategyInstances to relevant candle events
- Verify: all4 strategies process candles independently

### Step 5: Warmup per strategy
- Each StrategyInstance fetches its own historical data via REST
- Each warms its own indicators and HTF state
- Verify: warmup produces same DEMA-ATR values as current shared approach

### Step 6: Backtest compatibility
- Create HistoricalDataAdapter
- Verify: replay produces same signals as live
- Verify: same StrategyInstance class works for live and replay

### Step 7: Clean up
- Remove dead code
- Update documentation
- Run full test suite

---

## RISKS AND MITIGATIONS

| Risk | Mitigation |
|------|------------|
| DEMA-ATR warmup differences | Use same indicator code; warmup from same REST data |
| HTF mapping differences | HTFState uses identical bisect logic |
| Event ordering | EventBus processes synchronously (deterministic) |
| Thread safety | EventBus.publish is called under existing _lock |
| Performance (4 strategies × own indicators) | DEMAATR.update is O(1) per bar; negligible overhead |
| Backtest compatibility | Same StrategyInstance class, same event model |
