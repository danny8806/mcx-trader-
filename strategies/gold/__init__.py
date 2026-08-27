"""Gold strategy instances."""
from strategies.base_dema_strategy import BaseDEMAStrategy


class GoldStrategy01(BaseDEMAStrategy):
    """Gold Strategy 01 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "gold_01"),
            instrument=kwargs.pop("instrument", "GOLDM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class GoldStrategy02(BaseDEMAStrategy):
    """Gold Strategy 02 - 15m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "gold_02"),
            instrument=kwargs.pop("instrument", "GOLDM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "15m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class GoldStrategy03(BaseDEMAStrategy):
    """Gold Strategy 03 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "gold_03"),
            instrument=kwargs.pop("instrument", "GOLDM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class GoldStrategy04(BaseDEMAStrategy):
    """Gold Strategy 04 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "gold_04"),
            instrument=kwargs.pop("instrument", "GOLDM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )
