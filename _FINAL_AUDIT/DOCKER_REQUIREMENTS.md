# DOCKER REQUIREMENTS — MCX-TRADER (build contract; audit produced NO docker artifacts by design)

Generated 2026-08-30 09:34 UTC. To be executed only after the operator flips to deploy mode.

## Image
- Base: python:3.14-slim (runtime must match local 3.14; no native deps known).
- Non-root user; WORKDIR /app; copy code
  `COPY . /app` with .dockerignore excluding: dashboard-ui/node_modules, dist,
  _FINAL_AUDIT, _audit_*.py, tests, .git, .pytest_cache.
- Install: `pip install --no-cache-dir -r requirements*.txt` (bundle pinned).
- Build the dashboard UI before the image (or multi-stage node -> python).

## Runtime (docker run / compose)
- Port 8000 (HTTP + WS). Healthcheck: GET /api/health, 200 -> healthy.
- Volumes (persistent, NOT baked):
  - /app/data/db  (sqlite + system_state + dhan_token.json)
  - /app/logs
- Secrets via environment:
  - DHAN_CLIENT_ID, DHAN_TOKEN_PATH (default data/db/dhan_token.json mounted),
    DASHBOARD_API_KEY (optional; unset keeps admin open), TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID.
- Config: config/settings.json read at runtime; ${VAR} placeholders resolved from env
  (already used for telegram + dashboard).
- Command: `python -m dashboard.server` root (or uvicorn main:app) + engine starts on
  boot; keep healthcheck liveness separate from engine readiness.
- Restart policy: unless-stopped; log rotation; timezone mount Asia/Kolkata for forensics.

## Security notes
- Never bake dhan_token.json or API keys into the image.
- If DASHBOARD_API_KEY is set in prod, HTTPS/TLS is REQUIRED (middleware sends 403 on
  plain x-api-key over cleartext otherwise).
- Keep `kill_switch_enabled`, max_daily_loss etc. as env-overridable config; set real
  values before any funded live trading.
