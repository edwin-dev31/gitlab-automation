"""``gitlab-automation milestones ...`` - group and project milestones."""

from __future__ import annotations

import argparse
from typing import Any

from .. import markdown
from ..client import GitLabClient
from ..config import Config
from ..runner import Reporter, apply_filters, run_safely
from ._common import (
    add_filter_arguments,
    add_payload_argument,
    load_payload_for,
    print_table,
)


def create_milestones(
    client: GitLabClient, config: Config, args: argparse.Namespace
) -> int:
    _, items = load_payload_for(args, config, "milestones")
    selected = apply_filters(
        items, projects=args.project, titles=args.title, limit=args.limit
    )

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(
        f"Processing {len(selected)} milestone(s) out of {len(items)} declared."
    )

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("milestones")
        return 0

    existing = {} if config.dry_run else _existing(client, selected)

    for item in selected:
        title = str(item["title"]).strip()
        scope = str(item.get("scope", "group"))
        key = (scope, str(item.get("project", "")), title)

        if key in existing:
            reporter.skipped("milestone", title, f"already present in {scope}")
            continue

        run_safely(
            lambda item=item, title=title, scope=scope, key=key: _create_one(
                client, config, item, title, scope, key, existing, reporter
            ),
            kind="milestone",
            name=title,
            reporter=reporter,
        )

    reporter.finish("milestones")
    return 1 if reporter.summary.failed else 0


def _create_one(
    client: GitLabClient,
    config: Config,
    item: dict[str, Any],
    title: str,
    scope: str,
    key: tuple[str, str, str],
    existing: set[tuple[str, str, str]],
    reporter: Reporter,
) -> None:
    body: dict[str, Any] = {
        "title": title,
        "start_date": item["start_date"],
        "due_date": item["due_date"],
    }
    description = markdown.render_milestone(item)
    if description:
        body["description"] = description

    if item.get("state") in {"closed", "active"}:
        body["state"] = item["state"]

    detail = f"scope={scope}, {body['start_date']} -> {body['due_date']}"

    if config.dry_run:
        reporter.created("milestone", title, detail=detail)
        return

    if scope == "project":
        alias = str(item["project"])
        created = client.create_project_milestone(alias, body)
    else:
        created = client.create_group_milestone(body)

    existing.add(key)
    reporter.created("milestone", title, url=created.get("web_url"), detail=detail)


def _existing(
    client: GitLabClient, items: list[dict[str, Any]]
) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for title in {
        str(milestone["title"])
        for milestone in client.group_milestones()
    }:
        found.add(("group", "", title))
    for alias in sorted({str(i["project"]) for i in items if i.get("project")}):
        for milestone in client.project_milestones(alias):
            found.add(("project", alias, str(milestone["title"])))
    return found


def list_milestones(
    client: GitLabClient, config: Config, args: argparse.Namespace
) -> int:
    reporter = Reporter()
    rows: list[dict[str, Any]] = []

    for milestone in client.group_milestones():
        rows.append(
            {
                "scope": "group",
                "title": milestone.get("title"),
                "state": milestone.get("state"),
                "start": milestone.get("start_date") or "-",
                "due": milestone.get("due_date") or "-",
            }
        )

    for alias in args.project or sorted(config.projects):
        for milestone in client.project_milestones(alias):
            rows.append(
                {
                    "scope": alias,
                    "title": milestone.get("title"),
                    "state": milestone.get("state"),
                    "start": milestone.get("start_date") or "-",
                    "due": milestone.get("due_date") or "-",
                }
            )

    print_table(rows, ["scope", "title", "state", "start", "due"])
    reporter.info(f"{len(rows)} milestone(s) found.")
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "milestones",
        help="Create and list milestones (group or project scoped).",
    )
    actions = parser.add_subparsers(dest="action", required=True)

    create = actions.add_parser(
        "create",
        help="Create the milestones declared in a JSON document.",
        description=(
            "Items need 'title', 'start_date' and 'due_date'. Add "
            "\"scope\": \"project\" plus \"project\" to create it in a project "
            "instead of the group."
        ),
    )
    add_payload_argument(create)
    add_filter_arguments(create)
    create.set_defaults(handler=create_milestones)

    listing = actions.add_parser("list", help="List group and project milestones.")
    listing.add_argument(
        "--project", action="append", metavar="ALIAS", help="Repeatable. Defaults to all."
    )
    listing.set_defaults(handler=list_milestones)


__all__ = ["register"]
