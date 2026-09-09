"""Canonical active-physical-chunk to AudioBloco materialization boundary."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import AudioBloco, AudioChunk, AudioChunkTake
from app.services.audio_serving import AudioArtifactExpectation, verify_audio_artifact
from app.services.production_audio import (
    AssemblyResult,
    BlockAssemblyChunk,
    BlockAssemblyManifest,
    ProductionAudioError,
    reassemble_block_from_manifest,
)


async def active_block_manifest(
    session: AsyncSession, block: AudioBloco
) -> BlockAssemblyManifest:
    """Resolve exactly the active physical takes and verify DB/file provenance."""
    chunks = list(
        (
            await session.execute(
                select(AudioChunk)
                .where(AudioChunk.audio_bloco_id == block.id)
                .order_by(AudioChunk.chunk_index)
            )
        ).scalars()
    )
    if not chunks:
        raise ProductionAudioError("block has no durable physical chunks")
    entries: list[BlockAssemblyChunk] = []
    for expected_index, chunk in enumerate(chunks):
        if chunk.chunk_index != expected_index or chunk.current_take_id is None:
            raise ProductionAudioError("block active physical chunk selection is incomplete")
        if chunk.pause_after_frames is None:
            raise ProductionAudioError("block physical chunk has no frozen pause provenance")
        take = await session.get(AudioChunkTake, chunk.current_take_id)
        if (
            take is None
            or take.audio_chunk_id != chunk.id
            or take.status != "completed"
            or not take.audio_path
        ):
            raise ProductionAudioError("block current physical take is unavailable")
        path, metadata = verify_audio_artifact(
            take.audio_path,
            AudioArtifactExpectation(
                sha256=take.audio_sha256,
                byte_size=take.audio_byte_size,
                duration_seconds=take.audio_duration_seconds,
                sample_rate=take.audio_sample_rate,
                channels=take.audio_channels,
                frame_count=take.audio_frame_count,
                subtype=take.audio_subtype,
            ),
        )
        entries.append(
            BlockAssemblyChunk(
                chunk_id=str(chunk.id),
                chunk_index=chunk.chunk_index,
                current_take_id=str(take.id),
                audio_path=path,
                audio_sha256=metadata.sha256,
                frame_count=metadata.frame_count,
                sample_rate=metadata.sample_rate,
                channels=metadata.channels,
                subtype=metadata.subtype,
                pause_after_frames=chunk.pause_after_frames,
            )
        )
    return BlockAssemblyManifest(
        block_id=str(block.id), block_order=block.ordem, chunks=tuple(entries)
    )


async def materialize_active_block(
    session: AsyncSession, block: AudioBloco, destination: Path
) -> tuple[BlockAssemblyManifest, AssemblyResult]:
    manifest = await active_block_manifest(session, block)
    return manifest, reassemble_block_from_manifest(manifest, destination)


async def active_block_is_current(session: AsyncSession, block: AudioBloco) -> bool:
    """Return true only when the published block identifies the live chunk selection."""
    if not block.assembly_manifest_identity or not isinstance(block.assembly_manifest, dict):
        return False
    manifest = await active_block_manifest(session, block)
    return (
        block.assembly_manifest_identity == manifest.identity
        and block.assembly_manifest == manifest.identity_payload()
    )
