"""Option paper trading configuration."""
from __future__ import annotations

# Strategy parameters (from source code)
SL_PERCENT = 0.01
OI_THRESHOLD = 5_000_000
MAX_TRADES_PER_DAY = 2

# Schedule
ENTRY_TIME = "09:30"
RECHECK_TIME = "10:00"
EOD_EXIT_TIME = "15:15"

# Instruments
INSTRUMENTS = {
    "NIFTY": {"scrip": 13, "lot_size": 65, "exchange": "NSE_FNO"},
    "SENSEX": {"scrip": 51, "lot_size": 20, "exchange": "BSE_FNO"},
}

# Expiry days: Wednesday=2, Thursday=3
EXPIRY_DAYS = [2, 3]
