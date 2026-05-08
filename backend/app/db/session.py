"""Async SQLAlchemy session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _init() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        return
    settings = get_settings()
    _engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    _init()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
