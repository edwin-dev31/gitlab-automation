"""``gitlab-automation stories create`` - create user stories from JSON."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from .. import markdown
from ..client import GitLabClient
from ..config import Config
from ..issue_builder import (
    Preflight,
    apply_milestone_ids,
    build_issue_payload,
    preflight_items,
)
from ..runner import (
    AssigneeIndex,
    EpicIndex,
    Reporter,
    apply_filters,
    run_safely,
)
from ._common import (
    add_filter_arguments,
    add_payload_argument,
    load_payload_for,
)
from .issues import _attach_epic, _detail_for, _existing_titles, list_issues

LOGGER = logging.getLogger("gitlab-automation")

# ----------------------------------------------------------------------
# Creation
# ----------------------------------------------------------------------
def create_user_stories(
    client: GitLabClient, config: Config, args: argparse.Namespace
) -> int:
    _, items = load_payload_for(args, config, "user_stories")
    selected = apply_filters(
        items, projects=args.project, titles=args.title, limit=args.limit
    )

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(
        f"Processing {len(selected)} user story(ies) out of {len(items)} declared."
    )

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("user stories")
        return 0

    skip_epics = not bool(getattr(args, "assign_epics", True))

    check = preflight_items(
        selected,
        client,
        config,
        auto_create_labels=args.create_missing_labels,
        auto_create_milestones=args.create_missing_milestones,
        skip_epics=skip_epics,
    )

    assignees = AssigneeIndex(client)
    epics = EpicIndex(client, strict=False)
    existing = {} if config.dry_run else _existing_titles(client, selected)
    prepared = apply_milestone_ids(selected, check)

    for item in prepared:
        title = str(item["title"]).strip()
        alias = str(item["project"])

        if title in existing.get(alias, set()):
            reporter.skipped("user story", title, f"already present in '{alias}'")
            continue

        run_safely(
            lambda item=item, title=title, alias=alias: _create_one(
                client, config, args, item, title, alias, check, assignees, epics,
                existing, reporter,
            ),
            kind="user story",
            name=title,
            reporter=reporter,
        )

    reporter.finish("user stories")
    return 1 if reporter.summary.failed else 0


def _create_one(
    client: GitLabClient,
    config: Config,
    args: argparse.Namespace,
    item: dict[str, Any],
    title: str,
    alias: str,
    check: Preflight,
    assignees: AssigneeIndex,
    epics: EpicIndex,
    existing: dict[str, set[str]],
    reporter: Reporter,
) -> None:
    body = build_issue_payload(
        item,
        config,
        render=markdown.render_user_story,
        preflight=check,
        assignee_index=assignees,
    )

    detail = _detail_for(item)

    if config.dry_run:
        reporter.created("user story", title, detail=detail)
        return

    created = client.create_issue(alias, body)
    existing.setdefault(alias, set()).add(title)
    reporter.created("user story", title, url=created.get("web_url"), detail=detail)

    if args.assign_epics and item.get("epic"):
        _attach_epic(client, epics, check, created, item, title, reporter)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "stories",
        help="Create user stories (issues enriched with acceptance criteria).",
        aliases=["user-stories"],
    )
    actions = parser.add_subparsers(dest="action", required=True)

    create = actions.add_parser(
        "create",
        help="Create the user stories declared in a JSON document.",
        description=(
            "Required fields: project, title, description. Optional fields: "
            "acceptance_criteria, definition_of_done, notes, labels, milestone, "
            "start_date, due_date, assignees, epic, issue_type, weight, "
            "confidential, state."
        ),
    )
    add_payload_argument(create)
    add_filter_arguments(create)
    create.add_argument(
        "--create-missing-labels",
        action="store_true",
        help="Create referenced labels that do not exist yet in the target project.",
    )
    create.add_argument(
        "--create-missing-milestones",
        action="store_true",
        help="Create referenced milestones that do not exist yet in the target project.",
    )
    create.add_argument(
        "--assign-epics",
        action="store_true",
        default=True,
        help="Attach created stories to the epic named in their 'epic' field (default).",
    )
    create.add_argument(
        "--no-assign-epics",
        dest="assign_epics",
        action="store_false",
        help="Do not attach stories to their epic.",
    )
    create.set_defaults(handler=create_user_stories)

    listing = actions.add_parser("list", help="List existing issues as user stories.")
    listing.add_argument(
        "--project", action="append", metavar="ALIAS", help="Repeatable. Defaults to all."
    )
    listing.add_argument(
        "--state", choices=("opened", "closed", "all"), default="opened"
    )
    listing.add_argument(
        "--label", help="Only issues carrying this label, e.g. week::2."
    )
    listing.set_defaults(handler=list_issues)


__all__ = ["register"]

