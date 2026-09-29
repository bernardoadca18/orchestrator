"""Cheap list pages are selected before expensive operator projections."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.workflow_repository import WorkflowRepository
from app.services.operator_console import OperatorConsoleService


@pytest.mark.asyncio
async def test_simple_operator_list_projects_only_requested_page() -> None:
    service = OperatorConsoleService(AsyncSession())
    rows = [SimpleNamespace(id=index) for index in range(3)]
    service.workflows.listar_pagina_operador = AsyncMock(return_value=rows)
    service.workflows.listar = AsyncMock()
    service.summary = AsyncMock(side_effect=lambda row: row.id)

    assert await service.list_summaries(limit=3, offset=20, production_status="completed") == [
        0, 1, 2
    ]
    service.workflows.listar_pagina_operador.assert_awaited_once_with(
        production_status="completed", ordering="updated_desc", limit=3, offset=20
    )
    service.workflows.listar.assert_not_awaited()
    assert service.summary.await_count == 3


@pytest.mark.asyncio
async def test_operator_page_query_keeps_filter_order_and_bounds() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []
    await WorkflowRepository(session).listar_pagina_operador(
        production_status="completed", ordering="updated_desc", limit=10, offset=30
    )
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "production_status" in sql
    assert "ORDER BY" in sql and "atualizado_em DESC" in sql
    assert "LIMIT" in sql and "OFFSET" in sql
