"""Durable storyboard and asset bindings owned by the production workflow."""

from __future__ import annotations

import uuid
from copy import deepcopy

import httpx

from app.core.config import settings
from app.models.workflow import TimelinePlacement
from app.repositories.production_timeline_repository import ProductionTimelineRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.storyboard import StoryboardError, identity, resolve_storyboard


class StoryboardService:
    def __init__(self, session, config=settings):
        self.session, self.config = session, config
        self.workflows = WorkflowRepository(session)
        self.alignments = NarrationAlignmentService(session, config=config)
        self.timelines = ProductionTimelineRepository(session)

    async def workflow(self, workflow_id):
        wf = await self.workflows.obter_por_id(workflow_id, com_blocos=True, com_transcricao=True)
        if wf is None:
            raise StoryboardError("storyboard_missing", "Workflow not found")
        from app.services.production_capabilities import capabilities_for

        if capabilities_for(wf)["visual_mode"] != "storyboard":
            raise StoryboardError("storyboard_missing", "Workflow does not use storyboard visuals")
        return wf

    async def current(self, workflow_id):
        wf = await self.workflow(workflow_id)
        board = wf.resolved_storyboard
        alignment = await self.alignments.current(workflow_id)
        if (
            not board
            or not alignment
            or alignment.status != "completed"
            or board.get("identity") != identity({k: v for k, v in board.items() if k != "identity"})
            or board["final_audio_sha256"] != wf.audio_final_sha256
            or board["alignment_identity"] != alignment.fingerprint
            or board["script_identity"] != identity(wf.roteiro_snapshot)
        ):
            return None
        return board

    async def ensure(self, workflow_id):
        wf = await self.workflow(workflow_id)
        alignment = await self.alignments.current(workflow_id)
        board = resolve_storyboard(wf, alignment)
        if wf.resolved_storyboard != board:
            wf.resolved_storyboard = board
            await self.session.commit()
        return board

    async def asset_project(self, wf):
        # Persist identity before crossing the service boundary; PUT survives retries.
        if wf.storyboard_media_project_id is None:
            wf.storyboard_media_project_id = uuid.uuid5(wf.id, "storyboard-assets-v1")
            await self.session.commit()
        project_id = wf.storyboard_media_project_id
        async with httpx.AsyncClient(
            base_url=self.config.media_indexer_api_url, timeout=60
        ) as client:
            response = await client.put(
                f"/projects/{project_id}",
                json={
                    "name": f"POV Production {wf.id}",
                    "description": f"storyboard workflow:{wf.id}",
                },
            )
            response.raise_for_status()
        return project_id

    async def upload(self, workflow_id, scene_id, image):
        wf = await self.workflow(workflow_id)
        board = await self.current(workflow_id)
        if not board or scene_id not in {s["id"] for s in board["scenes"]}:
            raise StoryboardError("storyboard_asset_invalid", "Unknown or stale scene")
        if wf.production_status in {"queued", "running"}:
            raise StoryboardError(
                "storyboard_asset_invalid", "Wait for production to pause before changing assets"
            )
        if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise StoryboardError("storyboard_asset_invalid", "Unsupported image MIME")
        data = await image.read(25_000_001)
        if not data or len(data) > 25_000_000:
            raise StoryboardError(
                "storyboard_asset_invalid", "Image must be between 1 byte and 25 MB"
            )
        project_id = await self.asset_project(wf)
        import hashlib
        import json

        sha = hashlib.sha256(data).hexdigest()
        # Binary provenance is immutable. Scene membership lives in the replaceable binding,
        # so one image can intentionally serve several scenes without provenance conflicts.
        provenance = {
            "source_owner": "operator",
            "source_class": "pov_storyboard",
            "asset_class": "pov_scene",
            "declared_metadata": {"workflow_id": str(wf.id)},
        }
        async with httpx.AsyncClient(
            base_url=self.config.media_indexer_api_url, timeout=120
        ) as client:
            response = await client.post(
                f"/projects/{project_id}/images",
                files={"image": (image.filename, data, image.content_type)},
                data={"enqueue_semantic_index": "false", "provenance": json.dumps(provenance)},
            )
            response.raise_for_status()
            asset = response.json()["media_asset"]
        if (
            asset["sha256"] != sha
            or asset["byte_size"] != len(data)
            or asset["project_id"] != str(project_id)
            or asset["media_kind"] != "image"
            or asset["content_type"] not in {"image/png", "image/jpeg", "image/webp"}
        ):
            raise StoryboardError(
                "storyboard_asset_integrity_mismatch", "Ingested image identity differs"
            )
        # Serialize binding changes after the upload; never hold the DB lock during HTTP.
        wf = await self.lock_for_binding(workflow_id, board["identity"])
        bindings = dict(wf.storyboard_bindings or {})
        bindings[scene_id] = {
            "media_id": asset["id"],
            "project_id": asset["project_id"],
            "expected_sha256": sha,
            "expected_byte_size": len(data),
            "content_type": asset["content_type"],
            "width": asset.get("width"),
            "height": asset.get("height"),
            "provenance": provenance,
            "source_filename": image.filename,
            "scene_id": scene_id,
        }
        wf.storyboard_bindings = bindings
        await self._invalidate(wf)
        return await self.view(workflow_id)

    async def remove(self, workflow_id, scene_id):
        wf = await self.workflow(workflow_id)
        if not wf.resolved_storyboard or scene_id not in {
            s["id"] for s in wf.resolved_storyboard["scenes"]
        }:
            raise StoryboardError("storyboard_asset_invalid", "Unknown scene")
        if wf.production_status in {"queued", "running"}:
            raise StoryboardError("storyboard_asset_invalid", "Production is active")
        wf = await self.lock_for_binding(workflow_id, wf.resolved_storyboard["identity"])
        bindings = dict(wf.storyboard_bindings or {})
        bindings.pop(scene_id, None)
        wf.storyboard_bindings = bindings
        await self._invalidate(wf)
        return await self.view(workflow_id)

    async def lock_for_binding(self, workflow_id, board_identity):
        from sqlalchemy import select
        from app.models.workflow import WorkflowExecucao
        wf = (await self.session.execute(select(WorkflowExecucao).where(
            WorkflowExecucao.id == workflow_id
        ).with_for_update().execution_options(populate_existing=True))).scalar_one()
        if wf.production_status in {"queued", "running"} or not wf.resolved_storyboard or wf.resolved_storyboard["identity"] != board_identity:
            raise StoryboardError("storyboard_asset_invalid", "Production changed during upload; retry")
        return wf

    async def _invalidate(self, wf):
        wf.production_status = "blocked"
        wf.production_phase = "storyboard_assets"
        wf.production_error_category = "storyboard_asset_missing" if any(s["id"] not in (wf.storyboard_bindings or {}) for s in wf.resolved_storyboard["scenes"]) else None
        wf.production_error_message = "Aguardando imagens das cenas" if wf.production_error_category else None
        await self.session.commit()

    async def view(self, workflow_id):
        wf = await self.workflow(workflow_id)
        board = await self.current(workflow_id)
        if not board:
            return {
                "status": "stale" if wf.resolved_storyboard else "pending",
                "scenes": [],
                "ready": False,
            }
        result = deepcopy(board)
        bindings = wf.storyboard_bindings or {}
        from app.clients.video_composer_client import VideoComposerClient, VideoComposerError
        from app.services.production_capabilities import capabilities_for
        profile_name = capabilities_for(wf).get("render_profile") or self.config.video_composer_render_profile
        result["render_profile"] = None
        try:
            async with VideoComposerClient(self.config) as composer:
                profiles = await composer.get_render_profiles()
            selected = next((p for p in profiles if p.name == profile_name), None)
            if selected:
                result["render_profile"] = {"name": selected.name, "width": selected.width, "height": selected.height}
        except VideoComposerError:
            pass  # Asset editing remains available; the render phase checks readiness.
        result["ready"] = all(s["id"] in bindings for s in result["scenes"])
        for scene in result["scenes"]:
            scene["asset"] = deepcopy(bindings.get(scene["id"]))
            asset = scene["asset"]
            if asset and not asset.get("width"):
                async with httpx.AsyncClient(base_url=self.config.media_indexer_api_url, timeout=10) as client:
                    try:
                        response = await client.get(f"/media-assets/{asset['media_id']}")
                        response.raise_for_status()
                        metadata = response.json()
                        asset.update(width=metadata.get("width"), height=metadata.get("height"))
                    except httpx.HTTPError:
                        pass
            scene["quality_warnings"] = []
            if asset and asset.get("width") and asset.get("height"):
                width, height = asset["width"], asset["height"]
                if min(width, height) < 720:
                    scene["quality_warnings"].append("low_resolution")
                if max(width / height, height / width) > 3:
                    scene["quality_warnings"].append("extreme_aspect_ratio")
                profile = result["render_profile"]
                if profile and max(profile["width"] / width, profile["height"] / height) > 2:
                    scene["quality_warnings"].append("heavy_upscale_required")
            scene["preview_url"] = (
                f"/workflow/{workflow_id}/storyboard/scenes/{scene['id']}/image"
                if scene["asset"]
                else None
            )
        durations = [s["end_seconds"] - s["start_seconds"] for s in result["scenes"]]
        motion = {"none": 0, "pan": 0, "zoom": 0}
        issues = []
        for scene, duration in zip(result["scenes"], durations):
            intent = scene["motion_intent"]
            motion["none"] += int(intent == "none")
            motion["pan"] += int(intent.startswith("pan_"))
            motion["zoom"] += int("push_in" in intent or "pull_out" in intent)
            for code in scene["quality_warnings"]:
                issues.append({"severity": "warning", "code": code, "scene_id": scene["id"]})
            for code in scene.get("alignment_warnings", []):
                issues.append({"severity": "warning", "code": code, "scene_id": scene["id"]})
            if duration < 1.5 or duration > 15:
                issues.append({"severity": "warning", "code": "scene_pacing", "scene_id": scene["id"]})
            if not scene["asset"]:
                issues.append({"severity": "error", "code": "image_missing", "scene_id": scene["id"]})
        from app.services.presentation_spec import build_presentation_spec
        from app.services.production_capabilities import burned_in_captions_for
        presentation = build_presentation_spec(wf, duration_seconds=board["duration_seconds"], burned_in_captions=burned_in_captions_for(wf))
        captions = presentation.captions if presentation else None
        result["quality"] = {
            "scene_count": len(durations), "average_scene_duration": sum(durations) / len(durations),
            "min_scene_duration": min(durations), "max_scene_duration": max(durations),
            "motion_distribution": motion, "timeline_coverage": sum(durations) / board["duration_seconds"],
            "caption_cue_count": len(captions.cues) if captions else 0,
            "audio_mastering_status": (wf.mastering_metrics or {}).get("status"), "issues": issues,
        }
        return result

    async def timeline(self, workflow_id, *, create=False):
        wf = await self.workflow(workflow_id)
        board = await self.current(workflow_id)
        if board is None:
            if create:
                raise StoryboardError(
                    "storyboard_timing_resolution_failed", "Resolve current storyboard first"
                )
            return None
        bindings = wf.storyboard_bindings or {}
        if any(s["id"] not in bindings for s in board["scenes"]):
            if create:
                raise StoryboardError("storyboard_asset_missing", "Every scene needs an image")
            return None
        fingerprint = identity(
            {
                "storyboard": board["identity"],
                "bindings": {key: {k: v for k, v in value.items() if k != "source_filename"} for key, value in bindings.items()},
                "version": "storyboard_timeline_v1",
            }
        )
        timeline = await self.timelines.get_by_request_fingerprint(fingerprint)
        if timeline is None:
            # Earlier V1 artifacts included the upload filename. Reuse them only
            # when every original input still matches; new writes omit filenames.
            legacy_fingerprint = identity({
                "storyboard": board["identity"],
                "bindings": bindings,
                "version": "storyboard_timeline_v1",
            })
            legacy = await self.timelines.get_by_request_fingerprint(legacy_fingerprint)
            if legacy is not None and legacy.status == "completed":
                return legacy
        if (timeline and timeline.status == "completed") or not create:
            return timeline
        alignment = await self.alignments.current(workflow_id)
        duration = board["duration_seconds"]
        timeline, _ = await self.timelines.create_or_get(
            workflow_execucao_id=wf.id,
            narration_alignment_id=alignment.id,
            visual_plan_id=None,
            final_audio_sha256=wf.audio_final_sha256,
            narration_sample_rate=wf.audio_final_sample_rate,
            narration_channels=wf.audio_final_channels,
            narration_byte_size=wf.audio_final_byte_size,
            script_fingerprint=board["script_identity"],
            strategy_version="storyboard_timeline_v1",
            schema_version="production_timeline_schema_v3",
            validator_version="storyboard_timeline_validator_v1",
            policy={
                "visual_mode": "storyboard",
                "gap_policy": board["gap_policy"],
                "storyboard_identity": board["identity"],
            },
            request_fingerprint=fingerprint,
            fingerprint=fingerprint,
            status="pending",
            duration_seconds=duration,
        )
        placements = []
        for scene in board["scenes"]:
            asset = bindings[scene["id"]]
            placements.append(
                TimelinePlacement(
                    id=uuid.uuid5(timeline.id, scene["id"]),
                    production_timeline_id=timeline.id,
                    order=scene["order"],
                    visual_decision_id=None,
                    visual_decision_ids=[],
                    visual_query_ids=[],
                    source_type="static_image",
                    source_project_id=uuid.UUID(asset["project_id"]),
                    source_media_id=uuid.UUID(asset["media_id"]),
                    source_session_id=None,
                    source_image_semantic_index_id=None,
                    timeline_start_seconds=scene["start_seconds"],
                    timeline_end_seconds=scene["end_seconds"],
                    playback_rate=None,
                    transition="cut",
                    strategy="storyboard",
                    importance="required",
                    source_provenance=asset,
                    presentation=scene["presentation"],
                    diagnostics={"scene_id": scene["id"]},
                )
            )
        await self.timelines.replace_outputs(
            timeline,
            placements=placements,
            gaps=[],
            status="completed",
            fingerprint=fingerprint,
            coverage_seconds=duration,
            gap_seconds=0.0,
            coverage_ratio=1.0,
            required_gap_seconds=0.0,
            preferred_gap_seconds=0.0,
            optional_gap_seconds=0.0,
            background_gap_seconds=0.0,
            cut_count=max(0, len(placements) - 1),
            unplaced_query_count=0,
            average_shot_duration_seconds=duration / len(placements),
            metrics={"scene_count": len(placements)},
            diagnostics={"storyboard_identity": board["identity"]},
        )
        return await self.timelines.reload(timeline)
