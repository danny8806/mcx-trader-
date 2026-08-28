"""FastAPI dashboard server - READ/CONTROL layer over trading engine."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Add parent to path for engine imports
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from dashboard.event_bus import EventBus
from dashboard.ws_manager import ConnectionManager
from dashboard.routes import overview, strategies, positions, orders, trades
from dashboard.routes import pnl, market_data, risk, health, replay
from dashboard.routes import reconciliation, alerts, settings, audit_log, indicators

# Analytics routes
try:
    from analytics import routes as analytics_routes
    from analytics.schema import init_analytics_db
    _analytics_db = str(Path(__file__).resolve().parent.parent / "data" / "analytics.db")
    init_analytics_db(_analytics_db)
    analytics_routes.init(_analytics_db)
    print(f"[Analytics] Initialized with db: {_analytics_db}", file=sys.stderr, flush=True)
except Exception as e:
    print(f"[Analytics] Init failed: {e}", file=sys.stderr, flush=True)
    analytics_routes = None

logger = logging.getLogger(__name__)

# --- API Key Auth ---
# Generate on first run, persist to settings.json under dashboard.api_key
_api_key: Optional[str] = None
_ws_paths = {"/ws"}  # WebSocket endpoints exempt from API key check


def _get_api_key() -> Optional[str]:
    """Load API key from config. Only enforced if explicitly set (not null)."""
    global _api_key
    if _api_key is not None:
        return _api_key
    try:
        settings_path = Path(__file__).resolve().parent.parent / "config" / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text())
            dash_cfg = data.get("dashboard", {})
            key = dash_cfg.get("api_key")
            if key:
                _api_key = key
                return _api_key
            # null or missing = no auth required
            _api_key = ""  # sentinel: checked, no key configured
            return None
    except Exception:
        pass
    return None


async def verify_api_key(request: Request):
    """FastAPI dependency: verify API key via header or query param.
    
    Skips WebSocket paths (handled separately in WS endpoint).
    """
    if request.url.path in _ws_paths:
        return  # WS has its own auth flow
    key = _get_api_key()
    if not key:
        return  # No key configured = open (fallback)
    # Check header: X-API-Key
    provided = request.headers.get("X-API-Key")
    if not provided:
        # Check query param: ?api_key=...
        provided = request.query_params.get("api_key")
    if not provided or not secrets.compare_digest(provided, key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


event_bus = EventBus(max_events=50000)
ws_manager = ConnectionManager()
_engine = None
_persistence = None


def set_engine(engine):
    global _engine
    _engine = engine


def get_engine():
    return _engine


def get_event_bus():
    return event_bus


def get_ws_manager():
    return ws_manager


def get_persistence():
    return _persistence


def _snapshot_sync():
    """Synchronous snapshot call to run in thread pool."""
    if not _engine:
        return None
    return _engine.snapshot()


def _enrich_strategies(snap):
    """Enrich strategy data with PNL."""
    instruments = _engine.config.get("instruments", {})
    strategies_cfg = _engine.config.get("strategies", {})
    enriched_strats = {}
    for name, strat_snap in snap.get("strategies", {}).items():
        cfg = strategies_cfg.get(name, {})
        inst = cfg.get("instrument", "")
        inst_cfg = instruments.get(inst, {})
        pnl_engine = _engine.pnl_engines.get(name)
        pnl_snap = pnl_engine.snapshot() if pnl_engine else {}
        enriched_strats[name] = {
            **strat_snap,
            "enabled": cfg.get("enabled", True),
            "realized_net": pnl_snap.get("realized_net", {}).get("value", 0) if isinstance(pnl_snap.get("realized_net"), dict) else pnl_snap.get("realized_net", 0),
            "realized_gross": pnl_snap.get("realized_gross", {}).get("value", 0) if isinstance(pnl_snap.get("realized_gross"), dict) else pnl_snap.get("realized_gross", 0),
            "trade_count": pnl_snap.get("trade_count", 0),
            "wins": pnl_snap.get("wins", 0),
            "losses": pnl_snap.get("losses", 0),
            "win_rate": pnl_snap.get("win_rate", 0.0),
        }
    snap["strategies"] = enriched_strats
    return snap


async def _push_updates():
    """Background task: periodically push engine state via WebSocket."""
    import concurrent.futures
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")
    while True:
        try:
            loop = asyncio.get_running_loop()
            snap = await loop.run_in_executor(_executor, _snapshot_sync)
            if snap:
                snap = await loop.run_in_executor(_executor, _enrich_strategies, snap)
                await ws_manager.broadcast("engine_state", snap)
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Push update error: {e}")
            await asyncio.sleep(2.0)


async def _push_events():
    """Background task: push recent events via WebSocket."""
    import concurrent.futures
    _exec = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="events")
    last_id = 0
    while True:
        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(_exec, event_bus.get_recent, 50)
            new_events = [e for e in events if e["id"] > last_id]
            if new_events:
                await ws_manager.broadcast("events", new_events)
                last_id = new_events[-1]["id"]
            await asyncio.sleep(0.5)
        except Exception:
            await asyncio.sleep(1.0)


def _on_engine_event(event_type: str, data: dict):
    """Called from engine threads to publish to event bus."""
    event_bus.publish(event_type, data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _persistence
    import sys
    print("[Lifespan] Starting...", file=sys.stderr, flush=True)
    if _engine is None:
        try:
            from trading_engine import TradingEngine
            _engine = TradingEngine(event_callback=_on_engine_event)
        except Exception as e:
            logger.warning(f"Could not initialize TradingEngine: {e}")
    print("[Lifespan] Engine ready", file=sys.stderr, flush=True)

    if _persistence is None:
        try:
            from persistence.manager import PersistenceManager
            _persistence = PersistenceManager(
                state_path=str(Path(__file__).resolve().parent.parent / "system_state.json"),
                db_path=str(Path(__file__).resolve().parent.parent / "trading.db"),
            )
        except Exception:
            pass
    print("[Lifespan] Persistence ready", file=sys.stderr, flush=True)

    # Wire persistence to engine and restore state
    if _engine and _persistence:
        try:
            _engine.set_persistence(_persistence)
            saved = _persistence.load_state()
            if saved:
                _engine.restore(saved)
                print("[Lifespan] State restored from last session", file=sys.stderr, flush=True)
            else:
                print("[Lifespan] No saved state - fresh start", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[Lifespan] State restore failed: {e}", file=sys.stderr, flush=True)

    # Start the trading engine (WebSocket, backfill, Telegram, indicators)
    if _engine:
        try:
            _engine.start()
            print("[Lifespan] Engine started", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[Lifespan] Engine start failed: {e}", file=sys.stderr, flush=True)

    # Initialize all route modules with engine + event_bus
    for mod in [overview, strategies, positions, orders, trades,
                pnl, market_data, risk, health, replay,
                reconciliation, alerts, settings, audit_log, indicators]:
        try:
            if hasattr(mod, 'init'):
                if _persistence and 'persistence' in mod.init.__code__.co_varnames:
                    mod.init(_engine, event_bus, _persistence)
                else:
                    mod.init(_engine, event_bus)
        except Exception:
            pass
    print("[Lifespan] Routes ready", file=sys.stderr, flush=True)

    push_task = asyncio.create_task(_push_updates())
    events_task = asyncio.create_task(_push_events())
    print("[Lifespan] Background tasks started", file=sys.stderr, flush=True)
    yield
    print("[Lifespan] Shutting down", file=sys.stderr, flush=True)
    push_task.cancel()
    events_task.cancel()
    try:
        await asyncio.gather(push_task, events_task, return_exceptions=True)
    except Exception:
        pass
    if _engine:
        try:
            state = _engine.snapshot()
            if _persistence:
                _persistence.save_state(state)
                print("[Lifespan] State saved", file=sys.stderr, flush=True)
            # Stop token scheduler before engine
            if hasattr(_engine, 'data_adapter') and hasattr(_engine.data_adapter, 'rest'):
                _engine.data_adapter.rest.stop_scheduler()
            _engine.stop()
        except Exception as e:
            print(f"[Lifespan] Shutdown error: {e}", file=sys.stderr, flush=True)


app = FastAPI(
    title="GoldSilver Trading Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files (Docker build)
from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse

_frontend_dist = Path(__file__).resolve().parent.parent / "dashboard-ui" / "dist"
_frontend_available = _frontend_dist.exists()

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to verify API key on all HTTP requests."""
    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket and health endpoints
        if request.url.path in _ws_paths or request.url.path.startswith("/api/health"):
            return await call_next(request)
        key = _get_api_key()
        if key:
            provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if not provided or not secrets.compare_digest(provided, key):
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
        return await call_next(request)

