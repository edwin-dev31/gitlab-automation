"""``gitlab-automation doctor`` - verify the environment against GitLab.

Answers a single question: is this installation ready to push the data files?
"""

from __future__ import annotations

import argparse
from typing import Any

from ..client import GitLabClient
from ..config import Config
from ..runner import Reporter
from ._common import print_table

OK = "OK"
FAIL = "FAIL"
WARN = "WARN"


def _row(check: str, status: str, detail: str) -> dict[str, Any]:
    return {"check": check, "status": status, "detail": detail}


def doctor(client: GitLabClient, config: Config, args: argparse.Namespace) -> int:
    reporter = Reporter()
    rows: list[dict[str, Any]] = []

    # 1. Token
    try:
        user = client.verify_token()
        rows.append(
            _row("token", OK, f"valid, authenticated as '{user.get('username')}'")
        )
    except Exception as error:  # noqa: BLE001
        rows.append(_row("token", FAIL, str(error)))
        print_table(rows, ["check", "status", "detail"])
        reporter.info("Cannot continue without a valid token.")
        return 1

    # 2. Group
    if config.group_path:
        try:
            group_id = client.group_id
            rows.append(
                _row("group", OK, f"'{config.group_path}' resolved to id {group_id}")
            )
        except Exception as error:  # noqa: BLE001
            rows.append(_row("group", FAIL, f"{config.group_path}: {error}"))
    else:
        rows.append(_row("group", WARN, "GITLAB_GROUP_PATH is not set"))

    # 3. Projects
    if not config.projects:
        rows.append(_row("projects", WARN, "GITLAB_PROJECTS is empty"))
    else:
        for alias, path in sorted(config.projects.items()):
            try:
                project_id = client.project_id(alias)
                rows.append(_row(f"project:{alias}", OK, f"{path} (id {project_id})"))
            except Exception as error:  # noqa: BLE001
                rows.append(_row(f"project:{alias}", FAIL, f"{path}: {error}"))

    # 4. Data directory
    data_dir = config.data_dir
    if not data_dir.exists():
        rows.append(_row("data-dir", FAIL, f"{data_dir} does not exist"))
    else:
        rows.append(_row("data-dir", OK, str(data_dir)))

    # 5. Default labels
    if config.default_labels:
        for alias in sorted(config.projects):
            try:
                available = {
                    str(label["name"]) for label in client.project_labels(alias)
                }
                missing = sorted(set(config.default_labels) - available)
                if missing:
                    rows.append(
                        _row(
                            f"labels:{alias}",
                            WARN,
                            f"missing default label(s): {', '.join(missing)}",
                        )
                    )
                else:
                    rows.append(
                        _row(f"labels:{alias}", OK, "all default labels present")
                    )
            except Exception as error:  # noqa: BLE001
                rows.append(_row(f"labels:{alias}", FAIL, str(error)))

    print_table(rows, ["check", "status", "detail"])

    failures = [row for row in rows if row["status"] == FAIL]
    warnings = [row for row in rows if row["status"] == WARN]
    ok = len(rows) - len(failures) - len(warnings)
    reporter.info(
        f"{len(rows)} check(s): {ok} ok, {len(warnings)} warning(s), {len(failures)} failure(s)."
    )
    return 1 if failures else 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "doctor",
        help="Check the environment, the token, the group and every project.",
    )
    parser.set_defaults(handler=doctor)


__all__ = ["register"]
