"""Thin REST wrapper around os.tensor-ai.tech (Glide integrations API).

Phase 6.4.0 Foundation — the HTTP client only. Used by:
- Settings dialog (Validate button) for `validate_key`
- Phase 6.4.1 dialog (Команда dropdown) for `list_boards` / `board_schema`
- Phase 6.4.1 sender for `create_task`

Glide is a custom internal task-manager (parallel to Linear) at tensor-ai.tech.
Auth is via `Authorization: Bearer glide_pk_<workspace>_<random>`. Compared to
`linear_client.py`:

- REST instead of GraphQL (single endpoint per operation)
- Bearer-prefixed token (Linear has none)
- Stable error codes in body envelope `{error: {code, message, details}}`
- Idempotency-Key header recommended on every POST (24h memo per integration+key)
- `fields_warnings[]` is non-fatal (task IS created even with warnings)

Public API:
    class GlideError(Exception)
    class GlideClient(api_key)
        validate_key()       → GET /boards, returns {board_count, sample_names}
        list_boards()        → GET /boards
        board_schema(id)     → GET /boards/{id} (groups + columns + status options)
        create_task(...)     → POST /tasks
        close()              → release HTTP session
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://os.tensor-ai.tech/api/v1/integrations/in"
_DEFAULT_TIMEOUT_S = 30.0

# Glide priority is a fixed string set; sender (6.4.1) maps Priority enum → these.
_VALID_PRIORITIES = {"critical", "high", "medium", "low"}


class GlideError(Exception):
    """All Glide HTTP failures bubble up as this. Message is user-facing
    (Russian where the user is the audience; English for developer-facing
    network/code conditions)."""


class GlideClient:
    """One client per send-session. Reuse across calls."""

    def __init__(self, api_key: str):
        if not api_key or not api_key.strip():
            raise GlideError(
                "Glide API ключ не задан. "
                "Откройте Настройки → Glide и вставьте ключ."
            )
        self._api_key = api_key.strip()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        })

    def close(self) -> None:
        """Close the underlying connection pool. Safe to call from a separate
        thread to interrupt an in-flight request (raises ConnectionError in
        the worker, which bubbles up as GlideError)."""
        self._session.close()

    # ── HTTP plumbing ────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> dict:
        """Single HTTP-method entry point. Returns parsed JSON on 2xx.

        Raises GlideError with a user-facing message on:
        - Network failure (ConnectionError, Timeout, RequestException)
        - 401 — invalid/missing/expired token
        - 403 — forbidden scope or IP
        - 404 — resource not found
        - 422 — payload-validation failure (board_required, no_groups_on_board, ...)
        - 429 — rate-limited (includes X-RateLimit-Reset hint)
        - 5xx — server error
        - Non-JSON body
        """
        url = f"{_BASE_URL}{path}"
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            resp = self._session.request(
                method, url,
                json=json_body, headers=headers, timeout=timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise GlideError(f"Нет соединения с Glide: {e}") from e
        except requests.exceptions.Timeout as e:
            raise GlideError(f"Таймаут Glide (>{timeout}s)") from e
        except requests.exceptions.RequestException as e:
            raise GlideError(f"Ошибка сети Glide: {e}") from e

        if 200 <= resp.status_code < 300:
            try:
                return resp.json() if resp.content else {}
            except ValueError as e:
                raise GlideError(
                    f"Glide вернул не-JSON ответ: {resp.text[:200]}",
                ) from e

        # Non-2xx — try to parse the stable error envelope first.
        body_msg = _extract_error_message(resp)

        if resp.status_code == 429:
            reset = resp.headers.get("X-RateLimit-Reset", "?")
            raise GlideError(f"Glide 429 rate-limit (retry after Reset={reset}): {body_msg}")
        if resp.status_code == 401:
            raise GlideError(f"Glide 401: неверный или просроченный ключ ({body_msg})")
        if resp.status_code == 403:
            raise GlideError(f"Glide 403: нет доступа ({body_msg})")
        if resp.status_code == 404:
            raise GlideError(f"Glide 404: ресурс не найден ({body_msg})")
        # 4xx (except handled above) and 5xx fall through to generic.
        raise GlideError(f"Glide вернул {resp.status_code}: {body_msg}")

    # ── Public operations ────────────────────────────────────────────

    def validate_key(self) -> dict:
        """Cheapest call that proves auth works. Uses GET /boards which
        requires only `boards:read` scope (most tokens have it).

        Returns:
            {"board_count": int, "sample_names": list[str]}

        The sample is the first 3 board names — enough to render
        `✓ Подключено: 5 досок (Inbox, Sales, ...)`. Empty list is valid:
        a brand-new workspace can have a token with no visible boards.
        """
        boards = self.list_boards()
        return {
            "board_count": len(boards),
            "sample_names": [b.get("name", "?") for b in boards[:3]],
        }

    def list_boards(self) -> list[dict]:
        """GET /boards — every board the integration token can see.

        Returns: list of {id, name}. Sorted by Glide; we don't re-sort.
        """
        data = self._request("GET", "/boards")
        # Glide returns a JSON array directly, not wrapped. requests.json()
        # parses arrays into list — but our _request signature says dict.
        # Reality: requests' .json() returns whatever the body is, list or
        # dict. Cast for the type checker, validate at runtime.
        if isinstance(data, list):
            return data
        # Defensive: if Glide ever wraps it (e.g., {"data": [...]}), accept that.
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        raise GlideError(f"Glide /boards вернул неожиданный формат: {type(data).__name__}")

    def board_schema(self, board_id: str) -> dict:
        """GET /boards/{id} — full schema (groups + columns + status options).

        Used by Phase 6.4.1 to populate group dropdown and (later) field
        mapping UI. Return shape per Glide docs:
            {id, name, groups: [...], columns: [{id, title, column_type, options?}, ...]}
        """
        if not board_id:
            raise GlideError("board_id обязателен для board_schema")
        return self._request("GET", f"/boards/{board_id}")

    def create_task(
        self,
        *,
        title: str,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        board_id: Optional[str] = None,
        group_id: Optional[str] = None,
        fields: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """POST /tasks — create one task. Returns the response dict as-is.

        Args:
            title: required, ≤500 chars (Glide validates server-side).
            description: optional plain text.
            priority: one of "critical|high|medium|low" or None.
                When None, key is omitted from payload — Glide leaves default.
            board_id: optional UUID. Falls back to integration's default board
                if omitted; raises 422 board_required if no default either.
            group_id: optional UUID inside the board.
            fields: optional column-name → value map (level-2 mapping per
                Glide docs). Keys matched case-insensitively to column titles.
            idempotency_key: opaque ≤80 char string. RECOMMENDED for every
                POST per Glide docs — caller decides retry-safety strategy.

        Returns:
            {id, board_id, group_id, title, priority, description,
             created_at, fields_applied: [...], fields_warnings: [...]}

        Note on `fields_warnings`: a column-not-found or value-invalid does
        NOT raise — Glide creates the task without that column. Caller
        should inspect `result["fields_warnings"]` and surface to UI/log.
        We log them at WARNING level here so they always reach `logs/app.log`.
        """
        if not title or not title.strip():
            raise GlideError("title обязателен")
        if priority is not None and priority not in _VALID_PRIORITIES:
            raise GlideError(
                f"Glide priority должен быть одним из {sorted(_VALID_PRIORITIES)}, "
                f"получено: {priority!r}",
            )

        body: dict = {"title": title}
        if description is not None:
            body["description"] = description
        if priority is not None:
            body["priority"] = priority
        if board_id is not None:
            body["board_id"] = board_id
        if group_id is not None:
            body["group_id"] = group_id
        if fields:
            body["fields"] = dict(fields)

        result = self._request(
            "POST", "/tasks",
            json_body=body, idempotency_key=idempotency_key,
        )
        warnings = result.get("fields_warnings") or []
        if warnings:
            logger.warning(
                "Glide field-mapping warnings for task %r: %s",
                title, warnings,
            )
        return result


# ── Module-level helpers ─────────────────────────────────────────────


def _extract_error_message(resp: requests.Response) -> str:
    """Pull a usable message out of a non-2xx Glide response.

    Glide's stable envelope is:
        {"error": {"code": "rate_limited", "message": "...", "details": {...}}}

    We prefer the stable code (matches docs's error table), append message
    when present. Falls back to truncated raw body for non-JSON or
    unexpected shapes (defensive — server bugs shouldn't crash the client).
    """
    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code} (empty body)"

    err = (payload or {}).get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        code = err.get("code") or "unknown"
        msg = err.get("message")
        return f"{code}: {msg}" if msg else code
    # Unrecognised shape — return first bit of raw body for debugging.
    return str(payload)[:200]
