"""Core engine package."""
from .timeframe_engine import Bar, BarState, TIMEFRAMES
from .risk_engine import RiskEngine
from .fill_dedup import FillDeduplicator

# TradeCloseManager imported lazily to avoid circular import with execution.paper_broker
def _lazy_trade_close():
    from .trade_close import TradeCloseManager
    return TradeCloseManager

__all__ = [
    "Bar",
    "BarState",
    "TIMEFRAMES",
    "RiskEngine",
    "FillDeduplicator",
]
