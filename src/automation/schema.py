"""Declarative schema for the JSON documents stored in ``data/``.

The validator is intentionally dependency free: it produces human readable
errors with a JSON pointer-ish path (``stories[3].labels``) so a broken payload
can be fixed without guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal

from .config import Config, ConfigError

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVEL = Literal["error", "warning"]

#: Wildcard accepted in a label payload to target every configured alias.
ALL_PROJECTS_TARGET = "all"


class PayloadError(RuntimeError):
    """Raised when a JSON document cannot be read or parsed."""


@dataclass(frozen=True)
class FieldSpec:
    """Description of a single supported field."""

    name: str
    types: tuple[type, ...]
    required: bool = False
    choices: tuple[Any, ...] | None = None
    item_types: tuple[type, ...] = ()
    default: Any = None
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class KindSpec:
    """Description of one payload kind (epics, user stories, ...)."""

    kind: str
    collection_keys: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    supports_defaults: bool = True
    date_fields: tuple[str, ...] = ()
    pairwise_dates: tuple[tuple[str, str], ...] = ()

    def by_name(self) -> dict[str, FieldSpec]:
        return {spec.name: spec for spec in self.fields}

    def required_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.required)


# ----------------------------------------------------------------------
# Shared field groups
# ----------------------------------------------------------------------
LABELS_FIELD = FieldSpec(
    name="labels",
    types=(list,),
    item_types=(str,),
    description="Labels applied to the created object.",
)

DATES_FIELDS = (
    FieldSpec(
        name="start_date",
        types=(str,),
        description="ISO date (YYYY-MM-DD) marking the start of the work.",
    ),
    FieldSpec(
        name="due_date",
        types=(str,),
        description="ISO date (YYYY-MM-DD) marking the end of the work.",
    ),
)

ASSIGNEES_FIELD = FieldSpec(
    name="assignees",
    types=(list,),
    item_types=(str,),
    aliases=("assignee_ids",),
    description="Usernames resolved to GitLab user ids.",
)

EPIC_FIELD = FieldSpec(
    name="epic",
    types=(str,),
    description="Title of an existing epic this object must be attached to.",
)

PROJECT_FIELD = FieldSpec(
    name="project",
    types=(str,),
    required=True,
    description="Alias declared in GITLAB_PROJECTS.",
)

ISSUE_FIELD = FieldSpec(
    name="issue",
    types=(str,),
    required=True,
    description="Title of an existing issue the labels/milestone must be applied to.",
)


EPIC_SPEC = KindSpec(
    kind="epics",
    collection_keys=("epics", "stories"),
    supports_defaults=False,
    date_fields=("start_date", "due_date"),
    pairwise_dates=(("start_date", "due_date"),),
    fields=(
        FieldSpec(
            name="title",
            types=(str,),
            required=True,
            description="Epic title, used as the idempotency key.",
        ),
        FieldSpec(name="description", types=(str,), description="Epic description."),
        FieldSpec(
            name="objectives",
            types=(list,),
            item_types=(str,),
            description="Checklist rendered under 'Objectives'.",
        ),
        FieldSpec(
            name="notes",
            types=(list,),
            item_types=(str,),
            description="Free-form notes rendered as Markdown.",
        ),
        LABELS_FIELD,
        *DATES_FIELDS,
        ASSIGNEES_FIELD,
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed"),
            default="opened",
            description="Initial state of the epic.",
        ),
    ),
)

USER_STORY_SPEC = KindSpec(
    kind="user_stories",
    collection_keys=("user_stories", "stories", "userStories"),
    date_fields=("start_date", "due_date"),
    pairwise_dates=(("start_date", "due_date"),),
    fields=(
        PROJECT_FIELD,
        FieldSpec(
            name="title",
            types=(str,),
            required=True,
            description="Story title, used as the idempotency key.",
        ),
        FieldSpec(
            name="description",
            types=(str,),
            required=True,
            description="The 'As a ... I want ... so that ...' narrative.",
        ),
        FieldSpec(
            name="acceptance_criteria",
            types=(list,),
            item_types=(str,),
            description="Checklist rendered under 'Acceptance Criteria'.",
        ),
        FieldSpec(
            name="definition_of_done",
            types=(list,),
            item_types=(str,),
            aliases=("dod",),
            description="Checklist rendered under 'Definition of Done'.",
        ),
        FieldSpec(
            name="notes",
            types=(list,),
            item_types=(str,),
            description="Extra Markdown appended at the end of the description.",
        ),
        LABELS_FIELD,
        FieldSpec(
            name="milestone",
            types=(str,),
            description="Existing milestone title.",
        ),
        *DATES_FIELDS,
        ASSIGNEES_FIELD,
        FieldSpec(
            name="issue_type",
            types=(str,),
            choices=("issue", "task", "incident"),
            description="GitLab work item type.",
        ),
        FieldSpec(
            name="weight",
            types=(int,),
            description="Estimated weight (Premium feature).",
        ),
        FieldSpec(
            name="confidential",
            types=(bool,),
            default=False,
            description="Create the issue as confidential.",
        ),
        EPIC_FIELD,
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed"),
            default="opened",
            description="Initial state of the issue.",
        ),
    ),
)

ISSUE_SPEC = KindSpec(
    kind="issues",
    collection_keys=("issues", "tickets"),
    date_fields=("start_date", "due_date"),
    pairwise_dates=(("start_date", "due_date"),),
    fields=(
        PROJECT_FIELD,
        FieldSpec(
            name="title",
            types=(str,),
            required=True,
            description="Issue title, used as the idempotency key.",
        ),
        FieldSpec(name="description", types=(str,), description="Markdown description."),
        FieldSpec(
            name="notes",
            types=(list,),
            item_types=(str,),
            description="Extra Markdown appended at the end of the description.",
        ),
        LABELS_FIELD,
        FieldSpec(name="milestone", types=(str,), description="Existing milestone title."),
        *DATES_FIELDS,
        ASSIGNEES_FIELD,
        FieldSpec(
            name="issue_type",
            types=(str,),
            choices=("issue", "task", "incident"),
            description="GitLab work item type.",
        ),
        FieldSpec(
            name="weight",
            types=(int,),
            description="Estimated weight (Premium feature).",
        ),
        FieldSpec(
            name="confidential",
            types=(bool,),
            default=False,
            description="Create the issue as confidential.",
        ),
        EPIC_FIELD,
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed"),
            default="opened",
            description="Initial state of the issue.",
        ),
    ),
)

MILESTONE_SPEC = KindSpec(
    kind="milestones",
    collection_keys=("milestones",),
    fields=(
        FieldSpec(
            name="title",
            types=(str,),
            required=True,
            description="Milestone title, used as the idempotency key.",
        ),
        FieldSpec(name="description", types=(str,), description="Milestone description."),
        FieldSpec(
            name="scope",
            types=(str,),
            choices=("group", "project"),
            default="group",
            description="Where the milestone is created.",
        ),
        FieldSpec(
            name="project",
            types=(str,),
            description="Required when scope is 'project'.",
        ),
        FieldSpec(
            name="start_date",
            types=(str,),
            required=True,
            description="ISO date (YYYY-MM-DD) when the milestone starts.",
        ),
        FieldSpec(
            name="due_date",
            types=(str,),
            required=True,
            description="ISO date (YYYY-MM-DD) when the milestone ends.",
        ),
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed", "active"),
            default="opened",
            description="Initial state of the milestone.",
        ),
    ),
)

LABEL_SPEC = KindSpec(
    kind="labels",
    collection_keys=("labels",),
    supports_defaults=False,
    fields=(
        FieldSpec(
            name="name",
            types=(str,),
            required=True,
            description="Label name, e.g. 'status::backlog'.",
        ),
        FieldSpec(
            name="scope",
            types=(str,),
            choices=("group", "project"),
            default="project",
            description="Where the label is created.",
        ),
        FieldSpec(
            name="project",
            types=(str,),
            description="Required when scope is 'project'; 'all' targets every alias.",
        ),
        FieldSpec(
            name="color",
            types=(str,),
            description="Hex color including '#', e.g. '#1F75CB'.",
        ),
        FieldSpec(name="description", types=(str,), description="Label description."),
        FieldSpec(
            name="priority",
            types=(int,),
            description="Relative priority, lower is more important.",
        ),
    ),
)

EPIC_UPDATE_SPEC = KindSpec(
    kind="epic_updates",
    collection_keys=("epic_updates", "updates"),
    fields=(
        FieldSpec(
            name="epic",
            types=(str,),
            required=True,
            description="Title of the epic to update.",
        ),
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed"),
            description="Target state of the epic.",
        ),
        LABELS_FIELD,
    ),
)

ISSUE_UPDATE_SPEC = KindSpec(
    kind="issue_updates",
    collection_keys=("issue_updates",),
    fields=(
        PROJECT_FIELD,
        FieldSpec(
            name="issue",
            types=(str,),
            required=True,
            description="Title of the issue to update.",
        ),
        FieldSpec(
            name="state",
            types=(str,),
            choices=("opened", "closed"),
            description="Target state of the issue.",
        ),
        LABELS_FIELD,
        FieldSpec(
            name="milestone",
            types=(str,),
            description="Milestone title to attach.",
        ),
        FieldSpec(
            name="epic",
            types=(str,),
            description="Epic title to attach.",
        ),
        ASSIGNEES_FIELD,
    ),
)


KIND_SPECS: dict[str, KindSpec] = {
    spec.kind: spec
    for spec in (
        EPIC_SPEC,
        USER_STORY_SPEC,
        ISSUE_SPEC,
        MILESTONE_SPEC,
        LABEL_SPEC,
        EPIC_UPDATE_SPEC,
        ISSUE_UPDATE_SPEC,
    )
}

#: Filename stem -> kind. Used when a document omits the ``"type"`` key.
FILENAME_KINDS = {
    "epics": "epics",
    "user-stories": "user_stories",
    "user_stories": "user_stories",
    "stories": "user_stories",
    "issues": "issues",
    "milestones": "milestones",
    "labels": "labels",
    "epic-updates": "epic_updates",
    "epic_updates": "epic_updates",
    "issue-updates": "issue_updates",
    "issue_updates": "issue_updates",
}

#: Default document used by each command when ``--file`` is not provided.
DEFAULT_FILES = {
    "epics": "epics.json",
    "user_stories": "user-stories.json",
    "issues": "issues.json",
    "milestones": "milestones.json",
    "labels": "labels.json",
    "epic_updates": "epic-updates.json",
    "issue_updates": "issue-updates.json",
}


@dataclass
class Issue_:
    """A single validation finding."""

    level: LEVEL
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.path}: {self.message}"


@dataclass
class Payload:
    """A parsed and normalized JSON document."""

    kind: str
    spec: KindSpec
    items: list[dict[str, Any]]
    defaults: dict[str, Any]
    source: Path
    title: str = ""
    collection_present: bool = True

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class ValidationReport:
    """Aggregated findings for one or more payloads."""

    findings: list[Issue_] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue_]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Issue_]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str) -> None:
        self.findings.append(Issue_("error", path, message))

    def warn(self, path: str, message: str) -> None:
        self.findings.append(Issue_("warning", path, message))

    def extend(self, other: "ValidationReport") -> None:
        self.findings.extend(other.findings)


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def load_payload(path: str | Path) -> Payload:
    """Read a JSON document and wrap it into a :class:`Payload`.

    Raises:
        PayloadError: when the file is missing, not valid JSON, or has an
            unknown ``"type"``.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise PayloadError(f"Payload file not found: {file_path}")

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PayloadError(
            f"Invalid JSON in {file_path} at line {error.lineno} "
            f"column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(raw, dict):
        raise PayloadError(
            f"Invalid payload in {file_path}: the root value must be an object."
        )

    kind = str(raw.get("type") or FILENAME_KINDS.get(file_path.stem, "")).strip()
    if not kind:
        raise PayloadError(
            f"Cannot determine the payload type of {file_path}. Add a top level "
            f'"type" key (one of: {", ".join(sorted(KIND_SPECS))}) or use a known '
            f"file name ({', '.join(sorted(DEFAULT_FILES.values()))})."
        )

    spec = KIND_SPECS.get(kind)
    if spec is None:
        raise PayloadError(
            f"Unknown payload type {kind!r} in {file_path}. "
            f"Supported types: {', '.join(sorted(KIND_SPECS))}."
        )

    items: list[Any] = []
    collection_present = False
    for key in spec.collection_keys:
        if key in raw:
            value = raw[key]
            if not isinstance(value, list):
                raise PayloadError(
                    f"Invalid payload in {file_path}: {key!r} must be a list."
                )
            items = value
            collection_present = True
            break
    else:
        raise PayloadError(
            f"Invalid payload in {file_path}: expected a top level "
            f"{' or '.join(repr(key) for key in spec.collection_keys)} list."
        )

    defaults = raw.get("defaults", {})
    if defaults and not isinstance(defaults, dict):
        raise PayloadError(
            f"Invalid payload in {file_path}: 'defaults' must be an object."
        )

    return Payload(
        kind=kind,
        spec=spec,
        items=items,
        defaults=dict(defaults) if isinstance(defaults, dict) else {},
        source=file_path,
        title=str(raw.get("title", "")),
        collection_present=collection_present,
    )


def discover_payloads(data_dir: str | Path) -> list[Path]:
    """Return every JSON document inside ``data_dir``, sorted by name."""
    directory = Path(data_dir)
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def _check_type(
    value: Any, spec: FieldSpec, report: ValidationReport, path: str
) -> None:
    if isinstance(value, bool) and bool not in spec.types:
        report.error(path, f"expected {spec.types[0].__name__}, got boolean")
        return
    if not isinstance(value, spec.types):
        names = "/".join(t.__name__ for t in spec.types)
        report.error(path, f"expected {names}, got {type(value).__name__}")
        return
    if spec.item_types and isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, spec.item_types):
                names = "/".join(t.__name__ for t in spec.item_types)
                report.error(f"{path}[{index}]", f"expected {names}, got {type(item).__name__}")
    if spec.choices and value not in spec.choices:
        allowed = ", ".join(repr(choice) for choice in spec.choices)
        report.error(path, f"must be one of {allowed}, got {value!r}")


