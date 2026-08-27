"""Dashboard launcher - starts the FastAPI server."""
from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

import uvicorn
from dashboard.server import app, event_bus, set_engine, _on_engine_event
from dashboard.routes import overview, strategies, positions, orders, trades
from dashboard.routes import pnl, market_data, risk, health, replay
from dashboard.routes import reconciliation, alerts, settings, audit_log, indicators


def _start_engine_background(engine):
    try:
        engine.start()
        print("[Dashboard] TradingEngine started with Dhan live feed")
    except Exception as e:
        print(f"[Dashboard] Warning: Engine start failed: {e}")


def main():
    engine = None
    persistence = None

    try:
        from persistence.manager import PersistenceManager
        persistence = PersistenceManager()
    except Exception:
        pass

    try:
        from trading_engine import TradingEngine
        engine = TradingEngine(event_callback=_on_engine_event)
        if persistence:
            engine.set_persistence(persistence)
    except Exception as e:
        print(f"[Dashboard] Warning: Could not init TradingEngine: {e}")

    set_engine(engine)

    for mod in [overview, strategies, positions, orders, trades,
                pnl, market_data, risk, health, replay,
                reconciliation, alerts, settings, audit_log, indicators]:
        try:
            if hasattr(mod, 'init'):
                if persistence and hasattr(mod, 'init') and 'persistence' in mod.init.__code__.co_varnames:
                    mod.init(engine, event_bus, persistence)
                else:
                    mod.init(engine, event_bus)
        except Exception:
            pass

    if hasattr(alerts, 'init'):
        alerts.init(engine, event_bus)
    if hasattr(audit_log, 'init'):
        audit_log.init(engine, event_bus)
    if hasattr(health, 'init'):
        health.init(engine, event_bus)

    if engine:
        import threading
        t = threading.Thread(target=_start_engine_background, args=(engine,), daemon=True)
        t.start()

    print("=" * 60)
    print("  MCX GoldSilver Trading Dashboard")
    print("  http://localhost:8000")
    print("  Frontend: cd dashboard-ui && npm run dev")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
