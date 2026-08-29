"""FastAPI dashboard server - READ/CONTROL layer over trading engine."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
    _analytics_db = str(Path(__file__).resolve().parent.parent / "data" / "db" / "analytics.db")
    init_analytics_db(_analytics_db)
    analytics_routes.init(_analytics_db)
    print(f"[Analytics] Initialized with db: {_analytics_db}", file=sys.stderr, flush=True)
except Exception as e:
    print(f"[Analytics] Init failed: {e}", file=sys.stderr, flush=True)
    analytics_routes = None

logger = logging.getLogger(__name__)


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


async def _periodic_save_state():
    """Periodically persist engine state to disk (every 60s) for crash recovery."""
    while True:
        try:
            await asyncio.sleep(60)
            if _engine and _persistence:
                import concurrent.futures
                loop = asyncio.get_event_loop()
                state = await loop.run_in_executor(None, _engine.snapshot)
                if state:
                    await loop.run_in_executor(None, _persistence.save_state, state)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[SaveState] Periodic save failed: {e}", file=sys.stderr, flush=True)


_push_executor: concurrent.futures.ThreadPoolExecutor | None = None
_events_executor: concurrent.futures.ThreadPoolExecutor | None = None


async def _push_updates():
    """Background task: periodically push engine state via WebSocket."""
    global _push_executor
    if _push_executor is None:
        import concurrent.futures
        _push_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")
    while True:
        try:
            loop = asyncio.get_running_loop()
            snap = await loop.run_in_executor(_push_executor, _snapshot_sync)
            if snap:
                snap = await loop.run_in_executor(_push_executor, _enrich_strategies, snap)
                await ws_manager.broadcast("engine_state", snap)
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Push update error: {e}")
            await asyncio.sleep(2.0)


async def _push_events():
    """Background task: push recent events via WebSocket."""
    global _events_executor
    if _events_executor is None:
        import concurrent.futures
        _events_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="events")
    last_id = 0
    while True:
        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(_events_executor, event_bus.get_recent, 50)
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
                state_path=str(Path(__file__).resolve().parent.parent / "data" / "db" / "system_state.json"),
                db_path=str(Path(__file__).resolve().parent.parent / "data" / "db" / "trading.db"),
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
    save_task = asyncio.create_task(_periodic_save_state())
    print("[Lifespan] Background tasks started", file=sys.stderr, flush=True)
    yield
    print("[Lifespan] Shutting down", file=sys.stderr, flush=True)
    push_task.cancel()
    events_task.cancel()
    save_task.cancel()
    try:
        await asyncio.gather(push_task, events_task, return_exceptions=True)
    except Exception:
        pass
    # Shutdown thread pool executors
    global _push_executor, _events_executor
    for exc in (_push_executor, _events_executor):
        if exc:
            exc.shutdown(wait=False)
    _push_executor = None
    _events_executor = None
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
            if _persistence:
                _persistence.close()
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

# Register all routers
for r in [overview, strategies, positions, orders, trades, pnl, market_data,
          risk, health, replay, reconciliation, alerts, settings, audit_log, indicators]:
    app.include_router(r.router)

# Register analytics router
if analytics_routes:
    app.include_router(analytics_routes.router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Accept connection (auth gate removed - dashboard open)
    await websocket.accept()
    cid = f"client_{uuid.uuid4().hex[:8]}"
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
                open_positions = _engine.position_manager.get_positions_by_strategy(sid)
                if any(pos.is_open for pos in open_positions):
                    result["data"] = "Cannot pause a strategy with an open position; close it first."
                else:
                    strat = _engine.strategies[sid]
                    strat.pending_entry = None
                    strat.enabled = False
                    result["success"] = True
                    event_bus.publish("strategy_control", {"action": "pause", "strategy_id": sid})
        elif cmd == "resume_strategy":
            sid = params.get("strategy_id")
            if sid and sid in _engine.strategies:
                _engine.strategies[sid].enabled = True
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
