"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, tables, ws
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: rehydrate active tables from DB if persistence is enabled.
    from app.services.recovery import recover_tables
    try:
        await recover_tables()
    except Exception:
        # Don't block startup on recovery failure — operator can fix and restart.
        import logging
        logging.getLogger(__name__).exception("recovery failed at startup")
    yield
    # Shutdown: close clients


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
