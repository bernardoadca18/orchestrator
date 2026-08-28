"""Approved, deterministic pronunciation resolution for synthesis-only text."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

PRONUNCIATION_RESOLVER_VERSION = "neovids_pronunciation_resolver_v2"
DEFAULT_LOCALE = "pt-BR"
_ENTRY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_LOCALE_RE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")


class PronunciationConfigurationError(ValueError):
    """The configured pronunciation lexicon is invalid."""


class PronunciationCategory(StrEnum):
    PROPER_NAME = "proper_name"
    GAME_TITLE = "game_title"
    BRAND = "brand"
    TECHNICAL_TERM = "technical_term"
    ACRONYM = "acronym"
    INITIALISM = "initialism"
    ABBREVIATION = "abbreviation"
    CUSTOM = "custom"


class PronunciationMatchMode(StrEnum):
    TOKEN = "token"
    LITERAL = "literal"


class PronunciationApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class PronunciationEntry:
    id: str
    canonical_term: str
    synthesis_representation: str
    category: PronunciationCategory
    locale: str
    match_mode: PronunciationMatchMode
    case_sensitive: bool
    enabled: bool
    priority: int
    revision: int
    source: str
    approval_status: PronunciationApprovalStatus
    approved_by: str | None = None
    approved_at: str | None = None
    notes: str | None = None
    confidence: float | None = None
    projects: tuple[str, ...] = ()

    @property
    def production_active(self) -> bool:
        return self.enabled and self.approval_status is PronunciationApprovalStatus.APPROVED

    def applies_to(self, *, project: str | None, locale: str) -> bool:
        locale_matches = (
            self.locale.casefold() == "und" or self.locale.casefold() == locale.casefold()
        )
        project_matches = not self.projects or (project is not None and project in self.projects)
        return self.production_active and locale_matches and project_matches

    def semantic_payload(self) -> dict[str, Any]:
        """Return only fields that can alter production resolution or provenance."""
        return {
            "id": self.id,
            "revision": self.revision,
            "canonical_term": self.canonical_term,
            "synthesis_representation": self.synthesis_representation,
            "category": self.category.value,
            "locale": self.locale,
            "match_mode": self.match_mode.value,
            "case_sensitive": self.case_sensitive,
            "priority": self.priority,
            "source": self.source,
            "approval_status": self.approval_status.value,
            "approved_by": self.approved_by,
            "confidence": self.confidence,
            "projects": sorted(self.projects),
        }

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "canonical_term": self.canonical_term,
            "synthesis_representation": self.synthesis_representation,
            "category": self.category.value,
            "locale": self.locale,
            "match": {
                "mode": self.match_mode.value,
                "case_sensitive": self.case_sensitive,
            },
            "enabled": self.enabled,
            "priority": self.priority,
            "revision": self.revision,
            "source": self.source,
            "approval": {
                "status": self.approval_status.value,
                "approved_by": self.approved_by,
                "approved_at": self.approved_at,
            },
        }
        if self.notes is not None:
            result["notes"] = self.notes
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.projects:
            result["projects"] = list(self.projects)
        return result


@dataclass(frozen=True)
class PronunciationResult:
    canonical_text: str
    synthesis_text: str
    glossary_identity: str
    applied_replacements: tuple[dict[str, Any], ...] = ()
    resolution_input_text: str | None = None
    lexicon_version: str | None = None
    resolver_version: str = PRONUNCIATION_RESOLVER_VERSION

    @property
    def lexicon_identity(self) -> str:
        return self.glossary_identity


@dataclass(frozen=True)
class _Match:
    entry: PronunciationEntry
    start: int
    end: int
    original: str


class PronunciationPreprocessor:
    """Compile and apply a human-approved YAML pronunciation lexicon.

    Resolution is a single pass over the input. Replacement output is never fed back into
    the matcher, which prevents cascading substitutions and makes provenance offsets stable.
    """

    def __init__(self, *, version: str, entries: tuple[PronunciationEntry, ...]):
        if not version.strip():
            raise PronunciationConfigurationError("pronunciation lexicon version is required")
        self.version = version.strip()
        self._entries = entries
        self._compiled: dict[
            tuple[str | None, str],
            tuple[re.Pattern[str] | None, tuple[PronunciationEntry, ...]],
        ] = {}
        self._validate_entries()
        self._validate_idempotence()

    @classmethod
    def load(cls, path: Path) -> PronunciationPreprocessor:
        data = cls._read_mapping(path)
        version = str(data.get("version", "")).strip()
        if "entries" in data:
            raw_entries = data.get("entries")
            if not isinstance(raw_entries, list):
                raise PronunciationConfigurationError("pronunciation entries must be a list")
            entries = tuple(cls._parse_entry(item) for item in raw_entries)
        else:
            entries = cls._parse_legacy_entries(data)
        return cls(version=version, entries=entries)

    @staticmethod
    def _read_mapping(path: Path) -> dict[str, Any]:
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PronunciationConfigurationError("pronunciation lexicon is unreadable") from exc
        if not isinstance(data, dict):
            raise PronunciationConfigurationError("pronunciation lexicon must be a mapping")
        return data

    @classmethod
    def _parse_entry(cls, raw: Any) -> PronunciationEntry:
        if not isinstance(raw, dict):
            raise PronunciationConfigurationError("each pronunciation entry must be a mapping")
        match = raw.get("match", {})
        approval = raw.get("approval", {})
        if not isinstance(match, dict) or not isinstance(approval, dict):
            raise PronunciationConfigurationError("entry match and approval must be mappings")
        projects = raw.get("projects", [])
        if not isinstance(projects, list) or not all(
            isinstance(project, str) and project.strip() for project in projects
        ):
            raise PronunciationConfigurationError("entry projects must be non-empty strings")
        try:
            category = PronunciationCategory(str(raw.get("category", "custom")))
            match_mode = PronunciationMatchMode(str(match.get("mode", "token")))
            approval_status = PronunciationApprovalStatus(str(approval.get("status", "undecided")))
        except ValueError as exc:
            raise PronunciationConfigurationError("entry enum value is invalid") from exc
        confidence = raw.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, int | float) or isinstance(confidence, bool)
        ):
            raise PronunciationConfigurationError("entry confidence must be numeric")
        locale = raw.get("locale", DEFAULT_LOCALE)
        if not isinstance(locale, str):
            raise PronunciationConfigurationError("entry locale must be a string")
        return PronunciationEntry(
            id=cls._required_string(raw, "id"),
            canonical_term=unicodedata.normalize(
                "NFC", cls._required_string(raw, "canonical_term")
            ),
            synthesis_representation=unicodedata.normalize(
                "NFC", cls._required_string(raw, "synthesis_representation")
            ),
            category=category,
            locale=locale.strip(),
            match_mode=match_mode,
            case_sensitive=cls._strict_bool(match.get("case_sensitive", False), "case_sensitive"),
            enabled=cls._strict_bool(raw.get("enabled", True), "enabled"),
            priority=cls._strict_int(raw.get("priority", 100), "priority"),
            revision=cls._strict_int(raw.get("revision", 1), "revision"),
            source=cls._required_string(raw, "source", default="operator"),
            approval_status=approval_status,
            approved_by=cls._optional_string(approval.get("approved_by")),
            approved_at=cls._optional_string(approval.get("approved_at")),
            notes=cls._optional_string(raw.get("notes")),
            confidence=float(confidence) if confidence is not None else None,
            projects=tuple(sorted(set(project.strip() for project in projects))),
        )

    @classmethod
    def _parse_legacy_entries(cls, data: dict[str, Any]) -> tuple[PronunciationEntry, ...]:
        global_entries = data.get("global", {})
        projects = data.get("projects", {})
        if not isinstance(global_entries, dict) or not isinstance(projects, dict):
            raise PronunciationConfigurationError("legacy pronunciation glossary is invalid")
        parsed: list[PronunciationEntry] = []
        parsed.extend(cls._legacy_scope(global_entries, project=None))
        for project, entries in projects.items():
            if not isinstance(project, str) or not isinstance(entries, dict):
                raise PronunciationConfigurationError("legacy project glossaries are invalid")
            parsed.extend(cls._legacy_scope(entries, project=project))
        return tuple(parsed)

    @classmethod
    def _legacy_scope(
        cls, entries: dict[Any, Any], *, project: str | None
    ) -> list[PronunciationEntry]:
        result: list[PronunciationEntry] = []
        for canonical, synthesis in entries.items():
            if not isinstance(canonical, str) or not isinstance(synthesis, str):
                raise PronunciationConfigurationError("legacy glossary terms must be strings")
            scope = project or "global"
            digest = hashlib.sha256(f"{scope}:{canonical}".encode()).hexdigest()[:12]
            result.append(
                PronunciationEntry(
                    id=f"legacy.{scope}.{digest}".lower(),
                    canonical_term=unicodedata.normalize("NFC", canonical.strip()),
                    synthesis_representation=unicodedata.normalize("NFC", synthesis.strip()),
                    category=PronunciationCategory.CUSTOM,
                    locale=DEFAULT_LOCALE,
                    match_mode=PronunciationMatchMode.TOKEN,
                    case_sensitive=False,
                    enabled=True,
                    priority=100,
                    revision=1,
                    source="legacy_production_glossary",
                    approval_status=PronunciationApprovalStatus.APPROVED,
                    approved_by="existing-production-config",
                    projects=(project,) if project else (),
                )
            )
        return result

    def entries(
        self,
        *,
        production_only: bool = False,
        project: str | None = None,
        locale: str = DEFAULT_LOCALE,
    ) -> tuple[PronunciationEntry, ...]:
        entries = self._entries
        if production_only:
            entries = tuple(
                entry for entry in entries if entry.applies_to(project=project, locale=locale)
            )
        return tuple(sorted(entries, key=lambda entry: entry.id))

    def get_entry(self, entry_id: str) -> PronunciationEntry:
        try:
            return next(entry for entry in self._entries if entry.id == entry_id)
        except StopIteration as exc:
            raise LookupError(f"pronunciation entry not found: {entry_id}") from exc

    def identity(self, *, project: str | None = None, locale: str = DEFAULT_LOCALE) -> str:
        active = self.entries(production_only=True, project=project, locale=locale)
        canonical = json.dumps(
            {
                "resolver_version": PRONUNCIATION_RESOLVER_VERSION,
                "entries": [entry.semantic_payload() for entry in active],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def configuration_identity(self) -> str:
        """Identity for optimistic config editing, including inactive/operator metadata."""
        canonical = json.dumps(
            self.to_mapping(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def preprocess(
        self,
        resolution_input_text: str,
        *,
        project: str | None = None,
        locale: str = DEFAULT_LOCALE,
        canonical_text: str | None = None,
    ) -> PronunciationResult:
        normalized_input = unicodedata.normalize("NFC", resolution_input_text)
        matches = self._matches(normalized_input, project=project, locale=locale)
        result_parts: list[str] = []
        replacements: list[dict[str, Any]] = []
        input_cursor = 0
        output_cursor = 0
        canonical_offsets_valid = normalized_input == resolution_input_text and (
            canonical_text is None or canonical_text == resolution_input_text
        )
        for occurrence, match in enumerate(matches, start=1):
            prefix = normalized_input[input_cursor : match.start]
            result_parts.append(prefix)
            output_cursor += len(prefix)
            replacement = match.entry.synthesis_representation
            result_parts.append(replacement)
            output_start = output_cursor
            output_cursor += len(replacement)
            entry = match.entry
            replacements.append(
                {
                    "entry_id": entry.id,
                    "entry_version": entry.revision,
                    "lexicon_version": self.version,
                    "replacement_type": entry.category.value,
                    "locale": entry.locale,
                    "match_mode": entry.match_mode.value,
                    "case_sensitive": entry.case_sensitive,
                    "priority": entry.priority,
                    "original_text": match.original,
                    "replacement_text": replacement,
                    "changed": match.original != replacement,
                    "occurrence": occurrence,
                    "offset_space": "nfc_resolution_input_text",
                    "input_start": match.start,
                    "input_end": match.end,
                    "canonical_start": match.start if canonical_offsets_valid else None,
                    "canonical_end": match.end if canonical_offsets_valid else None,
                    "synthesis_start": output_start,
                    "synthesis_end": output_cursor,
                    "source": entry.source,
                    "approval_status": entry.approval_status.value,
                    "approved_by": entry.approved_by,
                    "confidence": entry.confidence,
                    "notes": entry.notes,
                }
            )
            input_cursor = match.end
        result_parts.append(normalized_input[input_cursor:])
        synthesis_text = "".join(result_parts)
        return PronunciationResult(
            canonical_text=canonical_text if canonical_text is not None else resolution_input_text,
            resolution_input_text=normalized_input,
            synthesis_text=synthesis_text,
            glossary_identity=self.identity(project=project, locale=locale),
            lexicon_version=self.version,
            applied_replacements=tuple(replacements),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "neovids_pronunciation_lexicon_v2",
            "version": self.version,
            "entries": [entry.to_mapping() for entry in self.entries()],
        }

    def _matches(self, text: str, *, project: str | None, locale: str) -> tuple[_Match, ...]:
        pattern, entries = self._compiled_matcher(project=project, locale=locale)
        if pattern is None:
            return ()
        selected: list[_Match] = []
        cursor = 0
        for raw_match in pattern.finditer(text):
            if raw_match.start() < cursor:
                continue
            group_name = raw_match.lastgroup
            if group_name is None:
                continue
            entry = entries[int(group_name[1:])]
            selected.append(
                _Match(
                    entry=entry,
                    start=raw_match.start(),
                    end=raw_match.end(),
                    original=raw_match.group(0),
                )
            )
            cursor = raw_match.end()
        return tuple(selected)

    def _compiled_matcher(
        self, *, project: str | None, locale: str
    ) -> tuple[re.Pattern[str] | None, tuple[PronunciationEntry, ...]]:
        key = (project, locale.casefold())
        cached = self._compiled.get(key)
        if cached is not None:
            return cached
        active = tuple(
            sorted(
                self.entries(production_only=True, project=project, locale=locale),
                key=lambda entry: (
                    0 if entry.projects else 1,
                    -entry.priority,
                    -len(entry.canonical_term),
                    entry.id,
                ),
            )
        )
        if not active:
            compiled: tuple[re.Pattern[str] | None, tuple[PronunciationEntry, ...]] = (None, active)
            self._compiled[key] = compiled
            return compiled
        alternatives = []
        for index, entry in enumerate(active):
            escaped = re.escape(entry.canonical_term)
            body = escaped if entry.case_sensitive else f"(?i:{escaped})"
            if entry.match_mode is PronunciationMatchMode.TOKEN:
                if self._is_word_character(entry.canonical_term[0]):
                    body = rf"(?<!\w){body}"
                if self._is_word_character(entry.canonical_term[-1]):
                    body = rf"{body}(?!\w)"
            alternatives.append(f"(?P<e{index}>{body})")
        compiled = (re.compile("|".join(alternatives)), active)
        self._compiled[key] = compiled
        return compiled

    def _validate_entries(self) -> None:
        ids: set[str] = set()
        for entry in self._entries:
            if not _ENTRY_ID_RE.fullmatch(entry.id):
                raise PronunciationConfigurationError(f"invalid pronunciation entry id: {entry.id}")
            if entry.id in ids:
                raise PronunciationConfigurationError(
                    f"duplicate pronunciation entry id: {entry.id}"
                )
            ids.add(entry.id)
            if not entry.canonical_term or not entry.synthesis_representation:
                raise PronunciationConfigurationError(
                    f"entry {entry.id} has an empty canonical or synthesis term"
                )
            if any(
                unicodedata.category(character) == "Cc"
                for value in (entry.canonical_term, entry.synthesis_representation)
                for character in value
            ):
                raise PronunciationConfigurationError(
                    f"entry {entry.id} contains control characters"
                )
            if not _LOCALE_RE.fullmatch(entry.locale):
                raise PronunciationConfigurationError(f"entry {entry.id} has an invalid locale")
            if not 0 <= entry.priority <= 10_000:
                raise PronunciationConfigurationError(f"entry {entry.id} has an invalid priority")
            if entry.revision < 1:
                raise PronunciationConfigurationError(f"entry {entry.id} has an invalid revision")
            if not entry.source:
                raise PronunciationConfigurationError(f"entry {entry.id} has an empty source")
            if entry.confidence is not None and not 0 <= entry.confidence <= 1:
                raise PronunciationConfigurationError(f"entry {entry.id} has invalid confidence")
            if entry.production_active and not entry.approved_by:
                raise PronunciationConfigurationError(
                    f"approved entry {entry.id} must record approved_by"
                )
        active = [entry for entry in self._entries if entry.production_active]
        for index, left in enumerate(active):
            for right in active[index + 1 :]:
                if self._ambiguous_collision(left, right):
                    raise PronunciationConfigurationError(
                        f"ambiguous pronunciation entries: {left.id} and {right.id}"
                    )

    def _validate_idempotence(self) -> None:
        projects = {project for entry in self._entries for project in entry.projects}
        contexts: set[str | None] = {None, *projects}
        locales = {entry.locale for entry in self._entries if entry.locale != "und"} or {
            DEFAULT_LOCALE
        }
        for entry in self._entries:
            if not entry.production_active:
                continue
            for project in contexts:
                for locale in locales:
                    if not entry.applies_to(project=project, locale=locale):
                        continue
                    matches = self._matches(
                        entry.synthesis_representation,
                        project=project,
                        locale=locale,
                    )
                    rewritten = self._apply_matches(entry.synthesis_representation, matches)
                    if rewritten != entry.synthesis_representation:
                        raise PronunciationConfigurationError(
                            f"entry {entry.id} produces non-idempotent pronunciation output"
                        )

    @staticmethod
    def _apply_matches(text: str, matches: tuple[_Match, ...]) -> str:
        parts: list[str] = []
        cursor = 0
        for match in matches:
            parts.extend((text[cursor : match.start], match.entry.synthesis_representation))
            cursor = match.end
        parts.append(text[cursor:])
        return "".join(parts)

    @staticmethod
    def _ambiguous_collision(left: PronunciationEntry, right: PronunciationEntry) -> bool:
        if left.locale.casefold() != right.locale.casefold() and "und" not in {
            left.locale.casefold(),
            right.locale.casefold(),
        }:
            return False
        scopes_overlap = (
            not left.projects
            or not right.projects
            or bool(set(left.projects) & set(right.projects))
        )
        if not scopes_overlap or left.match_mode is not right.match_mode:
            return False
        # A project-scoped entry is an explicit override of a global entry.
        if bool(left.projects) != bool(right.projects):
            return False
        if left.priority != right.priority:
            return False
        if left.case_sensitive and right.case_sensitive:
            same_term = left.canonical_term == right.canonical_term
        else:
            same_term = left.canonical_term.casefold() == right.canonical_term.casefold()
        return same_term and left.synthesis_representation != right.synthesis_representation

    @staticmethod
    def _is_word_character(character: str) -> bool:
        return bool(re.match(r"\w", character, flags=re.UNICODE))

    @staticmethod
    def _strict_bool(value: Any, name: str) -> bool:
        if not isinstance(value, bool):
            raise PronunciationConfigurationError(f"entry {name} must be a boolean")
        return value

    @staticmethod
    def _strict_int(value: Any, name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PronunciationConfigurationError(f"entry {name} must be an integer")
        return value

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PronunciationConfigurationError("optional entry metadata must be strings")
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _required_string(mapping: dict[str, Any], key: str, *, default: str | None = None) -> str:
        value = mapping.get(key, default)
        if not isinstance(value, str):
            raise PronunciationConfigurationError(f"entry {key} must be a string")
        return value.strip()
