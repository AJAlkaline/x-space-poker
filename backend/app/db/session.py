"""Async SQLAlchemy session factory.

Production: reads DATABASE_URL from settings.
Tests: call `set_engine(create_async_engine(...))` before app code runs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def set_engine(engine: AsyncEngine) -> None:
    """Override the engine (used by tests). Must be called before any session is opened."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        _init_default()
    assert _engine is not None
    return _engine


def _init_default() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        return
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )
    set_engine(engine)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the active engine. Commits on success, rolls
    back on exception."""
    if _sessionmaker is None:
        _init_default()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