def _check_date(value: str, path: str, report: ValidationReport) -> None:
    if not DATE_PATTERN.match(value):
        report.error(path, f"must use the ISO format YYYY-MM-DD, got {value!r}")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        report.error(path, f"is not a real calendar date: {value!r}")


def _validate_item(
    item: Any,
    spec: KindSpec,
    report: ValidationReport,
    path: str,
    config: Config | None,
) -> None:
    if not isinstance(item, dict):
        report.error(path, f"expected an object, got {type(item).__name__}")
        return

    known = spec.by_name()
    alias_map: dict[str, str] = {}
    for field_spec in spec.fields:
        for alias in field_spec.aliases:
            alias_map[alias] = field_spec.name

    for key in item:
        if key not in known and key not in alias_map:
            allowed = ", ".join(sorted(known))
            report.warn(f"{path}.{key}", f"unknown field, ignored. Allowed: {allowed}")

    normalized: dict[str, Any] = {}
    for field_spec in spec.fields:
        value = item.get(field_spec.name)
        if value is None:
            for alias in field_spec.aliases:
                if item.get(alias) is not None:
                    value = item[alias]
                    break
        if value is None:
            continue
        _check_type(value, field_spec, report, f"{path}.{field_spec.name}")
        normalized[field_spec.name] = value

    for name in spec.required_names():
        if normalized.get(name) in (None, "", []):
            report.error(f"{path}.{name}", "is required but missing or empty")

    for date_field in spec.date_fields:
        value = normalized.get(date_field)
        if isinstance(value, str):
            _check_date(value, f"{path}.{date_field}", report)

    for start_name, end_name in spec.pairwise_dates:
        start, end = normalized.get(start_name), normalized.get(end_name)
        if isinstance(start, str) and isinstance(end, str):
            if DATE_PATTERN.match(start) and DATE_PATTERN.match(end) and end < start:
                report.error(
                    f"{path}.{end_name}",
                    f"must not be earlier than {start_name} ({end} < {start})",
                )

    if config is not None and config.projects:
        project = normalized.get("project")
        is_wildcard = (
            spec.kind == "labels" and project == ALL_PROJECTS_TARGET
        )
        if (
            isinstance(project, str)
            and project
            and not is_wildcard
            and not config.has_project(project)
        ):
            known_aliases = ", ".join(sorted(config.projects))
            report.error(
                f"{path}.project",
                f"unknown alias {project!r}. Declared in GITLAB_PROJECTS: {known_aliases}",
            )

    labels = normalized.get("labels")
    if isinstance(labels, list):
        seen: set[str] = set()
        for index, label in enumerate(labels):
            if not isinstance(label, str):
                continue
            if label != label.strip():
                report.warn(
                    f"{path}.labels[{index}]",
                    f"has surrounding whitespace, trimmed to {label.strip()!r}",
                )
            if label in seen:
                report.warn(f"{path}.labels[{index}]", f"duplicate label {label!r}")
            seen.add(label)


