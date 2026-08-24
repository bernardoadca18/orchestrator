"""Deterministic pronunciation preprocessing for synthesis-only text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PronunciationConfigurationError(ValueError):
    """The configured pronunciation glossary is invalid."""


@dataclass(frozen=True)
class PronunciationResult:
    canonical_text: str
    synthesis_text: str
    glossary_identity: str


class PronunciationPreprocessor:
    def __init__(
        self, *, version: str, global_entries: dict[str, str], projects: dict[str, dict[str, str]]
    ):
        self.version = version
        self._global_entries = self._validate_entries(global_entries)
        self._projects = {
            name: self._validate_entries(entries) for name, entries in projects.items()
        }

    @classmethod
    def load(cls, path: Path) -> PronunciationPreprocessor:
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PronunciationConfigurationError("pronunciation glossary is unreadable") from exc
        if not isinstance(data, dict):
            raise PronunciationConfigurationError("pronunciation glossary must be a mapping")
        version = str(data.get("version", "")).strip()
        global_entries = data.get("global", {})
        projects = data.get("projects", {})
        if not version or not isinstance(global_entries, dict) or not isinstance(projects, dict):
            raise PronunciationConfigurationError("pronunciation glossary structure is invalid")
        if not all(
            isinstance(name, str) and isinstance(entries, dict)
            for name, entries in projects.items()
        ):
            raise PronunciationConfigurationError("project glossaries must be mappings")
        return cls(version=version, global_entries=global_entries, projects=projects)

    def preprocess(self, canonical_text: str, *, project: str | None = None) -> PronunciationResult:
        entries = dict(self._global_entries)
        if project and project in self._projects:
            entries.update(self._projects[project])
        canonical = json.dumps(
            {"version": self.version, "project": project, "entries": entries},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        result = canonical_text
        for original in sorted(entries, key=lambda item: (-len(item), item.casefold())):
            replacement = entries[original]
            pattern = re.compile(
                rf"(?<![\wÀ-ÿ]){re.escape(original)}(?![\wÀ-ÿ])",
                flags=re.IGNORECASE,
            )

            def replace(match: re.Match[str], replacement: str = replacement) -> str:
                matched = match.group(0)
                if matched[:1].isupper() and replacement[:1].islower():
                    return replacement[:1].upper() + replacement[1:]
                return replacement

            result = pattern.sub(replace, result)
        return PronunciationResult(canonical_text, result, identity)

    @staticmethod
    def _validate_entries(entries: dict[Any, Any]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for original, replacement in entries.items():
            if not isinstance(original, str) or not isinstance(replacement, str):
                raise PronunciationConfigurationError("glossary terms must be strings")
            if not original.strip() or not replacement.strip():
                raise PronunciationConfigurationError("glossary terms must not be empty")
            validated[original] = replacement
        return validated
