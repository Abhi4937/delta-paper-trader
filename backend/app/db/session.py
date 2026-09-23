"""Async SQLAlchemy engine + session factory + FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session; commits on success, rolls back on error.

    Always declare it as `Depends(get_session, scope="function")`: the default (request)
    scope exits AFTER the response is sent, so a client could read pre-commit data on its
    very next request (e.g. arm a live group right after saving its SL)."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
