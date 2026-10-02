"""Builds GitLab issue payloads out of validated JSON items.

Shared by the ``stories`` and ``issues`` commands so both produce byte
identical descriptions and label handling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from . import markdown
from .client import GitLabClient
from .config import Config, ConfigError
from .runner import AssigneeIndex, EpicIndex, LabelIndex, MilestoneIndex, label_string

LOGGER = logging.getLogger("gitlab-automation.issues")


@dataclass
class Preflight:
    """Result of the non destructive checks performed before writing."""

    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Cache of ``"<scope>::<title>"`` -> numeric id.
    resolved: dict[str, int] = field(default_factory=dict)
    #: Milestone titles that do not exist yet, per project alias.
    missing_milestones: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def merge(self, prefix: str, other: "Preflight") -> None:
        self.blocking.extend(f"{prefix}: {message}" for message in other.blocking)
        self.warnings.extend(f"{prefix}: {message}" for message in other.warnings)
        self.resolved.update(other.resolved)


Renderer = Callable[[dict[str, Any]], str]


def build_issue_payload(
    item: dict[str, Any],
    config: Config,
    *,
    render: Renderer,
    preflight: Preflight | None = None,
    assignee_index: AssigneeIndex | None = None,
) -> dict[str, Any]:
    """Translate a JSON item into the payload expected by ``POST /issues``.

    Args:
        item: A merged item coming from the JSON document.
        config: Runtime configuration.
        render: Description renderer (``markdown.render_user_story`` or
            ``markdown.render_issue``).
        preflight: Optional collector for non fatal problems.
        assignee_index: Optional index used to resolve usernames.

    Returns:
        The JSON body for the GitLab API. Only fields accepted by
        ``POST /projects/:id/issues`` are emitted; every other key of the JSON
        item is documentation only.
    """
    payload: dict[str, Any] = {
        "title": str(item["title"]).strip(),
        "description": markdown.compose(
            render(item), markdown.render_metadata(item)
        ),
        "labels": label_string(item.get("labels") or config.default_labels),
    }

    if item.get("milestone_id"):
        payload["milestone_id"] = str(item["milestone_id"])

    for date_field in ("start_date", "due_date"):
        if item.get(date_field):
            payload[date_field] = item[date_field]

    payload["issue_type"] = item.get("issue_type") or config.default_issue_type

    if item.get("weight") is not None:
        payload["weight"] = item["weight"]

    if item.get("confidential"):
        payload["confidential"] = True

    if item.get("state") == "closed":
        payload["state_event"] = "close"

    usernames = item.get("assignees") or config.default_assignees
    if usernames and assignee_index is not None:
        ids, unknown = assignee_index.resolve(usernames)
        if unknown and preflight is not None:
            preflight.warnings.append(
                f"unknown assignee(s) ignored: {', '.join(unknown)}"
            )
        if ids:
            payload["assignee_ids"] = sorted(set(ids))

    return {key: value for key, value in payload.items() if value not in (None, "")}


def resolve_milestone_ids(
    items: list[dict[str, Any]],
    milestones: MilestoneIndex,
    preflight: Preflight,
) -> None:
    """Validate that every referenced milestone exists and cache its id."""
    wanted_by_project: dict[str, set[str]] = {}
    for item in items:
        title_value = item.get("milestone")
        if not title_value:
            continue
        alias = str(item.get("project", ""))
        wanted_by_project.setdefault(alias, set()).add(str(title_value))

    for alias in sorted(wanted_by_project):
        known = milestones.project_milestones(alias)
        for title_value in sorted(wanted_by_project[alias]):
            key = f"{alias}::{title_value}"
            if title_value not in known:
                preflight.missing_milestones.setdefault(alias, []).append(title_value)
                preflight.warnings.append(
                    f"milestone '{title_value}' not found in project '{alias}'"
                )
            else:
                preflight.resolved.setdefault(key, known[title_value])


def apply_milestone_ids(
    items: Sequence[dict[str, Any]],
    preflight: Preflight,
) -> list[dict[str, Any]]:
    """Return copies of ``items`` where ``milestone`` titles became ``milestone_id``.

    A title that never made it into ``preflight.resolved`` cannot be sent to the
    API, so the field is dropped and reported instead of being passed through as a
    bogus identifier.
    """
    prepared: list[dict[str, Any]] = []
    for item in items:
        resolved = dict(item)
        title_value = resolved.get("milestone")
        if title_value:
            alias = str(resolved.get("project", ""))
            milestone_id = preflight.resolved.get(f"{alias}::{title_value}")
            resolved.pop("milestone", None)
            resolved["milestone_title"] = title_value
            if milestone_id is not None:
                resolved["milestone_id"] = milestone_id
            else:
                preflight.warnings.append(
                    f"milestone '{title_value}' could not be resolved in "
                    f"'{alias}'; the item was created without it"
                )
        prepared.append(resolved)
    return prepared


def resolve_epic_ids(
    items: list[dict[str, Any]],
    epics: EpicIndex,
    preflight: Preflight,
) -> None:
    """Validate that every referenced epic exists."""
    wanted = {
        str(item["epic"]) for item in items if item.get("epic")
    }
    for title in sorted(wanted):
        epic_id = epics.find(title)
        if epic_id is None:
            preflight.warnings.append(
                f"epic '{title}' not found; the issue will be created without it"
            )
        else:
            preflight.resolved.setdefault(f"epic::{title}", epic_id)


def check_labels(
    items: list[dict[str, Any]],
    labels_index: LabelIndex,
    preflight: Preflight,
    *,
    create_missing: bool = False,
) -> None:
    """Report labels that do not exist yet in the target projects."""
    by_project: dict[str, set[str]] = {}
    for item in items:
        alias = str(item.get("project", ""))
        values = item.get("labels") or []
        if values:
            by_project.setdefault(alias, set()).update(values)

    for alias in sorted(by_project):
        missing = labels_index.missing(alias, by_project[alias])
        if missing:
            message = (
                f"project '{alias}' is missing label(s): {', '.join(missing)}. "
                "Run 'gitlab-automation labels create' to add them."
            )
            if create_missing:
                preflight.warnings.append(message)
            else:
                preflight.blocking.append(message)



LABEL_PALETTE = ("#1F75CB", "#5EB85C", "#F0AD4E", "#D9534F", "#7F8C8D", "#9B59B6")


# ----------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------
def _labels_by_project(items: list[dict[str, Any]]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for item in items:
        grouped.setdefault(str(item.get("project", "")), set()).update(
            item.get("labels") or []
        )
    return {alias: labels for alias, labels in grouped.items() if labels}


def _color_for(label: str) -> str:
    """Deterministic colour so re-runs produce identical labels."""
    return LABEL_PALETTE[sum(ord(char) for char in label) % len(LABEL_PALETTE)]


def _window_of(items: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    starts = sorted(str(i["start_date"]) for i in items if i.get("start_date"))
    dues = sorted(str(i["due_date"]) for i in items if i.get("due_date"))
    return (starts[0] if starts else None, dues[-1] if dues else None)


def preflight_items(
    items: list[dict[str, Any]],
    client: GitLabClient,
    config: Config,
    *,
    auto_create_labels: bool,
    auto_create_milestones: bool,
    skip_epics: bool = False,
) -> Preflight:
    """Run every non destructive check, optionally creating what is missing.

    ``skip_epics`` stops the epic lookup entirely, so a group without epics never
    reaches the gated endpoint in the first place.
    """
    result = Preflight()
    labels_index = LabelIndex(client)
    milestones = MilestoneIndex(client)

    check_labels(items, labels_index, result, create_missing=auto_create_labels)
    resolve_milestone_ids(items, milestones, result)
    if not skip_epics:
        resolve_epic_ids(items, EpicIndex(client, strict=False), result)
    elif any(item.get("epic") for item in items):
        result.warnings.append(
            "epics skipped on request (--no-assign-epics); "
            "the items will be created without them"
        )

    for warning in result.warnings:
        LOGGER.warning("%s", warning)

    if config.dry_run:
        for problem in result.blocking:
            LOGGER.info("Dry run: %s would still be missing.", problem)
        return result

    if auto_create_labels:
        _create_missing_labels(items, client, labels_index, result)

    if auto_create_milestones:
        _create_missing_milestones(items, client, milestones, result)

    if not result.ok:
        raise ConfigError(
            "Preflight failed. Fix the payload or re-run with "
            "--create-missing-labels and/or --create-missing-milestones."
        )

    return result


def _create_missing_labels(
    items: list[dict[str, Any]],
    client: GitLabClient,
    labels_index: LabelIndex,
    result: Preflight,
) -> None:
    for alias, wanted in sorted(_labels_by_project(items).items()):
        for label in labels_index.missing(alias, wanted):
            try:
                client.create_project_label(alias, label, color=_color_for(label))
            except Exception as error:  # noqa: BLE001
                result.blocking.append(
                    f"could not create label '{label}' in '{alias}': {error}"
                )
            else:
                LOGGER.info("Created label '%s' in project '%s'.", label, alias)
        labels_index.invalidate_project(alias)


def _create_missing_milestones(
    items: list[dict[str, Any]],
    client: GitLabClient,
    milestones: MilestoneIndex,
    result: Preflight,
) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item.get("milestone"):
            key = (str(item["project"]), str(item["milestone"]))
            grouped.setdefault(key, []).append(item)

    for (alias, title_value), related in sorted(grouped.items()):
        if title_value in milestones.project_milestones(alias):
            continue
        start, due = _window_of(related)
        if not start or not due:
            result.blocking.append(
                f"cannot create milestone '{title_value}' in '{alias}': the stories "
                "referencing it need 'start_date' and 'due_date' (directly or in "
                "the 'defaults' block)"
            )
            continue
        try:
            client.create_project_milestone(
                alias, {"title": title_value, "start_date": start, "due_date": due}
            )
        except Exception as error:  # noqa: BLE001
            result.blocking.append(
                f"could not create milestone '{title_value}' in '{alias}': {error}"
            )
        else:
            LOGGER.info(
                "Created milestone '%s' in project '%s' (%s -> %s).",
                title_value, alias, start, due,
            )
            refreshed = milestones.refresh_project(alias)
            milestone_id = refreshed.get(title_value)
            if milestone_id is None:
                result.blocking.append(
                    f"milestone '{title_value}' was created in '{alias}' but could "
                    "not be read back"
                )
                continue
            result.resolved[f"{alias}::{title_value}"] = milestone_id
            for item in related:
                item.pop("milestone", None)

__all__ = [
    "LABEL_PALETTE",
    "Preflight",
    "apply_milestone_ids",
    "build_issue_payload",
    "check_labels",
    "preflight_items",
    "resolve_epic_ids",
    "resolve_milestone_ids",
]

