# ============================================================
# STAGE 1: Build frontend with Node.js
# ============================================================
FROM node:24-alpine AS frontend-build

WORKDIR /app/frontend

# Copy package files first (layer caching)
COPY dashboard-ui/package.json dashboard-ui/package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source
COPY dashboard-ui/src ./src
COPY dashboard-ui/index.html ./
COPY dashboard-ui/vite.config.ts ./
COPY dashboard-ui/tsconfig*.json ./
COPY dashboard-ui/.oxlintrc.json ./

# Build production
RUN npm run build

# ============================================================
# STAGE 2: Python runtime + serve
# ============================================================
FROM python:3.14-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY *.py ./
COPY config/ ./config/
COPY core/ ./core/
COPY data/ ./data/
COPY strategies/ ./strategies/
COPY htf/ ./htf/
COPY indicators/ ./indicators/
COPY execution/ ./execution/
COPY portfolio/ ./portfolio/
COPY persistence/ ./persistence/
COPY notifications/ ./notifications/
COPY monitoring/ ./monitoring/
COPY analytics/ ./analytics/
COPY reconciliation/ ./reconciliation/
COPY dashboard/ ./dashboard/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./dashboard-ui/dist

# Create data directory
RUN mkdir -p /app/data

# Environment variables (override via docker run -e)
ENV DHAN_CLIENT_ID=1102461741
ENV TRADING_PIN=107602
ENV TOTP_SECRET=VUQQFLIRDEJ46O2WPXDGNIBULKCJU7FO
ENV TELEGRAM_BOT_TOKEN=8985546227:AAHzsPZ8kJ-kAqk2rncpw8KGmJAz5DreeTQ
ENV TELEGRAM_CHAT_ID=2015223705,1228310685
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "dashboard/run.py"]
