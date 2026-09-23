"""Deterministic handoff artifacts for workflows edited in an external NLE."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from app.models.workflow import WorkflowExecucao
from app.services.audio_serving import (
    AudioArtifactExpectation,
    AudioServingIntegrityError,
    verify_audio_artifact,
)
from app.services.presentation_spec import PresentationInputError, segment_captions

SCHEMA_VERSION = "manual_edit_package.v1"
FILENAMES = ("script.txt", "script.json", "captions.srt", "markers.csv", "manifest.json")
logger = logging.getLogger(__name__)


class ManualEditPackageError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _validate_artifacts(manifest: dict[str, Any], files: dict[str, bytes], cues: list[Any]) -> None:
    """Check serialized handoff data before marking a workflow complete."""
    try:
        assert json.loads(files["manifest.json"]) == manifest
        assert json.loads(files["script.json"])["workflow_id"] == manifest["workflow_id"]
        assert files["script.txt"].decode("utf-8").strip()
        for name in ("script.txt", "script.json", "captions.srt", "markers.csv"):
            assert _sha(files[name]) == manifest["files"][name]["sha256"]
        chunks = files["captions.srt"].decode("utf-8").strip().split("\n\n")
        assert len(chunks) == len(cues)
        srt_pattern = re.compile(
            r"^(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+)$", re.DOTALL
        )
        previous_end = 0.0
        for index, (chunk, cue) in enumerate(zip(chunks, cues, strict=True), 1):
            match = srt_pattern.fullmatch(chunk)
            assert match and int(match[1]) == index and match[4].strip()
            assert match[2] == _srt_time(cue.start_seconds)
            assert match[3] == _srt_time(cue.end_seconds)
            assert (
                previous_end
                <= cue.start_seconds
                < cue.end_seconds
                <= manifest["duration_seconds"] + 0.001
            )
            previous_end = cue.end_seconds
        reader = csv.DictReader(io.StringIO(files["markers.csv"].decode("utf-8")))
        assert reader.fieldnames == ["start_seconds", "end_seconds", "name", "text", "block_id"]
        rows = list(reader)
        assert len(rows) == len(manifest["blocks"])
        previous_end = 0.0
        for row, block in zip(rows, manifest["blocks"], strict=True):
            start, end = float(row["start_seconds"]), float(row["end_seconds"])
            assert previous_end <= start < end <= manifest["duration_seconds"] + 0.001
            assert row["block_id"] == block["block_id"] and row["text"] == block["text"]
            previous_end = end
    except (AssertionError, ValueError, KeyError, UnicodeError) as exc:
        raise ManualEditPackageError(
            "manual_edit_package_integrity_failed", "Serialized package validation failed"
        ) from exc


def build_package(
    workflow: WorkflowExecucao, transcription: Any
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build and validate package content from one immutable script/audio identity."""
    if (
        not workflow.audio_final_path
        or not workflow.audio_final_sha256
        or not workflow.duracao_audio_segundos
    ):
        raise ManualEditPackageError(
            "manual_edit_package_audio_missing", "Final narration is unavailable"
        )
    if not isinstance(workflow.roteiro_snapshot, dict):
        raise ManualEditPackageError(
            "manual_edit_package_integrity_failed", "Script snapshot is unavailable"
        )
    if transcription is None or transcription.audio_sha256 != workflow.audio_final_sha256:
        raise ManualEditPackageError(
            "manual_edit_package_transcript_missing", "Matching final transcription is unavailable"
        )
    try:
        _, physical = verify_audio_artifact(
            workflow.audio_final_path,
            AudioArtifactExpectation(
                sha256=workflow.audio_final_sha256,
                byte_size=workflow.audio_final_byte_size,
                duration_seconds=workflow.duracao_audio_segundos,
                sample_rate=workflow.audio_final_sample_rate,
                channels=workflow.audio_final_channels,
                frame_count=workflow.audio_final_frame_count,
                subtype=workflow.audio_final_subtype,
            ),
        )
    except AudioServingIntegrityError as exc:
        raise ManualEditPackageError("manual_edit_package_integrity_failed", str(exc)) from exc
    snapshot = workflow.roteiro_snapshot
    raw_blocks = snapshot.get("blocos")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ManualEditPackageError(
            "manual_edit_package_integrity_failed", "Script blocks are unavailable"
        )
    audio_blocks = {block.ordem: block for block in workflow.blocos}
    blocks: list[dict[str, Any]] = []
    previous_end = 0.0
    for position, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            raise ManualEditPackageError(
                "manual_edit_package_integrity_failed", "Invalid script block"
            )
        order = position  # AudioService numbers snapshot blocks by their list position.
        text = raw.get("conteudo", "")
        audio = audio_blocks.get(order)
        if (
            not isinstance(text, str)
            or not text.strip()
            or audio is None
            or getattr(audio, "texto", text) != text
        ):
            raise ManualEditPackageError(
                "manual_edit_package_integrity_failed", "Script/audio block mismatch"
            )
        start, end = audio.narration_start_seconds, audio.narration_end_seconds
        if (
            start is None
            or end is None
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < previous_end
            or end <= start
            or end > physical.duration_seconds + 0.001
        ):
            raise ManualEditPackageError(
                "manual_edit_package_invalid_timestamps", "Invalid audio block timing"
            )
        previous_end = end
        blocks.append(
            {
                "block_id": str(audio.id),
                "order": order,
                "text": text,
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "audio": {
                    "url": f"/workflow/{workflow.id}/blocos/{audio.id}/audio",
                    "sha256": audio.audio_sha256,
                }
                if audio.audio_path
                else None,
            }
        )
    if [item["order"] for item in blocks] != list(range(len(blocks))):
        raise ManualEditPackageError(
            "manual_edit_package_integrity_failed", "Script blocks are unordered"
        )
    words = transcription.palavras_timestamps or []
    if not words:
        raise ManualEditPackageError(
            "manual_edit_package_transcript_missing", "Word timestamps are unavailable"
        )
    try:
        cues = segment_captions(words, duration_seconds=physical.duration_seconds)
    except PresentationInputError as exc:
        raise ManualEditPackageError("manual_edit_package_invalid_timestamps", str(exc)) from exc
    if not cues:
        raise ManualEditPackageError(
            "manual_edit_package_invalid_timestamps", "No caption cues were produced"
        )
    lines = [
        f"{index}\n{_srt_time(cue.start_seconds)} --> {_srt_time(cue.end_seconds)}\n{cue.text}\n"
        for index, cue in enumerate(cues, 1)
    ]
    srt = "\n".join(lines) + "\n"
    markers = io.StringIO(newline="")
    writer = csv.writer(markers)
    writer.writerow(("start_seconds", "end_seconds", "name", "text", "block_id"))
    for block in blocks:
        writer.writerow(
            (
                f"{block['start_time']:.3f}",
                f"{block['end_time']:.3f}",
                f"Block {block['order'] + 1:02}",
                block["text"],
                block["block_id"],
            )
        )
    script_json = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": str(workflow.id),
        "profile_id": workflow.perfil_producao,
        "content_locale": snapshot.get("content_locale", "pt-BR"),
        "script_revision": str(workflow.roteiro_id),
        "title": snapshot.get("titulo"),
        "blocks": blocks,
    }
    files = {
        "script.txt": ("\n\n".join(block["text"] for block in blocks) + "\n").encode("utf-8"),
        "script.json": _canonical(script_json),
        "captions.srt": srt.encode("utf-8"),
        "markers.csv": markers.getvalue().encode("utf-8"),
    }
    source = {
        "script_revision": str(workflow.roteiro_id),
        "script_sha256": _sha(_canonical(snapshot)),
        "audio_sha256": physical.sha256,
        "transcription_sha256": _sha(_canonical(words)),
    }
    identity = _sha(
        _canonical({"version": SCHEMA_VERSION, "workflow_id": str(workflow.id), "source": source})
    )
    base_url = f"/workflow/{workflow.id}/manual-edit-package/files"
    descriptors = {
        name: {"url": f"{base_url}/{name}", "sha256": _sha(data), "byte_size": len(data)}
        for name, data in files.items()
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "identity": identity,
        "manifest_url": f"{base_url}/manifest.json",
        "workflow_id": str(workflow.id),
        "profile_id": workflow.perfil_producao,
        "content_locale": script_json["content_locale"],
        "duration_seconds": physical.duration_seconds,
        "created_from": source,
        "files": {
            "narration": {
                "url": f"{base_url}/narration.wav",
                "sha256": physical.sha256,
                "byte_size": physical.byte_size,
                "duration_seconds": physical.duration_seconds,
                "sample_rate": physical.sample_rate,
                "channels": physical.channels,
                "mastering_config": workflow.mastering_config,
                "mastering_metrics": workflow.mastering_metrics,
            },
            **descriptors,
        },
        "blocks": blocks,
    }
    files["manifest.json"] = _canonical(manifest)
    _validate_artifacts(manifest, files, cues)
    return manifest, files