def validate_payload(
    payload: Payload, config: Config | None = None
) -> ValidationReport:
    """Validate a whole document, including its ``defaults`` block."""
    report = ValidationReport()

    if payload.spec.supports_defaults:
        for key in payload.defaults:
            field_spec = payload.spec.by_name().get(key)
            if field_spec is None:
                report.warn(
                    f"defaults.{key}",
                    "unknown default, ignored. Allowed: "
                    + ", ".join(sorted(payload.spec.by_name())),
                )
                continue
            _check_type(payload.defaults[key], field_spec, report, f"defaults.{key}")

    if not payload.collection_present:
        report.error(
            "payload",
            f"missing the top level "
            f"{' / '.join(repr(key) for key in payload.spec.collection_keys)} list",
        )

    titles: dict[str, int] = {}
    for index, item in enumerate(payload.items):
        path = f"{payload.spec.collection_keys[0]}[{index}]"
        _validate_item(item, payload.spec, report, path, config)

        if isinstance(item, dict) and isinstance(item.get("title"), str):
            title = item["title"].strip()
            # Titles are idempotency keys, but only within one target scope:
            # two projects may legitimately hold a milestone of the same name.
            scope = item.get("scope")
            key = (
                f"{item.get('project', '')}::{scope}::{title}"
                if scope is not None
                else f"{item.get('project', '')}::{title}"
            )
            if key in titles:
                other = titles[key]
                where = (
                    " in the same project"
                    if scope is None
                    else " with the same scope"
                )
                report.error(
                    f"{path}.title",
                    f"duplicates {payload.spec.collection_keys[0]}[{other}]{where}. "
                    "Titles are used as the idempotency key and must be unique.",
                )
            else:
                titles[key] = index

    if payload.kind == "milestones":
        _validate_milestone_scopes(payload, report)
    if payload.kind == "labels":
        _validate_label_scopes(payload, report)

    return report


