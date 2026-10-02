"""``gitlab-automation issues ...`` - generic issue management.

Issues and user stories share the same GitLab object, so this module owns the
shared flows (create, list, update) and ``stories`` layers the richer rendering
and the epic attachment on top of them.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import markdown
from ..client import GitLabClient
from ..config import Config, ConfigError
from ..issue_builder import (
    Preflight,
    apply_milestone_ids,
    build_issue_payload,
    preflight_items,
)
from ..runner import (
    AssigneeIndex,
    EpicIndex,
    MilestoneIndex,
    Reporter,
    apply_filters,
    label_string,
    run_safely,
)
from ._common import (
    add_filter_arguments,
    add_payload_argument,
    load_payload_for,
    print_table,
    window_of,
)


# ----------------------------------------------------------------------
# Creation
# ----------------------------------------------------------------------
def create_issues(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    """Create plain issues from an ``issues.json`` style document."""
    _, items = load_payload_for(args, config, "issues")
    selected = apply_filters(
        items, projects=args.project, titles=args.title, limit=args.limit
    )

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(f"Processing {len(selected)} issue(s) out of {len(items)} declared.")

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("issues")
        return 0

    check = preflight_items(
        selected,
        client,
        config,
        auto_create_labels=args.create_missing_labels,
        auto_create_milestones=args.create_missing_milestones,
    )

    assignees = AssigneeIndex(client)
    epics = EpicIndex(client)
    existing = {} if config.dry_run else _existing_titles(client, selected)
    prepared = apply_milestone_ids(selected, check)

    for item in prepared:
        title = str(item["title"]).strip()
        alias = str(item["project"])

        if title in existing.get(alias, set()):
            reporter.skipped("issue", title, f"already present in '{alias}'")
            continue

        run_safely(
            lambda item=item, title=title, alias=alias: _create_one(
                client, config, args, item, title, alias, check, assignees, epics,
                existing, reporter,
            ),
            kind="issue",
            name=title,
            reporter=reporter,
        )

    reporter.finish("issues")
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
        render=markdown.render_issue,
        preflight=check,
        assignee_index=assignees,
    )

    detail = _detail_for(item)

    if config.dry_run:
        reporter.created("issue", title, detail=detail)
        return

    created = client.create_issue(alias, body)
    existing.setdefault(alias, set()).add(title)
    reporter.created("issue", title, url=created.get("web_url"), detail=detail)

    if args.assign_epics and item.get("epic"):
        _attach_epic(client, epics, check, created, item, title, reporter)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def _attach_epic(
    client: GitLabClient,
    epics: EpicIndex,
    check: Preflight,
    created: dict[str, Any],
    item: dict[str, Any],
    title: str,
    reporter: Reporter,
) -> None:
    """Attach a freshly created issue to its epic, when the epic exists."""
    epic_id = check.resolved.get(f"epic::{item['epic']}") or epics.find(str(item["epic"]))
    if not epic_id:
        reporter.warning(
            "Epic '%s' not found; issue '%s' was created without it.",
            item["epic"],
            title,
        )
        return
    client.assign_epic(int(created["project_id"]), int(created["id"]), int(epic_id))
    reporter.info("Attached '%s' to epic '%s'.", title, item["epic"])


def _existing_titles(
    client: GitLabClient, items: list[dict[str, Any]]
) -> dict[str, set[str]]:
    """Collect the titles already present per project, for idempotency."""
    aliases = sorted({str(item["project"]) for item in items})
    return {
        alias: {str(issue["title"]) for issue in client.project_issues(alias)}
        for alias in aliases
    }


def _detail_for(item: dict[str, Any]) -> str:
    parts = [f"project={item.get('project')}"]
    if item.get("labels"):
        parts.append(f"labels={label_string(item['labels'])}")
    if item.get("milestone_title") or item.get("milestone"):
        parts.append(f"milestone={item.get('milestone_title') or item['milestone']}")
    if item.get("epic"):
        parts.append(f"epic={item['epic']}")
    window = window_of(item)
    if window:
        parts.append(window)
    return ", ".join(parts)


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------
def list_issues(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    """Print the issues of the selected projects as a table."""
    aliases = args.project or sorted(config.projects)
    if not aliases:
        raise ConfigError(
            "No project aliases given. Use --project or configure GITLAB_PROJECTS."
        )

    rows: list[dict[str, Any]] = []
    for alias in aliases:
        for issue in client.project_issues(alias, state=args.state):
            labels = issue.get("labels") or []
            if args.label and args.label not in labels:
                continue
            rows.append(
                {
                    "project": alias,
                    "id": issue.get("iid"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "milestone": (issue.get("milestone") or {}).get("title") or "-",
                    "labels": ",".join(labels) or "-",
                    "due": issue.get("due_date") or "-",
                }
            )

    print_table(rows, ["project", "id", "title", "state", "milestone", "labels", "due"])
    Reporter().info(f"{len(rows)} issue(s) matched.")
    return 0


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------
def update_issues(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    """Apply labels, state, milestone or epic changes to existing issues."""
    _, items = load_payload_for(args, config, "issue_updates")
    selected = apply_filters(
        items,
        projects=args.project,
        titles=args.issue,
        title_key="issue",
        limit=args.limit,
    )

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(f"Processing {len(selected)} update(s) out of {len(items)} declared.")

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("issue updates")
        return 0

    epics = EpicIndex(client).load()
    milestones = MilestoneIndex(client)
    assignees = AssigneeIndex(client)

    for item in selected:
        title = str(item["issue"]).strip()
        alias = str(item["project"])
        run_safely(
            lambda item=item, title=title, alias=alias: _update_one(
                client, config, item, title, alias, epics, milestones,
                assignees, reporter,
            ),
            kind="issue update",
            name=title,
            reporter=reporter,
        )

    reporter.finish("issue updates")
    return 1 if reporter.summary.failed else 0


def _update_one(
    client: GitLabClient,
    config: Config,
    item: dict[str, Any],
    title: str,
    alias: str,
    epics: dict[str, int],
    milestones: MilestoneIndex,
    assignees: AssigneeIndex,
    reporter: Reporter,
) -> None:
    changes: dict[str, Any] = {}

    if item.get("labels"):
        changes["labels"] = label_string(item["labels"])
    if item.get("state"):
        changes["state_event"] = "close" if item["state"] == "closed" else "open"
    if item.get("milestone"):
        milestone_id = milestones.project_milestones(alias).get(str(item["milestone"]))
        if milestone_id is None:
            reporter.warning(
                "Milestone '%s' not found in '%s'; leaving it unchanged.",
                item["milestone"],
                alias,
            )
        else:
            changes["milestone_id"] = milestone_id
    if item.get("epic"):
        epic_id = epics.get(str(item["epic"]))
        if epic_id is None:
            reporter.warning("Epic '%s' not found; leaving it unchanged.", item["epic"])
        else:
            changes["epic_id"] = epic_id
    if item.get("assignees"):
        ids, unknown = assignees.resolve(item["assignees"])
        if unknown:
            reporter.warning("Unknown assignee(s) ignored: %s", ", ".join(unknown))
        if ids:
            changes["assignee_ids"] = sorted(set(ids))
    for date_field in ("start_date", "due_date"):
        if item.get(date_field):
            changes[date_field] = item[date_field]

    if not changes:
        reporter.warning(
            "Update for '%s' declares no change; skipping.", title
        )
        return

    detail = ", ".join(sorted(changes))

    if config.dry_run:
        reporter.updated("issue", title, detail=detail)
        return

    issue = client.find_issue(alias, title)
    if issue is None:
        reporter.warning("Issue '%s' not found in '%s'.", title, alias)
        reporter.failed("issue update", title, f"not found in '{alias}'")
        return

    updated = client.update_issue(alias, int(issue["id"]), changes)
    reporter.updated("issue", title, url=updated.get("web_url"), detail=detail)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "issues",
        help="Create, list and update project issues.",
    )
    actions = parser.add_subparsers(dest="action", required=True)

    create = actions.add_parser(
        "create",
        help="Create the issues declared in a JSON document.",
        description=(
            "Required fields: project, title, description. Optional fields: "
            "notes, labels, milestone, start_date, due_date, assignees, epic, "
            "issue_type, weight, confidential, state."
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
        help="Attach created issues to the epic named in their 'epic' field (default).",
    )
    create.add_argument(
        "--no-assign-epics",
        dest="assign_epics",
        action="store_false",
        help="Do not attach issues to their epic.",
    )
    create.set_defaults(handler=create_issues)

    listing = actions.add_parser("list", help="List existing issues.")
    listing.add_argument(
        "--project", action="append", metavar="ALIAS", help="Repeatable. Defaults to all."
    )
    listing.add_argument(
        "--state", choices=("opened", "closed", "all"), default="opened"
    )
    listing.add_argument("--label", help="Only issues carrying this label.")
    listing.set_defaults(handler=list_issues)

    update = actions.add_parser(
        "update",
        help="Change labels, state, milestone or epic of existing issues.",
        description=(
            "Each item references an existing issue by title via its 'issue' "
            "field and declares only the fields to change: state, labels, "
            "milestone, epic, assignees, start_date, due_date."
        ),
    )
    add_payload_argument(update)
    update.add_argument(
        "--project", action="append", metavar="ALIAS", help="Repeatable."
    )
    update.add_argument(
        "--issue", action="append", metavar="TITLE", help="Repeatable. Exact title."
    )
    update.add_argument("--limit", type=int, metavar="N", help="Process at most N items.")
    update.set_defaults(handler=update_issues)


__all__ = ["create_issues", "list_issues", "register", "update_issues"]