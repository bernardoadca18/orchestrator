"""Pure deterministic policies used by the Stage 14 Visual Director."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from uuid import UUID


UNKNOWN_GAME = "__unknown_game__"

# Canonical game identifiers
CANONICAL_GAMES = frozenset({"gta3", "gta4", "gta5", "gta6", "rdr2"})


def canonical_game_from_session_label(label: str) -> str:
    """Extract canonical game identifier from session label.

    Mirrors visual_retrieval_evaluation.canonical_game_from_session_label
    to avoid cross-module import coupling.
    """
    normalized = label.strip().casefold().replace("-", "_").replace(" ", "_")
    # Check for GTA VI (6) first to avoid GTA V (5) matching
    for game in ("gta6", "gta_vi", "gta5", "gta_v", "gta4", "gta_iv", "gta3", "gta_iii", "rdr2"):
        if normalized == game or normalized.startswith(f"{game}_"):
            # Normalize to canonical form
            if game in ("gta_vi", "gta6"):
                return "gta6"
            if game in ("gta_v", "gta5"):
                return "gta5"
            if game in ("gta_iv", "gta4"):
                return "gta4"
            if game in ("gta_iii", "gta3"):
                return "gta3"
            return game
    return UNKNOWN_GAME


def extract_games_from_query(query: str) -> frozenset[str]:
    """Extract all canonical game identifiers from visual query text.

    Returns an empty set if no games are mentioned.
    A query can match multiple games (e.g., "GTA IV compared with GTA V").

    Supports:
    - GTA III / GTA 3 / Grand Theft Auto III / Grand Theft Auto 3 → gta3
    - GTA IV / GTA 4 / Grand Theft Auto IV / Grand Theft Auto 4 → gta4
    - GTA V / GTA 5 / Grand Theft Auto V / Grand Theft Auto 5 → gta5
    - GTA VI / GTA 6 / Grand Theft Auto VI / Grand Theft Auto 6 → gta6
    - RDR2 / RDR 2 / Red Dead Redemption 2 / Red Dead Redemption II → rdr2
    """
    normalized = query.casefold()
    games: set[str] = set()

    # GTA VI patterns (GTA 6, GTA VI, Grand Theft Auto VI, etc.)
    if re.search(r"\bgta\s*6\b|\bgta\s*vi\b|\bgrand\s*theft\s*auto\s*6\b|\bgrand\s*theft\s*auto\s*vi\b", normalized):
        games.add("gta6")

    # GTA V patterns (GTA 5, GTA V, Grand Theft Auto V, etc.)
    if re.search(r"\bgta\s*5\b|\bgta\s*v\b|\bgrand\s*theft\s*auto\s*5\b|\bgrand\s*theft\s*auto\s*v\b", normalized):
        games.add("gta5")

    # GTA IV patterns (GTA 4, GTA IV, Grand Theft Auto IV, etc.)
    if re.search(r"\bgta\s*4\b|\bgta\s*iv\b|\bgrand\s*theft\s*auto\s*4\b|\bgrand\s*theft\s*auto\s*iv\b", normalized):
        games.add("gta4")

    # GTA III patterns (GTA 3, GTA III, Grand Theft Auto III, etc.)
    if re.search(r"\bgta\s*3\b|\bgta\s*iii\b|\bgrand\s*theft\s*auto\s*3\b|\bgrand\s*theft\s*auto\s*iii\b", normalized):
        games.add("gta3")

    # RDR2 patterns (RDR2, RDR 2, Red Dead Redemption 2, etc.)
    if re.search(r"\brdr\s*2\b|\brdr2\b|\bred\s*dead\s*redemption\s*2\b|\bred\s*dead\s*redemption\s*ii\b", normalized):
        games.add("rdr2")

    return frozenset(games)


def game_identity_from_provenance(provenance: dict | None) -> str | None:
    """Extract canonical game from structured provenance metadata.

    Returns None if provenance is missing or doesn't contain game info.
    This is the preferred method for StaticImageCandidate and direct provenance.
    """
    if not provenance or not isinstance(provenance, dict):
        return None
    game = provenance.get("game")
    if not game or not isinstance(game, str):
        return None
    normalized = game.strip().casefold().replace("-", "_").replace(" ", "_")
    # Map to canonical identifiers
    if normalized in ("gta6", "gta_vi", "gta vi"):
        return "gta6"
    if normalized in ("gta5", "gta_v", "gta v"):
        return "gta5"
    if normalized in ("gta4", "gta_iv", "gta iv"):
        return "gta4"
    if normalized in ("gta3", "gta_iii", "gta iii"):
        return "gta3"
    if normalized in ("rdr2", "rdr_2", "rdr 2"):
        return "rdr2"
    return None


def candidate_game_identity(candidate: "HybridVisualCandidate") -> str | None:
    """Determine the canonical game identity for any candidate type.

    Returns:
    - Canonical game identifier (e.g., "gta5", "rdr2") if determinable
    - UNKNOWN_GAME if game is unknown but determinable
    - None if game identity cannot be established
    """
    # Try structured provenance first (preferred for images and direct sources)
    game = game_identity_from_provenance(candidate.provenance)
    if game is not None:
        return game

    # For VisualCandidate, try session label
    if hasattr(candidate, "session_label") and candidate.session_label:
        return canonical_game_from_session_label(candidate.session_label)

    # For StaticImageCandidate with no provenance game, check filename as fallback
    if hasattr(candidate, "media_filename") and candidate.media_filename:
        # Conservative filename-based extraction as last resort
        normalized = candidate.media_filename.casefold()
        for pattern, canonical in [
            (r"gta\s*6|gta\s*vi", "gta6"),
            (r"gta\s*5|gta\s*v(?!i)", "gta5"),
            (r"gta\s*4|gta\s*iv", "gta4"),
            (r"gta\s*3|gta\s*iii", "gta3"),
            (r"rdr\s*2|rdr2", "rdr2"),
        ]:
            if re.search(pattern, normalized):
                return canonical

    return None


def filter_candidates_by_game(
    candidates: list["HybridVisualCandidate"],
    query_games: frozenset[str],
) -> list["HybridVisualCandidate"]:
    """Filter candidates to only include those matching the query games.

    This is a conservative guard:
    - If query_games is empty (no games detected in query), all candidates pass through.
    - If query_games is non-empty, only candidates with matching game identity pass.
    - Candidates with unknown game identity are REJECTED for game-bound queries.

    Args:
        candidates: List of video segment or static image candidates
        query_games: Set of canonical game identifiers extracted from query

    Returns:
        Filtered list of candidates matching the query games, or all candidates
        if query_games is empty.
    """
    if not query_games:
        return list(candidates)

    filtered = []
    for candidate in candidates:
        game = candidate_game_identity(candidate)
        # Reject candidates with unknown game when query is game-bound
        if game is None or game == UNKNOWN_GAME:
            continue
        if game in query_games:
            filtered.append(candidate)

    return filtered


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    project_id: UUID
    session_id: UUID
    media_id: UUID
    start_seconds: float
    end_seconds: float
    semantic_score: float | None
    rank: int | None
    session_label: str
    media_filename: str
    temporal_index_id: UUID | None = None
    retrieval_candidate_id: UUID | None = None
    transcript_preview: str | None = None
    matched_note_ids: tuple[UUID, ...] = ()
    search_strategy: str | None = None
    provenance: dict | None = None
    selection_strategy: str = "media_search"
    selection_score: float = 0.0
    score_components: dict | None = None

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def source_type(self) -> str:
        return "video_segment"


@dataclass(frozen=True, slots=True)
class StaticImageCandidate:
    project_id: UUID
    media_id: UUID
    image_semantic_index_id: UUID
    semantic_score: float
    rank: int
    media_filename: str
    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int
    format_name: str
    provenance: dict
    semantic_preview: str | None = None
    semantic_index: dict | None = None
    selection_strategy: str = "hybrid_visual_fusion"
    selection_score: float = 0.0
    score_components: dict | None = None

    @property
    def source_type(self) -> str:
        return "static_image"


type HybridVisualCandidate = VisualCandidate | StaticImageCandidate


def normalize_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("visual query text must not be empty")
    if len(normalized) > 2_000:
        raise ValueError("visual query text exceeds 2000 characters")
    return normalized


def overlap_ratio(left: VisualCandidate, right: VisualCandidate) -> float:
    if left.session_id != right.session_id or left.media_id != right.media_id:
        return 0.0
    overlap = min(left.end_seconds, right.end_seconds) - max(
        left.start_seconds, right.start_seconds
    )
    if overlap <= 0:
        return 0.0
    return max(overlap / left.duration_seconds, overlap / right.duration_seconds)


def deduplicate_candidates(
    candidates: list[VisualCandidate], overlap_threshold: float = 0.5
) -> list[VisualCandidate]:
    """Keep the best of substantially overlapping ranges, deterministically."""
    ordered = sorted(
        candidates,
        key=lambda item: (
            -(item.semantic_score or 0.0),
            str(item.session_id),
            str(item.media_id),
            item.start_seconds,
            item.end_seconds,
        ),
    )
    kept: list[VisualCandidate] = []
    for candidate in ordered:
        if candidate.duration_seconds <= 0:
            continue
        if any(overlap_ratio(candidate, existing) > overlap_threshold for existing in kept):
            continue
        kept.append(candidate)
    return kept


def score_candidate(
    candidate: VisualCandidate,
    *,
    preferred_session_id: UUID | None,
    source_note_id: UUID | None,
    target_duration_seconds: float | None,
    already_selected: list[VisualCandidate],
    intent: str | None = None,
    best_semantic_score: float | None = None,
) -> VisualCandidate:
    """Apply the documented weak bonuses and diversity penalty."""
    semantic = candidate.semantic_score or 0.0
    preferred_session_bonus = 0.05 if candidate.session_id == preferred_session_id else 0.0
    source_note_bonus = (
        0.03 if source_note_id is not None and source_note_id in candidate.matched_note_ids else 0.0
    )
    duration_fit = 0.0
    if target_duration_seconds and target_duration_seconds > 0 and candidate.duration_seconds > 0:
        ratio = min(
            candidate.duration_seconds / target_duration_seconds,
            target_duration_seconds / candidate.duration_seconds,
        )
        duration_fit = 0.02 * ratio
    reuse_overlap = max(
        (overlap_ratio(candidate, previous) for previous in already_selected), default=0.0
    )
    exact_reuse_count = sum(
        overlap_ratio(candidate, previous) >= 0.95 for previous in already_selected
    )
    consecutive_exact_reuse = bool(
        already_selected and overlap_ratio(candidate, already_selected[-1]) >= 0.95
    )
    source_note_anchored = bool(
        source_note_id is not None and source_note_id in candidate.matched_note_ids
    )
    overlap_penalty = 0.12 * reuse_overlap
    exact_reuse_penalty = min(0.36, 0.18 * exact_reuse_count)
    consecutive_reuse_penalty = 0.08 if consecutive_exact_reuse else 0.0
    if source_note_anchored:
        overlap_penalty *= 0.25
        exact_reuse_penalty *= 0.25
        consecutive_reuse_penalty *= 0.25
    reuse_penalty = overlap_penalty + exact_reuse_penalty + consecutive_reuse_penalty
    semantic_band_penalty = 0.0
    if (
        intent == "evidence"
        and best_semantic_score is not None
        and best_semantic_score - semantic > 0.02
    ):
        semantic_band_penalty = 0.25
    score = max(
        0.0,
        min(
            1.0,
            semantic
            + preferred_session_bonus
            + source_note_bonus
            + duration_fit
            - reuse_penalty
            - semantic_band_penalty,
        ),
    )
    return replace(
        candidate,
        selection_score=round(score, 6),
        score_components={
            "semantic": round(semantic, 6),
            "preferred_session_bonus": round(preferred_session_bonus, 6),
            "source_note_bonus": round(source_note_bonus, 6),
            "duration_fit_bonus": round(duration_fit, 6),
            "reuse_overlap": round(reuse_overlap, 6),
            "reuse_penalty": round(reuse_penalty, 6),
            "exact_reuse_count": exact_reuse_count,
            "exact_reuse_penalty": round(exact_reuse_penalty, 6),
            "consecutive_reuse_penalty": round(consecutive_reuse_penalty, 6),
            "source_note_anchored": source_note_anchored,
            "semantic_band_penalty": semantic_band_penalty,
        },
    )


def rank_candidates(
    candidates: list[VisualCandidate],
    *,
    preferred_session_id: UUID | None,
    source_note_id: UUID | None,
    target_duration_seconds: float | None,
    already_selected: list[VisualCandidate],
    intent: str | None = None,
) -> list[VisualCandidate]:
    best_semantic_score = max(
        (candidate.semantic_score or 0.0 for candidate in candidates), default=0.0
    )
    scored = [
        score_candidate(
            candidate,
            preferred_session_id=preferred_session_id,
            source_note_id=source_note_id,
            target_duration_seconds=target_duration_seconds,
            already_selected=already_selected,
            intent=intent,
            best_semantic_score=best_semantic_score,
        )
        for candidate in deduplicate_candidates(candidates)
    ]
    return sorted(
        scored,
        key=lambda item: (
            bool((item.score_components or {}).get("semantic_band_penalty")),
            -item.selection_score,
            -(item.semantic_score or 0.0),
            str(item.session_id),
            str(item.media_id),
            item.start_seconds,
            item.end_seconds,
        ),
    )


def direct_candidate(
    *,
    project_id: UUID,
    session_id: UUID,
    media_id: UUID,
    start_seconds: float,
    end_seconds: float,
    session_label: str,
    media_filename: str,
) -> VisualCandidate:
    return VisualCandidate(
        project_id=project_id,
        session_id=session_id,
        media_id=media_id,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        semantic_score=None,
        rank=None,
        session_label=session_label,
        media_filename=media_filename,
        selection_strategy="preferred_provenance",
        selection_score=1.0,
        score_components={"trusted_preferred_provenance": 1.0},
        provenance={"source": "production_script"},
    )


def _same_source(left: HybridVisualCandidate, right: HybridVisualCandidate) -> bool:
    if isinstance(left, StaticImageCandidate) or isinstance(right, StaticImageCandidate):
        return (
            isinstance(left, StaticImageCandidate)
            and isinstance(right, StaticImageCandidate)
            and left.media_id == right.media_id
        )
    return overlap_ratio(left, right) >= 0.95


def fuse_hybrid_candidates(
    video_candidates: list[VisualCandidate],
    image_candidates: list[StaticImageCandidate],
    *,
    already_selected: list[HybridVisualCandidate],
    preferred_session_id: UUID | None,
    source_note_id: UUID | None,
    rrf_k: int,
    video_weight: float,
    image_weight: float,
    strategy_version: str,
) -> list[HybridVisualCandidate]:
    """Weighted RRF without treating cross-modal native scores as calibrated."""
    videos = deduplicate_candidates(video_candidates)
    images_by_media: dict[UUID, StaticImageCandidate] = {}
    for image_candidate in sorted(
        image_candidates,
        key=lambda item: (item.rank, -item.semantic_score, str(item.media_id)),
    ):
        images_by_media.setdefault(image_candidate.media_id, image_candidate)
    images = list(images_by_media.values())
    candidates: list[HybridVisualCandidate] = list(videos)
    candidates.extend(images)
    scored: list[HybridVisualCandidate] = []
    for candidate in candidates:
        weight = image_weight if isinstance(candidate, StaticImageCandidate) else video_weight
        native_rank = candidate.rank or 1
        rrf_score = weight / (rrf_k + native_rank)
        preferred_bonus = (
            0.003
            if isinstance(candidate, VisualCandidate)
            and candidate.session_id == preferred_session_id
            else 0.0
        )
        note_bonus = (
            0.002
            if isinstance(candidate, VisualCandidate)
            and source_note_id is not None
            and source_note_id in candidate.matched_note_ids
            else 0.0
        )
        reuse_count = sum(_same_source(candidate, previous) for previous in already_selected)
        consecutive_reuse = bool(
            already_selected and _same_source(candidate, already_selected[-1])
        )
        reuse_penalty = min(0.012, 0.004 * reuse_count) + (
            0.003 if consecutive_reuse else 0.0
        )
        fusion_score = max(0.0, rrf_score + preferred_bonus + note_bonus - reuse_penalty)
        scored.append(
            replace(
                candidate,
                selection_strategy=strategy_version,
                selection_score=round(fusion_score, 9),
                score_components={
                    "fusion_strategy": strategy_version,
                    "source_kind": candidate.source_type,
                    "native_rank": native_rank,
                    "native_score": round(candidate.semantic_score or 0.0, 6),
                    "modality_weight": weight,
                    "rrf_k": rrf_k,
                    "rrf_score": round(rrf_score, 9),
                    "preferred_session_bonus": preferred_bonus,
                    "source_note_bonus": note_bonus,
                    "reuse_count": reuse_count,
                    "consecutive_reuse": consecutive_reuse,
                    "reuse_penalty": round(reuse_penalty, 9),
                    "fusion_score": round(fusion_score, 9),
                },
            )
        )
    # Native scores are compared only after modality and native rank have tied.
    return sorted(
        scored,
        key=lambda item: (
            -item.selection_score,
            0 if isinstance(item, VisualCandidate) else 1,
            item.rank or 1,
            -(item.semantic_score or 0.0),
            str(item.media_id),
            item.start_seconds if isinstance(item, VisualCandidate) else 0.0,
        ),
    )
