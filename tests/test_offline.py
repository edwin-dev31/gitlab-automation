"""Offline unit tests. Run with ``python -m unittest discover -s tests``."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from automation import markdown
from automation.cli import build_parser
from automation.client import GitLabError
from automation.commands.epics import ensure_epics_supported
from automation.config import Config, ConfigError, load_config
from automation.issue_builder import (
    Preflight,
    apply_milestone_ids,
    build_issue_payload,
    resolve_milestone_ids,
)
from automation.schema import (
    PayloadError,
    discover_payloads,
    load_payload,
    merged_items,
    validate_payload,
)


def texts(issues) -> str:
    """Flatten validation findings into one searchable blob."""
    return "\n".join(
        f"{finding.path} {finding.message}" for finding in issues
    )


def make_config(**overrides) -> Config:
    base = Config(
        gitlab_url="https://gitlab.example.com",
        token="unit-test-token",
        group_path="team",
        group_id=7,
        projects={
            "repo": "team/repo",
            "api": "team/api",
            "web": "team/web",
        },
        data_dir=Path("data"),
        default_labels=("status::backlog",),
        default_milestone=None,
        default_issue_type="issue",
        default_assignees=(),
        default_state="opened",
        timeout=30,
        max_retries=3,
        verify_ssl=True,
        log_level="INFO",
        color=False,
        dry_run=True,
    )
    return replace(base, **overrides)


class SchemaTests(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "issues.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_defaults_are_merged_and_item_wins(self) -> None:
        path = self._write(
            {
                "type": "issues",
                "defaults": {"labels": ["a"], "issue_type": "task"},
                "issues": [{"title": "One", "labels": ["b"]}],
            }
        )
        payload = load_payload(path)
        self.assertEqual(payload.kind, "issues")
        merged = merged_items(payload)
        self.assertEqual(merged[0]["labels"], ["a", "b"])
        self.assertEqual(merged[0]["issue_type"], "task")

    def test_missing_collection_is_rejected(self) -> None:
        path = self._write({"type": "issues"})
        with self.assertRaises(PayloadError):
            load_payload(path)

    def test_empty_collection_is_accepted(self) -> None:
        path = self._write({"type": "milestones", "milestones": []})
        payload = load_payload(path)
        self.assertEqual(payload.count, 0)
        self.assertTrue(payload.collection_present)
        report = validate_payload(payload, make_config())
        self.assertEqual(report.errors, [])

    def test_unknown_project_alias_is_an_error(self) -> None:
        path = self._write(
            {"type": "issues", "issues": [{"project": "nope", "title": "One"}]}
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertTrue("unknown alias" in texts(report.errors))

    def test_alias_check_is_skipped_without_projects(self) -> None:
        path = self._write(
            {"type": "issues", "issues": [{"project": "anything", "title": "One"}]}
        )
        report = validate_payload(load_payload(path), make_config(projects={}))
        self.assertEqual(report.errors, [])

    def test_duplicate_titles_are_rejected(self) -> None:
        path = self._write(
            {
                "type": "issues",
                "issues": [
                    {"project": "repo", "title": "Same"},
                    {"project": "repo", "title": "Same"},
                ],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertTrue("duplicates" in texts(report.errors))

    def test_reversed_dates_are_rejected(self) -> None:
        path = self._write(
            {
                "type": "issues",
                "issues": [
                    {
                        "project": "repo",
                        "title": "Backwards",
                        "start_date": "2026-03-10",
                        "due_date": "2026-03-01",
                    }
                ],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertTrue("due_date" in texts(report.errors))

    def test_label_wildcard_all_is_accepted(self) -> None:
        path = self._write(
            {
                "type": "labels",
                "labels": [{"name": "x", "scope": "project", "project": "all"}],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertEqual(report.errors, [])

    def test_label_requires_a_project_when_scoped_to_one(self) -> None:
        path = self._write(
            {"type": "labels", "labels": [{"name": "x", "scope": "project"}]}
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertTrue("project" in texts(report.errors))

    def test_label_colour_must_be_hex(self) -> None:
        path = self._write(
            {
                "type": "labels",
                "labels": [
                    {"name": "x", "scope": "project", "project": "all", "color": "red"}
                ],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertTrue("color" in texts(report.errors))

    def test_same_milestone_title_in_two_projects_is_allowed(self) -> None:
        window = {"start_date": "2026-03-02", "due_date": "2026-03-13"}
        path = self._write(
            {
                "type": "milestones",
                "milestones": [
                    {"title": "Week 3", "scope": "project", "project": "repo", **window},
                    {
                        "title": "Week 3",
                        "scope": "project",
                        "project": "web",
                        **window,
                    },
                ],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertEqual(texts(report.errors), "")

    def test_same_milestone_title_in_one_project_is_rejected(self) -> None:
        window = {"start_date": "2026-03-02", "due_date": "2026-03-13"}
        path = self._write(
            {
                "type": "milestones",
                "milestones": [
                    {"title": "Week 3", "scope": "project", "project": "repo", **window},
                    {"title": "Week 3", "scope": "project", "project": "repo", **window},
                ],
            }
        )
        report = validate_payload(load_payload(path), make_config())
        self.assertIn("duplicates", texts(report.errors))

    def test_discovery_only_reads_the_top_level(self) -> None:
        directory = Path(tempfile.mkdtemp())
        (directory / "issues.json").write_text(
            json.dumps({"type": "issues", "issues": []}), encoding="utf-8"
        )
        nested = directory / "examples"
        nested.mkdir()
        (nested / "more.json").write_text(
            json.dumps({"type": "issues", "issues": []}), encoding="utf-8"
        )
        found = {path.name for path in discover_payloads(directory)}
        self.assertEqual(found, {"issues.json"})


class ConfigTests(unittest.TestCase):
    def test_project_root_holds_data_and_env(self) -> None:
        # The package lives under src/, so a naive parent.parent lookup would
        # point at src/ and silently break both data_dir and .env discovery.
        from automation import config as config_module

        root = config_module.PROJECT_ROOT
        self.assertTrue((root / "pyproject.toml").is_file(), root)
        self.assertTrue((root / "data").is_dir(), root)
        self.assertEqual(config_module.DEFAULT_DATA_DIR, root / "data")
        self.assertEqual(config_module.ENV_FILE, root / ".env")

    def test_default_data_dir_resolves_inside_the_project(self) -> None:
        os.environ["GITLAB_DATA_DIR"] = "data"
        try:
            config = load_config(require_token=False)
        finally:
            os.environ.pop("GITLAB_DATA_DIR", None)
        self.assertTrue(str(config.data_dir).endswith("data"))
        self.assertNotIn(f"{os.sep}src{os.sep}data", str(config.data_dir))

    def test_token_is_optional_when_not_required(self) -> None:
        os.environ.pop("GITLAB_TOKEN", None)
        config = load_config(require_token=False)
        self.assertEqual(config.token, "")

    def test_token_is_required_by_default(self) -> None:
        os.environ.pop("GITLAB_TOKEN", None)
        with self.assertRaises(ConfigError):
            load_config()

    def test_malformed_project_entry_is_rejected(self) -> None:
        os.environ["GITLAB_TOKEN"] = "x"
        os.environ["GITLAB_PROJECTS"] = "missing-equals-sign"
        try:
            with self.assertRaises(ConfigError):
                load_config()
        finally:
            os.environ.pop("GITLAB_PROJECTS", None)


class IssuePayloadTests(unittest.TestCase):
    def test_user_story_renders_the_expected_sections(self) -> None:
        preflight = Preflight()
        preflight.resolved["repo::Sprint 3"] = 12
        item = apply_milestone_ids(
            [
                {
                    "project": "repo",
                    "title": "US-01: Do the thing",
                    "description": "As a dev, I want it.",
                    "acceptance_criteria": ["It works"],
                    "definition_of_done": ["Tested"],
                    "milestone": "Sprint 3",
                    "start_date": "2026-03-02",
                    "due_date": "2026-03-06",
                    "labels": ["week::2"],
                }
            ],
            preflight,
        )[0]
        body = build_issue_payload(
            item, make_config(), render=markdown.render_user_story
        )
        self.assertEqual(body["milestone_id"], "12")
        self.assertEqual(body["labels"], "week::2")
        self.assertEqual(body["start_date"], "2026-03-02")
        self.assertIn("Acceptance Criteria", body["description"])
        self.assertIn("Definition of Done", body["description"])
        self.assertIn("Sprint 3", body["description"])
        self.assertNotIn("milestone_title", body)

    def test_milestone_title_is_never_sent_as_an_id(self) -> None:
        body = build_issue_payload(
            {"project": "repo", "title": "T", "milestone": "Sprint 3"},
            make_config(),
            render=markdown.render_issue,
        )
        self.assertNotIn("milestone_id", body)

    def test_milestone_titles_are_swapped_for_ids(self) -> None:
        preflight = Preflight()
        preflight.resolved["repo::Sprint 3"] = 42
        prepared = apply_milestone_ids(
            [{"project": "repo", "title": "T", "milestone": "Sprint 3"}], preflight
        )
        self.assertEqual(prepared[0]["milestone_id"], 42)
        self.assertNotIn("milestone", prepared[0])
        self.assertEqual(preflight.warnings, [])

    def test_unresolvable_milestone_is_dropped_and_reported(self) -> None:
        preflight = Preflight()
        prepared = apply_milestone_ids(
            [{"project": "repo", "title": "T", "milestone": "Ghost"}], preflight
        )
        self.assertNotIn("milestone_id", prepared[0])
        self.assertNotIn("milestone", prepared[0])
        self.assertTrue(preflight.warnings)

    def test_missing_milestone_is_recorded_without_raising(self) -> None:
        class _StubMilestones:
            def project_milestones(self, alias: str) -> dict[str, int]:
                return {}

        preflight = Preflight()
        resolve_milestone_ids(
            [{"project": "repo", "title": "T", "milestone": "Ghost"}],
            _StubMilestones(),
            preflight,
        )
        self.assertEqual(preflight.missing_milestones["repo"], ["Ghost"])
        self.assertTrue(any("Ghost" in warning for warning in preflight.warnings))

    def test_metadata_renders_project_and_dates(self) -> None:
        rendered = markdown.render_metadata(
            {
                "project": "repo",
                "start_date": "2026-03-02",
                "due_date": "2026-03-06",
                "epic": "Epic 1",
            }
        )
        self.assertIn("repo", rendered)
        self.assertIn("2026-03-02", rendered)
        self.assertIn("Epic 1", rendered)

    def test_notes_render_as_a_list(self) -> None:
        rendered = markdown.render_issue(
            {
                "title": "T",
                "description": "D",
                "notes": ["first", "second"],
            }
        )
        self.assertIn("- first", rendered)
        self.assertIn("- second", rendered)


class EpicSupportTests(unittest.TestCase):
    """The 403 gate must fail loudly for epics and softly for the items."""

    class FakeClient:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.calls = 0

        def group_epics(self):
            self.calls += 1
            raise GitLabError(self.status_code, "Forbidden", "/groups/7/epics")

    def test_strict_mode_explains_the_licence_limit(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            ensure_epics_supported(self.FakeClient(403), make_config())
        self.assertIn("Premium/Ultimate", str(caught.exception))

    def test_tolerant_mode_returns_none_instead_of_failing(self) -> None:
        client = self.FakeClient(403)
        self.assertIsNone(ensure_epics_supported(client, make_config(), strict=False))
        self.assertEqual(client.calls, 1)

    def test_tolerant_mode_still_raises_other_errors(self) -> None:
        with self.assertRaises(GitLabError):
            ensure_epics_supported(self.FakeClient(500), make_config(), strict=False)


class CliFlagTests(unittest.TestCase):
    """Flags added to keep Free-tier groups usable must stay wired in."""

    def parse(self, argv: list[str]) -> object:
        return build_parser().parse_args(argv)

    def test_stories_assign_epics_defaults_to_true(self) -> None:
        self.assertTrue(self.parse(["stories", "create"]).assign_epics)

    def test_no_assign_epics_turns_the_epic_lookup_off(self) -> None:
        self.assertFalse(
            self.parse(["stories", "create", "--no-assign-epics"]).assign_epics
        )

    def test_skip_unsupported_is_available_on_all_epic_actions(self) -> None:
        for action in ("create", "list", "update"):
            with self.subTest(action=action):
                argv = ["epics", action, "--skip-unsupported"]
                self.assertTrue(self.parse(argv).skip_unsupported)

    def test_skip_unsupported_is_off_by_default(self) -> None:
        self.assertFalse(self.parse(["epics", "create"]).skip_unsupported)

    def test_global_dry_run_is_accepted_after_the_subcommand(self) -> None:
        self.assertTrue(
            self.parse(["stories", "create", "--no-assign-epics", "--dry-run"]).dry_run
        )


if __name__ == "__main__":
    unittest.main()