"""Shared test bootstrap for fresh_audit tests."""
from __future__ import annotations
import importlib
import sys
from pathlib import Path
from types import ModuleType

_PKG = Path(__file__).resolve().parent.parent

def _make_package(name: str, path: Path, is_pkg: bool = True) -> ModuleType:
    mod = ModuleType(name)
    mod.__file__ = str(path / "__init__.py") if is_pkg else str(path)
    mod.__package__ = name
    if is_pkg:
        mod.__path__ = [str(path)]
    sys.modules[name] = mod
    return mod

def _load_module(name: str, file_path: Path, package: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def bootstrap():
    if "gsl" in sys.modules:
        return
    gsl = _make_package("gsl", _PKG)
    ind = _make_package("gsl.indicators", _PKG / "indicators")
    ind.dema = _load_module("gsl.indicators.dema", _PKG / "indicators" / "dema.py", "gsl.indicators")
    ind.atr = _load_module("gsl.indicators.atr", _PKG / "indicators" / "atr.py", "gsl.indicators")
    ind.dema_atr = _load_module("gsl.indicators.dema_atr", _PKG / "indicators" / "dema_atr.py", "gsl.indicators")
    core = _make_package("gsl.core", _PKG / "core")
    core.timeframe_engine = _load_module("gsl.core.timeframe_engine", _PKG / "core" / "timeframe_engine.py", "gsl.core")
    sys.modules["core.timeframe_engine"] = core.timeframe_engine
    core.risk_engine = _load_module("gsl.core.risk_engine", _PKG / "core" / "risk_engine.py", "gsl.core")
    sys.modules["core.risk_engine"] = core.risk_engine
    htf = _make_package("gsl.htf", _PKG / "htf")
    htf.backtest_style_htf = _load_module("gsl.htf.backtest_style_htf", _PKG / "htf" / "backtest_style_htf.py", "gsl.htf")
    sys.modules["htf.backtest_style_htf"] = htf.backtest_style_htf
    strats = _make_package("gsl.strategies", _PKG / "strategies")
    strats.base_dema_strategy = _load_module(
        "gsl.strategies.base_dema_strategy", _PKG / "strategies" / "base_dema_strategy.py", "gsl.strategies",
    )
    sys.modules["strategies.base_dema_strategy"] = strats.base_dema_strategy
