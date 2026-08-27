"""Overview routes - portfolio summary, gold/silver panels."""
from __future__ import annotations
import asyncio
import time
from typing import Any, Optional
from fastapi import APIRouter

router = APIRouter()

_engine = None
_bus = None


def init(engine, event_bus):
    global _engine, _bus
    _engine = engine
    _bus = event_bus


def _ts():
    return time.time()


def _safe(val, default=0.0):
    return float(val) if val is not None else default


def _get_overview_sync():
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        e = _engine
        account = e.account_engine.snapshot()
        positions = e.position_manager.snapshot()
        open_pos = positions.get("open_positions", {})
        orders_snap = e.execution_engine.snapshot()
        risk_snap = e.risk_engine.snapshot()
        strategies_snap = {name: s.snapshot() for name, s in e.strategies.items()}

        realized = sum(
            pnl.snapshot().get("realized_net", 0) for pnl in e.pnl_engines.values()
        )
        unrealized = sum(
            pos.get("unrealized_pnl", 0) for pos in open_pos.values()
        )

        equity = _safe(account.get("equity", 0))
        starting = _safe(account.get("starting_capital", 0))
        net_pnl = equity - starting

        return {
            "total_equity": {"value": equity, "timestamp": _ts()},
            "starting_capital": {"value": starting, "timestamp": _ts()},
            "today_pnl": {"value": risk_snap.get("daily_pnl", 0), "timestamp": _ts()},
            "total_net_pnl": {"value": net_pnl, "timestamp": _ts()},
            "realized_pnl": {"value": realized, "timestamp": _ts()},
            "unrealized_pnl": {"value": unrealized, "timestamp": _ts()},
            "margin_used": {"value": _safe(account.get("used_margin", 0)), "timestamp": _ts()},
            "available_margin": {"value": _safe(equity) - _safe(account.get("used_margin", 0)), "timestamp": _ts()},
            "open_positions_count": {"value": len(open_pos), "timestamp": _ts()},
            "active_orders_count": {"value": orders_snap.get("orders_count", 0), "timestamp": _ts()},
            "active_strategies_count": {"value": len(strategies_snap), "timestamp": _ts()},
            "kill_switch": {"value": risk_snap.get("kill_switch_active", False), "timestamp": _ts()},
            "strategies": strategies_snap,
            "positions": open_pos,
            "account": account,
        }
    except Exception as e:
        return {"error": str(e), "timestamp": _ts()}


@router.get("/api/overview")
async def get_overview():
    return await asyncio.to_thread(_get_overview_sync)


def _get_instrument_overview_sync(instrument: str):
    if not _engine:
        return {"error": "Engine not initialized"}
    try:
        e = _engine
        instrument_upper = instrument.upper()

        prices = e.execution_engine._current_prices
        ltp = prices.get(instrument_upper, 0.0)

        strat_summaries = []
        for name, strat in e.strategies.items():
            if strat.instrument != instrument_upper:
                continue
            snap = strat.snapshot()
            pnl_eng = e.pnl_engines.get(name)
            pnl_snap = pnl_eng.snapshot() if pnl_eng else {}
            strat_summaries.append({
                "strategy_id": name,
                "status": snap.get("state", "unknown"),
                "fast_timeframe": getattr(strat, "fast_timeframe", ""),
                "htf_timeframe": getattr(strat, "htf_timeframe", ""),
                "position_side": snap.get("position_side"),
                "stop_price": snap.get("stop_price"),
                "bars_processed": snap.get("bars_processed", 0),
                "pending_entry": snap.get("pending_entry"),
                "trades": pnl_snap.get("trade_count", 0),
                "win_rate": pnl_snap.get("win_rate", 0),
                "net_pnl": pnl_snap.get("realized_net", 0),
            })

        return {
            "instrument": instrument_upper,
            "ltp": ltp,
            "spread": 0.0,
            "strategies": strat_summaries,
            "timestamp": _ts(),
        }
    except Exception as e:
        return {"error": str(e), "timestamp": _ts()}


@router.get("/api/overview/{instrument}")
async def get_instrument_overview(instrument: str):
    return await asyncio.to_thread(_get_instrument_overview_sync, instrument)
