"""Deterministic, frozen presentation decisions for Video Composer.

This module never performs ASR or editorial generation.  It projects the
authoritative final-audio transcription and explicit script metadata into a
strict presentation contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from app.core.timestamp_contract import (
    MAX_CAPTION_WORD_DURATION_SECONDS,
    MAX_WORD_OVERLAP_NORMALIZATION_SECONDS,
    TIMESTAMP_EPSILON_SECONDS,
    WORD_TIMESTAMP_TOLERANCE_SECONDS,
)
from app.models.workflow import WorkflowExecucao
from app.schemas.workflow import (
    BrandingDisabledOut,
    CaptionCueOut,
    CaptionsPresentationOut,
    PresentationSpecOut,
    SectionTitlePresentationOut,
    TransitionPresentationOut,
)

CAPTION_POLICY_VERSION = "caption_policy_v1"
PRESENTATION_STRATEGY_VERSION = "longform_presentation_v1"
PRESENTATION_PROFILE_ID = "longform_professional_v1"


@dataclass(frozen=True)
class CaptionPolicy:
    max_line_chars: int = 42
    max_lines: int = 2
    max_cue_seconds: float = MAX_CAPTION_WORD_DURATION_SECONDS
    preferred_min_seconds: float = 0.8
    pause_break_seconds: float = 0.75
    max_cues: int = 5000
    max_words: int = 50_000
    timestamp_epsilon_seconds: float = TIMESTAMP_EPSILON_SECONDS
    max_word_overlap_normalization_seconds: float = MAX_WORD_OVERLAP_NORMALIZATION_SECONDS
    duration_tolerance_seconds: float = WORD_TIMESTAMP_TOLERANCE_SECONDS


CAPTION_POLICY_V1 = CaptionPolicy()
_CLOSING_PUNCTUATION = re.compile(r"^[,.;:!?%…\)\]\}]+$")
_OPENING_PUNCTUATION = re.compile(r"^[\(\[\{]+$")
_SENTENCE_END = re.compile(r"[.!?…][\"'\u201d\u2019\)\]]*$")


class PresentationInputError(ValueError):
    """Compact, structured failure for a rejected presentation input."""

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.reason = reason
        self.details = details or {}
        suffix = " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in self.details.items()
            if value is not None
        )
        rendered = message
        if reason:
            rendered += f": reason={reason}"
        if suffix:
            rendered += f" {suffix}"
        super().__init__(rendered)

    @property
    def log_fields(self) -> dict[str, object]:
        return {"reason": self.reason, **self.details}


def _timestamp_error(
    *,
    reason: str,
    word_index: int,
    token: str,
    start: float,
    end: float,
    previous_start: float | None,
    previous_end: float | None,
    previous_normalized_end: float | None,
    duration_seconds: float,
    block_order: int | None,
) -> PresentationInputError:
    return PresentationInputError(
        "caption word timestamp invalid",
        reason=reason,
        details={
            "word_index": word_index,
            "token": token,
            "start": start,
            "end": end,
            "previous_start": previous_start,
            "previous_end": previous_end,
            "previous_normalized_end": previous_normalized_end,
            "duration_seconds": duration_seconds,
            "word_duration_seconds": end - start,
            "overlap_seconds": (
                max(0.0, previous_normalized_end - start)
                if previous_normalized_end is not None
                else 0.0
            ),
            "block_order": block_order,
        },
    )


def _block_order_for_word(
    start: float,
    end: float,
    block_ranges: list[tuple[int, float, float]] | None,
) -> int | None:
    if not block_ranges:
        return None
    midpoint = start + max(0.0, end - start) / 2
    for order, block_start, block_end in block_ranges:
        if block_start <= midpoint <= block_end:
            return order
    return None


def canonical_hash(payload: object) -> str:
    def normalize(value: object) -> object:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        return value

    encoded = json.dumps(
        normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_token(value: Any) -> str:
    if not isinstance(value, str):
        raise PresentationInputError("caption word must be text")
    token = " ".join(value.split())
    if not token or len(token) > 100:
        raise PresentationInputError("caption word must contain 1-100 characters")
    return token


def _render_tokens(tokens: list[str]) -> str:
    result = ""
    previous_opening = False
    for token in tokens:
        if not result:
            result = token
        elif _CLOSING_PUNCTUATION.fullmatch(token) or previous_opening:
            result += token
        else:
            result += " " + token
        previous_opening = bool(_OPENING_PUNCTUATION.fullmatch(token))
    return result


def _wrap_two_lines(text: str, policy: CaptionPolicy) -> str | None:
    if len(text) <= policy.max_line_chars:
        return text
    words = text.split(" ")
    candidates: list[tuple[int, int, str, str]] = []
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        if len(left) <= policy.max_line_chars and len(right) <= policy.max_line_chars:
            candidates.append((abs(len(left) - len(right)), index, left, right))
    if not candidates:
        return None
    _, _, left, right = min(candidates)
    return f"{left}\n{right}"


def segment_captions(
    raw_words: list[dict[str, Any]],
    *,
    duration_seconds: float,
    policy: CaptionPolicy = CAPTION_POLICY_V1,
    block_ranges: list[tuple[int, float, float]] | None = None,
) -> list[CaptionCueOut]:
    """Segment words with bounded, local timing normalization for caption cues."""

    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise PresentationInputError("narration duration must be finite and positive")
    if len(raw_words) > policy.max_words:
        raise PresentationInputError("caption word limit exceeded")
    parsed: list[tuple[str, float, float]] = []
    previous_start: float | None = None
    previous_end: float | None = None
    previous_normalized_end: float | None = None
    for word_index, raw in enumerate(raw_words):
        if not isinstance(raw, dict):
            raise PresentationInputError("caption word payload must be an object")
        try:
            token = _normalize_token(raw["palavra"])
            start = float(raw["inicio"])
            end = float(raw["fim"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PresentationInputError("caption word payload is malformed") from exc
        block_order = _block_order_for_word(start, end, block_ranges)
        reason = None
        if not math.isfinite(start):
            reason = "non_finite_start"
        elif not math.isfinite(end):
            reason = "non_finite_end"
        elif start < 0:
            reason = "negative_start"
        elif end < start:
            reason = "end_not_after_start"
        elif end > duration_seconds + policy.duration_tolerance_seconds:
            reason = "end_exceeds_duration"
        elif end - start > policy.max_cue_seconds:
            reason = "word_duration_exceeds_policy"
        if reason:
            raise _timestamp_error(
                reason=reason,
                word_index=word_index,
                token=token,
                start=start,
                end=end,
                previous_start=previous_start,
                previous_end=previous_end,
                previous_normalized_end=previous_normalized_end,
                duration_seconds=duration_seconds,
                block_order=block_order,
            )

        normalized_end = min(end, duration_seconds)
        if normalized_end == start:
            normalized_end = min(
                duration_seconds,
                start + policy.timestamp_epsilon_seconds,
            )
        normalized_start = start
        if previous_normalized_end is not None and normalized_start < previous_normalized_end:
            overlap = previous_normalized_end - normalized_start
            if overlap > policy.max_word_overlap_normalization_seconds:
                raise _timestamp_error(
                    reason="overlaps_previous_word",
                    word_index=word_index,
                    token=token,
                    start=start,
                    end=end,
                    previous_start=previous_start,
                    previous_end=previous_end,
                    previous_normalized_end=previous_normalized_end,
                    duration_seconds=duration_seconds,
                    block_order=block_order,
                )
            normalized_start = previous_normalized_end
        if normalized_end <= normalized_start:
            raise _timestamp_error(
                reason="end_not_after_normalization",
                word_index=word_index,
                token=token,
                start=start,
                end=end,
                previous_start=previous_start,
                previous_end=previous_end,
                previous_normalized_end=previous_normalized_end,
                duration_seconds=duration_seconds,
                block_order=block_order,
            )
        parsed.append((token, normalized_start, normalized_end))
        previous_start = start
        previous_end = end
        previous_normalized_end = normalized_end

    cues: list[CaptionCueOut] = []
    current: list[tuple[str, float, float]] = []

    def flush() -> None:
        if not current:
            return
        text = _wrap_two_lines(_render_tokens([item[0] for item in current]), policy)
        if text is None:
            raise PresentationInputError("caption cue cannot fit the versioned line policy")
        cues.append(
            CaptionCueOut(
                text=text,
                start_seconds=current[0][1],
                end_seconds=current[-1][2],
                preset="subtle_longform_v1",
            )
        )
        current.clear()

    for word in parsed:
        if current:
            gap = word[1] - current[-1][2]
            candidate = [*current, word]
            candidate_text = _render_tokens([item[0] for item in candidate])
            if (
                gap > policy.pause_break_seconds
                or word[2] - current[0][1] > policy.max_cue_seconds
                or _wrap_two_lines(candidate_text, policy) is None
            ):
                flush()
        current.append(word)
        current_text = _render_tokens([item[0] for item in current])
        if (
            _SENTENCE_END.search(current_text)
            and current[-1][2] - current[0][1] >= policy.preferred_min_seconds
        ):
            flush()
        if len(cues) > policy.max_cues:
            raise PresentationInputError("caption cue limit exceeded")
    flush()
    if len(cues) > policy.max_cues:
        raise PresentationInputError("caption cue limit exceeded")
    return cues


def _explicit_section_titles(
    workflow: WorkflowExecucao, *, duration_seconds: float
) -> list[SectionTitlePresentationOut]:
    raw_blocks = workflow.roteiro_snapshot.get("blocos", [])
    if not isinstance(raw_blocks, list):
        raise PresentationInputError("script blocks must be a list")
    blocks_by_order = {block.ordem: block for block in (workflow.blocos or [])}
    titles: list[SectionTitlePresentationOut] = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            raise PresentationInputError("script block must be an object")
        title = raw.get("display_title")
        if title is None:
            continue
        if not isinstance(title, str):
            raise PresentationInputError("display_title must be text")
        title = " ".join(title.split())
        order = int(raw.get("ordem", index))
        block = blocks_by_order.get(order)
        if not title or len(title) > 120 or block is None:
            raise PresentationInputError("explicit section title is invalid")
        start = block.narration_start_seconds
        end = block.narration_end_seconds
        if (
            start is None
            or end is None
            or end <= start
            or start < 0
            or start >= duration_seconds
            or end > duration_seconds
        ):
            raise PresentationInputError("section title block timing is unavailable")
        title_end = min(float(end), float(start) + 2.4)
        titles.append(
            SectionTitlePresentationOut(
                text=title,
                start_seconds=float(start),
                end_seconds=title_end,
                preset="minimal_section_v1",
            )
        )
    return titles


def build_presentation_spec(
    workflow: WorkflowExecucao,
    *,
    duration_seconds: float,
) -> PresentationSpecOut | None:
    """Build V1 from current authoritative artifacts, or return legacy absence."""

    transcription = getattr(workflow, "transcricao", None)
    if transcription is None:
        return None
    if not workflow.audio_final_sha256 or transcription.audio_sha256 != workflow.audio_final_sha256:
        raise PresentationInputError("transcription belongs to another final narration")
    cues = segment_captions(
        transcription.palavras_timestamps,
        duration_seconds=duration_seconds,
        block_ranges=[
            (
                block.ordem,
                float(block.narration_start_seconds),
                float(block.narration_end_seconds),
            )
            for block in (workflow.blocos or [])
            if block.narration_start_seconds is not None and block.narration_end_seconds is not None
        ],
    )
    section_titles = _explicit_section_titles(workflow, duration_seconds=duration_seconds)
    transitions = [
        TransitionPresentationOut(
            type="fade_to_black",
            boundary_seconds=item.start_seconds,
            duration_seconds=0.3,
            preset="section_boundary_fade_v1",
        )
        for item in section_titles
        if item.start_seconds >= 0.15 and item.start_seconds <= duration_seconds - 0.15
    ]
    content = {
        "schema_version": "presentation_spec_v1",
        "strategy_version": PRESENTATION_STRATEGY_VERSION,
        "profile_id": PRESENTATION_PROFILE_ID,
        "captions": CaptionsPresentationOut(
            policy_version=CAPTION_POLICY_VERSION,
            preset="subtle_longform_v1",
            cues=cues,
        ).model_dump(mode="json"),
        "section_titles": [item.model_dump(mode="json") for item in section_titles],
        "branding": BrandingDisabledOut(preset="branding_disabled_v1", enabled=False).model_dump(
            mode="json"
        ),
        "transitions": [item.model_dump(mode="json") for item in transitions],
    }
    return PresentationSpecOut.model_validate({**content, "fingerprint": canonical_hash(content)})
