"""Markdown builders used to compose GitLab descriptions.

Keeping the rendering in one place makes the JSON payloads declarative and the
resulting issues consistent across projects.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

ISSUE_TEMPLATE_HEADING = "User Story"
EPIC_TEMPLATE_HEADING = "Description"


def _bullets(items: Iterable[Any], *, checked: bool = False) -> list[str]:
    marker = "- [ ] " if checked else "- "
    return [f"{marker}{_clean(item)}" for item in items if _clean(item)]


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _section(title: str, items: Sequence[str]) -> list[str]:
    if not items:
        return []
    return [f"## {title}", "", *items, ""]


def _block(text: str) -> list[str]:
    text = _clean(text)
    return [text, ""] if text else []


def render_user_story(item: dict[str, Any]) -> str:
    """Render a user story payload into an issue description."""
    lines: list[str] = []
    if item.get("title"):
        lines += [f"# {_clean(item['title'])}", ""]
    lines += _block(item.get("description", ""))
    lines += _section("Acceptance Criteria", _bullets(item.get("acceptance_criteria", []), checked=True))
    lines += _section(
        "Definition of Done", _bullets(item.get("definition_of_done", []), checked=True)
    )
    lines += _section("Notes", _bullets(item.get("notes", [])))
    return _tidy(lines)


def render_epic(item: dict[str, Any]) -> str:
    """Render an epic payload into an epic description."""
    lines: list[str] = []
    if item.get("description"):
        lines += [f"# {_clean(item['title'])}", ""] if item.get("title") else []
        lines += _block(item["description"])
    elif item.get("title"):
        lines += [f"# {_clean(item['title'])}", ""]
    lines += _section("Objectives", _bullets(item.get("objectives", []), checked=True))
    lines += _section("Notes", _bullets(item.get("notes", [])))
    return _tidy(lines)


def render_issue(item: dict[str, Any]) -> str:
    """Render a generic issue payload into an issue description."""
    lines: list[str] = []
    if item.get("description"):
        lines += _block(item["description"])
    lines += _section("Notes", _bullets(item.get("notes", [])))
    return _tidy(lines)


def render_milestone(item: dict[str, Any]) -> str:
    return _tidy(_block(item.get("description", "")))


def render_metadata(item: dict[str, Any]) -> str:
    """Render a compact traceability table appended to issues and epics."""
    rows: list[tuple[str, str]] = []
    if item.get("project"):
        rows.append(("Project", str(item["project"])))
    if item.get("epic"):
        rows.append(("Epic", str(item["epic"])))
    milestone = item.get("milestone_title") or item.get("milestone")
    if milestone:
        rows.append(("Milestone", str(milestone)))
    if item.get("start_date") or item.get("due_date"):
        window = f"{item.get('start_date', '...')} -> {item.get('due_date', '...')}"
        rows.append(("Schedule", window))
    if item.get("weight") is not None:
        rows.append(("Weight", str(item["weight"])))
    if not rows:
        return ""
    lines = ["## Metadata", "", "| Field | Value |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    lines.append("")
    return "\n".join(lines)


def compose(*blocks: str) -> str:
    """Join non-empty Markdown blocks with a blank line between them."""
    return "\n".join(block.strip() for block in blocks if block and block.strip())


def _tidy(lines: list[str]) -> str:
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n" if text.strip() else ""


__all__ = [
    "compose",
    "render_epic",
    "render_issue",
    "render_metadata",
    "render_milestone",
    "render_user_story",
]
