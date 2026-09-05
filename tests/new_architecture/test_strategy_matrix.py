"""Strategy Matrix contract (Phase 44-45) — dynamic execution of the route.
"""
import pytest

from dashboard.routes import strategies as strategies_routes
from dashboard.routes import pnl as pnl_routes

from ._harness import SIDS


@pytest.fixture()
def wired(engine):
    """Wire the dashboard route singletons to the live engine."""
    strategies_routes.init(engine, engine.event_bus)
    pnl_routes.init(engine, engine.event_bus, persistence=None)
    yield engine


def test_strategy_matrix_lists_all_four(wired):
    res = strategies_routes._list_strategies_sync()
    assert "error" not in res
    ids = [s["strategy_id"] for s in res["strategies"]]
    assert set(ids) == set(SIDS)
    assert res["count"] == len(SIDS)


def test_strategy_matrix_rows_have_expected_keys(wired):
    res = strategies_routes._list_strategies_sync()
    for row in res["strategies"]:
        for key in ("strategy_id", "instrument", "fast_timeframe",
                    "htf_timeframe", "quantity", "state", "trade_count"):
            assert key in row, f"matrix row missing {key} for {row.get('strategy_id')}"


def test_strategy_matrix_filter_by_instrument(wired):
    res = strategies_routes._list_strategies_sync(instrument="GOLDM")
    assert {s["instrument"] for s in res["strategies"]} == {"GOLDM"}
    res2 = strategies_routes._list_strategies_sync(instrument="SILVERM")
    assert {s["instrument"] for s in res2["strategies"]} == {"SILVERM"}


def test_portfolio_pnl_contract(wired):
    res = pnl_routes._get_portfolio_pnl_sync()
    assert "error" not in res
    assert "portfolio" in res and "by_instrument" in res
    assert set(res["portfolio"].keys()) >= {"equity", "starting_capital", "realized_pnl"}