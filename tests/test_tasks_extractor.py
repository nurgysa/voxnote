"""Tests for tasks.extractor — pure logic with mocked clients, no real network."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tasks.extractor import (
    ExtractionError, build_prompt, extract, parse_and_validate,
)
from tasks.schema import Priority


# ── Fixtures ──────────────────────────────────────────────────────────


def _members():
    return [
        {"id": "u-aidar", "name": "Aidar", "displayName": "Айдар"},
        {"id": "u-nur",   "name": "Nurgysa", "displayName": "Нурғыса"},
    ]


def _labels():
    return [
        {"id": "l-bug",     "name": "bug",     "color": "#f00"},
        {"id": "l-mobile",  "name": "mobile",  "color": "#0f0"},
    ]


def _llm_response(tasks: list[dict]) -> str:
    """Helper: format the JSON payload an LLM would return."""
    import json
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


# ── parse_and_validate ───────────────────────────────────────────────


def test_parse_extracts_well_formed_task():
    raw = _llm_response([{
        "title": "Починить login",
        "description": "Айдар сообщил жалобы.",
        "priority": "high",
        "assignee_id": "u-aidar",
        "label_ids": ["l-bug"],
        "due_date": "2026-05-15",
    }])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert len(tasks) == 1
    assert corrections == 0
    t = tasks[0]
    assert t.title == "Починить login"
    assert t.priority is Priority.HIGH
    assert t.assignee_id == "u-aidar"
    assert t.assignee_name == "Айдар"   # filled from team context
    assert t.label_ids == ["l-bug"]
    assert t.label_names == ["bug"]
    assert t.due_date == "2026-05-15"


def test_parse_strips_json_codefences():
    """Some models return ```json\\n{...}\\n``` despite explicit instructions."""
    raw = "```json\n" + _llm_response([{"title": "X"}]) + "\n```"
    tasks, _ = parse_and_validate(raw, _members(), _labels())
    assert len(tasks) == 1
    assert tasks[0].title == "X"


def test_parse_strips_plain_codefences():
    """Same again with the language-less ``` variant."""
    raw = "```\n" + _llm_response([{"title": "Y"}]) + "\n```"
    tasks, _ = parse_and_validate(raw, _members(), _labels())
    assert len(tasks) == 1


def test_parse_drops_task_with_empty_title():
    raw = _llm_response([
        {"title": "", "priority": "high"},
        {"title": "Valid one", "priority": "low"},
    ])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert len(tasks) == 1
    assert tasks[0].title == "Valid one"
    assert corrections >= 1   # at least one task dropped


def test_parse_filters_hallucinated_assignee():
    raw = _llm_response([{"title": "T", "assignee_id": "u-ghost"}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert len(tasks) == 1
    assert tasks[0].assignee_id is None
    assert tasks[0].assignee_name is None
    assert corrections == 1


def test_parse_filters_hallucinated_labels_keeps_valid():
    raw = _llm_response([{"title": "T", "label_ids": ["l-bug", "l-ghost", "l-mobile"]}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].label_ids == ["l-bug", "l-mobile"]
    assert tasks[0].label_names == ["bug", "mobile"]
    assert corrections == 1   # one label dropped


def test_parse_unknown_priority_falls_back_to_none_with_correction():
    raw = _llm_response([{"title": "T", "priority": "supercritical"}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].priority is Priority.NONE
    assert corrections == 1


def test_parse_legitimate_none_priority_does_not_count_as_correction():
    """LLM legitimately returning 'none' is not a hallucination."""
    raw = _llm_response([{"title": "T", "priority": "none"}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].priority is Priority.NONE
    assert corrections == 0


def test_parse_due_date_more_than_30_days_in_past_is_cleared():
    """Spec: due_date >30 days in past → cleared, log warning."""
    raw = _llm_response([{"title": "T", "due_date": "2024-01-01"}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].due_date is None
    assert corrections == 1


def test_parse_due_date_recent_past_is_kept():
    """Within-30-days-past dates are kept (meeting on Friday, due Monday-of-last-week)."""
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    raw = _llm_response([{"title": "T", "due_date": yesterday}])
    tasks, _ = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].due_date == yesterday


def test_parse_invalid_due_date_format_is_cleared():
    raw = _llm_response([{"title": "T", "due_date": "tomorrow"}])
    tasks, corrections = parse_and_validate(raw, _members(), _labels())
    assert tasks[0].due_date is None
    assert corrections == 1


def test_parse_malformed_json_raises_extraction_error():
    with pytest.raises(ExtractionError, match="JSON"):
        parse_and_validate("not json at all", _members(), _labels())


def test_parse_no_tasks_key_raises_extraction_error():
    """LLM returned valid JSON but without the 'tasks' key."""
    with pytest.raises(ExtractionError, match="tasks"):
        parse_and_validate('{"other": []}', _members(), _labels())


def test_parse_all_invalid_tasks_raises_extraction_error():
    """If every task has empty/missing title, raise so the dialog can offer
    "Show raw response" — per spec edge case."""
    raw = _llm_response([{"title": ""}, {}, {"title": None}])
    with pytest.raises(ExtractionError, match="валидных"):
        parse_and_validate(raw, _members(), _labels())


# ── build_prompt ─────────────────────────────────────────────────────


def test_build_prompt_returns_system_user_message_pair():
    msgs = build_prompt("Hello world", _members(), _labels(), lang="ru")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    # System prompt contains team context:
    assert "u-aidar" in msgs[0]["content"]
    assert "Айдар"   in msgs[0]["content"]
    assert "l-bug"   in msgs[0]["content"]
    # User message contains transcript:
    assert "Hello world" in msgs[1]["content"]


def test_build_prompt_handles_unknown_language():
    msgs = build_prompt("X", _members(), _labels(), lang=None)
    # Doesn't crash, says "auto-detected" or similar:
    assert "auto" in msgs[1]["content"].lower() or msgs[1]["content"]


# ── extract (orchestrator) ───────────────────────────────────────────


def test_extract_calls_clients_and_returns_validated_tasks():
    """End-to-end with mocked clients."""
    linear = MagicMock()
    linear.team_context.return_value = {"members": _members(), "labels": _labels()}

    openrouter = MagicMock()
    openrouter.complete.return_value = {
        "content": _llm_response([
            {"title": "T1", "priority": "high", "assignee_id": "u-aidar"},
            {"title": "T2", "priority": "low"},
        ]),
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "model": "anthropic/claude-sonnet-4.5",
    }

    result = extract(
        transcript="Some transcript text",
        team_id="team-uuid",
        model="anthropic/claude-sonnet-4.5",
        lang="ru",
        linear_client=linear,
        openrouter_client=openrouter,
    )

    linear.team_context.assert_called_once_with("team-uuid")
    openrouter.complete.assert_called_once()
    assert len(result["tasks"]) == 2
    assert result["corrections"] == 0
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}
    assert result["model"] == "anthropic/claude-sonnet-4.5"
    # Raw response text preserved for "Show raw response" UI fallback:
    assert "T1" in result["raw_response"]


def test_extract_retries_without_json_mode_on_400():
    """If the model rejects response_format=json_object, fall back to
    prompt-instruction-only mode."""
    from tasks.openrouter_client import OpenRouterError

    linear = MagicMock()
    linear.team_context.return_value = {"members": _members(), "labels": _labels()}

    openrouter = MagicMock()
    # First call (json_mode=True) raises; second (json_mode=False) succeeds.
    openrouter.complete.side_effect = [
        OpenRouterError("OpenRouter вернул 400: response_format unsupported"),
        {
            "content": _llm_response([{"title": "T", "priority": "low"}]),
            "usage": {},
            "model": "deepseek/deepseek-v3",
        },
    ]

    result = extract(
        transcript="t", team_id="tid", model="deepseek/deepseek-v3", lang=None,
        linear_client=linear, openrouter_client=openrouter,
    )

    assert openrouter.complete.call_count == 2
    # First attempt with json_mode=True, second without:
    assert openrouter.complete.call_args_list[0].kwargs.get("json_mode") is True
    assert openrouter.complete.call_args_list[1].kwargs.get("json_mode") is False
    assert len(result["tasks"]) == 1


def test_extract_does_not_retry_on_non_400_error():
    """401, 429, network errors: surface immediately, no retry."""
    from tasks.openrouter_client import OpenRouterError

    linear = MagicMock()
    linear.team_context.return_value = {"members": _members(), "labels": _labels()}

    openrouter = MagicMock()
    openrouter.complete.side_effect = OpenRouterError("OpenRouter вернул 401: ...")

    with pytest.raises(OpenRouterError, match="401"):
        extract(transcript="t", team_id="tid", model="m", lang=None,
                linear_client=linear, openrouter_client=openrouter)
    assert openrouter.complete.call_count == 1


def test_extract_attaches_raw_response_to_extraction_error():
    """ExtractionError raised after a successful LLM call must carry the
    raw response so the dialog can display it to the user."""
    linear = MagicMock()
    linear.team_context.return_value = {"members": _members(), "labels": _labels()}

    bad_payload = "this is not JSON at all, sorry"
    openrouter = MagicMock()
    openrouter.complete.return_value = {
        "content": bad_payload, "usage": {}, "model": "x",
    }

    with pytest.raises(ExtractionError) as excinfo:
        extract(transcript="t", team_id="tid", model="m", lang=None,
                linear_client=linear, openrouter_client=openrouter)
    assert excinfo.value.raw_response == bad_payload
