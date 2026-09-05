"""Silver strategy instances — factory functions creating independent StrategyInstances."""
from strategies.instance import StrategyInstance


def create_silver_5m(strategy_id: str = "silver_02", instrument: str = "SILVERM", **kwargs) -> StrategyInstance:
    """SILVERM 5m strategy: fast=5m, mid=15m, slow=1h."""
    return StrategyInstance(
        strategy_id=strategy_id,
        instrument=instrument,
        security_id="483080",
        fast_timeframe="5m",
        mid_timeframe="15m",
        htf_timeframe="1h",
        quantity=kwargs.get("quantity", 1),
        capital=kwargs.get("capital", 300_000.0),
        multiplier=kwargs.get("multiplier", 5.0),
    )


def create_silver_15m(strategy_id: str = "silver_01", instrument: str = "SILVERM", **kwargs) -> StrategyInstance:
    """SILVERM 15m strategy: fast=15m, mid=15m, slow=1h."""
    return StrategyInstance(
        strategy_id=strategy_id,
        instrument=instrument,
        security_id="483080",
        fast_timeframe="15m",
        mid_timeframe="15m",
        htf_timeframe="1h",
        quantity=kwargs.get("quantity", 1),
        capital=kwargs.get("capital", 300_000.0),
        multiplier=kwargs.get("multiplier", 5.0),
    )


# Legacy class wrappers for backward compatibility
class SilverStrategy01:
    def __new__(cls, **kwargs):
        return create_silver_15m(**kwargs)


class SilverStrategy02:
    def __new__(cls, **kwargs):
        return create_silver_5m(**kwargs)


class SilverStrategy03:
    def __new__(cls, **kwargs):
        return create_silver_5m(strategy_id=kwargs.pop("strategy_id", "silver_03"), **kwargs)


class SilverStrategy04:
    def __new__(cls, **kwargs):
        return create_silver_5m(strategy_id=kwargs.pop("strategy_id", "silver_04"), **kwargs)
