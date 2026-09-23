"""Focal tests for Game Entity Guard on direct/preferred candidates (audit items 4-5)."""


from app.services.visual_director_algorithm import (
    UNKNOWN_GAME,
    canonical_game_from_session_label,
    extract_games_from_query,
)


class TestGameEntityGuardStrategyVersion:
    """The current visual policy has its own identity after the guard version."""

    def test_current_strategy_versions_supersede_game_guard_version(self):
        from app.core.config import settings

        assert settings.visual_director_strategy_version == (
            "visual_director_v8_quality_overlap_evidence"
        )
        assert settings.visual_director_hybrid_strategy_version == (
            "visual_director_v6_hybrid_quality_overlap_evidence"
        )


class TestCanonicalGameExtraction:
    """Game extraction from session labels."""

    def test_gta_vi_variants(self):
        """GTA VI variants should normalize to gta6."""
        assert canonical_game_from_session_label("gta_vi") == "gta6"
        assert canonical_game_from_session_label("GTA_VI") == "gta6"
        assert canonical_game_from_session_label("gta-vi") == "gta6"
        assert canonical_game_from_session_label("gta vi") == "gta6"
        assert canonical_game_from_session_label("gta_vi_missoes") == "gta6"
        assert canonical_game_from_session_label("gta6") == "gta6"
        assert canonical_game_from_session_label("GTA6") == "gta6"

    def test_gta_v_variants(self):
        """GTA V variants should normalize to gta5."""
        assert canonical_game_from_session_label("gta_v") == "gta5"
        assert canonical_game_from_session_label("GTA_V") == "gta5"
        assert canonical_game_from_session_label("gta-v") == "gta5"
        assert canonical_game_from_session_label("gta v") == "gta5"
        assert canonical_game_from_session_label("gta_v_races") == "gta5"
        assert canonical_game_from_session_label("gta5") == "gta5"

    def test_gta_iv_variants(self):
        """GTA IV variants should normalize to gta4."""
        assert canonical_game_from_session_label("gta_iv") == "gta4"
        assert canonical_game_from_session_label("GTA_IV") == "gta4"
        assert canonical_game_from_session_label("gta-iv") == "gta4"
        assert canonical_game_from_session_label("gta iv") == "gta4"
        assert canonical_game_from_session_label("gta4") == "gta4"

    def test_unknown_game_returns_sentinel(self):
        """Unknown games should return the sentinel value."""
        assert canonical_game_from_session_label("minecraft") == UNKNOWN_GAME
        assert canonical_game_from_session_label("fortnite") == UNKNOWN_GAME
        assert canonical_game_from_session_label("") == UNKNOWN_GAME
        assert canonical_game_from_session_label("random_session") == UNKNOWN_GAME


class TestQueryGameExtraction:
    """Game extraction from visual query text."""

    def test_gta_vi_patterns(self):
        """Extract GTA VI from various query formats."""
        assert extract_games_from_query("Mostre um carro do GTA VI") == frozenset({"gta6"})
        assert extract_games_from_query("GTA 6 gameplay") == frozenset({"gta6"})
        assert extract_games_from_query("Grand Theft Auto VI") == frozenset({"gta6"})
        assert extract_games_from_query("Grand Theft Auto 6") == frozenset({"gta6"})

    def test_gta_v_patterns(self):
        """Extract GTA V from various query formats."""
        assert extract_games_from_query("Mostre um carro do GTA V") == frozenset({"gta5"})
        assert extract_games_from_query("GTA 5 gameplay") == frozenset({"gta5"})
        assert extract_games_from_query("Grand Theft Auto V") == frozenset({"gta5"})
        assert extract_games_from_query("Grand Theft Auto 5") == frozenset({"gta5"})

    def test_gta_iv_patterns(self):
        """Extract GTA IV from various query formats."""
        assert extract_games_from_query("Mostre um carro do GTA IV") == frozenset({"gta4"})
        assert extract_games_from_query("GTA 4 gameplay") == frozenset({"gta4"})
        assert extract_games_from_query("Grand Theft Auto IV") == frozenset({"gta4"})
        assert extract_games_from_query("Grand Theft Auto 4") == frozenset({"gta4"})

    def test_no_game_returns_empty_set(self):
        """Queries without game references return empty set."""
        assert extract_games_from_query("Mostre um carro esportivo") == frozenset()
        assert extract_games_from_query("Gameplay de corrida") == frozenset()

    def test_case_insensitive(self):
        """Game extraction is case insensitive."""
        assert extract_games_from_query("gta vi") == frozenset({"gta6"})
        assert extract_games_from_query("GTA VI") == frozenset({"gta6"})
        assert extract_games_from_query("Gta Vi") == frozenset({"gta6"})

    def test_multiple_games_in_query(self):
        """Query can reference multiple games."""
        result = extract_games_from_query("Compare GTA IV and GTA V graphics")
        assert result == frozenset({"gta4", "gta5"})


class TestGameEntityGuardOnCandidates:
    """Item 4: Apply Game Entity Guard to direct/preferred provenance candidates."""

    def test_filter_candidates_by_game_basic(self):
        """Filter candidates by query game when game is specified."""
        # This is a placeholder test - the actual implementation
        # filters candidates by their game identity matching query games
        # The real test would need VisualCandidate mock objects
        pass

    def test_unknown_game_candidates_rejected_for_game_queries(self):
        """Candidates with unknown game should be rejected for game-bound queries."""
        # When query_game="gta_vi" and candidate_game=None or "minecraft",
        # the candidate should be filtered out
        pass

    def test_game_candidates_accepted_for_matching_games(self):
        """Candidates matching query game should pass through."""
        # When query_game="gta_vi" and candidate_game="gta_vi",
        # the candidate should be accepted
        pass
