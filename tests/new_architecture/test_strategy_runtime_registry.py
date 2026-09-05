"""StrategyRuntimeRegistry (Phase 3, service-registry contract)."""
import pytest

from strategies.runtime import StrategyRuntime, StrategyRuntimeRegistry


def _rt(sid):
    return StrategyRuntime(strategy_id=sid)


def test_register_and_require():
    reg = StrategyRuntimeRegistry()
    reg.register(_rt("gold_01"))
    assert reg.require("gold_01").strategy_id == "gold_01"
    assert reg["gold_01"].strategy_id == "gold_01"


def test_duplicate_register_raises():
    reg = StrategyRuntimeRegistry()
    reg.register(_rt("gold_01"))
    with pytest.raises(ValueError):
        reg.register(_rt("gold_01"))


def test_register_or_replace_allows_dup():
    reg = StrategyRuntimeRegistry()
    reg.register(_rt("gold_01"))
    reg.register_or_replace(_rt("gold_01"))
    assert len(reg.all()) == 1


def test_require_unknown_raises():
    reg = StrategyRuntimeRegistry()
    with pytest.raises(KeyError):
        reg.require("nope")


def test_strategy_ids_and_snapshot():
    reg = StrategyRuntimeRegistry()
    for sid in ("gold_01", "gold_02", "silver_01", "silver_02"):
        reg.register(_rt(sid))
    assert reg.strategy_ids == ["gold_01", "gold_02", "silver_01", "silver_02"]
    assert reg.snapshot()["strategy_ids"] == reg.strategy_ids
    assert reg.snapshot()["runtimes"]["gold_01"] == "gold_01"