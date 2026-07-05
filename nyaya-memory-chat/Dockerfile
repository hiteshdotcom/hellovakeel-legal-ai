# ── Stage 1: build the React SPA ────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # tsc --noEmit && vite build → /fe/dist

# ── Stage 2: python runtime that serves API + the built SPA ─────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Python deps first for layer caching. Cognee is intentionally NOT installed
# (MEMORY_BACKEND=local uses the Postgres-native memchat schema).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code + SQL migrations (auto-applied on startup by init_db()).
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY web/ ./web/

# The compiled SPA from stage 1, at the path app/main.py expects.
COPY --from=frontend /fe/dist ./frontend/dist

# Render injects $PORT; bind to it (fallback 8000 for local `docker run`).
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
