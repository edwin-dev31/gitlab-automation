"""``gitlab-automation validate`` - offline checks on the JSON documents."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..client import GitLabClient
from ..config import Config
from ..schema import PayloadError, discover_payloads, load_payload, validate_payload
from ._common import print_table

LOGGER = logging.getLogger("gitlab-automation")


def validate(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    if args.file:
        candidates = [Path(args.file)]
        if not candidates[0].exists():
            candidate = config.data_dir / candidates[0]
            if candidate.exists():
                candidates = [candidate]
    else:
        candidates = discover_payloads(config.data_dir)

    if not candidates:
        LOGGER.warning(
            "No JSON document found in %s. Create one to get started.",
            config.data_dir,
        )
        return 0

    if not args.quiet:
        print_table(
            [
                {
                    "file": path.name,
                    "type": _safe_type(path),
                    "items": _safe_count(path),
                }
                for path in candidates
            ],
            ["file", "type", "items"],
        )
        print()

    total_errors = 0
    total_warnings = 0

    for path in candidates:
        try:
            payload = load_payload(path)
        except PayloadError as error:
            total_errors += 1
            LOGGER.error("%s", error)
            continue

        report = validate_payload(payload, config)

        for warning in report.warnings:
            LOGGER.warning("%s", warning)
        for error in report.errors:
            LOGGER.error("%s", error)

        total_errors += len(report.errors)
        total_warnings += len(report.warnings)

        if args.strict:
            for warning in report.warnings:
                LOGGER.error("[ERROR-STRICT] %s", warning)
            total_errors += len(report.warnings)
            total_warnings -= len(report.warnings)

        if report.ok and not report.warnings:
            LOGGER.info("%s is valid (%s item(s)).", path.name, payload.count)

    LOGGER.info(
        "Validated %s file(s): %s error(s), %s warning(s).",
        len(candidates),
        total_errors,
        total_warnings,
    )
    return 1 if total_errors else 0


def _safe_type(path: Path) -> str:
    try:
        return load_payload(path).kind
    except PayloadError:
        return "?"


def _safe_count(path: Path) -> int:
    try:
        return load_payload(path).count
    except PayloadError:
        return 0


def register(subparsers: object) -> None:
    parser = subparsers.add_parser(
        "validate",
        help="Validate the JSON documents without calling the GitLab API.",
    )
    parser.add_argument(
        "--file", help="Validate only this document. Defaults to every file in data/."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors."
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Skip the summary table."
    )
    parser.set_defaults(handler=validate, offline=True)


__all__ = ["register"]
