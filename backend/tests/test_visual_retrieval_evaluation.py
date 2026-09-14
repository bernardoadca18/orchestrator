from __future__ import annotations

import csv
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.clients.media_indexer_search import (
    ImageSearchCandidate,
    ImageSearchResultSet,
    MediaProjectMetadata,
    MediaSearchCandidate,
    MediaSearchResultSet,
    MediaSessionSummary,
)
from app.core.config import Settings
from app.services.visual_retrieval_evaluation import (
    CandidateReport,
    EvaluationDataset,
    EvaluationQuerySpec,
    EvaluationScopeSpec,
    StaticFilterSpec,
    StaticScopeSpec,
    VisualRetrievalEvaluator,
    VisualRetrievalReport,
    canonical_game_from_provenance,
    canonical_game_from_session_label,
    comparison,
    expected_game_status,
    labeled_metrics,
    load_dataset,
    objective_metrics,
    parse_review_labels,
    render_review_csv,
    temporal_scope_status,
)

PROJECT_ID = uuid.UUID("30000000-0000-4000-8000-000000000001")
IV = uuid.UUID("30000000-0000-4000-8000-000000000004")
V = uuid.UUID("30000000-0000-4000-8000-000000000005")
VI = uuid.UUID("30000000-0000-4000-8000-000000000006")


class FakeCorpusProvider:
    def __init__(self, *, leak: bool = False, static_game: str = "gta_vi") -> None:
        self.leak = leak
        self.static_game = static_game
        self.project_calls: list[list[uuid.UUID] | None] = []
        self.sessions = (
            MediaSessionSummary(IV, PROJECT_ID, uuid.uuid4(), "gta_iv_gameplay_ep000", "ready"),
            MediaSessionSummary(V, PROJECT_ID, uuid.uuid4(), "gta_v_gameplay_ep005", "ready"),
            MediaSessionSummary(VI, PROJECT_ID, uuid.uuid4(), "gta_vi_official_trailer_1", "ready"),
        )

    async def list_projects(self):
        return (MediaProjectMetadata(PROJECT_ID, "GTA / Rockstar Canonical Corpus", None),)

    async def list_project_sessions(self, project_id):
        assert project_id == PROJECT_ID
        return self.sessions

    async def search_project(self, project_id, *, query, session_ids, **kwargs):
        self.project_calls.append(session_ids)
        selected = list(session_ids or [item.id for item in self.sessions])
        if self.leak and session_ids:
            selected = [VI]
        results = tuple(
            self._candidate(session_id, rank)
            for rank, session_id in enumerate(selected, 1)
        )
        return MediaSearchResultSet(
            query=query,
            scope="project",
            project_id=project_id,
            session_id=None,
            results=results,
            searched_sessions=len(selected),
            skipped_sessions=0,
            session_summaries=(),
            strategy="media_search_v4_hybrid_evidence",
            embedding_provider="ollama",
            embedding_model="mxbai-embed-large:latest",
            embedding_dimensions=1024,
        )

    async def search_images(self, project_id, *, query, filters, **kwargs):
        candidate = ImageSearchCandidate(
            project_id=project_id,
            media_id=uuid.uuid4(),
            score=0.75,
            rank=1,
            original_filename="official.png",
            content_type="image/png",
            byte_size=123,
            sha256="a" * 64,
            width=1920,
            height=1080,
            format_name="PNG",
            provenance={"source_owner": "rockstar_games", "game": self.static_game},
            semantic_preview="official image",
            image_semantic_index_id=uuid.uuid4(),
            semantic_index={"strategy": "operator_semantic_text_embedding_v1"},
        )
        return ImageSearchResultSet(query, project_id, (candidate,), 1, 1, filters)

    @staticmethod
    def _candidate(session_id: uuid.UUID, rank: int) -> MediaSearchCandidate:
        return MediaSearchCandidate(
            session_id=session_id,
            media_id=uuid.uuid5(uuid.NAMESPACE_URL, str(session_id)),
            start_seconds=10.0,
            end_seconds=20.0,
            duration_seconds=10.0,
            score=0.8,
            rank=rank,
            session_label="fixture",
            media_filename="fixture.mp4",
            temporal_index_id=uuid.uuid5(uuid.NAMESPACE_OID, str(session_id)),
            retrieval_candidate_id=None,
            transcript_preview="fixture",
            matched_note_ids=(),
            provenance={"compatibility_signature": "fixture"},
        )


