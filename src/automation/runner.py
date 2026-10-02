"""Shared execution helpers: reporting, counters and resource resolvers.

The resolvers cache GitLab lookups (labels, milestones, epics, users) so a
payload with dozens of items only pays for one API round trip per resource.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .client import GitLabClient, GitLabError
from .config import ConfigError

LOGGER = logging.getLogger("gitlab-automation.runner")


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
@dataclass
class Summary:
    """Counters for a single command execution."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
        }

    def total(self) -> int:
        return sum(self.as_dict().values())


class Reporter:
    """Formats consistent, human friendly progress output."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.summary = Summary()
        self._failures: list[tuple[str, str]] = []

    # -- individual events -------------------------------------------------
    def created(self, kind: str, name: str, url: str | None = None, detail: str = "") -> None:
        self.summary.created += 1
        prefix = "Would create" if self.dry_run else "Created"
        LOGGER.info("%s %s '%s'.%s", prefix, kind, name, _detail(detail, url))

    def updated(self, kind: str, name: str, url: str | None = None, detail: str = "") -> None:
        self.summary.updated += 1
        prefix = "Would update" if self.dry_run else "Updated"
        LOGGER.info("%s %s '%s'.%s", prefix, kind, name, _detail(detail, url))

    def skipped(self, kind: str, name: str, reason: str = "already exists") -> None:
        self.summary.skipped += 1
        LOGGER.info("Skipped %s '%s' (%s).", kind, name, reason)

    def failed(self, kind: str, name: str, reason: str) -> None:
        self.summary.failed += 1
        self._failures.append((name, reason))
        LOGGER.error("Failed to create %s '%s': %s", kind, name, reason)

    def info(self, message: str) -> None:
        LOGGER.info(message)

    def detail(self, message: str) -> None:
        LOGGER.debug(message)

    def warning(self, message: str) -> None:
        LOGGER.warning(message)

    # -- final report ------------------------------------------------------
    def finish(self, label: str = "items") -> None:
        counts = self.summary.as_dict()
        verb = "would be " if self.dry_run else ""
        LOGGER.info(
            "Summary for %s: %screated=%s updated=%s skipped=%s failed=%s (total %s).",
            label,
            verb,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            self.summary.total(),
        )
        if self._failures:
            LOGGER.error("Failed entries:")
            for name, reason in self._failures:
                LOGGER.error("  - %s: %s", name, reason)


def _detail(detail: str, url: str | None) -> str:
    parts = [part for part in (detail, url or "") if part]
    return f" [{'; '.join(parts)}]" if parts else ""


# ----------------------------------------------------------------------
# Resource resolvers
# ----------------------------------------------------------------------
class LabelIndex:
    """Lazily loads and caches the labels available in each scope."""

    def __init__(self, client: GitLabClient) -> None:
        self._client = client
        self._project: dict[str, set[str]] = {}
        self._group: set[str] | None = None

    def project_labels(self, alias: str) -> set[str]:
        if alias not in self._project:
            self._project[alias] = {
                str(label["name"]) for label in self._client.project_labels(alias)
            }
            LOGGER.debug("Loaded %s labels for project '%s'.", len(self._project[alias]), alias)
        return self._project[alias]

    def invalidate_project(self, alias: str) -> None:
        """Drop the cached label set so the next read hits the API again."""
        self._project.pop(alias, None)

    def group_labels(self) -> set[str]:
        if self._group is None:
            self._group = {
                str(label["name"]) for label in self._client.group_labels()
            }
            LOGGER.debug("Loaded %s group labels.", len(self._group))
        return self._group

    def missing(self, alias: str, labels: Iterable[str]) -> list[str]:
        available = self.project_labels(alias)
        return sorted({label for label in labels if label not in available})

    def missing_group(self, labels: Iterable[str]) -> list[str]:
        available = self.group_labels()
        return sorted({label for label in labels if label not in available})


class MilestoneIndex:
    """Caches milestone titles for a project or the group."""

    def __init__(self, client: GitLabClient) -> None:
        self._client = client
        self._projects: dict[str, dict[str, int]] = {}
        self._group: dict[str, int] | None = None

    def project_milestones(self, alias: str) -> dict[str, int]:
        if alias not in self._projects:
            self._projects[alias] = {
                str(milestone["title"]): int(milestone["id"])
                for milestone in self._client.project_milestones(alias)
            }
        return self._projects[alias]

    def invalidate_project(self, alias: str) -> None:
        self._projects.pop(alias, None)

    def refresh_project(self, alias: str) -> dict[str, int]:
        self.invalidate_project(alias)
        return self.project_milestones(alias)

    def group_milestones(self) -> dict[str, int]:
        if self._group is None:
            self._group = {
                str(milestone["title"]): int(milestone["id"])
                for milestone in self._client.group_milestones()
            }
        return self._group


class EpicIndex:
    """Maps epic titles to their numeric ids.

    ``strict`` decides what happens when the group refuses the epics API. The
    epics commands want a hard failure, because creating nothing is not the job
    they were asked to do. The item commands want to carry on and create the
    issue without its epic, so they pass ``strict=False``.
    """

    def __init__(self, client: GitLabClient, strict: bool = True) -> None:
        self._client = client
        self._strict = strict
        self._titles: dict[str, int] | None = None

    def load(self) -> dict[str, int]:
        if self._titles is None:
            try:
                epics = self._client.group_epics()
            except GitLabError as error:
                if error.status_code != 403:
                    raise
                if self._strict:
                    raise ConfigError(
                        "GitLab epics are a Premium/Ultimate feature and this group "
                        "answered 403. It is a licence limit, not a permissions "
                        "problem: the same token reaches the group's labels and "
                        "milestones fine.\n"
                        '  Fix: remove the "epic" field from the items in '
                        "data/user-stories.json (or whatever payload declares it) and "
                        "run again."
                    ) from error
                LOGGER.warning(
                    "This group does not support epics (403). Carrying on without "
                    "them: every item will be created without an epic. Run on a "
                    "Premium/Ultimate group, or pass --no-assign-epics to silence "
                    "this."
                )
                self._titles = {}
                return self._titles
            self._titles = {str(epic["title"]): int(epic["id"]) for epic in epics}
            LOGGER.debug("Loaded %s epics.", len(self._titles))
        return self._titles

    def find(self, title: str) -> int | None:
        return self.load().get(title)


class AssigneeIndex:
    """Maps usernames to GitLab user ids using the group membership list."""

    def __init__(self, client: GitLabClient) -> None:
        self._client = client
        self._users: dict[str, int] | None = None

    def load(self) -> dict[str, int]:
        if self._users is None:
            self._users = {}
            for member in self._client.group_members():
                username = member.get("username")
                if username:
                    self._users[str(username).lower()] = int(member["id"])
        return self._users

    def resolve(self, usernames: Sequence[str]) -> tuple[list[int], list[str]]:
        """Return ``(ids, unknown)`` for the given usernames."""
        users = self.load()
        ids: list[int] = []
        unknown: list[str] = []
        for username in usernames:
            key = username.strip().lower()
            if not key:
                continue
            user_id = users.get(key)
            if user_id is None:
                unknown.append(username)
            else:
                ids.append(user_id)
        return ids, unknown


# ----------------------------------------------------------------------
# Filtering
# ----------------------------------------------------------------------
def apply_filters(
    items: Sequence[dict[str, Any]],
    *,
    projects: Sequence[str] | None = None,
    titles: Sequence[str] | None = None,
    title_key: str = "title",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Apply the optional ``--project``/``--title``/``--limit`` filters.

    ``title_key`` points at the field that identifies the remote object, which is
    ``title`` for creation payloads but ``issue``/``epic`` for update payloads.
    """
    result = list(items)

    if projects:
        wanted = {alias.strip().lower() for alias in projects}
        result = [
            item
            for item in result
            if str(item.get("project", "")).strip().lower() in wanted
        ]

    if titles:
        wanted_titles = {title.strip().lower() for title in titles}
        result = [
            item
            for item in result
            if str(item.get(title_key, "")).strip().lower() in wanted_titles
        ]

    if limit is not None and limit > 0:
        result = result[:limit]

    return result


def label_string(labels: Iterable[str]) -> str:
    """GitLab accepts labels as a comma separated string."""
    return ",".join(dict.fromkeys(label for label in labels if label))


def run_safely(action: Callable[[], Any], *, kind: str, name: str, reporter: Reporter) -> bool:
    """Execute ``action`` and convert any exception into a reported failure."""
    try:
        action()
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        reporter.failed(kind, name, f"{error.__class__.__name__}: {error}")
        return False
    return True


__all__ = [
    "AssigneeIndex",
    "EpicIndex",
    "LabelIndex",
    "MilestoneIndex",
    "Reporter",
    "Summary",
    "apply_filters",
    "label_string",
    "run_safely",
]


