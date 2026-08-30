"""Main entry point for Gold Silver Live Trading System.

Startup state machine:
  INITIALIZING → RESTORING → RECONCILING → WARMING_UP → READY → TRADING

Shutdown:
  STOPPED → persist → exit
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR.parent))

_PKG_NAME = _PKG_DIR.name.replace(" ", "_")

import importlib
_engine_mod = importlib.import_module(f"{_PKG_NAME}.trading_engine")
_persist_mod = importlib.import_module(f"{_PKG_NAME}.persistence")

TradingEngine = _engine_mod.TradingEngine
PersistenceManager = _persist_mod.PersistenceManager


def main():
    print("=" * 60)
    print("Gold Silver Live Trading System")
    print("=" * 60)

    persistence = PersistenceManager(
        state_path=str(_PKG_DIR / "data" / "db" / "system_state.json"),
        db_path=str(_PKG_DIR / "data" / "db" / "trading.db"),
    )

    engine = TradingEngine(config_path=str(_PKG_DIR / "config" / "settings.json"))
    engine.set_persistence(persistence)

    # Restore state (sets engine status to RESTORING)
    saved_state = persistence.load_state()
    if saved_state:
        print("[System] Restoring state from last session...")
        engine.restore(saved_state)
        print("[System] State restored")
    else:
        print("[System] No saved state — fresh start")

    def shutdown(signum, frame):
        print("\n[System] Shutting down...")
        engine.stop()
        # Final persist before exit
        try:
            state = engine.snapshot()
            persistence.save_state(state)
            persistence.save_account_snapshot_from_state(state)
            print("[System] Final state saved")
        except Exception as e:
            print(f"[System] WARNING: Final state save failed: {e}")
        persistence.close()
        print("[System] Goodbye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    engine.start()
    print("[System] Trading engine started")
    print("[System] Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(60)
            try:
                state = engine.snapshot()
                persistence.save_state(state)
                persistence.save_account_snapshot_from_state(state)
            except Exception as e:
                print(f"[System] WARNING: Periodic state save failed: {e}")
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
