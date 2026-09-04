# DOCKER BUILD REQUIREMENTS

**Date:** 2026-08-27
**Git:** master @ `41fbf85`

---

## SYSTEM REQUIREMENTS

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.14.6 | Backend runtime |
| Node.js | v24.16.0 | Frontend build only |
| FastAPI | Latest | Backend framework |
| Vite | Latest | Frontend bundler |
| SQLite | 3.x | Database (WAL mode) |

---

## PORTS

| Service | Port | Protocol |
|---------|------|----------|
| FastAPI Backend | 8000 | HTTP |
| Dhan WebSocket | 30000 | WSS (outbound) |

---

## ENVIRONMENT VARIABLES (Required in Docker)

| Variable | Source | Purpose |
|----------|--------|---------|
| `DHAN_ACCESS_TOKEN` | settings.json → move to env | Dhan API authentication |
| `DHAN_CLIENT_ID` | settings.json → move to env | Dhan client identifier |
| `TOTP_SECRET` | settings.json → move to env | TOTP generation for auth |
| `TRADING_PIN` | settings.json → move to env | Trading PIN |
| `BOT_TOKEN` | settings.json → move to env | Telegram bot token |

---

## DATABASE FILES

| File | Tables | Notes |
|------|--------|-------|
| `trading.db` | trades, orders, fills, account_snapshots, events | Primary database, WAL mode |
| `trading.db` | fill_dedup | Fill deduplication table |
| `analytics.db` | — | Secondary analytics (if used) |

---

## SECURITY IDs

| Instrument | Security ID |
|------------|-------------|
| GOLDM-Sep26 | `563946` |
| SILVERM-Nov26 | `483080` |

---

## STRATEGY CONFIGURATION

| Strategy | Capital |
|----------|---------|
| gold_01 | Rs 3,00,000 |
| gold_02 | Rs 3,00,000 |
| silver_01 | Rs 3,00,000 |
| silver_02 | Rs 3,00,000 |
| **Total** | **Rs 12,00,000** |

---

## DOCKERFILE GUIDELINES

### Backend (Python)
```
- Base image: python:3.14-slim
- Copy: backend/, trading_engine/, settings.json
- Install: pip install -r requirements.txt
- Expose: 8000
- CMD: uvicorn app:app --host 0.0.0.0 --port 8000
```

### Frontend (Node build → nginx serve)
```
- Build stage: node:24-alpine → npm run build
- Serve stage: nginx:alpine → copy dist/
- Expose: 3000 (or mapped to host)
```

### Combined (Optional)
```
- Multi-stage: node build → python backend → nginx frontend
- Or: separate containers with docker-compose
```

---

## BUILD COMMANDS

```bash
# Backend dependencies
pip install -r requirements.txt

# Frontend build
cd dashboard-ui && npm install && npm run build

# Tests (verify before Docker build)
pytest --tb=short -q   # 557 tests
```

---

## FILES TO INCLUDE IN DOCKER CONTEXT

| Path | Type | Required |
|------|------|----------|
| `*.py` | Backend source | Yes |
| `dashboard-ui/` | Frontend source | Yes |
| `dashboard-ui/package.json` | Node deps | Yes |
| `dashboard-ui/src/**` | Frontend source | Yes |
| `settings.json` | Configuration | Yes |
| `requirements.txt` | Python deps | Yes |
| `.gitignore` | Git ignore rules | No |
| `trading.db` | Database | Optional (for persistent state) |

---

## FILES TO EXCLUDE FROM DOCKER CONTEXT

| Path | Reason |
|------|--------|
| `__pycache__/` | Bytecode cache |
| `.pytest_cache/` | Test cache |
| `dashboard-ui/dist/` | Build artifacts (rebuilt in Docker) |
| `*.db-shm`, `*.db-wal` | SQLite temp files |
| `audit_*.md` | Documentation |
| `test_*.py` (standalone) | Not part of production |
| `node_modules/` | Installed in container |

---

## PERSISTENT VOLUME MOUNTS

| Container Path | Host Path | Purpose |
|----------------|-----------|---------|
| `/app/data/trading.db` | `./data/trading.db` | Database persistence |
| `/app/data/analytics.db` | `./data/analytics.db` | Analytics persistence |
| `/app/logs/` | `./logs/` | Application logs |
