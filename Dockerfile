# ── Stage 1: Frontend build ──────────────────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ── Stage 2: Python backend ─────────────────────────────────────────────
FROM python:3.12-slim AS backend

WORKDIR /app

# System deps for psycopg binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./src/
COPY main.py server.py ./
COPY --from=frontend-build /app/web/.next ./web/.next
COPY --from=frontend-build /app/web/public ./web/public
COPY --from=frontend-build /app/web/package.json ./web/package.json
COPY --from=frontend-build /app/web/node_modules ./web/node_modules

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
