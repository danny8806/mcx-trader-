"""Shared deterministic harness for the new-architecture acceptance suite."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from strategies.types import Signal, SignalType

SIDS = ["gold_01", "gold_02", "silver_01", "silver_02"]
INST = {"gold_01": "GOLDM", "gold_02": "GOLDM",
        "silver_01": "SILVERM", "silver_02": "SILVERM"}
PRICE = {"gold_01": 78000.0, "gold_02": 78000.0,
         "silver_01": 239000.0, "silver_02": 239000.0}


def write_config(root: Path) -> Path:
    data = {
        "system": {"name": "ReversAll", "version": "1.0.0", "environment": "paper",
                   "log_level": "INFO",
                   "db_path": str(root / "data" / "db" / "trading.db"),
                   "state_path": str(root / "data" / "db" / "system_state.json")},
        "dhan": {"client_id": "TEST", "access_token": "", "ws_url": "wss://fake",
                 "rest_base": "https://fake",
                 "token_file": str(root / "data" / "db" / "dhan_token.json"),
                 "pin": "", "totp_secret": ""},
        "instruments": {
            "GOLDM": {"symbol": "MCX:GOLDM202610", "security_id": "569003",
                      "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                      "multiplier": 10.0, "tick_size": 1.0, "lot_size": 1,
                      "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                      "margin_model": {"slope": 0.125, "intercept": 126930.0}},
            "SILVERM": {"symbol": "MCX:SILVERM202611", "security_id": "483080",
                        "exchange_segment": "MCX_COMM", "instrument": "FUTCOM",
                        "multiplier": 5.0, "tick_size": 1.0, "lot_size": 1,
                        "session_open": "09:00", "session_close": "23:30", "session_minutes": 870,
                        "margin_model": {"slope": 0.0625, "intercept": 142900.0}},
        },
        "indicators": {"dema_period": 3, "atr_period": 6, "atr_factor": 1.0},
        "strategies": {
            "gold_01": {"instrument": "GOLDM", "fast_timeframe": "5m",
                        "mid_timeframe": "15m", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "gold_02": {"instrument": "GOLDM", "fast_timeframe": "15m",
                        "mid_timeframe": "1h", "htf_timeframe": "1h",
                        "quantity": 1, "capital": 500000, "enabled": True},
            "silver_01": {"instrument": "SILVERM", "fast_timeframe": "15m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 500000, "enabled": True},
            "silver_02": {"instrument": "SILVERM", "fast_timeframe": "5m",
                          "mid_timeframe": "15m", "htf_timeframe": "1h",
                          "quantity": 1, "capital": 500000, "enabled": True},
        },
        "paper_execution": {"slippage_ticks": 0, "latency_ms": 0, "partial_fill_probability": 0},
        "charges": {
            "GOLDM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                      "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                      "gst_pct": 18.0, "stamp_duty_pct": 0.0},
            "SILVERM": {"brokerage_per_side": 20.0, "stt_sell_pct": 0.01,
                        "exchange_pct": 0.0026, "sebi_pct": 0.0001,
                        "gst_pct": 18.0, "stamp_duty_pct": 0.0},
        },
        "risk": {"max_open_positions_per_strategy": 1, "max_open_positions_total": 8,
                 "max_daily_loss": 999999999.0, "max_drawdown_pct": 100.0,
                 "margin_per_trade_pct": 6.5, "kill_switch_enabled": False},
        "account": {"starting_capital": 2000000.0,
                    "starting_capital_per_strategy": 500000.0, "currency": "INR"},
        "telegram": {"bot_token": "", "chat_id": "", "enabled": False},
        "dashboard": {"api_key": ""},
        "execution_mode": "paper",
    }
    cfg_path = root / "settings.json"
    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "db").mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    return cfg_path


def default_signal(strategy_id, signal_type=SignalType.LONG, trigger=None,
                   timestamp=None, signal_id=None, **kwargs) -> Signal:
    trigger = trigger if trigger is not None else PRICE[strategy_id]
    ts = timestamp if timestamp is not None else time.time()
    return Signal(
        signal_type=signal_type, instrument=INST[strategy_id],
        strategy_id=strategy_id, timestamp=ts, trigger_price=trigger,
        stop_price=kwargs.pop("stop_price", 0.0), quantity=kwargs.pop("quantity", 1),
        signal_id=signal_id or f"sig-{strategy_id}-{int(ts * 1000) % 10**6}",
        **kwargs,
    )


def exit_signal(strategy_id, signal_type, price, reason, ts, signal_id) -> Signal:
    sig = default_signal(strategy_id, signal_type=signal_type, trigger=price,
                         timestamp=ts, signal_id=signal_id)
    sig.metadata = {"exit": True, "exit_reason": reason, "exit_price": price}
    return sig


def process(engine, strategy_id, signal_type, held_price, ts) -> None:
    sig = default_signal(strategy_id, signal_type=signal_type, trigger=held_price, timestamp=ts)
    engine.execution_engine.update_price(INST[strategy_id], held_price)
    engine._process_signal(sig)


def open_long(engine, strategy_id, ts) -> None:
    process(engine, strategy_id, SignalType.LONG, PRICE[strategy_id], ts)


def open_short(engine, strategy_id, ts) -> None:
    process(engine, strategy_id, SignalType.SHORT, PRICE[strategy_id], ts)


def positions(engine, strategy_id):
    return engine.position_manager.get_positions_by_strategy(strategy_id)


def open_positions(engine) -> list:
    return engine.position_manager.open_positions


def db_rows(persistence, sql, params=()) -> list[dict]:
    return persistence._db.query(sql, params)