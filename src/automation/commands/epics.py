"""``gitlab-automation epics ...`` - manage group level epics."""

from __future__ import annotations

import argparse
from typing import Any

from .. import markdown
from ..client import GitLabClient, GitLabError
from ..config import Config, ConfigError
from ..runner import (
    AssigneeIndex,
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


def build_epic_payload(
    item: dict[str, Any],
    config: Config,
    assignees: AssigneeIndex,
) -> tuple[dict[str, Any], list[str]]:
    """Translate a JSON epic into the GitLab API payload.

    Returns:
        ``(payload, warnings)`` where warnings describe silently ignored input.
    """
    warnings: list[str] = []
    payload: dict[str, Any] = {
        "title": str(item["title"]).strip(),
        "description": markdown.compose(
            markdown.render_epic(item),
            markdown.render_metadata(item),
        ),
    }

    labels = item.get("labels") or list(config.default_labels)
    if labels:
        payload["labels"] = label_string(labels)

    for field in ("start_date", "due_date"):
        if item.get(field):
            payload[field] = item[field]

    usernames = item.get("assignees") or config.default_assignees
    if usernames:
        ids, unknown = assignees.resolve(usernames)
        if unknown:
            warnings.append(f"unknown assignee(s) ignored: {', '.join(unknown)}")
        if ids:
            payload["assignee_ids"] = sorted(set(ids))

    if item.get("state") == "closed":
        payload["state_event"] = "close"

    return payload, warnings


EPICS_ARE_PREMIUM = (
    "GitLab epics are a Premium/Ultimate feature and the group answered 403. "
    "On Free and Starter the endpoint exists but is refused, so this is a "
    "licence limit, not a permissions problem: the token reaches the group fine "
    "(group info, labels and milestones all answer 200).\n"
    "  To keep going without epics: remove the \"epic\" field from the items in "
    "your payload, or run the epic-bearing documents through a group on a paid "
    "tier."
)

EPICS_UNSUPPORTED_SUMMARY = (
    "This group does not support epics (403 on the epics API), so nothing was "
    "written. This is a licence limit on Free/Starter, not a permissions "
    "problem. Re-run without --skip-unsupported for the full explanation, or "
    "move the payload to a Premium/Ultimate group."
)


def ensure_epics_supported(
    client: GitLabClient,
    config: Config,
    strict: bool = True,
) -> list[dict[str, Any]] | None:
    """Return the existing group epics, or explain why epics are unavailable.

    A bare ``403 Forbidden`` gives no clue that epics are gated behind a paid
    tier, so the status is turned into an explanation the reader can act on.

    ``strict`` chooses between failing and carrying on. When it is ``False`` the
    unsupported case returns ``None`` instead of raising, which lets a scripted
    ``epics create`` step report the limitation and still exit ``0`` rather than
    taking the rest of the pipeline down with it.

    Args:
        client: the configured GitLab client.
        config: the active configuration (unused, kept for symmetry).
        strict: raise on ``403`` instead of returning ``None``.

    Returns:
        The group epics, or ``None`` when unsupported and ``strict`` is ``False``.
    """
    try:
        return client.group_epics()
    except GitLabError as error:
        if error.status_code != 403:
            raise
        if strict:
            raise ConfigError(EPICS_ARE_PREMIUM) from error
        return None


def create_epics(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    _, items = load_payload_for(args, config, "epics")
    selected = apply_filters(items, titles=args.title, limit=args.limit)

    reporter = Reporter(dry_run=config.dry_run)
    reporter.info(
        f"Processing {len(selected)} epic(s) out of {len(items)} declared."
    )

    if not selected:
        reporter.warning("Nothing to process.")
        reporter.finish("epics")
        return 0

    skip_unsupported = bool(getattr(args, "skip_unsupported", False))

    # In dry run we never touch the API, so there is nothing to probe and no
    # reason to fail on a group that cannot host epics.
    existing: dict[str, dict[str, Any]] = {}
    if not config.dry_run:
        found = ensure_epics_supported(client, config, strict=not skip_unsupported)
        if found is None:
            reporter.warning(EPICS_UNSUPPORTED_SUMMARY)
            reporter.finish("epics")
            return 0
        existing = {str(epic["title"]): epic for epic in found}

    assignees = AssigneeIndex(client)

    for item in selected:
        title = str(item["title"]).strip()

        if title in existing:
            reporter.skipped("epic", title)
            continue

        def action(item: dict[str, Any] = item, title: str = title) -> None:
            body, warnings = build_epic_payload(item, config, assignees)
            for warning in warnings:
                reporter.warning(warning)
            detail = _detail_for(item)
            if config.dry_run:
                reporter.created("epic", title, detail=detail)
                return
            created = client.create_epic(body)
            existing[title] = created
            reporter.created("epic", title, url=created.get("web_url"), detail=detail)

        run_safely(action, kind="epic", name=title, reporter=reporter)

    reporter.finish("epics")
    return 1 if reporter.summary.failed else 0


def _detail_for(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("labels"):
        parts.append(f"labels={label_string(item['labels'])}")
    window = window_of(item)
    if window:
        parts.append(window)
    return ", ".join(parts)


def list_epics(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    reporter = Reporter()
    epics = ensure_epics_supported(
        client, config, strict=not getattr(args, "skip_unsupported", False)
    )
    if epics is None:
        reporter.warning(EPICS_UNSUPPORTED_SUMMARY)
        return 0

    rows = [
        {
            "id": epic.get("iid") or epic.get("id"),
            "title": epic.get("title"),
            "state": epic.get("state"),
            "labels": ",".join(epic.get("labels", [])) or "-",
            "start": epic.get("start_date") or "-",
            "due": epic.get("due_date") or "-",
        }
        for epic in epics
        if not args.state or args.state == "all" or epic.get("state") == args.state
    ]

    print_table(rows, ["id", "title", "state", "labels", "start", "due"])
    reporter.info(f"{len(rows)} epic(s) matched.")
    return 0


def update_epics(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    _, items = load_payload_for(args, config, "epic_updates")
    selected = apply_filters(
        items, titles=args.title, title_key="epic", limit=args.limit
    )

    reporter = Reporter(dry_run=config.dry_run)
    found = ensure_epics_supported(
        client, config, strict=not getattr(args, "skip_unsupported", False)
    )
    if found is None:
        reporter.warning(EPICS_UNSUPPORTED_SUMMARY)
        reporter.finish("epics")
        return 0
    epics = {str(epic["title"]): epic for epic in found}

    for item in selected:
        title = str(item["epic"]).strip()
        epic = epics.get(title)
        if epic is None:
            reporter.skipped("epic", title, "not found in the group")
            continue

        changes: dict[str, Any] = {}
        if item.get("labels"):
            changes["labels"] = label_string(item["labels"])

        target_state = item.get("state")
        if target_state == "closed" and epic.get("state") != "closed":
            changes["state_event"] = "close"
        elif target_state == "opened" and epic.get("state") == "closed":
            changes["state_event"] = "reopen"

        if not changes:
            reporter.skipped("epic", title, "nothing to change")
            continue

        detail = ", ".join(sorted(changes))

        def action(changes: dict[str, Any] = changes, title: str = title,
                   epic: dict[str, Any] = epic, detail: str = detail) -> None:
            if config.dry_run:
                reporter.updated("epic", title, detail=detail)
                return
            client.request("PUT", f"epics/{epic['id']}", payload=changes, expected=(200,))
            reporter.updated("epic", title, url=epic.get("web_url"), detail=detail)

        run_safely(action, kind="epic", name=title, reporter=reporter)

    reporter.finish("epics")
    return 1 if reporter.summary.failed else 0


def _add_skip_unsupported(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-unsupported",
        action="store_true",
        help=(
            "Treat 'group does not support epics' as a no-op: print a warning "
            "and exit 0 instead of failing. Useful to keep scripted runs green "
            "on Free/Starter groups."
        ),
    )


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "epics", help="Create, list and update group level epics."
    )
    actions = parser.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create", help="Create the epics of a JSON document.")
    add_payload_argument(create)
    add_filter_arguments(create)
    _add_skip_unsupported(create)
    create.set_defaults(handler=create_epics)

    listing = actions.add_parser("list", help="List existing epics.")
    listing.add_argument("--state", choices=("opened", "closed", "all"), default="all")
    _add_skip_unsupported(listing)
    listing.set_defaults(handler=list_epics)

    update = actions.add_parser(
        "update", help="Apply labels and/or state changes to existing epics."
    )
    add_payload_argument(update)
    add_filter_arguments(update)
    _add_skip_unsupported(update)
    update.set_defaults(handler=update_epics)


__all__ = ["register"]
