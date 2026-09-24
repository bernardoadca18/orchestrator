"""Deterministic editorial-to-audio resolution; no search or generative providers."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.services.narration_alignment_algorithm import (
    BlockRange,
    build_block_text_alignment,
    partition_words_by_block,
    validate_timed_words,
)
from app.services.narration_alignment_service import NarrationAlignmentService

RESOLVER_VERSION = "storyboard_resolver_v2"


class StoryboardError(ValueError):
    def __init__(self, category: str, message: str):
        self.category = category
        super().__init__(message)


def identity(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def resolve_motion(intent: str) -> dict:
    t = dict(
        start_scale=1.0,
        end_scale=1.0,
        start_x=0.0,
        end_x=0.0,
        start_y=0.0,
        end_y=0.0,
        easing="linear",
    )
    if intent == "slow_push_in":
        t["end_scale"] = 1.08
    elif intent == "slow_pull_out":
        t["start_scale"] = 1.08
    elif intent.startswith("pan_"):
        axis_sign = {
            "pan_left": ("x", -1),
            "pan_right": ("x", 1),
            "pan_up": ("y", -1),
            "pan_down": ("y", 1),
        }
        base = intent.removesuffix("_push_in")
        if base not in axis_sign:
            raise StoryboardError("storyboard_motion_invalid", "Unknown motion intent")
        axis, sign = axis_sign[base]
        t.update(start_scale=1.12, end_scale=1.18 if intent.endswith("_push_in") else 1.12)
        t[f"start_{axis}"] = -sign * 0.04
        t[f"end_{axis}"] = sign * 0.04
    elif intent != "none":
        raise StoryboardError("storyboard_motion_invalid", "Unknown motion intent")
    return dict(
        policy_version="static_image_presentation_v2", fit="cover", background="black", transform=t
    )


def resolve_storyboard(workflow, alignment) -> dict:
    imported = workflow.roteiro_snapshot.get("imported_storyboard")
    if not imported or not imported.get("scenes"):
        raise StoryboardError("storyboard_missing", "Imported storyboard is missing")
    transcription = workflow.transcricao
    duration = workflow.duracao_audio_segundos
    if (
        not alignment
        or alignment.status != "completed"
        or not transcription
        or not workflow.audio_final_sha256
        or transcription.audio_sha256 != workflow.audio_final_sha256
        or alignment.final_audio_sha256 != workflow.audio_final_sha256
        or not duration
        or not math.isfinite(duration)
    ):
        raise StoryboardError(
            "storyboard_timing_resolution_failed", "Current audio alignment is unavailable"
        )
    imported_content = {k: v for k, v in imported.items() if k != "identity"}
    if identity(imported_content) != imported.get("identity"):
        raise StoryboardError("storyboard_anchor_invalid", "Imported storyboard identity mismatch")
    try:
        words = NarrationAlignmentService._parse_words(transcription.palavras_timestamps)
        validate_timed_words(words, duration_seconds=duration, tolerance_seconds=0.05)
        blocks = sorted(workflow.blocos, key=lambda b: b.ordem)
        ranges = tuple(
            BlockRange(b.ordem, b.narration_start_seconds, b.narration_end_seconds) for b in blocks
        )
        partitioned = partition_words_by_block(words, ranges, tolerance_seconds=0.05)
        mappings = {}
        for b in blocks:
            canonical = workflow.roteiro_snapshot["blocos"][b.ordem]["conteudo"]
            if canonical != b.texto:
                raise ValueError("Audio block differs from frozen narration")
            mappings[b.ordem] = build_block_text_alignment(
                canonical, b.synthesis_text or canonical, partitioned[b.ordem]
            )
        scenes = []
        previous_anchor = (-1, 0)
        previous_word_end = 0.0
        for order, scene in enumerate(imported["scenes"]):
            a = scene["narration_anchor"]
            block = workflow.roteiro_snapshot["blocos"][a["block_order"]]
            if (
                block["tipo"] != a["block"]
                or not 0 <= a["start_char"] < a["end_char"] <= len(block["conteudo"])
                or block["conteudo"][a["start_char"] : a["end_char"]] != a["text"]
                or (a["block_order"], a["start_char"]) < previous_anchor
            ):
                raise ValueError("Invalid canonical narration anchor")
            previous_anchor = (a["block_order"], a["end_char"])
            m = mappings[a["block_order"]]
            tokens = [
                t
                for t in m.canonical_tokens
                if t.start_char < a["end_char"] and t.end_char > a["start_char"]
            ]
            indexes = [m.canonical_to_asr_word[t.order] for t in tokens]
            coverage = sum(i is not None for i in indexes) / len(indexes) if indexes else 0.0
            # Minor internal ASR substitutions do not change measured anchor boundaries.
            # Both boundaries must still be mapped; never interpolate missing timestamps.
            if not indexes or indexes[0] is None or indexes[-1] is None or coverage < 0.9:
                raise ValueError("Scene contains unaligned words")
            start = m.asr_words[indexes[0]].start_seconds
            end = m.asr_words[indexes[-1]].end_seconds
            if not (
                math.isfinite(start)
                and math.isfinite(end)
                and 0 <= start < end <= duration
                and start >= previous_word_end - 0.001
            ):
                raise ValueError("Scene word intervals overlap or exceed narration")
            previous_word_end = end
            scenes.append(
                {
                    **scene,
                    "order": order,
                    "anchor_start_seconds": start,
                    "anchor_end_seconds": end,
                    "alignment_coverage": coverage,
                    "alignment_warnings": (
                        ["internal_anchor_words_unaligned"] if coverage < 1.0 else []
                    ),
                    "start_seconds": start,
                    "end_seconds": end,
                    "presentation": resolve_motion(scene["motion_intent"]),
                }
            )
        # Hold the outgoing visual through unanchored narration/silence. No invented scenes.
        scenes[0]["start_seconds"] = 0.0
        for i, scene in enumerate(scenes):
            scene["end_seconds"] = (
                scenes[i + 1]["start_seconds"] if i + 1 < len(scenes) else duration
            )
            if scene["end_seconds"] <= scene["start_seconds"]:
                raise ValueError("Scene has no visual duration")
        result = dict(
            schema_version="resolved_storyboard_v1",
            resolver_version=RESOLVER_VERSION,
            imported_identity=imported["identity"],
            script_identity=identity(workflow.roteiro_snapshot),
            final_audio_sha256=workflow.audio_final_sha256,
            alignment_identity=alignment.fingerprint,
            duration_seconds=duration,
            scenes=scenes,
            status="ready",
            gap_policy="hold_previous_until_next_scene; extend_first_and_last_to_audio_bounds",
        )
        result["identity"] = identity(result)
        return result
    except StoryboardError:
        raise
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise StoryboardError("storyboard_timing_resolution_failed", str(exc)) from exc
