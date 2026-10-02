"""``gitlab-automation labels ...`` - create and inspect labels.

This is the companion of ``stories create``: it makes sure every label used by a
payload exists before the issues that reference it are created.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..client import GitLabClient
from ..config import Config, ConfigError
from ..runner import Reporter, apply_filters, run_safely
from ..schema import ALL_PROJECTS_TARGET
from ._common import (
    add_filter_arguments,
    add_payload_argument,
    load_payload_for,
    print_table,
)

ALL_ALIASES = ALL_PROJECTS_TARGET


def _targets(alias: str | None, config: Config) -> list[str]:
    """Resolve a label target to a concrete list of project aliases."""
    if not alias or alias == ALL_ALIASES:
        return sorted(config.projects)
    if not config.has_project(alias):
        known = ", ".join(sorted(config.projects)) or "<none>"
        raise ConfigError(f"Unknown project alias {alias!r}. Known aliases: {known}")
    return [alias]


def create_labels(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    _, items = load_payload_for(args, config, "labels")
    selected = apply_filters(
        items,
        projects=args.project,
        titles=args.title,
        title_key="name",
        limit=args.limit,
    )

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(f"Processing {len(selected)} label(s).")

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("labels")
        return 0

    existing = {} if config.dry_run else _existing(client, config, selected)

    for item in selected:
        name = str(item["name"]).strip()
        scope = str(item.get("scope", "project"))
        aliases = _targets(item.get("project"), config) if scope == "project" else [""]

        for alias in aliases:
            key = (scope, alias, name)
            if key in existing:
                reporter.skipped("label", name, f"already present in {alias or scope}")
                continue

            def action(
                name: str = name, scope: str = scope, alias: str = alias,
                item: dict[str, Any] = item, key: tuple[str, str, str] = key,
            ) -> None:
                body = {
                    "color": item.get("color"),
                    "description": item.get("description"),
                    "priority": item.get("priority"),
                }
                body = {k: v for k, v in body.items() if v is not None}
                where = alias or scope
                detail = f"scope={where}"
                if config.dry_run:
                    reporter.created("label", name, detail=detail)
                    return
                if scope == "group":
                    client.create_group_label(name, **body)
                else:
                    client.create_project_label(alias, name, **body)
                existing.add(key)
                reporter.created("label", name, detail=detail)

            run_safely(
                action, kind="label", name=f"{alias or scope}/{name}", reporter=reporter
            )

    reporter.finish("labels")
    return 1 if reporter.summary.failed else 0


def _existing(
    client: GitLabClient, config: Config, items: list[dict[str, Any]]
) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    if any(item.get("scope", "project") == "group" for item in items):
        for label in client.group_labels():
            found.add(("group", "", str(label["name"])))
    for alias in sorted(
        {
            target
            for item in items
            if item.get("scope", "project") == "project"
            for target in _targets(item.get("project"), config)
        }
    ):
        for label in client.project_labels(alias):
            found.add(("project", alias, str(label["name"])))
    return found


def list_labels(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    reporter = Reporter()
    rows: list[dict[str, Any]] = []

    if args.scope in {"all", "group"} and config.group_path:
        for label in client.group_labels():
            rows.append(
                {
                    "scope": "group",
                    "name": label.get("name"),
                    "color": label.get("color"),
                    "description": (label.get("description") or "-")[:60],
                }
            )

    if args.scope in {"all", "project"}:
        for alias in args.project or sorted(config.projects):
            for label in client.project_labels(alias):
                rows.append(
                    {
                        "scope": alias,
                        "name": label.get("name"),
                        "color": label.get("color"),
                        "description": (label.get("description") or "-")[:60],
                    }
                )

    print_table(rows, ["scope", "name", "color", "description"])
    reporter.info(f"{len(rows)} label(s) found.")
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "labels", help="Create and list labels in projects or in the group."
    )
    actions = parser.add_subparsers(dest="action", required=True)

    create = actions.add_parser(
        "create",
        help="Create the labels declared in a JSON document.",
        description=(
            "Items need 'name'. Optional: scope ('project' by default), "
            "project (use 'all' for every alias in GITLAB_PROJECTS), color, "
            "description, priority."
        ),
    )
    add_payload_argument(create)
    add_filter_arguments(create)
    create.set_defaults(handler=create_labels)

    listing = actions.add_parser("list", help="List existing labels.")
    listing.add_argument(
        "--project", action="append", metavar="ALIAS", help="Repeatable. Defaults to all."
    )
    listing.add_argument(
        "--scope", choices=("all", "group", "project"), default="all"
    )
    listing.set_defaults(handler=list_labels)


__all__ = ["register"]
