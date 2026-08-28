"""Explicit, optimistic and atomic mutations for the YAML production lexicon."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.services.pronunciation import (
    DEFAULT_LOCALE,
    PronunciationApprovalStatus,
    PronunciationCategory,
    PronunciationConfigurationError,
    PronunciationEntry,
    PronunciationMatchMode,
    PronunciationPreprocessor,
)
from app.services.pronunciation_lab import approved_candidate


class PronunciationLexiconMutationError(ValueError):
    """A requested production lexicon mutation is unsafe or stale."""


@dataclass(frozen=True)
class LexiconMutationResult:
    entry: PronunciationEntry
    previous_configuration_identity: str
    next_configuration_identity: str
    previous_production_identity: str
    next_production_identity: str
    next_version: str
    applied: bool
    proposed_mapping: dict[str, Any]


class PronunciationLexiconStore:
    def __init__(self, path: Path):
        self.path = path

    def promote_lab_candidate(
        self,
        *,
        run_directory: Path,
        candidate_id: str,
        entry_id: str,
        category: PronunciationCategory,
        locale: str = DEFAULT_LOCALE,
        match_mode: PronunciationMatchMode = PronunciationMatchMode.TOKEN,
        case_sensitive: bool = False,
        priority: int = 100,
        projects: tuple[str, ...] = (),
        notes: str | None = None,
        expected_configuration_identity: str | None = None,
        apply: bool = False,
    ) -> LexiconMutationResult:
        candidate, review = approved_candidate(run_directory, candidate_id)
        canonical_term = str(candidate["target_term"])
        synthesis = str(candidate["candidate_text"])
        reviewer = str(review["reviewer"])

        def mutation(current: PronunciationPreprocessor) -> PronunciationEntry:
            existing = self._entry_or_none(current, entry_id)
            if existing is not None and existing.canonical_term != canonical_term:
                raise PronunciationLexiconMutationError(
                    "an existing stable entry id cannot be reassigned to another canonical term"
                )
            revision = existing.revision + 1 if existing is not None else 1
            return PronunciationEntry(
                id=entry_id,
                canonical_term=canonical_term,
                synthesis_representation=synthesis,
                category=category,
                locale=locale,
                match_mode=match_mode,
                case_sensitive=case_sensitive,
                enabled=True,
                priority=priority,
                revision=revision,
                source=f"pronunciation_lab:{run_directory.name}:{candidate_id}",
                approval_status=PronunciationApprovalStatus.APPROVED,
                approved_by=reviewer,
                approved_at=str(review["timestamp"]),
                notes=notes if notes is not None else review.get("notes"),
                projects=tuple(sorted(set(projects))),
            )

        return self._mutate(
            mutation,
            target_entry_id=entry_id,
            locale=locale,
            expected_configuration_identity=expected_configuration_identity,
            apply=apply,
        )

    def disable(
        self,
        entry_id: str,
        *,
        expected_configuration_identity: str | None = None,
        apply: bool = False,
    ) -> LexiconMutationResult:
        current = PronunciationPreprocessor.load(self.path)
        existing = self._entry_or_none(current, entry_id)
        locale = existing.locale if existing is not None else DEFAULT_LOCALE

        def mutation(resolver: PronunciationPreprocessor) -> PronunciationEntry:
            entry = resolver.get_entry(entry_id)
            if not entry.enabled:
                raise PronunciationLexiconMutationError("pronunciation entry is already disabled")
            return replace(entry, enabled=False, revision=entry.revision + 1)

        return self._mutate(
            mutation,
            target_entry_id=entry_id,
            locale=locale,
            expected_configuration_identity=expected_configuration_identity,
            apply=apply,
        )

    def _mutate(
        self,
        mutation: Any,
        *,
        target_entry_id: str,
        locale: str,
        expected_configuration_identity: str | None,
        apply: bool,
    ) -> LexiconMutationResult:
        if apply and not expected_configuration_identity:
            raise PronunciationLexiconMutationError(
                "--apply requires the expected current configuration identity"
            )
        lock_digest = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:20]
        lock_path = Path(tempfile.gettempdir()) / f"neovids-pronunciation-{lock_digest}.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = PronunciationPreprocessor.load(self.path)
            current_config_identity = current.configuration_identity()
            if (
                expected_configuration_identity is not None
                and expected_configuration_identity != current_config_identity
            ):
                raise PronunciationLexiconMutationError(
                    "pronunciation lexicon changed since it was reviewed"
                )
            changed_entry = mutation(current)
            entries = [
                changed_entry if entry.id == target_entry_id else entry
                for entry in current.entries()
            ]
            if not any(entry.id == target_entry_id for entry in current.entries()):
                entries.append(changed_entry)
            next_version = self._next_version(current.version)
            try:
                proposed = PronunciationPreprocessor(
                    version=next_version,
                    entries=tuple(entries),
                )
            except PronunciationConfigurationError as exc:
                raise PronunciationLexiconMutationError(
                    f"proposed production entry makes the lexicon invalid: {exc}"
                ) from exc
            mapping = proposed.to_mapping()
            result = LexiconMutationResult(
                entry=changed_entry,
                previous_configuration_identity=current_config_identity,
                next_configuration_identity=proposed.configuration_identity(),
                previous_production_identity=current.identity(locale=locale),
                next_production_identity=proposed.identity(locale=locale),
                next_version=next_version,
                applied=apply,
                proposed_mapping=mapping,
            )
            if apply:
                self._atomic_write(mapping)
            return result

    def _atomic_write(self, mapping: dict[str, Any]) -> None:
        body = yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False).encode()
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _entry_or_none(
        resolver: PronunciationPreprocessor, entry_id: str
    ) -> PronunciationEntry | None:
        try:
            return resolver.get_entry(entry_id)
        except LookupError:
            return None

    @staticmethod
    def _next_version(version: str) -> str:
        try:
            return str(int(version) + 1)
        except ValueError as exc:
            raise PronunciationLexiconMutationError(
                "automatic mutation requires an integer lexicon version"
            ) from exc
