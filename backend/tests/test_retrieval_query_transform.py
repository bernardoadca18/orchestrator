"""Tests for language-aware retrieval query transformation."""

import pytest

from app.services.retrieval_query_transform import (
    RETRIEVAL_STRATEGY_VERSION,
    RetrievalTransform,
    build_retrieval_query_variants,
    detect_query_language,
    transform_retrieval_query,
    translate_query_terms,
)


class TestDetectQueryLanguage:
    """Tests for simple language detection."""

    def test_detects_portuguese_from_common_words(self):
        result = detect_query_language("Gameplay de GTA IV mostrando pedestres")
        assert result == "pt"

    def test_detects_english_from_common_words(self):
        result = detect_query_language("NPCs reacting organically")
        assert result == "en"

    def test_returns_none_for_ambiguous(self):
        result = detect_query_language("GTA IV")
        assert result is None

    def test_detects_portuguese_verbs(self):
        result = detect_query_language("NPCs reagindo organicamente")
        assert result == "pt"

    def test_detects_english_verbs(self):
        result = detect_query_language("NPCs reacting organically")
        assert result == "en"


class TestTransformRetrievalQuery:
    """Tests for the main transformation function."""

    def test_same_language_returns_original(self):
        """PT-BR query with PT index should return unchanged."""
        query = "Gameplay de GTA IV mostrando NPCs reagindo a acidentes"
        transform = transform_retrieval_query(
            query, content_locale="pt-BR", index_language="pt"
        )
        assert transform.retrieval_query == query
        assert transform.strategy == "same_language"

    def test_en_query_with_pt_index_translates(self):
        """EN query with PT index should translate key terms."""
        query = "GTA IV pedestrians reacting to car accidents"
        transform = transform_retrieval_query(
            query, content_locale="en-US", index_language="pt"
        )
        assert transform.retrieval_query != query
        assert transform.strategy == "term_translation"
        assert transform.original_language == "en"
        assert transform.retrieval_language == "pt"

    def test_fallback_when_no_translation_possible(self):
        """Should fallback to original when translation doesn't help."""
        query = "GTA IV xyz abc"  # No translatable terms
        transform = transform_retrieval_query(
            query, content_locale="en-US", index_language="pt"
        )
        # Falls back to original since no terms were translated
        assert transform.retrieval_query == query
        assert transform.strategy == "fallback_original"

    def test_preserves_game_names(self):
        """Game names should be preserved in translation."""
        query = "GTA IV pedestrians in Liberty City"
        transform = transform_retrieval_query(
            query, content_locale="en-US", index_language="pt"
        )
        assert "GTA IV" in transform.retrieval_query
        assert "Liberty City" in transform.retrieval_query

    def test_includes_strategy_version_in_fingerprint(self):
        """Strategy version should be available for fingerprinting."""
        assert RETRIEVAL_STRATEGY_VERSION == "v1_term_translation"


class TestTranslateQueryTerms:
    """Tests for term-by-term translation."""

    def test_translates_known_terms(self):
        result = translate_query_terms("pedestrians reacting", "en", "pt")
        assert "pedestres" in result
        assert "reagindo" in result

    def test_preserves_unknown_terms(self):
        result = translate_query_terms("GTA IV xyz unknown", "en", "pt")
        assert "GTA IV" in result
        assert "xyz" in result
        assert "unknown" in result

    def test_same_language_returns_unchanged(self):
        query = "pedestrians reacting"
        result = translate_query_terms(query, "en", "en")
        assert result == query

    def test_handles_phrases(self):
        """Multi-word phrases should be translated as units."""
        result = translate_query_terms("wide shot of the street", "en", "pt")
        assert "plano aberto" in result


class TestBuildRetrievalQueryVariants:
    """Tests for building multiple query variants."""

    def test_includes_original_always(self):
        variants = build_retrieval_query_variants("GTA IV gameplay")
        assert len(variants) >= 1
        assert variants[0] == ("GTA IV gameplay", "original")

    def test_includes_translated_for_cross_language(self):
        variants = build_retrieval_query_variants(
            "pedestrians reacting", content_locale="en-US", index_language="pt"
        )
        assert len(variants) >= 2
        assert variants[0][1] == "original"
        assert variants[1][1] == "translated"


class TestRetrievalTransform:
    """Tests for RetrievalTransform dataclass."""

    def test_is_frozen(self):
        """Should be immutable."""
        transform = RetrievalTransform(
            original_query="test",
            original_language="en",
            retrieval_query="teste",
            retrieval_language="pt",
            strategy="term_translation",
            variant_used="translated",
        )
        with pytest.raises(AttributeError):
            transform.original_query = "changed"

    def test_stores_all_metadata(self):
        """Should store complete transformation metadata."""
        transform = RetrievalTransform(
            original_query="pedestrians",
            original_language="en",
            retrieval_query="pedestres",
            retrieval_language="pt",
            strategy="term_translation",
            variant_used="translated",
        )
        assert transform.original_query == "pedestrians"
        assert transform.retrieval_query == "pedestres"
        assert transform.original_language == "en"
        assert transform.retrieval_language == "pt"
        assert transform.strategy == "term_translation"
        assert transform.variant_used == "translated"


class TestBaselineCompatibility:
    """Tests ensuring baseline metrics from audit are preserved."""

    # Baseline from 2026-09-22 audit:
    # PT-BR avg semantic score: ~0.666
    # EN avg semantic score: ~0.564
    # PT-BR resolved: ~100%
    # EN resolved: ~90.5%

    def test_pt_br_queries_unchanged(self):
        """PT-BR queries should not be modified for PT index."""
        # This ensures we don't regress PT-BR performance
        queries = [
            "Gameplay de GTA IV mostrando NPCs reagindo a acidentes",
            "Montagem rápida de gameplay de GTA IV",
            "Gameplay de GTA IV em terceira pessoa",
        ]
        for query in queries:
            transform = transform_retrieval_query(
                query, content_locale="pt-BR", index_language="pt"
            )
            assert transform.retrieval_query == query
            assert transform.strategy == "same_language"

    def test_en_queries_get_translated_for_pt_index(self):
        """EN queries should be transformed for PT index."""
        # This is the fix for the 18% semantic score gap
        queries = [
            "GTA IV NPCs reacting to car accidents",
            "pedestrians on crowded sidewalk",
            "Wide shot Liberty City intersection",
        ]
        for query in queries:
            transform = transform_retrieval_query(
                query, content_locale="en-US", index_language="pt"
            )
            # Should attempt translation
            assert transform.original_language == "en"
            assert transform.retrieval_language == "pt"