app.add_middleware(APIKeyMiddleware)

# Register all routers
for r in [overview, strategies, positions, orders, trades, pnl, market_data,
          risk, health, replay, reconciliation, alerts, settings, audit_log, indicators]:
    app.include_router(r.router)

# Register analytics router
if analytics_routes:
    app.include_router(analytics_routes.router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, client_id: Optional[str] = None, api_key: Optional[str] = None):
    # Verify WebSocket API key (from query param)
    key = _get_api_key()
    if key:
        if not api_key or not secrets.compare_digest(api_key, key):
            await websocket.close(code=4001, reason="Invalid API key")
            return
    await websocket.accept()
    cid = client_id or f"client_{uuid.uuid4().hex[:8]}"
    ws_manager.connect(cid, websocket, ["all"])
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                if action == "subscribe":
                    channels = msg.get("channels", ["all"])
                    ws_manager.subscribe(cid, channels)
                elif action == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "ts": time.time()}))
                elif action == "command":
                    await _handle_command(msg, websocket)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(cid)


async def _handle_command(msg: dict, websocket: WebSocket):
    """Handle control commands from dashboard."""
    cmd = msg.get("command")
    params = msg.get("params", {})
    if not _engine:
        await websocket.send_text(json.dumps({"type": "error", "data": "Engine not running"}))
        return

    result = {"command": cmd, "success": False, "data": None}
    try:
        if cmd == "pause_strategy":
            sid = params.get("strategy_id")
            if sid and sid in _engine.strategies:
                from strategies.base_dema_strategy import StrategyState
                _engine.strategies[sid].state = StrategyState.FLAT
                _engine.strategies[sid].position_side = None
                _engine.strategies[sid].stop_price = None
                _engine.strategies[sid].pending_entry = None
                result["success"] = True
                event_bus.publish("strategy_control", {"action": "pause", "strategy_id": sid})
        elif cmd == "resume_strategy":
            sid = params.get("strategy_id")
            if sid and sid in _engine.strategies:
                result["success"] = True
                event_bus.publish("strategy_control", {"action": "resume", "strategy_id": sid})
        elif cmd == "emergency_stop":
            for sid, strat in _engine.strategies.items():
                event_bus.publish("emergency_stop", {"strategy_id": sid})
            result["success"] = True
        elif cmd == "get_snapshot":
            result["success"] = True
            result["data"] = _engine.snapshot()
        elif cmd == "get_trades":
            if _persistence:
                result["success"] = True
                result["data"] = _persistence.get_trades()
    except Exception as e:
        result["data"] = str(e)
    await websocket.send_text(json.dumps({"type": "command_result", "data": result}, default=str))


@app.get("/api/health")
async def api_health():
    return {
        "status": "ok",
        "engine": _engine is not None,
        "persistence": _persistence is not None,
        "ws_connections": ws_manager.active_connections,
        "event_bus": event_bus.get_stats(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# Frontend catch-all AFTER all API routes
if _frontend_available:
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=404)
        file_path = _frontend_dist / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_frontend_dist / "index.html"))
