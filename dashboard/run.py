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


def main():
    print("=" * 60)
    print("  MCX GoldSilver Trading Dashboard")
    print("  http://localhost:8000")
    print("  Frontend: cd dashboard-ui && npm run dev")
    print("=" * 60)

    uvicorn.run(app, host=os.getenv("DASHBOARD_HOST", "127.0.0.1"), port=8000, log_level="info")


if __name__ == "__main__":
    main()
