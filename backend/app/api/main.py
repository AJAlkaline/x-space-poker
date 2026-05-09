"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, tables, ws
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log = __import__("logging").getLogger(__name__)
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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Spaces Poker", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(tables.router, prefix="/tables", tags=["tables"])
    app.include_router(ws.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
