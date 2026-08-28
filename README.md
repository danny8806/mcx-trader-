# MCX Gold/Silver Algorithmic Trading System

Automated trading system for MCX GOLDM and SILVERM futures with 4 independent strategies.

## Features

- **4 Strategies**: gold_01, gold_02, silver_01, silver_02
- **Real-time Data**: Dhan API REST + WebSocket
- **Indicators**: DEMA(3) + ATR(6) with HTF confirmation
- **Risk Management**: Per-strategy capital (Rs 3,00,000 each)
- **Dashboard**: Live React frontend with WebSocket updates
- **Telegram Alerts**: Trade notifications to multiple chats
- **Paper Trading**: Simulated execution with real market data

## Quick Start

### Docker (Recommended)

```bash
docker build -t mcx-trader .
docker run -d \
  --name mcx-trader \
  -p 8000:8000 \
  -v ./data:/app/data \
  mcx-trader
```

Dashboard: http://localhost:8000

### Local Development

```bash
# Backend
pip install -r requirements.txt
python dashboard/run.py

# Frontend (separate terminal)
cd dashboard-ui
npm install
npm run dev
```

## Configuration

Edit `config/settings.json`:

- **Dhan API**: client_id, pin, totp_secret
- **Telegram**: bot_token, chat_id (comma-separated for multiple)
- **Instruments**: GOLDM (563946), SILVERM (483080)
- **Strategies**: capital, timeframes, enabled/disabled

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `DHAN_CLIENT_ID` | Dhan broker client ID |
| `TRADING_PIN` | Trading PIN |
| `TOTP_SECRET` | TOTP secret for auto-login |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Chat IDs (comma-separated) |
| `DASHBOARD_API_KEY` | Required key for dashboard HTTP/WebSocket access |
| `DASHBOARD_HOST` | Bind address; defaults to `127.0.0.1` locally (`0.0.0.0` in Docker) |

Set these values through the environment or a deployment secret store. Do not
place tokens, PINs, TOTP seeds, or dashboard keys in `config/settings.json`.

## Architecture

```
Dhan REST API → Candle Fetcher → DEMA/ATR → Strategy → Risk → Order → Fill → P&L
Dhan WebSocket → LTP Feed (live prices only)
SQLite DB → Trade persistence, fill dedup
Telegram → Trade alerts, daily summary
React Dashboard → Live monitoring
```

## Server Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 2 GB | 4 GB |
| Storage | 10 GB SSD | 20 GB SSD |
| Network | 1 Mbps | 10 Mbps |

## License

Private - Paper Trading Only
