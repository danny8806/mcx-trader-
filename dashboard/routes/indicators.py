"""Indicators routes - live indicator values, HTF mapping debug."""
from __future__ import annotations
import asyncio
import time
from typing import Optional
from fastapi import APIRouter
from dashboard.routes.strategies import _flat_indicator, _with_flat_indicators
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
            if ind is not None:
                result[key] = _with_flat_indicators(_flat_indicator(ind))
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
        sids = [sid for sid, s in _engine.strategies.items()
                if s.instrument.upper() == inst]
        result = {}
        for sid in sids:
            for key, ind in _engine.indicators.items():
                if key.startswith(sid + "_") and ind is not None:
                    result[key] = _with_flat_indicators(_flat_indicator(ind))
        return {"instrument": inst, "indicators": result}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/indicators/{instrument}")
async def get_instrument_indicators(instrument: str):
    return await asyncio.to_thread(_get_instrument_indicators_sync, instrument)

def _strategy_htf_entry(strategy_id: str, strat) -> dict:
    try:
        hts = dict(strat.slow_htf_state.snapshot())
    except Exception:
        return {}
    hts["strategy_id"] = strategy_id
    slow_ind = getattr(strat, "slow_indicator", None)
    if slow_ind is not None:
        slow_flat = _flat_indicator(slow_ind)
        if slow_flat:
            hts["indicator"] = slow_flat
    return hts


def _get_htf_state_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        result = {}
        for sid, strat in _engine.strategies.items():
            result[f"{sid}_{strat.htf_timeframe}"] = _strategy_htf_entry(sid, strat)
        return {"htf": result, "count": len(result), "timestamp": time.time()}
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
        result = {}
        for sid, strat in _engine.strategies.items():
            if strat.instrument.upper() == inst:
                result[f"{sid}_{strat.htf_timeframe}"] = _strategy_htf_entry(sid, strat)
        return {"instrument": inst, "htf": result}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/htf/{instrument}")
async def get_instrument_htf(instrument: str):
    return await asyncio.to_thread(_get_instrument_htf_sync, instrument)
