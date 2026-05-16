"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from app.api import auth, tables, ws
from app.core.config import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    redis_client = None

    # When persistence is enabled, install the Redis-backed OAuth state store
    # so the PKCE flow works across multiple processes. Redis is connected
    # lazily by the client; if it's unreachable, OAuth flows will fail when
    # they try to put/pop state, but the app stays up.
    if settings.persistence_enabled:
        try:
            import redis.asyncio as redis_async

            from app.api import auth as auth_module
            from app.services.oauth import RedisStateStore
            redis_client = redis_async.from_url(settings.redis_url)
            auth_module.set_state_store(RedisStateStore(redis_client))
            log.info("OAuth state store: Redis at %s", settings.redis_url)
        except Exception:
            log.exception("failed to install Redis state store; OAuth across "
                          "multiple processes will fail")

    # Rehydrate active tables from DB if persistence is enabled.
    from app.services.recovery import recover_tables
    try:
        await recover_tables()
    except Exception:
        log.exception("recovery failed at startup")

    yield

    # Shutdown: close the Redis client if we opened one.
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            log.exception("error closing Redis client on shutdown")


# Path prefixes the SPA fallback must NOT intercept. Each backend
# router contributes to this list; keep them in sync if you add new
# top-level routers.
_API_PREFIXES = ("api/", "auth/", "ws/")
_API_EXACT = {"health"}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Spaces Poker", version="0.1.0", lifespan=lifespan)

    # CORS is only meaningful in dev (frontend on :5173, backend on :8000).
    # In single-origin production deployment, CORS isn't needed. Configure
    # via env if you ever split origins again.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # API routes. The /api prefix exists to keep the namespace separate
    # from SPA client-side routes (so a hypothetical "/tables/ABC" client
    # route doesn't collide with the JSON endpoint). /auth/* and /ws/*
    # stay at the root because the OAuth callback URL is registered
    # externally with X — moving it would require re-registering — and
    # because /ws/* is a long-standing WebSocket convention.
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(tables.router, prefix="/api/tables", tags=["tables"])
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # Keep a /health alias at the root for health checks (Docker, ELB).
    @app.get("/health")
    async def health_root():
        return {"status": "ok"}

    # Serve the built frontend, if present. The frontend dir is configurable
    # via STATIC_DIR; defaults to ../../frontend/dist relative to the backend
    # source tree (the convention used by the dev build).
    #
    # In Docker, the multi-stage build copies the dist into /app/static.
    # In dev, leave STATIC_DIR unset — the frontend runs from `vite dev`
    # on its own port and the backend doesn't serve any HTML.
    static_dir_env = os.environ.get("STATIC_DIR")
    if static_dir_env:
        static_dir = Path(static_dir_env)
    else:
        # Best-effort default. If this doesn't exist, the mount below will
        # raise on app startup, which is the right failure mode in prod.
        static_dir = Path(__file__).resolve().parent.parent.parent / "static"

    if static_dir.exists():
        # Mount /assets/* (Vite's hashed asset bundle) and serve index.html
        # for the bare root. Everything else goes through the SPA fallback
        # below so React Router can handle client-side routes.
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets", StaticFiles(directory=assets_dir), name="assets",
            )

        index_html = static_dir / "index.html"
        if not index_html.exists():
            log.warning("static_dir %s exists but has no index.html", static_dir)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            """Return index.html for any unmatched route so React Router
            can take over client-side. Excludes API prefixes — those
            should return JSON 404s, not the SPA shell."""
            # Reject anything that should have hit a real backend route.
            if (
                full_path.startswith(_API_PREFIXES)
                or full_path in _API_EXACT
                or full_path.startswith("assets/")
            ):
                raise HTTPException(404)
            # Serve a real asset if it happens to exist at the static root
            # (favicon.ico, robots.txt, manifest.json, etc.).
            candidate = static_dir / full_path
            if candidate.is_file() and candidate.resolve().is_relative_to(
                static_dir.resolve(),
            ):
                return FileResponse(candidate)
            # Otherwise it's a client-side route — serve the SPA shell.
            return FileResponse(index_html)
    else:
        log.info("no STATIC_DIR found at %s; SPA not served by backend", static_dir)

    return app


app = create_app()