def _dataset(queries: list[EvaluationQuerySpec]) -> EvaluationDataset:
    return EvaluationDataset(
        name="scope-matrix",
        default_scope=EvaluationScopeSpec(),
        queries=queries,
    )


def _candidate_report(*, source_type: str = "temporal", game: str = "gta_iv"):
    return CandidateReport(
        candidate_identity="candidate",
        source_type=source_type,
        project_id=PROJECT_ID,
        session_id=IV if source_type == "temporal" else None,
        media_id=uuid.uuid4(),
        game=game,
        scope_status="IN_SCOPE",
        game_status="EXPECTED_GAME",
        rank=1,
        semantic_score=0.8,
    )


def test_report_schema_rejects_unknown_version() -> None:
    with pytest.raises(ValidationError):
        VisualRetrievalReport.model_validate(
            {
                "schema_version": "future",
                "harness_version": "gta_visual_retrieval_evaluation_v1",
            }
        )


def test_dataset_identity_is_deterministic_and_scope_sensitive(tmp_path: Path) -> None:
    fixture = Path("../data/gta-visual-retrieval/probes-v1.json")
    first = load_dataset(fixture)
    copied = tmp_path / "copy.json"
    copied.write_text(fixture.read_text())
    second = load_dataset(copied)
    assert first.dataset_identity == second.dataset_identity
    changed = first.model_copy(deep=True)
    changed.queries[0].scope = EvaluationScopeSpec(games=["gta_v"])
    assert changed.dataset_identity != first.dataset_identity


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("gta_iv_gameplay_ep000", "gta_iv"),
        ("gta_v_gameplay_ep005", "gta_v"),
        ("gta_vi_official_trailer_1", "gta_vi"),
        ("001", "UNKNOWN_GAME"),
    ],
)
def test_canonical_session_game_classifier(label: str, expected: str) -> None:
    assert canonical_game_from_session_label(label) == expected


def test_wrong_game_and_unknown_metadata_are_objective() -> None:
    assert canonical_game_from_provenance({}) == "UNKNOWN_GAME"
    assert expected_game_status("UNKNOWN_GAME", "gta_vi") == "UNKNOWN_GAME"
    assert expected_game_status("gta_v", "gta_vi") == "WRONG_GAME"
    assert expected_game_status("gta_vi", "gta_vi") == "EXPECTED_GAME"


def test_explicit_allowed_sessions_and_project_wide_semantics_differ() -> None:
    assert temporal_scope_status(IV, frozenset({IV})) == "IN_SCOPE"
    assert temporal_scope_status(V, frozenset({IV})) == "OUT_OF_SCOPE"
    assert temporal_scope_status(VI, frozenset()) == "PROJECT_WIDE"


def test_temporal_and_static_candidates_remain_distinct() -> None:
    temporal = _candidate_report()
    static = _candidate_report(source_type="static", game="UNKNOWN_GAME")
    assert temporal.session_id == IV
    assert static.session_id is None
    assert static.source_type == "static"


@pytest.mark.asyncio
async def test_cross_game_raw_scope_matrix_and_out_of_scope_preferred() -> None:
    provider = FakeCorpusProvider()
    queries = [
        EvaluationQuerySpec(
            id=game,
            query=f"probe {game}",
            expected_game=game,
            scope=EvaluationScopeSpec(games=[game]),
        )
        for game in ("gta_iv", "gta_v", "gta_vi")
    ]
    queries.extend(
        [
            EvaluationQuerySpec(
                id="mixed",
                query="comparison",
                scope=EvaluationScopeSpec(games=["gta_iv", "gta_v"]),
            ),
            EvaluationQuerySpec(
                id="preferred",
                query="street",
                expected_game="gta_iv",
                preferred_game="gta_v",
                scope=EvaluationScopeSpec(games=["gta_iv"]),
            ),
            EvaluationQuerySpec(
                id="project-wide",
                query="all games",
                scope=EvaluationScopeSpec(media_session_ids=[]),
            ),
        ]
    )
    evaluator = VisualRetrievalEvaluator(Settings(), media_provider=provider)
    report = await evaluator.evaluate(
        _dataset(queries), mode="dataset", input_identity="matrix"
    )
    assert report.violations == []
    assert all(
        candidate.scope_status == "IN_SCOPE"
        for query in report.queries[:-1]
        for candidate in query.raw_temporal
    )
    assert {item.game for item in report.queries[3].raw_temporal} == {"gta_iv", "gta_v"}
    assert report.queries[4].diagnostics["preferred_session_in_scope"] is False
    assert {
        item.scope_status for item in report.queries[-1].raw_temporal
    } == {"PROJECT_WIDE"}


