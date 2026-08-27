"""Settings routes."""
from __future__ import annotations
import asyncio
import time
from fastapi import APIRouter
router = APIRouter()
_engine = None

def init(engine, event_bus):
    global _engine
    _engine = engine

def _get_settings_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    return {
        "system": _engine.config.get("system", {}),
        "dhan": {"client_id": _engine.config.get("dhan.client_id", ""), "ws_url": _engine.config.get("dhan.ws_url", "")},
        "instruments": _engine.config.get("instruments", {}),
        "strategies": _engine.config.get("strategies", {}),
        "indicators": _engine.config.get("indicators", {}),
        "risk": _engine.config.get("risk", {}),
        "account": _engine.config.get("account", {}),
        "paper_execution": _engine.config.get("paper_execution", {}),
        "telegram": {
            "bot_token": "***" if _engine.config.get("telegram.bot_token", "") else "",
            "chat_id": _engine.config.get("telegram.chat_id", ""),
            "enabled": _engine.config.get("telegram.enabled", False),
        },
        "timestamp": time.time(),
    }

@router.get("/api/settings")
async def get_settings():
    return await asyncio.to_thread(_get_settings_sync)

def _refresh_settings_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    _engine.config.load()
    return {"status": "refreshed", "timestamp": time.time()}

@router.post("/api/settings/refresh")
async def refresh_settings():
    return await asyncio.to_thread(_refresh_settings_sync)
