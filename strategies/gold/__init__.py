"""Gold strategy instances — factory functions creating independent StrategyInstances."""
from strategies.instance import StrategyInstance


def create_gold_5m(strategy_id: str = "gold_01", instrument: str = "GOLDM", **kwargs) -> StrategyInstance:
    """GOLDM 5m strategy: fast=5m, mid=15m, slow=1h."""
    return StrategyInstance(
        strategy_id=strategy_id,
        instrument=instrument,
        security_id="569003",
        fast_timeframe="5m",
        mid_timeframe="15m",
        htf_timeframe="1h",
        quantity=kwargs.get("quantity", 1),
        capital=kwargs.get("capital", 300_000.0),
        multiplier=kwargs.get("multiplier", 10.0),
    )


def create_gold_15m(strategy_id: str = "gold_02", instrument: str = "GOLDM", **kwargs) -> StrategyInstance:
    """GOLDM 15m strategy: fast=15m, mid=15m, slow=1h."""
    return StrategyInstance(
        strategy_id=strategy_id,
        instrument=instrument,
        security_id="569003",
        fast_timeframe="15m",
        mid_timeframe="15m",
        htf_timeframe="1h",
        quantity=kwargs.get("quantity", 1),
        capital=kwargs.get("capital", 300_000.0),
        multiplier=kwargs.get("multiplier", 10.0),
    )


# Legacy class wrappers for backward compatibility
class GoldStrategy01:
    def __new__(cls, **kwargs):
        return create_gold_5m(**kwargs)


class GoldStrategy02:
    def __new__(cls, **kwargs):
        return create_gold_15m(**kwargs)


class GoldStrategy03:
    def __new__(cls, **kwargs):
        return create_gold_5m(strategy_id=kwargs.pop("strategy_id", "gold_03"), **kwargs)


class GoldStrategy04:
    def __new__(cls, **kwargs):
        return create_gold_5m(strategy_id=kwargs.pop("strategy_id", "gold_04"), **kwargs)
