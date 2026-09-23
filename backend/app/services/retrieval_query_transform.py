"""Language-aware retrieval query transformation for visual search.

This module addresses the multilingual embedding mismatch problem where
English queries have degraded semantic scores when matched against
Portuguese-indexed temporal segments.

Strategy: Generate retrieval queries in the predominant language of the
indexed content to achieve same-language semantic matching while preserving
the original editorial query for audit/display purposes.

Baseline metrics (2026-09-22):
- PT-BR avg semantic score: ~0.666
- EN avg semantic score: ~0.564
- PT-BR resolved: ~100%
- EN resolved: ~90.5%
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Supported locales
ContentLocale = Literal["pt-BR", "en-US"]
RetrievalLanguage = Literal["pt", "en"]

# Simple language detection patterns
PT_PATTERNS = [
    r"\b(de|da|do|das|dos|em|na|no|nas|nos|com|para|por|que|uma|um|o|a|os|as)\b",
    r"\b(é|são|está|estão|tem|têm|foi|foram|ser|estar|ter)\b",
    r"\b(mostrando|demonstrando|destacando|reagindo|sendo|tendo)\b",
    r"\b(missão|personagem|cenário)\b",
]

EN_PATTERNS = [
    r"\b(the|a|an|of|in|on|at|to|for|with|is|are|was|were|being|having)\b",
    r"\b(showing|demonstrating|highlighting|reacting|walking|running|driving)\b",
    r"\b(footage|clip|scene|mission|character|scenario|pedestrian|traffic)\b",
]


@dataclass(frozen=True, slots=True)
class RetrievalTransform:
    """Result of retrieval query transformation."""

    original_query: str
    original_language: str | None  # Detected or provided
    retrieval_query: str
    retrieval_language: RetrievalLanguage
    strategy: str
    variant_used: str  # Which variant produced the retrieval query


def detect_query_language(query: str) -> str | None:
    """Detect the language of a query using simple pattern matching.

    Returns 'pt', 'en', or None if undetermined.
    This is a lightweight detection - not meant for complex multilingual text.
    """
    query_lower = query.lower()

    pt_matches = sum(1 for pattern in PT_PATTERNS if re.search(pattern, query_lower))
    en_matches = sum(1 for pattern in EN_PATTERNS if re.search(pattern, query_lower))

    if pt_matches > en_matches:
        return "pt"
    if en_matches > pt_matches:
        return "en"
    return None


def detect_content_language_from_sessions(
    session_summaries: tuple[dict, ...] | None,
) -> RetrievalLanguage:
    """Determine predominant language of indexed content from session metadata.

    Falls back to Portuguese as the predominant language of the canonical corpus.
    """
    if not session_summaries:
        return "pt"

    # Only explicit index or session language metadata is trustworthy.
    pt_count = 0
    en_count = 0

    for summary in session_summaries:
        # Check for explicit language metadata
        lang = summary.get("notes_language") or summary.get("language")
        if lang:
            if lang.startswith("pt"):
                pt_count += 1
            elif lang.startswith("en"):
                en_count += 1
            continue

    # Default to Portuguese for the canonical corpus
    if pt_count >= en_count:
        return "pt"
    return "en"


# Translation glossary for common visual query terms
# This is a minimal glossary for key terms - not a full translation system
TRANSLATION_GLOSSARY: dict[str, dict[str, str]] = {
    # English -> Portuguese
    "en_to_pt": {
        # Phrases first (longer matches)
        "wide shot": "plano aberto",
        "close up": "close-up",
        "car accidents": "acidentes de carro",
        "car physics": "física de carros",
        "open world": "mundo aberto",
        # Game-specific terms (keep as-is)
        "gta iv": "GTA IV",
        "gta v": "GTA V",
        "gta vi": "GTA VI",
        "gta 4": "GTA IV",
        "gta 5": "GTA V",
        "gta 6": "GTA VI",
        "liberty city": "Liberty City",
        "vice city": "Vice City",
        "los santos": "Los Santos",
        # Common visual query terms
        "gameplay": "gameplay",
        "footage": "gameplay",
        "clip": "clipe",
        "showing": "mostrando",
        "demonstrating": "demonstrando",
        "highlighting": "destacando",
        "featuring": "apresentando",
        "displaying": "exibindo",
        "depicting": "representando",
        "illustrating": "ilustrando",
        "pedestrians": "pedestres",
        "pedestrian": "pedestre",
        "reacting": "reagindo",
        "reactions": "reações",
        "reaction": "reação",
        "walking": "andando",
        "running": "correndo",
        "driving": "dirigindo",
        "car": "carro",
        "cars": "carros",
        "vehicle": "veículo",
        "vehicles": "veículos",
        "street": "rua",
        "streets": "ruas",
        "intersection": "interseção",
        "traffic": "trânsito",
        "crowded": "lotado",
        "busy": "movimentado",
        "dense": "denso",
        "urban": "urbano",
        "city": "cidade",
        "night": "noite",
        "daytime": "dia",
        "nighttime": "noite",
        "scene": "cena",
        "scenes": "cenas",
        "npc": "NPC",
        "npcs": "NPCs",
        "character": "personagem",
        "characters": "personagens",
        "player": "jogador",
        "physics": "física",
        "engine": "motor",
        "animation": "animação",
        "animations": "animações",
        "accident": "acidente",
        "accidents": "acidentes",
        "crash": "colisão",
        "crashes": "colisões",
        "fight": "briga",
        "fighting": "brigando",
        "combat": "combate",
        "shooting": "tiro",
        "explosion": "explosão",
        "fire": "fogo",
        "weather": "clima",
        "rain": "chuva",
        "sunny": "ensolarado",
        "ambient": "ambiente",
        "atmosphere": "atmosfera",
        "atmospheric": "atmosférico",
        "comparison": "comparação",
        "comparing": "comparando",
        "contrast": "contraste",
        "transition": "transição",
        "evidence": "evidência",
        "illustration": "ilustração",
        "montage": "montagem",
        "quick": "rápido",
        "slow": "lento",
        "smooth": "suave",
        "chaotic": "caótico",
        "calm": "calmo",
        "peaceful": "pacífico",
        "violent": "violento",
        "spontaneous": "espontâneo",
        "organic": "orgânico",
        "dynamic": "dinâmico",
        "static": "estático",
        "moving": "em movimento",
        "stationary": "parado",
    },
    # Portuguese -> English
    "pt_to_en": {
        # Phrases first
        "plano aberto": "wide shot",
        "acidentes de carro": "car accidents",
        "física de carros": "car physics",
        "mundo aberto": "open world",
        # Game-specific terms
        "gta iv": "GTA IV",
        "gta v": "GTA V",
        "gta vi": "GTA VI",
        "liberty city": "Liberty City",
        "vice city": "Vice City",
        "los santos": "Los Santos",
        # Common terms
        "gameplay": "gameplay",
        "mostrando": "showing",
        "demonstrando": "demonstrating",
        "destacando": "highlighting",
        "apresentando": "featuring",
        "exibindo": "displaying",
        "representando": "depicting",
        "ilustrando": "illustrating",
        "pedestres": "pedestrians",
        "pedestre": "pedestrian",
        "reagindo": "reacting",
        "reações": "reactions",
        "reação": "reaction",
        "andando": "walking",
        "correndo": "running",
        "dirigindo": "driving",
        "carro": "car",
        "carros": "cars",
        "veículo": "vehicle",
        "veículos": "vehicles",
        "rua": "street",
        "ruas": "streets",
        "interseção": "intersection",
        "trânsito": "traffic",
        "lotado": "crowded",
        "movimentado": "busy",
        "denso": "dense",
        "urbano": "urban",
        "cidade": "city",
        "noite": "night",
        "dia": "daytime",
        "cena": "scene",
        "cenas": "scenes",
        "personagem": "character",
        "personagens": "characters",
        "jogador": "player",
        "física": "physics",
        "motor": "engine",
        "animação": "animation",
        "animações": "animations",
        "acidente": "accident",
        "acidentes": "accidents",
        "colisão": "crash",
        "colisões": "crashes",
        "briga": "fight",
        "brigando": "fighting",
        "combate": "combat",
        "tiro": "shooting",
        "explosão": "explosion",
        "fogo": "fire",
        "clima": "weather",
        "chuva": "rain",
        "ensolarado": "sunny",
        "ambiente": "ambient",
        "atmosfera": "atmosphere",
        "atmosférico": "atmospheric",
        "comparação": "comparison",
        "comparando": "comparing",
        "contraste": "contrast",
        "transição": "transition",
        "evidência": "evidence",
        "ilustração": "illustration",
        "montagem": "montage",
        "rápido": "quick",
        "lento": "slow",
        "suave": "smooth",
        "caótico": "chaotic",
        "calmo": "calm",
        "pacífico": "peaceful",
        "violento": "violent",
        "espontâneo": "spontaneous",
        "orgânico": "organic",
        "dinâmico": "dynamic",
        "estático": "static",
        "em movimento": "moving",
        "parado": "stationary",
    },
}


def translate_query_terms(
    query: str,
    source_lang: str,
    target_lang: str,
) -> str:
    """Translate key terms in query using glossary.

    This is NOT a full translation - it replaces known terms to improve
    semantic matching. Unknown terms are preserved.
    """
    if source_lang == target_lang:
        return query

    glossary_key = f"{source_lang}_to_{target_lang}"
    glossary = TRANSLATION_GLOSSARY.get(glossary_key, {})

    if not glossary:
        return query

    # Tokenize and translate word by word for cleaner results
    words = query.split()
    translated_words = []
    i = 0
    while i < len(words):
        # Try matching longer phrases first (up to 3 words)
        matched = False
        for length in range(3, 0, -1):
            if i + length <= len(words):
                phrase = " ".join(words[i:i+length])
                phrase_lower = phrase.lower()
                if phrase_lower in glossary:
                    translated_words.append(glossary[phrase_lower])
                    i += length
                    matched = True
                    break
                # Try case-sensitive match
                if phrase in glossary:
                    translated_words.append(glossary[phrase])
                    i += length
                    matched = True
                    break
        if not matched:
            # Single word translation
            word_lower = words[i].lower()
            if word_lower in glossary:
                translated_words.append(glossary[word_lower])
            else:
                translated_words.append(words[i])
            i += 1

    return " ".join(translated_words)


def transform_retrieval_query(
    original_query: str,
    *,
    content_locale: ContentLocale | None = None,
    index_language: RetrievalLanguage | None = None,
    session_summaries: tuple[dict, ...] | None = None,
) -> RetrievalTransform:
    """Transform a visual query for optimal retrieval against indexed content.

    The goal is to produce a retrieval query in the same language as the
    indexed content to maximize semantic similarity scores.

    Args:
        original_query: The editorial visual query from the script
        content_locale: The content locale from workflow (pt-BR or en-US)
        index_language: Explicit language of indexed content (overrides detection)
        session_summaries: Metadata from media sessions to detect language

    Returns:
        RetrievalTransform with original/retrieval queries and metadata
    """
    # Detect original query language
    detected_lang = detect_query_language(original_query)

    # Use content_locale if provided, otherwise detected
    query_language = None
    if content_locale:
        query_language = "pt" if content_locale == "pt-BR" else "en"
    elif detected_lang:
        query_language = detected_lang

    # Determine index language
    if index_language:
        target_lang = index_language
    else:
        target_lang = detect_content_language_from_sessions(session_summaries)

    # Decide if transformation is needed
    if query_language == target_lang:
        # Same language - no transformation needed
        return RetrievalTransform(
            original_query=original_query,
            original_language=query_language,
            retrieval_query=original_query,
            retrieval_language=target_lang,
            strategy="same_language",
            variant_used="original",
        )

    # Cross-language transformation needed
    if query_language and target_lang:
        # Translate query terms
        translated = translate_query_terms(
            original_query,
            source_lang=query_language,
            target_lang=target_lang,
        )

        # Check if translation actually changed anything
        if translated != original_query:
            return RetrievalTransform(
                original_query=original_query,
                original_language=query_language,
                retrieval_query=translated,
                retrieval_language=target_lang,
                strategy="term_translation",
                variant_used="translated",
            )

    # Fallback: use original query
    return RetrievalTransform(
        original_query=original_query,
        original_language=query_language,
        retrieval_query=original_query,
        retrieval_language=target_lang,
        strategy="fallback_original",
        variant_used="original",
    )


def build_retrieval_query_variants(
    original_query: str,
    *,
    content_locale: ContentLocale | None = None,
    index_language: RetrievalLanguage | None = None,
) -> list[tuple[str, str]]:
    """Build multiple retrieval query variants for testing.

    Returns list of (query_variant, variant_name) tuples.
    """
    variants: list[tuple[str, str]] = []

    # Always include original
    variants.append((original_query, "original"))

    # Add translated variant if cross-language
    transform = transform_retrieval_query(
        original_query,
        content_locale=content_locale,
        index_language=index_language,
    )

    if transform.retrieval_query != original_query:
        variants.append((transform.retrieval_query, "translated"))

    return variants


# Strategy version for fingerprint inclusion
RETRIEVAL_STRATEGY_VERSION = "v1_term_translation"
