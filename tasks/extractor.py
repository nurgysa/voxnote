"""Orchestrator for the tasks pipeline.

Pure logic with no I/O — receives `linear_client` and `openrouter_client`
as parameters so tests can inject mocks. The dialog is responsible for
constructing real clients and threading.

Public API:
    extract(transcript, team_id, model, lang, linear_client, openrouter_client)
        → {"tasks": list[Task], "corrections": int, "usage": dict,
           "model": str, "raw_response": str}

    build_prompt(transcript, members, labels, lang) → list[dict]   # exposed for tests
    parse_and_validate(raw_text, members, labels) → (list[Task], int)
    ExtractionError                                # raised on unrecoverable LLM-output issues
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Protocol

from tasks.openrouter_client import OpenRouterError
from tasks.schema import Task, priority_from_string

logger = logging.getLogger(__name__)

# Set of all priority strings that are "legitimate". Used to distinguish
# "the LLM said 'critical'" (corrections += 1) from "the LLM said 'none'"
# (no correction needed — that's the literal default).
_KNOWN_PRIORITIES = {"none", "low", "medium", "high", "urgent"}

# How far in the past a due_date may be before we treat it as a hallucination.
# Picks up "due tomorrow" said in a meeting last month — that's still useful.
_DUE_DATE_PAST_TOLERANCE = timedelta(days=30)

_CODEFENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


class ExtractionError(Exception):
    """LLM returned content that we cannot turn into any valid Task list.

    `raw_response` carries the offending LLM output so callers can show it
    to the user / log it for prompt tuning. Set by `extract()` after a
    successful network round-trip; None when raised before we have one.
    """
    def __init__(self, msg: str, raw_response: str | None = None):
        super().__init__(msg)
        self.raw_response = raw_response


class _LLMClient(Protocol):
    """Duck-typed shape we need from openrouter_client."""
    def complete(self, model: str, messages: list[dict],
                 json_mode: bool = ..., temperature: float = ...,
                 timeout: float = ...) -> dict: ...


class _LinearClient(Protocol):
    """Duck-typed shape we need from linear_client."""
    def team_context(self, team_id: str) -> dict: ...


# ── Public functions ─────────────────────────────────────────────────


def build_prompt(
    transcript: str,
    members: list[dict],
    labels: list[dict],
    lang: str | None,
    context: str | None = None,
) -> list[dict]:
    """Construct the system+user message pair fed to OpenRouter."""
    member_lines = "\n".join(
        f"- id={m['id']} | name={m.get('displayName') or m.get('name', '?')}"
        for m in members
    )
    label_lines = "\n".join(
        f"- id={lbl['id']} | name={lbl['name']}" for lbl in labels
    )
    system = (
        "You are a meeting-task extraction assistant. Output strictly valid JSON.\n"
        "No prose, no markdown fences. Required schema:\n"
        '{"tasks": [{\n'
        '  "title": "string, required",\n'
        '  "description": "string",\n'
        '  "priority": "none|low|medium|high|urgent",\n'
        '  "assignee_id": "id from team_members below, or null",\n'
        '  "label_ids": ["ids from team_labels below"],\n'
        '  "due_date": "YYYY-MM-DD or null"\n'
        "}]}\n\n"
        "Rules:\n"
        "- Only assign people whose IDs are in team_members below.\n"
        "- Only use label IDs from team_labels below.\n"
        "- If unsure, leave assignee_id null and label_ids empty.\n"
        "- Use the meeting's dominant language for title and description.\n\n"
        f"team_members:\n{member_lines or '(none)'}\n\n"
        f"team_labels:\n{label_lines or '(none)'}\n"
    )
    lang_hint = f"language: {lang}" if lang else "language: auto-detected"
    context_block = f"{context}\n\n" if context else ""
    user = (
        f"{context_block}"
        f"Meeting transcript ({lang_hint}):\n\n"
        f"{transcript}\n\n"
        "Return only the JSON object."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def parse_and_validate(
    raw_text: str,
    members: list[dict],
    labels: list[dict],
) -> tuple[list[Task], int]:
    """Parse the LLM response, validate every field, return (tasks, corrections).

    Strips markdown codefences if present. Filters out hallucinated assignee
    and label IDs against the supplied team context. Drops tasks with empty
    titles. Raises ExtractionError on unrecoverable issues:
      - Malformed JSON
      - Missing top-level 'tasks' key
      - Every task fails the title rule
    """
    cleaned = _strip_codefence(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"LLM вернул некорректный JSON: {e}") from e

    if not isinstance(data, dict) or "tasks" not in data:
        raise ExtractionError(
            "LLM ответ не содержит ключ 'tasks'. Попробуйте другую модель."
        )

    member_ids = {m["id"] for m in members}
    member_name_by_id = {
        m["id"]: m.get("displayName") or m.get("name") or m["id"]
        for m in members
    }
    label_ids_set = {lbl["id"] for lbl in labels}
    label_name_by_id = {lbl["id"]: lbl["name"] for lbl in labels}

    tasks: list[Task] = []
    corrections = 0

    for raw_item in data.get("tasks", []):
        if not isinstance(raw_item, dict):
            corrections += 1
            logger.warning("LLM task item is not a dict: %r", raw_item)
            continue

        title = (raw_item.get("title") or "").strip()
        if not title:
            corrections += 1
            logger.warning("dropping task with empty title: %r", raw_item)
            continue

        # Priority
        raw_priority = raw_item.get("priority")
        priority = priority_from_string(raw_priority)
        if (
            raw_priority is not None
            and isinstance(raw_priority, str)
            and raw_priority.strip().lower() not in _KNOWN_PRIORITIES
            and raw_priority.strip() != ""   # empty string → legitimate fallback
        ):
            corrections += 1
            logger.warning(
                "unknown priority %r → fallback NONE (task=%r)", raw_priority, title,
            )

        # Assignee
        raw_assignee = raw_item.get("assignee_id")
        assignee_id: str | None = None
        assignee_name: str | None = None
        if raw_assignee:
            if raw_assignee in member_ids:
                assignee_id = raw_assignee
                assignee_name = member_name_by_id.get(raw_assignee)
            else:
                corrections += 1
                logger.warning(
                    "hallucinated assignee_id %r dropped (task=%r)",
                    raw_assignee, title,
                )

        # Labels
        raw_labels = raw_item.get("label_ids") or []
        clean_label_ids: list[str] = []
        for lid in raw_labels:
            if lid in label_ids_set:
                clean_label_ids.append(lid)
            else:
                corrections += 1
                logger.warning(
                    "hallucinated label_id %r dropped (task=%r)", lid, title,
                )
        clean_label_names = [label_name_by_id[lid] for lid in clean_label_ids]

        # Due date
        raw_due = raw_item.get("due_date")
        due_date = _validate_due_date(raw_due)
        if raw_due and due_date is None:
            corrections += 1
            logger.warning(
                "invalid/stale due_date %r cleared (task=%r)", raw_due, title,
            )

        description = raw_item.get("description") or ""
        if not isinstance(description, str):
            corrections += 1
            logger.warning("non-string description coerced to empty (task=%r)", title)
            description = ""

        tasks.append(Task(
            title=title,
            description=description,
            priority=priority,
            assignee_id=assignee_id,
            assignee_name=assignee_name,
            label_ids=clean_label_ids,
            label_names=clean_label_names,
            due_date=due_date,
        ))

    if not tasks:
        raise ExtractionError(
            "LLM не вернул валидных задач. Попробуйте другую модель."
        )

    return tasks, corrections


def extract(
    *,
    transcript: str,
    model: str,
    lang: str | None,
    openrouter_client: _LLMClient,
    # Phase 6.4.1: extractor no longer fetches team context itself.
    # Caller passes pre-fetched members/labels (Linear path) or empty
    # lists (Glide path — no LLM grounding). The legacy team_id +
    # linear_client params remain for backward compat with Phase 6.0–6.3
    # callers (and the 21 existing tests); when both paths are provided,
    # the explicit members/labels win.
    members: list | None = None,
    labels: list | None = None,
    context: str | None = None,
    team_id: str | None = None,
    linear_client: _LinearClient | None = None,
) -> dict:
    """Run the full extraction. Returns dict with tasks, corrections, usage,
    model echo, raw_response (for debugging / 'Show raw response' UI).

    Raises:
        OpenRouterError, LinearError — for network/HTTP/auth issues
        ExtractionError              — for unrecoverable LLM-output issues
    """
    if members is None and labels is None and linear_client is not None and team_id:
        # Backward-compat path: fetch context ourselves.
        ctx = linear_client.team_context(team_id)
        members = ctx.get("members") or []
        labels  = ctx.get("labels")  or []
    members = members or []
    labels  = labels  or []

    messages = build_prompt(transcript, members, labels, lang, context=context)

    # First attempt: JSON mode. Some models reject response_format with 400;
    # we detect via "400" in the error message and retry once without.
    try:
        response = openrouter_client.complete(
            model=model, messages=messages, json_mode=True,
        )
    except OpenRouterError as e:
        if "вернул 400:" in str(e):
            logger.info(
                "model %s rejected json_mode, retrying without response_format",
                model,
            )
            response = openrouter_client.complete(
                model=model, messages=messages, json_mode=False,
            )
        else:
            raise

    raw_content = response["content"]
    try:
        tasks, corrections = parse_and_validate(raw_content, members, labels)
    except ExtractionError as e:
        # We have the raw LLM output here; attach it to the exception so
        # the dialog can show "Show raw response" affordance and so log
        # readers can debug prompt issues directly.
        logger.warning(
            "ExtractionError; raw LLM response logged for review:\n%s",
            raw_content[:2000],
        )
        raise ExtractionError(str(e), raw_response=raw_content) from e

    return {
        "tasks": tasks,
        "corrections": corrections,
        "usage": response.get("usage", {}),
        "model": response.get("model", model),
        "raw_response": raw_content,
        "members": members,
        "labels": labels,
    }


def extract_one_task(
    *,
    free_text: str,
    members: list | None = None,
    labels: list | None = None,
    lang: str | None,
    model: str,
    openrouter_client: _LLMClient,
) -> Task | None:
    """Extract ONE task from a short free-form description.

    Mirrors ``extract()`` but for a single task — used by the Söyle
    auto-fill flow (Phase 6.5). The prompt machinery doesn't care whether
    the input is a meeting transcript or 1-3 sentences; we just feed the
    free text in the user-message slot and take the first task from the
    LLM's output.

    Returns None when the LLM produces no valid tasks (e.g. it couldn't
    figure out a title from the input). Caller surfaces this as a
    user-visible error and lets the user re-phrase.

    Raises the same exceptions as ``extract()``: ``OpenRouterError``
    on network/HTTP, ``ExtractionError`` on unrecoverable LLM output.
    """
    members = members or []
    labels  = labels  or []

    messages = build_prompt(free_text, members, labels, lang)

    try:
        response = openrouter_client.complete(
            model=model, messages=messages, json_mode=True,
        )
    except OpenRouterError as e:
        if "вернул 400:" in str(e):
            logger.info(
                "model %s rejected json_mode in extract_one_task, retrying without response_format",
                model,
            )
            response = openrouter_client.complete(
                model=model, messages=messages, json_mode=False,
            )
        else:
            raise

    raw_content = response["content"]
    try:
        tasks, _corrections = parse_and_validate(raw_content, members, labels)
    except ExtractionError as e:
        logger.warning(
            "extract_one_task: ExtractionError; raw LLM response:\n%s",
            raw_content[:2000],
        )
        raise ExtractionError(str(e), raw_response=raw_content) from e

    return tasks[0] if tasks else None


# ── Helpers ──────────────────────────────────────────────────────────


def _strip_codefence(text: str) -> str:
    """Remove ``` or ```json fences if the response is wrapped in them."""
    m = _CODEFENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


def _validate_due_date(raw: object) -> str | None:
    """Accept ISO YYYY-MM-DD strings within tolerance window. Else None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        d = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    if date.today() - d > _DUE_DATE_PAST_TOLERANCE:
        return None
    return d.isoformat()
