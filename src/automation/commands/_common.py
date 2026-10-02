"""Shared plumbing for the CLI commands."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from ..config import Config, ConfigError
from ..schema import (
    Payload,
    load_payload,
    resolve_data_file,
    validate_payload,
)

LOGGER = logging.getLogger("gitlab-automation")


def load_payload_for(
    args: argparse.Namespace, config: Config, kind: str
) -> tuple[Payload, list[dict[str, Any]]]:
    """Resolve, load and validate the document backing ``kind``.

    Validation errors abort the command before any write happens; warnings are
    returned so the caller can decide how loudly to report them.

    Returns:
        ``(payload, items)`` where items already include ``defaults`` applied.
    """
    from ..schema import merged_items

    path = resolve_data_file(config, kind, getattr(args, "file", None))
    payload = load_payload(path)
    report = validate_payload(payload, config)

    for warning in report.warnings:
        LOGGER.warning("%s", warning)

    if not report.ok:
        raise ConfigError(
            f"{path} is invalid: {len(report.errors)} error(s). "
            f"Run 'gitlab-automation validate' to inspect them."
        )

    return payload, merged_items(payload)


def add_payload_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--file",
        help="Path to the JSON document. Defaults to the file that matches the command.",
    )


def add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        action="append",
        metavar="ALIAS",
        help="Only process items of this project alias. Repeatable.",
    )
    parser.add_argument(
        "--title",
        action="append",
        metavar="TITLE",
        help="Only process items whose title matches exactly. Repeatable.",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="Process at most N items."
    )


def print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Render a simple aligned table on stdout."""
    if not rows:
        print("(no rows)")
        return
    widths = {
        column: max(len(column), *(len(str(row.get(column, ""))) for row in rows))
        for column in columns
    }
    print("  ".join(column.upper().ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print(
            "  ".join(
                str(row.get(column, "")).ljust(widths[column]) for column in columns
            )
        )


def window_of(item: dict[str, Any]) -> str:
    start, due = item.get("start_date"), item.get("due_date")
    if start and due:
        return f"{start} -> {due}"
    if start:
        return f"starts {start}"
    if due:
        return f"due {due}"
    return ""


__all__ = [
    "add_filter_arguments",
    "add_payload_argument",
    "load_payload_for",
    "print_table",
    "window_of",
]
