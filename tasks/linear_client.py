"""Thin GraphQL wrapper around api.linear.app.

Three operations used across all phases:
- Bootstrap query (validate_key + list_teams in one round-trip)
- TeamContext query (members + labels for a given team)
- CreateIssue mutation (Phase 6.3)

Linear quirk: Authorization header is the raw API key (NO 'Bearer' prefix).
Most APIs use Bearer; this is a frequent source of 401s when copy-pasting
client code from other projects.
"""
from __future__ import annotations

import requests

_GRAPHQL_URL = "https://api.linear.app/graphql"
_DEFAULT_TIMEOUT_S = 30.0


class LinearError(Exception):
    """All Linear HTTP/GraphQL failures bubble up as this."""


class LinearClient:
    """One client per session. Reuse across calls."""

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise LinearError(
                "Linear API ключ не задан. "
                "Откройте Настройки → Linear и вставьте ключ."
            )
        self._api_key = api_key.strip()
        self._session = requests.Session()
        self._session.headers.update(self._build_headers())

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,   # NB: no 'Bearer' prefix
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        """Close connections. Safe to call from another thread to cancel."""
        self._session.close()

    def _graphql(
        self,
        query: str,
        variables: dict | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> dict:
        """Send a GraphQL query/mutation. Returns the 'data' field on success.

        Raises LinearError on:
        - HTTP non-200
        - GraphQL 'errors' array present in response
        - Network failure
        """
        body = {"query": query}
        if variables:
            body["variables"] = variables

        try:
            resp = self._session.post(
                _GRAPHQL_URL, json=body, timeout=timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise LinearError(f"Нет соединения с Linear: {e}") from e
        except requests.exceptions.Timeout as e:
            raise LinearError(f"Таймаут Linear (>{timeout}s)") from e
        except requests.exceptions.RequestException as e:
            raise LinearError(f"Ошибка сети Linear: {e}") from e

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "?")
            raise LinearError(f"Linear 429 rate-limit (retry after {retry_after}s)")
        if resp.status_code != 200:
            raise LinearError(
                f"Linear вернул {resp.status_code}: {resp.text[:200]}"
            )

        payload = resp.json()
        if "errors" in payload and payload["errors"]:
            msgs = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise LinearError(f"Linear GraphQL: {msgs}")

        return payload.get("data", {})