def _validate_milestone_scopes(payload: Payload, report: ValidationReport) -> None:
    seen: set[str] = set()
    for index, item in enumerate(payload.items):
        path = f"milestones[{index}]"
        if not isinstance(item, dict):
            continue
        scope = item.get("scope", "group")
        if scope == "project" and not item.get("project"):
            report.error(
                f"{path}.project", "is required when scope is 'project'"
            )
        key = f"{item.get('project', '*')}::{item.get('title', '')}"
        if key in seen:
            report.error(f"{path}.title", f"duplicates an earlier milestone ({key})")
        seen.add(key)


def _validate_label_scopes(payload: Payload, report: ValidationReport) -> None:
    seen: set[str] = set()
    for index, item in enumerate(payload.items):
        path = f"labels[{index}]"
        if not isinstance(item, dict):
            continue
        scope = item.get("scope", "project")
        name = str(item.get("name", ""))
        target = item.get("project")
        if scope == "project" and not target:
            report.error(
                f"{path}.project",
                "is required when scope is 'project' (use 'all' for every alias)",
            )
        if isinstance(target, str) and target != ALL_PROJECTS_TARGET and "::" in target:
            report.error(
                f"{path}.project",
                f"'all' is the only wildcard accepted, got {target!r}",
            )
        color = item.get("color")
        if isinstance(color, str) and not re.match(r"^#[0-9A-Fa-f]{6}$", color):
            report.error(
                f"{path}.color",
                f"must be a hex color like '#1F75CB', got {color!r}",
            )
        key = f"{scope}:{target or '*'}::{name}"
        if key in seen:
            report.error(f"{path}.name", f"duplicates an earlier label ({name})")
        seen.add(key)


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------
def merged_items(payload: Payload, overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return items with ``defaults`` and CLI overrides applied.

    Precedence is ``item > overrides > defaults``, except for ``labels`` where
    the three sources are concatenated and de-duplicated while preserving
    order, because labels are additive by nature.
    """
    layers = [payload.defaults, overrides or {}]
    result: list[dict[str, Any]] = []

    for raw in payload.items:
        item = dict(raw) if isinstance(raw, dict) else {}

        for alias_name, canonical in _alias_map(payload.spec).items():
            if alias_name in item and canonical not in item:
                item[canonical] = item[alias_name]

        labels: list[str] = []
        for source in (*layers, item):
            values = source.get("labels")
            if isinstance(values, list):
                labels.extend(value.strip() for value in values if isinstance(value, str))
        merged: dict[str, Any] = {}
        for layer in (*layers, item):
            for key, value in layer.items():
                if key == "labels":
                    continue
                if value is not None:
                    merged[key] = value
        merged["labels"] = list(dict.fromkeys(labels))
        result.append(merged)

    return result


def _alias_map(spec: KindSpec) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for field_spec in spec.fields:
        for alias in field_spec.aliases:
            mapping[alias] = field_spec.name
    return mapping


def resolve_data_file(
    config: Config,
    kind: str,
    override: str | Path | None = None,
) -> Path:
    """Return the path of the document backing ``kind``.

    Raises:
        ConfigError: when the file cannot be located.
    """
    if override:
        path = Path(override)
        if not path.is_absolute():
            candidate = config.data_dir / path
            path = candidate if candidate.exists() else path
        return path

    default_name = DEFAULT_FILES.get(kind)
    candidates: Iterable[Path] = (
        [config.data_dir / default_name] if default_name else []
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Fall back to any document in data/ that declares the same type.
    for candidate in discover_payloads(config.data_dir):
        try:
            document = load_payload(candidate)
        except PayloadError:
            continue
        if document.kind == kind:
            return candidate

    expected = config.data_dir / (default_name or f"{kind}.json")
    raise ConfigError(
        f"No payload found for type {kind!r}. Expected {expected} "
        f"or pass an explicit path with --file."
    )


__all__ = [
    "DEFAULT_FILES",
    "FILENAME_KINDS",
    "KIND_SPECS",
    "KindSpec",
    "Payload",
    "PayloadError",
    "ValidationReport",
    "discover_payloads",
    "load_payload",
    "merged_items",
    "resolve_data_file",
    "validate_payload",
]