@pytest.mark.asyncio
async def test_raw_provider_scope_leak_is_a_blocker_violation() -> None:
    evaluator = VisualRetrievalEvaluator(Settings(), media_provider=FakeCorpusProvider(leak=True))
    report = await evaluator.evaluate(
        _dataset(
            [
                EvaluationQuerySpec(
                    id="iv-only",
                    query="Niko",
                    expected_game="gta_iv",
                    scope=EvaluationScopeSpec(games=["gta_iv"]),
                )
            ]
        ),
        mode="dataset",
        input_identity="leak",
    )
    assert report.metrics["temporal_scope_leakage_count"] == 1
    assert report.violations[0]["category"] == "MEDIA_SCOPE_LEAKAGE"


@pytest.mark.asyncio
async def test_static_game_filter_and_unknown_are_not_invented() -> None:
    scope = EvaluationScopeSpec(
        games=["gta_vi"],
        static_images=StaticScopeSpec(
            enabled=True, filters=[StaticFilterSpec(game="gta_vi")]
        ),
    )
    evaluator = VisualRetrievalEvaluator(
        Settings(), media_provider=FakeCorpusProvider(static_game="gta_iv")
    )
    report = await evaluator.evaluate(
        _dataset(
            [
                EvaluationQuerySpec(
                    id="static",
                    query="Lucia artwork",
                    expected_game="gta_vi",
                    scope=scope,
                )
            ]
        ),
        mode="dataset",
        input_identity="static",
    )
    candidate = report.queries[0].raw_static[0]
    assert candidate.scope_status == "OUT_OF_SCOPE"
    assert candidate.game_status == "WRONG_GAME"
    assert report.metrics["static_wrong_game_count"] == 1
    assert report.metrics["static_scope_leakage_count"] == 1
    assert report.violations[0]["category"] == "STATIC_SCOPE_LEAKAGE"


@pytest.mark.asyncio
async def test_review_parser_metrics_and_comparison(tmp_path: Path) -> None:
    evaluator = VisualRetrievalEvaluator(Settings(), media_provider=FakeCorpusProvider())
    report = await evaluator.evaluate(
        _dataset(
            [
                EvaluationQuerySpec(
                    id="q1", query="Niko", scope=EvaluationScopeSpec(games=["gta_iv"])
                )
            ]
        ),
        mode="dataset",
        input_identity="review",
    )
    report.queries[0].selected = report.queries[0].raw_temporal[0]
    report.queries[0].selection_status = "resolved"
    review = tmp_path / "review.csv"
    review.write_text(render_review_csv(report))
    rows = list(csv.DictReader(review.open()))
    rows[0]["selected_label"] = "GOOD"
    with review.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    parsed = parse_review_labels(review, report)
    assert labeled_metrics(report, parsed)["good_rate"] == 1.0
    assert comparison(report, report)["metrics"]["raw_candidate_queries"]["delta"] == 0


def test_objective_metrics_count_reuse_and_scores() -> None:
    candidate = _candidate_report()
    from app.services.visual_retrieval_evaluation import QueryEvaluationReport

    queries = [
        QueryEvaluationReport(
            id=str(index),
            query="query",
            intent="evidence",
            importance="required",
            scope={},
            selection_status="resolved",
            selected=candidate,
            raw_temporal=[candidate],
            latency_seconds=0.1,
        )
        for index in range(2)
    ]
    metrics = objective_metrics(queries)
    assert metrics["resolved"] == 2
    assert metrics["duplicate_reuse_rate"] == 0.5
    assert metrics["raw_temporal_scores"]["mean"] == 0.8
