"""Strategy routes - strategy list, details, parameters, enable/disable."""
from __future__ import annotations
import asyncio
import time
from typing import Any, Optional
from fastapi import APIRouter, Query

router = APIRouter()
_engine = None
_bus = None


def init(engine, event_bus):
    global _engine, _bus
    _engine = engine
    _bus = event_bus


def _with_flat_indicators(ind_snap: dict) -> dict:
    """Flatten the persistence-shaped DEMA/ATR snapshot into the field names the
    UI reads (dema_value / atr_value) while KEEPING the raw nested snapshot
    (dema/atr dicts) intact for backward compatibility."""
    out = dict(ind_snap)
    dema = ind_snap.get("dema") or {}
    atr = ind_snap.get("atr") or {}
    dema_val = None
    if dema.get("ema1") is not None and dema.get("ema2") is not None:
        dema_val = 2 * dema["ema1"] - dema["ema2"]
    out["dema_value"] = dema_val
    out["atr_value"] = atr.get("atr")
    return out


def _list_strategies_sync(instrument: Optional[str] = None, status: Optional[str] = None):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        result = []
        for name, strat in _engine.strategies.items():
            snap = strat.snapshot()
            inst = snap.get("instrument", "")
            state = snap.get("state", "unknown")
            if instrument and inst != instrument.upper():
                continue
            if status and state != status:
                continue
            pnl_eng = _engine.pnl_engines.get(name)
            pnl_snap = pnl_eng.snapshot() if pnl_eng else {}
            cfg = _engine.config.strategy(name)
            result.append({
                "strategy_id": name,
                "instrument": inst,
                "fast_timeframe": cfg.get("fast_timeframe", strat.fast_timeframe),
                "htf_timeframe": cfg.get("htf_timeframe", strat.htf_timeframe),
                "quantity": cfg.get("quantity", strat.quantity),
                "enabled": snap.get("enabled", cfg.get("enabled", True)),
                "state": state,
                "position_side": snap.get("position_side"),
                "stop_price": snap.get("stop_price"),
                "pending_entry": snap.get("pending_entry"),
                "bars_processed": snap.get("bars_processed", 0),
                "trade_count": pnl_snap.get("trade_count", 0),
                "wins": pnl_snap.get("wins", 0),
                "losses": pnl_snap.get("losses", 0),
                "win_rate": pnl_snap.get("win_rate", 0),
                "realized_net": pnl_snap.get("realized_net", 0),
                "realized_gross": pnl_snap.get("realized_gross", 0),
                "realized_charges": pnl_snap.get("realized_charges", 0),
            })
        return {"strategies": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/strategies")
async def list_strategies(instrument: Optional[str] = None, status: Optional[str] = None):
    return await asyncio.to_thread(_list_strategies_sync, instrument, status)


def _get_strategy_sync(strategy_id: str):
    if not _engine or strategy_id not in _engine.strategies:
        return {"error": f"Strategy {strategy_id} not found"}
    try:
        strat = _engine.strategies[strategy_id]
        snap = strat.snapshot()
        inst = snap.get("instrument", "")
        pnl_eng = _engine.pnl_engines.get(strategy_id)
        pnl_snap = pnl_eng.snapshot() if pnl_eng else {}
        cfg = _engine.config.strategy(strategy_id)

        fast_key = f"{inst}:{strat.fast_timeframe}"
        fast_ind = _engine.indicators.get(fast_key)
        ind_snap = fast_ind.snapshot() if fast_ind else {}

        htf_snap = _engine.htf_engine.snapshot()
        htf_key = f"{inst}:{strat.htf_timeframe}"
        htf_state = htf_snap.get(htf_key, {})

        positions = _engine.position_manager.get_positions_by_strategy(strategy_id)
        pos_list = []
        for p in positions:
            if not p.is_open:
                continue
            psnap = p.snapshot()
            psnap["entry_price"] = psnap.get("average_entry")
            pos_list.append(psnap)

        return {
            "strategy_id": strategy_id,
            "configuration": {
                "instrument": inst,
                "fast_timeframe": cfg.get("fast_timeframe", strat.fast_timeframe),
                "htf_timeframe": cfg.get("htf_timeframe", strat.htf_timeframe),
                "quantity": cfg.get("quantity", strat.quantity),
                "enabled": snap.get("enabled", cfg.get("enabled", True)),
                "dema_period": _engine.config.get("indicators.dema_period", 3),
                "atr_period": _engine.config.get("indicators.atr_period", 6),
                "atr_factor": _engine.config.get("indicators.atr_factor", 1.0),
                "starting_capital": _engine.config.get("account.starting_capital", 0),
            },
            "current_state": {
                "state": snap.get("state"),
                "position_side": snap.get("position_side"),
                "stop_price": snap.get("stop_price"),
                "pending_entry": snap.get("pending_entry"),
                "bars_processed": snap.get("bars_processed", 0),
            },
            "indicators": _with_flat_indicators(ind_snap),
            "htf": htf_state,
            "performance": pnl_snap,
            "positions": pos_list,
            "snapshot": snap,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    return await asyncio.to_thread(_get_strategy_sync, strategy_id)


def _control_strategy_sync(strategy_id: str, action: str):
    if not _engine or strategy_id not in _engine.strategies:
        return {"error": f"Strategy {strategy_id} not found"}
    try:
        strat = _engine.strategies[strategy_id]
        if action == "pause":
            open_positions = _engine.position_manager.get_positions_by_strategy(strategy_id)
            if any(pos.is_open for pos in open_positions):
                return {"success": False, "strategy_id": strategy_id, "action": action,
                        "error": "Cannot pause a strategy with an open position; close it first."}
            strat.pending_entry = None
            strat.enabled = False
            if _bus:
                _bus.publish("strategy_control", {
                    "strategy_id": strategy_id, "action": action, "timestamp": time.time(),
                })
            return {"success": True, "strategy_id": strategy_id, "action": action}
        if action == "resume":
            strat.enabled = True
            if _bus:
                _bus.publish("strategy_control", {
                    "strategy_id": strategy_id, "action": action, "timestamp": time.time(),
                })
            return {"success": True, "strategy_id": strategy_id, "action": action}
        return {"success": False, "strategy_id": strategy_id, "action": action,
                "error": f"Unknown action '{action}' (expected pause or resume)"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/api/strategies/{strategy_id}/control")
async def control_strategy(strategy_id: str, body: dict):
    action = body.get("action", "")
    return await asyncio.to_thread(_control_strategy_sync, strategy_id, action)


def _get_strategy_parameters_sync(strategy_id: str):
    if not _engine or strategy_id not in _engine.strategies:
        return {"error": f"Strategy {strategy_id} not found"}
    try:
        strat = _engine.strategies[strategy_id]
        inst = strat.instrument
        cfg = _engine.config.strategy(strategy_id)
        inst_cfg = _engine.config.instrument(inst)
        indicators = _engine.config.get("indicators", {})
        paper = _engine.config.get("paper_execution", {})
        risk = _engine.config.get("risk", {})
        charges = _engine.config.get("charges", {}).get(inst, {})

        return {
            "strategy": {
                "fast_timeframe": cfg.get("fast_timeframe", strat.fast_timeframe),
                "htf_timeframe": cfg.get("htf_timeframe", strat.htf_timeframe),
                "quantity": cfg.get("quantity", strat.quantity),
                "enabled": cfg.get("enabled", True),
            },
            "instrument": inst_cfg,
            "indicators": indicators,
            "execution": paper,
            "risk": risk,
            "charges": charges,
            "source": "settings.json",
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/strategies/{strategy_id}/parameters")
async def get_strategy_parameters(strategy_id: str):
    return await asyncio.to_thread(_get_strategy_parameters_sync, strategy_id)
