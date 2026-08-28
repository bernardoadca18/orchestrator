#!/usr/bin/env python3
"""Operator CLI for production pronunciation rules and controlled laboratory runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.voice_client import VoiceClient
from app.core.config import settings
from app.services.pronunciation import (
    DEFAULT_LOCALE,
    PronunciationCategory,
    PronunciationConfigurationError,
    PronunciationMatchMode,
    PronunciationPreprocessor,
)
from app.services.pronunciation_lab import (
    InitialismCandidateSource,
    LabCandidate,
    LabSpec,
    ManualCandidateSource,
    PronunciationLabRunner,
    PronunciationLabValidationError,
    VoiceClientLabGateway,
    load_manifest,
    record_review,
)
from app.services.pronunciation_lexicon_store import (
    PronunciationLexiconMutationError,
    PronunciationLexiconStore,
)
from app.services.text_normalization import PortugueseTextNormalizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NeoVids Pronunciation Lab and lexicon tools")
    parser.add_argument(
        "--lexicon",
        type=Path,
        default=settings.pronunciation_glossary_path,
        help="production pronunciation YAML",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="list pronunciation entries")
    list_command.add_argument("--all", action="store_true", help="include inactive entries")
    list_command.add_argument("--project")
    list_command.add_argument("--locale", default=DEFAULT_LOCALE)

    show = commands.add_parser("show", help="inspect one pronunciation entry")
    show.add_argument("entry_id")

    commands.add_parser("validate", help="validate the lexicon and print its identities")

    resolve = commands.add_parser("resolve", help="preview normalization and pronunciation")
    resolve.add_argument("--text", required=True)
    resolve.add_argument("--project")
    resolve.add_argument("--locale", default=DEFAULT_LOCALE)

    lab = commands.add_parser("lab-run", help="run a controlled Qwen candidate matrix")
    lab.add_argument("--target", required=True)
    lab.add_argument("--context", action="append", required=True)
    lab.add_argument("--candidate", action="append", default=[])
    lab.add_argument("--initialism-candidates", action="store_true")
    lab.add_argument("--language", default="Portuguese")
    lab.add_argument("--locale", default=DEFAULT_LOCALE)
    lab.add_argument("--voice", default="bernardo_v1")
    lab.add_argument("--generation", default="narration_temp080")
    lab.add_argument("--seeds", nargs="+", type=int, default=[101])
    lab.add_argument("--base-url", default=settings.voice_api_url)
    lab.add_argument("--output", type=Path, default=Path("pronunciation-lab-results"))
    lab.add_argument("--run-id")

    lab_show = commands.add_parser("lab-show", help="inspect a machine-readable lab manifest")
    lab_show.add_argument("run_directory", type=Path)

    review = commands.add_parser("review", help="record an explicit human candidate review")
    review.add_argument("run_directory", type=Path)
    review.add_argument("candidate_id")
    review.add_argument("--status", choices=("approved", "rejected", "undecided"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes")

    promote = commands.add_parser(
        "promote",
        help="preview or atomically promote an approved lab candidate",
    )
    promote.add_argument("run_directory", type=Path)
    promote.add_argument("candidate_id")
    promote.add_argument("--entry-id", required=True)
    promote.add_argument(
        "--category",
        choices=tuple(category.value for category in PronunciationCategory),
        required=True,
    )
    promote.add_argument("--locale", default=DEFAULT_LOCALE)
    promote.add_argument(
        "--match-mode",
        choices=tuple(mode.value for mode in PronunciationMatchMode),
        default=PronunciationMatchMode.TOKEN.value,
    )
    promote.add_argument("--case-sensitive", action="store_true")
    promote.add_argument("--priority", type=int, default=100)
    promote.add_argument("--project", action="append", default=[])
    promote.add_argument("--notes")
    promote.add_argument("--expected-config-identity")
    promote.add_argument("--apply", action="store_true")

    disable = commands.add_parser("disable", help="preview or atomically disable an entry")
    disable.add_argument("entry_id")
    disable.add_argument("--expected-config-identity")
    disable.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (
        PronunciationConfigurationError,
        PronunciationLabValidationError,
        PronunciationLexiconMutationError,
        LookupError,
    ) as exc:
        parser.error(str(exc))
    return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "list":
        resolver = PronunciationPreprocessor.load(args.lexicon)
        entries = resolver.entries(
            production_only=not args.all,
            project=args.project,
            locale=args.locale,
        )
        _print_json([entry.to_mapping() for entry in entries])
        return 0
    if args.command == "show":
        entry = PronunciationPreprocessor.load(args.lexicon).get_entry(args.entry_id)
        _print_json(entry.to_mapping())
        return 0
    if args.command == "validate":
        resolver = PronunciationPreprocessor.load(args.lexicon)
        _print_json(
            {
                "valid": True,
                "version": resolver.version,
                "configuration_identity": resolver.configuration_identity(),
                "production_identity": resolver.identity(),
                "entry_count": len(resolver.entries()),
                "production_entry_count": len(resolver.entries(production_only=True)),
            }
        )
        return 0
    if args.command == "resolve":
        resolver = PronunciationPreprocessor.load(args.lexicon)
        normalizer = PortugueseTextNormalizer(version=settings.text_normalizer_version)
        normalization = normalizer.normalize(args.text)
        result = resolver.preprocess(
            normalization.normalized_text,
            project=args.project,
            locale=args.locale,
            canonical_text=args.text,
        )
        _print_json(
            {
                "canonical": args.text,
                "normalized": normalization.normalized_text,
                "synthesis": result.synthesis_text,
                "text_normalizer_identity": normalization.identity,
                "lexicon_identity": result.lexicon_identity,
                "replacements": result.applied_replacements,
            }
        )
        return 0
    if args.command == "lab-run":
        return asyncio.run(_run_lab(args))
    if args.command == "lab-show":
        records = load_manifest(args.run_directory / "manifest.jsonl")
        _print_json(
            {
                "run_id": args.run_directory.name,
                "total": len(records),
                "completed": sum(record.get("status") == "completed" for record in records),
                "failed": sum(record.get("status") != "completed" for record in records),
                "records": records,
            }
        )
        return 0
    if args.command == "review":
        _print_json(
            record_review(
                args.run_directory,
                candidate_id=args.candidate_id,
                status=args.status,
                reviewer=args.reviewer,
                notes=args.notes,
            )
        )
        return 0
    if args.command == "promote":
        mutation_result = PronunciationLexiconStore(args.lexicon).promote_lab_candidate(
            run_directory=args.run_directory,
            candidate_id=args.candidate_id,
            entry_id=args.entry_id,
            category=PronunciationCategory(args.category),
            locale=args.locale,
            match_mode=PronunciationMatchMode(args.match_mode),
            case_sensitive=args.case_sensitive,
            priority=args.priority,
            projects=tuple(args.project),
            notes=args.notes,
            expected_configuration_identity=args.expected_config_identity,
            apply=args.apply,
        )
        _print_mutation(mutation_result)
        return 0
    if args.command == "disable":
        mutation_result = PronunciationLexiconStore(args.lexicon).disable(
            args.entry_id,
            expected_configuration_identity=args.expected_config_identity,
            apply=args.apply,
        )
        _print_mutation(mutation_result)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


async def _run_lab(args: argparse.Namespace) -> int:
    candidates = list(ManualCandidateSource.candidates(args.candidate))
    if args.initialism_candidates:
        candidates.extend(InitialismCandidateSource.candidates(args.target, locale=args.locale))
    unique: dict[str, LabCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.text, candidate)
    config = settings.model_copy(update={"voice_api_url": args.base_url})
    spec = LabSpec(
        target_term=args.target,
        contexts=tuple(args.context),
        candidates=tuple(unique.values()),
        language=args.language,
        locale=args.locale,
        voice_profile=args.voice,
        generation_profile=args.generation,
        seeds=tuple(args.seeds),
        run_id=args.run_id,
    )
    async with VoiceClient(config) as voice:
        summary = await PronunciationLabRunner(VoiceClientLabGateway(voice)).run(
            spec,
            output_root=args.output,
        )
    _print_json(
        {
            "run_id": summary.run_id,
            "run_directory": summary.run_directory,
            "manifest": summary.manifest_path,
            "total": len(summary.records),
            "completed": sum(record["status"] == "completed" for record in summary.records),
            "failed": sum(record["status"] != "completed" for record in summary.records),
        }
    )
    return 0


def _print_mutation(result: Any) -> None:
    _print_json(
        {
            "applied": result.applied,
            "entry": result.entry.to_mapping(),
            "previous_configuration_identity": result.previous_configuration_identity,
            "next_configuration_identity": result.next_configuration_identity,
            "previous_production_identity": result.previous_production_identity,
            "next_production_identity": result.next_production_identity,
            "next_version": result.next_version,
            "instruction": (
                "Lexicon updated atomically. Commit and deploy the reviewed YAML."
                if result.applied
                else "Preview only. Re-run with --apply and --expected-config-identity."
            ),
        }
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
