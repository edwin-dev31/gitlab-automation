"""Thin, typed wrapper around the GitLab REST API v4.

Only the endpoints required by the automation commands are implemented. Every
call goes through :meth:`GitLabClient.request`, which centralizes retries,
error reporting and dry-run behaviour.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Iterator

import requests

from .config import Config, ConfigError

LOGGER = logging.getLogger("gitlab-automation.client")

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class GitLabError(RuntimeError):
    """Raised when GitLab answers with an unexpected status code."""

    def __init__(self, status_code: int, message: str, url: str) -> None:
        super().__init__(f"[{status_code}] {message} while calling {url}")
        self.status_code = status_code
        self.message = message
        self.url = url


class GitLabClient:
    """Reusable API client bound to a single :class:`Config`."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(config.headers)
        self._group_id: int | None = config.group_id
        self._project_ids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        expected: tuple[int, ...],
    ) -> requests.Response:
        attempts = max(1, self._config.max_retries)

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    timeout=self._config.timeout,
                    verify=self._config.verify_ssl,
                )
            except requests.RequestException as error:
                if attempt == attempts:
                    raise GitLabError(0, f"request failed: {error}", url) from error
                self._sleep(attempt, f"network error ({error.__class__.__name__})")
                continue

            if response.status_code in expected:
                return response

            if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                self._sleep(attempt, f"status {response.status_code}")
                continue

            raise GitLabError(response.status_code, self._describe(response), url)

        raise GitLabError(0, "request failed", url)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected: Iterable[int] = (200, 201),
    ) -> Any:
        """Call the API and return the decoded JSON body.

        Args:
            method: HTTP verb.
            path: Path relative to the API root, e.g. ``groups/1/epics``.
            params: Query string parameters.
            payload: JSON body.
            expected: Status codes treated as success.

        Returns:
            Decoded JSON, or ``None`` when the response has no body.
        """
        url = f"{self._config.api_url}/{path.lstrip('/')}"
        response = self._send(
            method, url, params=params, payload=payload, expected=tuple(expected)
        )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise GitLabError(
                response.status_code, "response body is not valid JSON", url
            ) from error

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected: Iterable[int] = (200,),
    ) -> list[dict[str, Any]]:
        """Collect every page of a paginated endpoint into a single list."""
        url = f"{self._config.api_url}/{path.lstrip('/')}"
        page = 1
        collected: list[dict[str, Any]] = []

        while True:
            query = {"per_page": 100, "page": page, **(params or {})}
            response = self._send(
                "GET", url, params=query, payload=None, expected=tuple(expected)
            )
            batch = response.json()
            if not isinstance(batch, list):
                if isinstance(batch, dict):
                    for value in batch.values():
                        if isinstance(value, list):
                            batch = value
                            break
            if not batch:
                break

            collected.extend(batch)

            next_page = response.headers.get("X-Next-Page") or ""
            if not next_page.strip():
                break
            page = int(next_page)

        return collected

    def iter_paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Lazy variant of :meth:`paginate`."""
        yield from self.paginate(path, params=params)

    @staticmethod
    def _describe(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return (response.text or "").strip()[:500]
        if isinstance(body, dict):
            if "message" in body:
                message = body["message"]
                if isinstance(message, dict):
                    return "; ".join(f"{k}: {v}" for k, v in message.items())
                if isinstance(message, list):
                    return "; ".join(str(item) for item in message)
                return str(message)
            return "; ".join(f"{k}: {v}" for k, v in body.items())
        return str(body)[:500]

    @staticmethod
    def _sleep(attempt: int, reason: str) -> None:
        delay = 2**attempt
        LOGGER.warning(
            "Transient failure on attempt %s (%s). Retrying in %ss.",
            attempt,
            reason,
            delay,
        )
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------
    @property
    def group_id(self) -> int:
        """Numeric id of ``GITLAB_GROUP_PATH``, resolved once and cached."""
        if self._group_id is not None:
            return self._group_id
        if not self._config.group_path:
            raise ConfigError(
                "GITLAB_GROUP_PATH is not set. Group level commands require it."
            )
        encoded = requests.utils.quote(self._config.group_path, safe="")
        group = self.request("GET", f"groups/{encoded}")
        self._group_id = int(group["id"])
        LOGGER.debug("Resolved group %s to id %s", self._config.group_path, self._group_id)
        return self._group_id

    def project_id(self, alias: str) -> int:
        """Numeric id of a configured project alias, resolved once and cached."""
        if alias in self._project_ids:
            return self._project_ids[alias]
        path = self._config.project_path(alias)
        encoded = requests.utils.quote(path, safe="")
        project = self.request("GET", f"projects/{encoded}")
        self._project_ids[alias] = int(project["id"])
        LOGGER.debug("Resolved project %s (%s) to id %s", alias, path, self._project_ids[alias])
        return self._project_ids[alias]

    def project_path_encoded(self, alias: str) -> str:
        return requests.utils.quote(self._config.project_path(alias), safe="")

    def list_projects(self) -> list[dict[str, Any]]:
        return self.paginate("projects", params={"membership": True})

    def group_members(self) -> list[dict[str, Any]]:
        return self.paginate(f"groups/{self.group_id}/members/all")

    def verify_token(self) -> dict[str, Any]:
        return self.request("GET", "user")

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def project_labels(self, alias: str) -> list[dict[str, Any]]:
        return self.paginate(
            f"projects/{self.project_path_encoded(alias)}/labels",
            params={"include_ancestor_groups": True},
        )

    def group_labels(self) -> list[dict[str, Any]]:
        return self.paginate(
            f"groups/{self.group_id}/labels",
            params={"include_ancestor_groups": True},
        )

    def create_project_label(
        self, alias: str, name: str, **attributes: Any
    ) -> dict[str, Any]:
        payload = {"name": name}
        payload.update({key: value for key, value in attributes.items() if value is not None})
        return self.request(
            "POST", f"projects/{self.project_path_encoded(alias)}/labels", payload=payload
        )

    def create_group_label(self, name: str, **attributes: Any) -> dict[str, Any]:
        payload = {"name": name}
        payload.update({key: value for key, value in attributes.items() if value is not None})
        return self.request("POST", f"groups/{self.group_id}/labels", payload=payload)

    # ------------------------------------------------------------------
    # Epics
    # ------------------------------------------------------------------
    def group_epics(self) -> list[dict[str, Any]]:
        return self.paginate(
            f"groups/{self.group_id}/epics",
            params={
                "include_ancestor_groups": False,
                "include_descendant_groups": False,
            },
        )

    def create_epic(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"groups/{self.group_id}/epics", payload=payload)

    def close_epic(self, epic_id: int) -> dict[str, Any]:
        return self.request(
            "PUT", f"epics/{epic_id}", payload={"state_event": "close"}, expected=(200,)
        )

    def assign_epic(self, project_id: int, issue_id: int, epic_id: int) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"projects/{project_id}/issues/{issue_id}",
            payload={"epic_id": epic_id},
            expected=(200,),
        )

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------
    def project_issues(self, alias: str, state: str = "all") -> list[dict[str, Any]]:
        return self.paginate(
            f"projects/{self.project_path_encoded(alias)}/issues",
            params={"state": state},
        )

    def find_issue(self, alias: str, title: str) -> dict[str, Any] | None:
        matches = self.paginate(
            f"projects/{self.project_path_encoded(alias)}/issues",
            params={"state": "all", "search": title, "in": "title"},
        )
        for issue in matches:
            if issue.get("title") == title:
                return issue
        return None

    def create_issue(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST", f"projects/{self.project_path_encoded(alias)}/issues", payload=payload
        )

    def update_issue(
        self, alias: str, issue_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"projects/{self.project_path_encoded(alias)}/issues/{issue_id}",
            payload=payload,
            expected=(200,),
        )

    def close_issue(self, alias: str, issue_id: int) -> dict[str, Any]:
        return self.update_issue(alias, issue_id, {"state_event": "close"})

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------
    def project_milestones(self, alias: str) -> list[dict[str, Any]]:
        return self.paginate(
            f"projects/{self.project_path_encoded(alias)}/milestones"
        )

    def create_project_milestone(
        self, alias: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"projects/{self.project_path_encoded(alias)}/milestones",
            payload=payload,
        )

    def group_milestones(self) -> list[dict[str, Any]]:
        return self.paginate(f"groups/{self.group_id}/milestones")

    def create_group_milestone(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"groups/{self.group_id}/milestones", payload=payload)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "GitLabClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["GitLabClient", "GitLabError"]
