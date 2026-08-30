"""Indicators routes - live indicator values, HTF mapping debug."""
from __future__ import annotations
import asyncio
import time
from typing import Optional
from fastapi import APIRouter
from dashboard.routes.strategies import _with_flat_indicators
router = APIRouter()
_engine = None

def init(engine, event_bus):
    global _engine
    _engine = engine

def _get_all_indicators_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        result = {}
        for key, ind in _engine.indicators.items():
            result[key] = _with_flat_indicators(ind.snapshot())
        return {"indicators": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/indicators")
async def get_all_indicators():
    return await asyncio.to_thread(_get_all_indicators_sync)

def _get_instrument_indicators_sync(instrument: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        inst = instrument.upper()
        result = {}
        for key, ind in _engine.indicators.items():
            if key.startswith(inst + ":"):
                result[key] = _with_flat_indicators(ind.snapshot())
        return {"instrument": inst, "indicators": result}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/indicators/{instrument}")
async def get_instrument_indicators(instrument: str):
    return await asyncio.to_thread(_get_instrument_indicators_sync, instrument)

def _get_htf_state_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        return {"htf": _engine.htf_engine.snapshot(), "timestamp": time.time()}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/htf")
async def get_htf_state():
    return await asyncio.to_thread(_get_htf_state_sync)

def _get_instrument_htf_sync(instrument: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        inst = instrument.upper()
        htf_snap = _engine.htf_engine.snapshot()
        result = {}
        for key, val in htf_snap.items():
            if key.startswith(inst + ":"):
                result[key] = val
        return {"instrument": inst, "htf": result}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/htf/{instrument}")
async def get_instrument_htf(instrument: str):
    return await asyncio.to_thread(_get_instrument_htf_sync, instrument)