def materialize_package(
    root: str | Path, workflow_id: Any, manifest: dict[str, Any], files: dict[str, bytes]
) -> Path:
    """Atomic fixed-name writes; retries preserve the same identity and bytes."""
    directory = Path(root) / str(workflow_id) / "manual_edit_package" / manifest["identity"]
    directory.mkdir(parents=True, exist_ok=True)
    for name in FILENAMES:
        target = directory / name
        data = files[name]
        if target.is_file() and _sha(target.read_bytes()) == _sha(data):
            continue
        temporary = directory / f".{name}.{os.getpid()}.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, target)
    return directory


class ManualEditPackageService:
    def __init__(self, session: Any, repository: Any, storage_root: str):
        self.session = session
        self.repository = repository
        self.storage_root = storage_root

    async def ensure(self, workflow_id: Any) -> dict[str, Any]:
        logger.info("manual_edit_package_started", extra={"workflow_id": str(workflow_id)})
        workflow = await self.repository.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            raise ManualEditPackageError(
                "manual_edit_package_integrity_failed", "Workflow is unavailable"
            )
        transcription = workflow.transcricao
        try:
            manifest, files = build_package(workflow, transcription)
            materialize_package(self.storage_root, workflow.id, manifest, files)
        except ManualEditPackageError:
            logger.exception("manual_edit_package_failed", extra={"workflow_id": str(workflow_id)})
            raise
        except Exception as exc:
            logger.exception("manual_edit_package_failed", extra={"workflow_id": str(workflow_id)})
            raise ManualEditPackageError(
                "manual_edit_package_export_failed", "Manual edit package export failed"
            ) from exc
        if workflow.manual_edit_package != manifest:
            workflow.manual_edit_package = manifest
            await self.session.commit()
            logger.info(
                "manual_edit_package_completed",
                extra={"workflow_id": str(workflow_id), "identity": manifest["identity"]},
            )
        else:
            logger.info(
                "manual_edit_package_reused",
                extra={"workflow_id": str(workflow_id), "identity": manifest["identity"]},
            )
        return manifest
