######################################################################
# Stage 1: build the frontend
######################################################################
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output: /frontend/dist/

######################################################################
# Stage 2: Python deps
######################################################################
FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml ./
RUN pip install --upgrade pip wheel && pip install -e .

######################################################################
# Stage 3: runtime — backend + bundled SPA assets
######################################################################
FROM base AS runtime
RUN useradd -r -u 1000 -m app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY backend/ /app/
# SPA assets — served at / by FastAPI (see app/api/main.py STATIC_DIR).
COPY --from=frontend-build /frontend/dist /app/static
ENV STATIC_DIR=/app/static
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health'); sys.exit(0)" || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
