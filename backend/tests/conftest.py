from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models.workflow import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def write_tone():
    def write(
        path: Path,
        *,
        sample_rate: int = 24_000,
        duration: float = 1.0,
        amplitude: float = 0.08,
        frequency: float = 220.0,
        channels: int = 1,
    ) -> Path:
        time = np.arange(round(sample_rate * duration), dtype=np.float64) / sample_rate
        samples = amplitude * np.sin(2 * np.pi * frequency * time)
        if channels > 1:
            samples = np.repeat(samples[:, None], channels, axis=1)
        sf.write(path, samples, sample_rate, format="WAV", subtype="PCM_16")
        return path

    return write


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session for unit tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session

    await engine.dispose()
