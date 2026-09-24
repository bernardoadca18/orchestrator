import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.workflow import StaticImagePresentationV2Out
from app.services.storyboard import StoryboardError, identity, resolve_motion, resolve_storyboard


def fixture():
    text = "Você acorda. A cidade desperta."
    scenes = [
        dict(
            id=f"scene-{i}",
            narration_anchor=dict(
                block="intro", block_order=0, start_char=start, end_char=end, text=text[start:end]
            ),
            visual_intent="view",
            prompt="view",
            motion_intent="slow_push_in",
        )
        for i, (start, end) in enumerate([(0, 12), (13, len(text))])
    ]
    imported = dict(schema_version="imported_storyboard_v1", style={}, continuity={}, scenes=scenes)
    imported["identity"] = identity(imported)
    wf = NS(
        id=uuid4(),
        roteiro_snapshot={
            "blocos": [{"tipo": "intro", "conteudo": text}],
            "imported_storyboard": imported,
        },
        audio_final_sha256="a" * 64,
        duracao_audio_segundos=6.0,
        blocos=[
            NS(
                ordem=0,
                texto=text,
                synthesis_text=text,
                narration_start_seconds=0.0,
                narration_end_seconds=6.0,
            )
        ],
        transcricao=NS(
            audio_sha256="a" * 64,
            palavras_timestamps=[
                dict(palavra=w, inicio=i + 0.2, fim=i + 0.9)
                for i, w in enumerate(["Você", "acorda", "A", "cidade", "desperta"])
            ],
        ),
    )
    return wf, NS(id=uuid4(), status="completed", final_audio_sha256="a" * 64, fingerprint="b" * 64)


def test_word_timing_gap_free_and_immutable():
    wf, a = fixture()
    original = copy.deepcopy(wf.roteiro_snapshot)
    result = resolve_storyboard(wf, a)
    assert result["scenes"][0]["start_seconds"] == 0
    assert result["scenes"][0]["end_seconds"] == result["scenes"][1]["start_seconds"] == 2.2
    assert result["scenes"][-1]["end_seconds"] == 6
    assert wf.roteiro_snapshot == original
    assert result == resolve_storyboard(wf, a)
    wf.audio_final_sha256 = wf.transcricao.audio_sha256 = a.final_audio_sha256 = "c" * 64
    assert resolve_storyboard(wf, a)["identity"] != result["identity"]


def test_multiple_blocks():
    wf, a = fixture()
    text = "The river flows."
    wf.roteiro_snapshot["blocos"].append({"tipo": "river", "conteudo": text})
    wf.blocos.append(
        NS(
            ordem=1,
            texto=text,
            synthesis_text=text,
            narration_start_seconds=6.0,
            narration_end_seconds=10.0,
        )
    )
    wf.duracao_audio_segundos = 10.0
    wf.transcricao.palavras_timestamps += [
        dict(palavra=w, inicio=6 + i, fim=6.8 + i) for i, w in enumerate(["The", "river", "flows"])
    ]
    imported = wf.roteiro_snapshot["imported_storyboard"]
    imported["scenes"].append(
        dict(
            id="river",
            narration_anchor=dict(
                block="river", block_order=1, start_char=0, end_char=len(text), text=text
            ),
            visual_intent="river",
            prompt="river",
            motion_intent="none",
        )
    )
    imported.pop("identity")
    imported["identity"] = identity(imported)
    result = resolve_storyboard(wf, a)
    assert result["scenes"][-1]["start_seconds"] == 6
    assert result["scenes"][-1]["end_seconds"] == 10


def test_anchor_accepts_minor_internal_asr_substitution_but_requires_boundaries():
    wf, alignment = fixture()
    text = "You find a cobbled street. People carry baskets toward the market square."
    wf.roteiro_snapshot["blocos"][0]["conteudo"] = text
    wf.blocos[0].texto = wf.blocos[0].synthesis_text = text
    wf.duracao_audio_segundos = wf.blocos[0].narration_end_seconds = 13.0
    words = text.replace(" a ", " the ").split()
    wf.transcricao.palavras_timestamps = [
        dict(palavra=w, inicio=i, fim=i + 0.8) for i, w in enumerate(words)
    ]
    imported = wf.roteiro_snapshot["imported_storyboard"]
    imported["scenes"] = [imported["scenes"][0]]
    imported["scenes"][0]["narration_anchor"].update(start_char=0, end_char=len(text), text=text)
    imported.pop("identity")
    imported["identity"] = identity(imported)
    scene = resolve_storyboard(wf, alignment)["scenes"][0]
    assert scene["alignment_coverage"] > 0.9
    assert scene["anchor_start_seconds"] == 0
    assert scene["anchor_end_seconds"] == len(words) - 0.2
    assert scene["alignment_warnings"] == ["internal_anchor_words_unaligned"]
    wf.transcricao.palavras_timestamps[4]["palavra"] = "building"
    with pytest.raises(StoryboardError):
        resolve_storyboard(wf, alignment)
    wf.transcricao.palavras_timestamps[4]["palavra"] = words[4]
    wf.transcricao.palavras_timestamps[0]["palavra"] = "Someone"
    with pytest.raises(StoryboardError):
        resolve_storyboard(wf, alignment)


