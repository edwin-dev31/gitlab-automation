"""Runtime configuration loaded from environment variables.

Every value has a sane default so the project stays usable with nothing but a
``GITLAB_TOKEN`` in the ``.env`` file. Nothing here touches the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _discover_project_root() -> Path:
    """Return the repository root: the nearest ancestor holding ``pyproject.toml``.

    The package sits under ``src/``, so the root is not a fixed number of levels
    above this file. Walking up keeps ``data/`` and ``.env`` correct whatever the
    layout, and still works for an editable install, where this file points into
    the clone itself. Falls back to the working directory when installed as a
    wheel, which has no project files around it.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


PROJECT_ROOT = _discover_project_root()
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


class ConfigError(RuntimeError):
    """Raised when the environment is incomplete or inconsistent."""


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from error


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _get_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = _get(name)
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of the environment."""

    gitlab_url: str
    token: str
    group_path: str
    group_id: int | None
    projects: dict[str, str]
    data_dir: Path
    default_labels: tuple[str, ...]
    default_milestone: str | None
    default_issue_type: str
    default_assignees: tuple[str, ...]
    default_state: str
    timeout: int
    max_retries: int
    verify_ssl: bool
    log_level: str
    color: bool
    dry_run: bool

    @property
    def api_url(self) -> str:
        return f"{self.gitlab_url}/api/v4"

    @property
    def headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"}

    def project_path(self, alias: str) -> str:
        """Return the full namespace path of a project alias."""
        try:
            return self.projects[alias]
        except KeyError as error:
            known = ", ".join(sorted(self.projects)) or "<none configured>"
            raise ConfigError(
                f"Unknown project alias {alias!r}. Known aliases: {known}"
            ) from error

    def has_project(self, alias: str) -> bool:
        return alias in self.projects


def load_config(
    *,
    data_dir: str | Path | None = None,
    dry_run: bool | None = None,
    require_token: bool = True,
) -> Config:
    """Build a :class:`Config` from the environment.

    Args:
        data_dir: Override for the directory holding the JSON documents.
        dry_run: Forces dry-run mode regardless of the environment.
        require_token: When ``False`` an empty ``GITLAB_TOKEN`` is tolerated, which
            lets offline commands such as ``validate`` run without credentials.

    Raises:
        ConfigError: when required values are missing or malformed.
    """
    token = os.getenv("GITLAB_TOKEN", "").strip()
    if not token and require_token:
        raise ConfigError(
            "GITLAB_TOKEN is not set. Copy .env.example to .env and fill it in."
        )

    gitlab_url = _get("GITLAB_URL", "https://gitlab.com").rstrip("/")

    raw_projects = _get_list("GITLAB_PROJECTS")
    projects: dict[str, str] = {}
    for entry in raw_projects:
        if "=" not in entry:
            raise ConfigError(
                f"GITLAB_PROJECTS entry {entry!r} is invalid. "
                "Expected format: alias=group/subgroup/project"
            )
        alias, path = entry.split("=", 1)
        projects[alias.strip()] = path.strip().strip("/")

    group_path = _get("GITLAB_GROUP_PATH").strip("/")
    group_id_raw = _get("GITLAB_GROUP_ID")
    group_id = int(group_id_raw) if group_id_raw else None

    if group_path and group_id is None:
        # Derive the numeric id lazily only when a command needs it.
        pass

    resolved_data_dir = Path(data_dir) if data_dir else Path(
        _get("GITLAB_DATA_DIR") or DEFAULT_DATA_DIR
    )
    if not resolved_data_dir.is_absolute():
        resolved_data_dir = PROJECT_ROOT / resolved_data_dir

    default_assignees: tuple[str, ...] = ()
    if _get("GITLAB_DEFAULT_ASSIGNEES"):
        default_assignees = tuple(
            name.strip()
            for name in _get("GITLAB_DEFAULT_ASSIGNEES").split(",")
            if name.strip()
        )

    return Config(
        gitlab_url=gitlab_url,
        token=token,
        group_path=group_path,
        group_id=group_id,
        projects=projects,
        data_dir=resolved_data_dir,
        default_labels=_get_list("GITLAB_DEFAULT_LABELS", ("status::backlog",)),
        default_milestone=_get("GITLAB_DEFAULT_MILESTONE") or None,
        default_issue_type=_get("GITLAB_DEFAULT_ISSUE_TYPE", "issue"),
        default_assignees=default_assignees,
        default_state=_get("GITLAB_DEFAULT_STATE", "opened"),
        timeout=_get_int("GITLAB_TIMEOUT", 30),
        max_retries=_get_int("GITLAB_MAX_RETRIES", 3),
        verify_ssl=_get_bool("GITLAB_VERIFY_SSL", True),
        log_level=_get("GITLAB_LOG_LEVEL", "INFO").upper(),
        color=_get_bool("GITLAB_COLOR", True),
        dry_run=_get_bool("GITLAB_DRY_RUN", False) if dry_run is None else dry_run,
    )


__all__ = [
    "Config",
    "ConfigError",
    "DEFAULT_DATA_DIR",
    "PROJECT_ROOT",
    "load_config",
]

