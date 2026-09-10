"""Persistence boundary for the durable Stage 17 Composer mirror."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.video_composer_client import ComposerRender
from app.models.workflow import WorkflowRender


class WorkflowRenderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, render_id: UUID) -> WorkflowRender | None:
        return await self.session.get(WorkflowRender, render_id)

    async def get_by_request_fingerprint(self, value: str) -> WorkflowRender | None:
        statement = select(WorkflowRender).where(WorkflowRender.request_fingerprint == value)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_composer_render_id(self, value: str) -> WorkflowRender | None:
        statement = select(WorkflowRender).where(WorkflowRender.composer_render_id == value)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: UUID) -> Sequence[WorkflowRender]:
        statement = (
            select(WorkflowRender)
            .where(WorkflowRender.workflow_execucao_id == workflow_id)
            .order_by(WorkflowRender.created_at.desc())
        )
        return (await self.session.execute(statement)).scalars().all()

    async def get_current(
        self,
        workflow_id: UUID,
        production_timeline_id: UUID,
        render_profile: str,
        mode: str,
    ) -> WorkflowRender | None:
        statement = select(WorkflowRender).where(
            WorkflowRender.workflow_execucao_id == workflow_id,
            WorkflowRender.production_timeline_id == production_timeline_id,
            WorkflowRender.render_profile == render_profile,
            WorkflowRender.mode == mode,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def create_or_get(self, **values: object) -> tuple[WorkflowRender, bool]:
        fingerprint = str(values["request_fingerprint"])
        existing = await self.get_by_request_fingerprint(fingerprint)
        if existing is not None:
            return existing, False
        render = WorkflowRender(**values)
        self.session.add(render)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_request_fingerprint(fingerprint)
            if existing is None:
                raise
            return existing, False
        await self.session.refresh(render)
        return render, True

    async def bind_remote(self, render: WorkflowRender, remote: ComposerRender) -> None:
        render.composer_render_id = remote.id
        await self._copy_remote(render, remote)

    async def bind_remote_job(self, render: WorkflowRender, remote: ComposerRender) -> None:
        await self.bind_remote(render, remote)

    async def sync_remote(self, render: WorkflowRender, remote: ComposerRender) -> None:
        await self._copy_remote(render, remote)

    async def sync_remote_job(self, render: WorkflowRender, remote: ComposerRender) -> None:
        await self.sync_remote(render, remote)

    async def mark_completed(self, render: WorkflowRender, remote: ComposerRender) -> None:
        if remote.status != "completed":
            raise ValueError("only a completed remote render can complete the local mirror")
        await self._copy_remote(render, remote)

    async def _copy_remote(self, render: WorkflowRender, remote: ComposerRender) -> None:
        render.render_profile_identity = remote.render_profile_identity
        render.render_fingerprint = remote.render_fingerprint
        render.manifest_fingerprint = remote.manifest_fingerprint
        render.status = remote.status
        render.phase = remote.phase
        render.progress = remote.progress
        render.frames_rendered = remote.frames_rendered
        render.total_frames = remote.total_frames
        render.metrics = remote.metrics
        render.error_category = remote.error.category if remote.error else None
        render.error_message = remote.error.message if remote.error else None
        if remote.started_at and render.started_at is None:
            render.started_at = remote.started_at
        if remote.completed_at:
            render.completed_at = remote.completed_at
        if remote.output:
            render.output_sha256 = remote.output.sha256
            render.output_byte_size = remote.output.byte_size
            render.output_duration_seconds = remote.output.duration_seconds
            render.output_width = remote.output.width
            render.output_height = remote.output.height
            render.output_fps = remote.output.fps
            render.output_frame_count = remote.output.frame_count
            render.output_video_codec = remote.output.video_codec
            render.output_audio_codec = remote.output.audio_codec
            render.output_audio_sample_rate = remote.output.audio_sample_rate
        await self.session.commit()
        await self.session.refresh(render)

    async def mark_failed(self, render: WorkflowRender, category: str, message: str) -> None:
        render.status = "failed"
        render.phase = "failed"
        render.error_category = category
        render.error_message = message
        render.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def reset_for_explicit_retry(
        self, render: WorkflowRender, *, commit: bool = True
    ) -> None:
        render.status = "pending"
        render.phase = "pending"
        render.progress = 0.0
        render.frames_rendered = 0
        render.error_category = None
        render.error_message = None
        render.started_at = dt.datetime.now(dt.UTC)
        render.completed_at = None
        if commit:
            await self.session.commit()
            await self.session.refresh(render)
        else:
            await self.session.flush()