@pytest.mark.parametrize(
    "failure", ["stale", "nan", "missing_word", "anchor", "audio", "alignment"]
)
def test_invalid_alignment_fails_closed(failure):
    wf, a = fixture()
    if failure == "stale":
        wf.transcricao.audio_sha256 = "c" * 64
    if failure == "nan":
        wf.transcricao.palavras_timestamps[0]["inicio"] = float("nan")
    if failure == "missing_word":
        wf.transcricao.palavras_timestamps = wf.transcricao.palavras_timestamps[:1]
    if failure == "anchor":
        wf.roteiro_snapshot["imported_storyboard"]["scenes"][0]["narration_anchor"]["end_char"] = (
            999
        )
    if failure == "audio":
        wf.audio_final_sha256 = None
    if failure == "alignment":
        a.status = "blocked"
    with pytest.raises(StoryboardError):
        resolve_storyboard(wf, a)


@pytest.mark.parametrize(
    "motion",
    [
        "none",
        "slow_push_in",
        "slow_pull_out",
        "pan_left",
        "pan_right",
        "pan_up",
        "pan_down",
        "pan_left_push_in",
        "pan_right_push_in",
    ],
)
def test_motion_bounds(motion):
    p = StaticImagePresentationV2Out.model_validate(resolve_motion(motion))
    for progress in (0.0, 0.5, 1.0):
        t = p.transform
        scale = t.start_scale + (t.end_scale - t.start_scale) * progress
        for axis in ("x", "y"):
            value = (
                getattr(t, "start_" + axis) * (1 - progress) + getattr(t, "end_" + axis) * progress
            )
            assert abs(value) <= (scale - 1) / 2 + 1e-9


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0, 3.0])
def test_invalid_motion(value):
    p = resolve_motion("none")
    p["transform"]["start_scale"] = value
    with pytest.raises(ValueError):
        StaticImagePresentationV2Out.model_validate(p)


@pytest.mark.asyncio
async def test_routing_waits_for_assets_without_visual_director():
    from tests.test_production_orchestration_service import (
        FakeComposer,
        _remote,
        _service,
        _timeline,
        _workflow,
    )

    wf = _workflow(phase="narration_alignment", status="running")
    wf.production_capabilities = dict(
        delivery_mode="rendered_video",
        visual_mode="storyboard",
        requires_media_scope=False,
        caption_policy="burned_in",
        render_profile=None,
        version="production_capabilities_v1",
    )
    timeline = _timeline(wf.id)
    service = _service(wf, timeline, FakeComposer(_remote(wf.id, timeline)))
    service.visual.current = AsyncMock(
        side_effect=AssertionError("Visual Director must not be called")
    )
    service.alignments.ensure = AsyncMock(return_value=(NS(status="completed"), False))
    service.storyboards = NS(
        ensure=AsyncMock(),
        current=AsyncMock(return_value={"identity": "valid"}),
        view=AsyncMock(return_value={"ready": False}),
    )
    result = await service.process(wf.id)
    assert result.terminal
    assert wf.production_phase == "storyboard_assets" and wf.production_status == "blocked"
    service.visual.ensure.assert_not_awaited()
    service.visual.current.assert_not_awaited()
    await service.resume(wf)
    await service.process(wf.id)
    assert wf.production_phase == "storyboard_assets" and wf.production_status == "blocked"
    assert service.storyboards.ensure.await_count == 1


