"""Silver strategy instances."""
from strategies.base_dema_strategy import BaseDEMAStrategy


class SilverStrategy01(BaseDEMAStrategy):
    """Silver Strategy 01 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "silver_01"),
            instrument=kwargs.pop("instrument", "SILVERM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class SilverStrategy02(BaseDEMAStrategy):
    """Silver Strategy 02 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "silver_02"),
            instrument=kwargs.pop("instrument", "SILVERM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class SilverStrategy03(BaseDEMAStrategy):
    """Silver Strategy 03 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "silver_03"),
            instrument=kwargs.pop("instrument", "SILVERM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )


class SilverStrategy04(BaseDEMAStrategy):
    """Silver Strategy 04 - 5m/1H DEMA-ATR crossover."""
    def __init__(self, **kwargs):
        super().__init__(
            strategy_id=kwargs.pop("strategy_id", "silver_04"),
            instrument=kwargs.pop("instrument", "SILVERM"),
            fast_timeframe=kwargs.pop("fast_timeframe", "5m"),
            htf_timeframe=kwargs.pop("htf_timeframe", "1h"),
            **kwargs,
        )
