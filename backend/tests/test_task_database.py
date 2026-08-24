from __future__ import annotations

from typing import Any

import pytest

from app.core import database


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        return None


class _TaskEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_task_session_uses_and_disposes_a_fresh_engine_per_task(monkeypatch) -> None:
    engines: list[_TaskEngine] = []
    sessions: list[object] = []

    def create_engine(*args: Any, **kwargs: Any) -> _TaskEngine:
        engine = _TaskEngine()
        engines.append(engine)
        return engine

    def sessionmaker(*args: Any, **kwargs: Any):
        def factory() -> _SessionContext:
            session = object()
            sessions.append(session)
            return _SessionContext(session)

        return factory

    monkeypatch.setattr(database, "create_async_engine", create_engine)
    monkeypatch.setattr(database, "async_sessionmaker", sessionmaker)

    async with database.task_session() as first:
        assert first is sessions[0]
    async with database.task_session() as second:
        assert second is sessions[1]

    assert first is not second
    assert len(engines) == 2
    assert all(engine.disposed for engine in engines)
