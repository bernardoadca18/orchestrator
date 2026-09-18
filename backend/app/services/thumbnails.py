"""Workflow-owned package history and explicit human selection."""

import datetime as dt
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from app.clients.media_indexer_search import MediaIndexerError, MediaIndexerSearchProvider
from app.models.workflow import WorkflowExecucao, WorkflowThumbnailPackage
from app.schemas.thumbnail import STRATEGY, ThumbnailRequest
from app.services.content_locale import workflow_content_locale
from app.services.thumbnail_identity import digest
from app.services.visual_director_service import VisualDirectorInputError, VisualDirectorService


def build_request(workflow, scope, revision):
    snapshot = workflow.roteiro_snapshot
    title = snapshot.get("titulo") or snapshot.get("title")
    if not title:
        raise ValueError("Frozen script title is required")
    locale = workflow_content_locale(workflow)
    body = dict(
        schema_version="thumbnail_request_v1",
        workflow_id=str(workflow.id),
        project_id=str(scope.project_id),
        script_identity=digest({"script_id": str(workflow.roteiro_id), "snapshot": snapshot}),
        content_locale=locale,
        channel_profile="seis_estrelas_pt_v1" if locale == "pt-BR" else "six_stars_en_v1",
        video_title=title,
        script={
            "title": title,
            "blocks": [
                {
                    "order": b.get("ordem", i),
                    "type": b.get("tipo", "narration"),
                    "content": b["conteudo"],
                }
                for i, b in enumerate(snapshot.get("blocos", []))
            ],
        },
        media_scope={
            "project_id": str(scope.project_id),
            "session_ids": sorted(str(s) for s in scope.session_ids),
            "static_images": scope.static_images.to_dict()
            if scope.static_images
            else {
                "enabled": True,
                "asset_classes": [],
                "collections": [],
                "source_owners": [],
                "source_classes": [],
            },
        },
        candidate_count=3,
        creative_revision=revision,
        strategy_version="thumbnail_director_v2",
        concept_prompt_version="thumbnail_concepts_v2",
        template_set_version="thumbnail_templates_v1",
        renderer_version="libvips_thumbnail_v1",
    )
    return ThumbnailRequest(**body, request_fingerprint=digest(body)).model_dump(mode="json")


def candidate_for(package, candidate_id):
    if package is None or package.status != "completed" or not package.result:
        raise HTTPException(409, "Package de thumbnails ainda não concluído")
    candidate = next(
        (c for c in package.result["candidates"] if c["candidate_id"] == candidate_id), None
    )
    if not candidate or candidate["status"] != "available" or not candidate.get("artifact"):
        raise HTTPException(404, "Candidate não disponível neste package")
    return candidate


def projection(package):
    result = {
        key: getattr(package, key)
        for key in (
            "id",
            "request_fingerprint",
            "creative_revision",
            "status",
            "result",
            "selected_candidate_id",
            "error_category",
            "error_message",
            "created_at",
            "completed_at",
            "selected_at",
        )
    }

    result["requires_regeneration"] = bool(
        package.result and package.result.get("strategy_version") != STRATEGY
    )
    return result


class ThumbnailService:
    def __init__(self, session):
        self.session = session

    async def workflow(self, workflow_id, lock=False):
        query = select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id)
        if lock:
            query = query.with_for_update()
        item = await self.session.scalar(query)
        if item is None:
            raise HTTPException(404, "Workflow não encontrado")
        return item

    async def current(self, workflow_id):
        return await self.session.scalar(
            select(WorkflowThumbnailPackage)
            .where(WorkflowThumbnailPackage.workflow_execucao_id == workflow_id)
            .order_by(WorkflowThumbnailPackage.creative_revision.desc())
            .limit(1)
        )

    async def ensure(self, workflow_id, regenerate=False):
        workflow = await self.workflow(workflow_id, lock=True)
        previous = await self.current(workflow_id)
        if previous and not regenerate:
            if previous.status == "failed":
                previous.status = "pending"
                previous.error_category = previous.error_message = None
                previous.completed_at = None
            await self.session.commit()
            return previous
        if previous and previous.status in {"pending", "running"}:
            raise HTTPException(409, "Geração atual ainda em andamento")
        queries = [
            SimpleNamespace(preferred_session_id=uuid.UUID(q["preferred_session_id"]))
            for b in workflow.roteiro_snapshot.get("blocos", [])
            for q in b.get("visual_queries", [])
            if q.get("preferred_session_id")
        ]
        try:
            scope = VisualDirectorService(self.session)._scope(workflow.roteiro_snapshot, queries)
            if scope is None:
                raise ValueError("Workflow has no frozen media scope")
            project = scope.project_id
            # Legacy scopes may contain session IDs only; resolve through the public API.
            provider = MediaIndexerSearchProvider()
            for session_id in scope.session_ids if project is None else ():
                metadata = await provider.get_session(session_id)
                if project and metadata.project_id != project:
                    raise ValueError("Sessions must belong to the scoped project")
                project = metadata.project_id
            if project is None:
                raise ValueError("Media project is required")
            from dataclasses import replace

            scope = replace(scope, project_id=project)
            snapshot = build_request(
                workflow, scope, previous.creative_revision + 1 if previous else 0
            )
        except MediaIndexerError as exc:
            raise HTTPException(503, "Media Indexer indisponível para validar o escopo") from exc
        except (ValueError, KeyError, VisualDirectorInputError) as exc:
            raise HTTPException(
                422,
                "Snapshot congelado precisa de título, blocos, locale e escopo de mídia válidos",
            ) from exc
        package = WorkflowThumbnailPackage(
            workflow_execucao_id=workflow_id,
            request_fingerprint=snapshot["request_fingerprint"],
            creative_revision=snapshot["creative_revision"],
            request_snapshot=snapshot,
            status="pending",
        )
        self.session.add(package)
        await self.session.commit()
        await self.session.refresh(package)
        return package

    async def select(self, workflow_id, candidate_id, package_id=None):
        await self.workflow(workflow_id, lock=True)
        package = await self.current(workflow_id)
        if package_id and (package is None or package.id != package_id):
            raise HTTPException(409, "As opções mudaram; atualize antes de selecionar")
        candidate_for(package, candidate_id)
        if package.result.get("strategy_version") != STRATEGY:
            raise HTTPException(
                409, "Gere novas opções para corrigir a correspondência entre texto e imagem"
            )
        package.selected_candidate_id = candidate_id
        package.selected_at = dt.datetime.now(dt.UTC)
        await self.session.commit()
        return package
