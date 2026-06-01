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

import logging

import requests

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://api.linear.app/graphql"
_DEFAULT_TIMEOUT_S = 30.0

_VIEWER_QUERY = """
query Viewer {
  viewer { id name email }
}
"""

_BOOTSTRAP_QUERY = """
query Bootstrap {
  viewer { id name email }
  teams { nodes { id name key } }
}
"""

_TEAM_CONTEXT_QUERY = """
query TeamContext($teamId: String!) {
  team(id: $teamId) {
    members { nodes { id name displayName email } }
    labels  { nodes { id name color } }
  }
}
"""

_CREATE_ISSUE_MUTATION = """
mutation CreateIssue(
  $teamId: String!, $title: String!, $description: String,
  $priority: Int, $assigneeId: String, $labelIds: [String!],
  $dueDate: TimelessDate
) {
  issueCreate(input: {
    teamId: $teamId, title: $title, description: $description,
    priority: $priority, assigneeId: $assigneeId,
    labelIds: $labelIds, dueDate: $dueDate
  }) {
    success
    issue { id identifier url }
  }
}
"""

_CREATE_COMMENT_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}
"""

_MAX_ISSUES = 2000

_TEAM_ISSUES_QUERY = """
query TeamIssues($teamId: String!, $after: String) {
  team(id: $teamId) {
    issues(
      first: 250, after: $after,
      filter: { state: { type: { nin: ["completed", "canceled"] } } },
      orderBy: updatedAt
    ) {
      nodes { id identifier title url description }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


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

        try:
            payload = resp.json()
        except ValueError as e:
            raise LinearError(f"Linear вернул не-JSON ответ: {resp.text[:200]}") from e
        if "errors" in payload and payload["errors"]:
            msgs = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise LinearError(f"Linear GraphQL: {msgs}")

        return payload.get("data", {})

    def bootstrap(self) -> dict:
        """Validate + fetch all accessible teams in a single round-trip.

        Returns dict:
            - viewer: {id, name, email}
            - teams: list[{id, name, key}]

        Cached by callers in config['linear_teams_cache'] with 24h TTL.
        """
        data = self._graphql(_BOOTSTRAP_QUERY)
        viewer = data.get("viewer")
        if not viewer:
            raise LinearError("Linear: viewer не найден в ответе bootstrap")
        teams_node = data.get("teams") or {}
        teams = teams_node.get("nodes", [])
        return {"viewer": viewer, "teams": teams}

    def validate_key(self) -> dict:
        """GraphQL `viewer` query — confirms the key works.

        Returns dict with id, name, email of the authenticated user.
        Raises LinearError on any failure.
        """
        data = self._graphql(_VIEWER_QUERY)
        viewer = data.get("viewer")
        if not viewer:
            raise LinearError("Linear: viewer не найден в ответе")
        return viewer

    def team_context(self, team_id: str) -> dict:
        """Fetch members + labels for a team in a single GraphQL query.

        Returns dict:
            - members: list[{id, name, displayName, email}]
            - labels: list[{id, name, color}]

        Used by extractor to give the LLM authoritative context for assignee
        and label resolution. NOT cached — team membership and labels change
        frequently enough that staleness costs more than the network call.
        """
        data = self._graphql(_TEAM_CONTEXT_QUERY, {"teamId": team_id})
        team = data.get("team")
        if not team:
            raise LinearError(f"Linear: команда {team_id} не найдена")
        members = (team.get("members") or {}).get("nodes", [])
        labels  = (team.get("labels")  or {}).get("nodes", [])
        return {"members": members, "labels": labels}

    def list_issues(self, team_id: str) -> list[dict]:
        """All ACTIVE issues in a team (not completed/canceled), for dedup.

        Cursor-paginates 250/page until exhausted or the _MAX_ISSUES safety
        cap (logs a WARNING and returns the partial set if hit — that's the
        signal to adopt server-side search). Each issue: {id, identifier,
        title, url, description}. Raises LinearError on HTTP/network failure.
        """
        issues: list[dict] = []
        cursor: str | None = None
        while True:
            data = self._graphql(
                _TEAM_ISSUES_QUERY, {"teamId": team_id, "after": cursor},
            )
            conn = (data.get("team") or {}).get("issues") or {}
            issues.extend(conn.get("nodes") or [])
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            if len(issues) >= _MAX_ISSUES:
                logger.warning(
                    "Linear team %s has >%d active issues; dedup registry "
                    "capped (consider server-side search retrieval)",
                    team_id, _MAX_ISSUES,
                )
                break
            cursor = page.get("endCursor")
        logger.info("linear list_issues team=%s fetched=%d", team_id, len(issues))
        return issues

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        label_ids: list[str] | None = None,
        due_date: str | None = None,
    ) -> dict:
        """Create a single Linear issue. Returns {id, identifier, url} on success.

        Only `team_id` and `title` are required by Linear. None values are
        *omitted* from the GraphQL variables (not sent as null) — Linear
        treats null as 'set this field to null' rather than 'leave default'.

        Raises LinearError if Linear returns success=false or any HTTP/network
        failure.
        """
        variables: dict = {"teamId": team_id, "title": title}
        if description is not None:
            variables["description"] = description
        if priority is not None:
            variables["priority"] = priority
        if assignee_id is not None:
            variables["assigneeId"] = assignee_id
        if label_ids:
            variables["labelIds"] = list(label_ids)
        if due_date is not None:
            variables["dueDate"] = due_date

        data = self._graphql(_CREATE_ISSUE_MUTATION, variables)
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            raise LinearError(f"Linear отказался создать тикет: {result}")
        return result["issue"]

    def add_comment(self, issue_id: str, body: str) -> None:
        """Post a comment to an existing issue (task-dedup).

        ``issue_id`` is the node UUID (``issue["id"]``), NOT the ENG-123
        identifier — commentCreate rejects the human identifier. Raises
        LinearError on success=false or any HTTP/network failure.
        """
        data = self._graphql(
            _CREATE_COMMENT_MUTATION, {"issueId": issue_id, "body": body},
        )
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise LinearError(f"Linear отказался добавить комментарий: {result}")