@pytest.mark.asyncio
async def test_current_timeline_reuses_legacy_filename_identity_only_for_same_inputs():
    from app.services.storyboard_service import StoryboardService

    bindings = {"scene-1": {"media_id": "image-a", "source_filename": "scene-1.png"}}
    board = {"identity": "board-a", "scenes": [{"id": "scene-1"}]}
    legacy_key = identity(
        {"storyboard": board["identity"], "bindings": bindings, "version": "storyboard_timeline_v1"}
    )
    timeline = NS(status="completed")
    service = StoryboardService(AsyncMock())
    service.workflow = AsyncMock(return_value=NS(storyboard_bindings=bindings))
    service.current = AsyncMock(return_value=board)
    service.timelines = NS(
        get_by_request_fingerprint=AsyncMock(
            side_effect=lambda key: timeline if key == legacy_key else None,
        )
    )
    assert await service.timeline(uuid4()) is timeline
    bindings["scene-1"]["media_id"] = "image-b"
    assert await service.timeline(uuid4()) is None


@pytest.mark.asyncio
async def test_upload_replace_remove_and_no_semantic_index(respx_mock):
    import hashlib
    import io

    import httpx
    from starlette.datastructures import Headers, UploadFile

    from app.services.storyboard_service import StoryboardService

    wf = NS(
        id=uuid4(),
        storyboard_bindings={},
        production_status="blocked",
        production_phase="storyboard_assets",
        resolved_storyboard={"identity": "test", "scenes": [{"id": "scene-1"}]},
    )
    project = uuid4()
    media = uuid4()
    data = b"image fixture"
    service = StoryboardService(AsyncMock())
    service.workflow = AsyncMock(return_value=wf)
    service.lock_for_binding = AsyncMock(return_value=wf)
    service.current = AsyncMock(return_value=wf.resolved_storyboard)
    service.asset_project = AsyncMock(return_value=project)
    service.view = AsyncMock(return_value={"ready": True})

    def response(request):
        assert b'name="enqueue_semantic_index"\r\n\r\nfalse' in request.content
        return httpx.Response(
            202,
            json={
                "media_asset": {
                    "id": str(media),
                    "project_id": str(project),
                    "media_kind": "image",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "byte_size": len(data),
                    "content_type": "image/png",
                }
            },
        )

    route = respx_mock.post(
        f"{service.config.media_indexer_api_url}/projects/{project}/images"
    ).mock(side_effect=response)

    async def upload():
        image = UploadFile(
            io.BytesIO(data), filename="test.png", headers=Headers({"content-type": "image/png"})
        )
        return await service.upload(wf.id, "scene-1", image)

    await upload()
    first = copy.deepcopy(wf.storyboard_bindings)
    await upload()
    assert wf.storyboard_bindings == first and len(wf.storyboard_bindings) == 1
    media = uuid4()
    await upload()
    assert wf.storyboard_bindings["scene-1"]["media_id"] == str(media)
    await service.remove(wf.id, "scene-1")
    assert wf.storyboard_bindings == {}
    assert route.call_count == 3
    with pytest.raises(StoryboardError):
        await service.remove(wf.id, "unknown-scene")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["mime", "scene", "sha", "project", "active"])
async def test_invalid_asset_rejected(respx_mock, failure):
    import io

    from starlette.datastructures import Headers, UploadFile

    from app.services.storyboard_service import StoryboardService

    wf = NS(
        id=uuid4(),
        storyboard_bindings={},
        production_status="running" if failure == "active" else "blocked",
    )
    service = StoryboardService(AsyncMock())
    project = uuid4()
    service.workflow = AsyncMock(return_value=wf)
    service.lock_for_binding = AsyncMock(return_value=wf)
    service.current = AsyncMock(return_value={"scenes": [{"id": "scene-1"}]})
    service.asset_project = AsyncMock(return_value=project)
    respx_mock.post(f"{service.config.media_indexer_api_url}/projects/{project}/images").respond(
        202,
        json={
            "media_asset": {
                "id": str(uuid4()),
                "project_id": str(uuid4()) if failure == "project" else str(project),
                "media_kind": "image",
                "sha256": "f" * 64,
                "byte_size": 1,
                "content_type": "image/png",
            }
        },
    )
    image = UploadFile(
        io.BytesIO(b"x"),
        filename="test.png",
        headers=Headers({"content-type": "image/gif" if failure == "mime" else "image/png"}),
    )
    with pytest.raises(StoryboardError):
        await service.upload(wf.id, "wrong" if failure == "scene" else "scene-1", image)
    assert wf.storyboard_bindings == {}
