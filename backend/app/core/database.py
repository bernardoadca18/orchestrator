"""Engine e sessões assíncronas (SQLAlchemy 2.0 + asyncpg)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency do FastAPI para injeção de sessão por request."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """Create a task-local engine bound to the current Celery event loop.

    Celery invokes each synchronous task through a fresh ``asyncio.run``. A
    process-global asyncpg pool would otherwise retain connections owned by a
    closed loop and fail on the second task in the same worker process.
    """
    task_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    task_session_factory = async_sessionmaker(
        bind=task_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with task_session_factory() as session:
            yield session
    finally:
        await task_engine.dispose()
