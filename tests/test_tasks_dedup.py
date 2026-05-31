"""Tests for the task-dedup engine (PR-2). Pure logic — no FS/network."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from tasks.dedup import (
    FUZZY_HIGH,
    FUZZY_LOW,
    SentTask,
    build_sent_registry,
    disambiguate_via_llm,
    find_candidates,
    normalize_title,
)
from tasks.openrouter_client import OpenRouterError
from tasks.persistence import PersistenceError
from tasks.schema import Task, TaskStatus


def test_thresholds_are_sane():
    assert 0.0 < FUZZY_LOW < FUZZY_HIGH < 1.0


def test_sent_task_is_frozen_value_object():
    s = SentTask(
        title="Починить логин",
        backend="linear",
        container_id="team-1",
        ref="node-uuid-1",
        identifier="ENG-1",
        url="http://x/ENG-1",
        meeting_name="2026-05-20_10-00-00_standup",
        meeting_date="2026-05-20_10-00-00",
    )
    assert s.title == "Починить логин"
    assert s.ref == "node-uuid-1"
    with pytest.raises(FrozenInstanceError):
        s.title = "x"  # type: ignore[misc]


def test_normalize_lowercases_and_collapses_punct_and_space():
    assert normalize_title("  Починить   ЛОГИН!! ") == "починить логин"
    assert normalize_title("Fix: the   bug.") == "fix the bug"


def test_normalize_preserves_cyrillic_and_kazakh_letters():
    # Unicode-aware \w must keep RU/KZ letters; only punctuation goes.
    assert normalize_title("Әзірлеу: есеп —  v2") == "әзірлеу есеп v2"


def test_normalize_empty_and_none_safe():
    assert normalize_title("") == ""
    assert normalize_title("!!!") == ""


# ── build_sent_registry ──────────────────────────────────────────────


def _sent(title, ref, **kw):
    """A Task in SENT state with a backend_ref (eligible for the registry)."""
    return Task(
        title=title,
        status=TaskStatus.SENT,
        backend_ref=ref,
        linear_issue_id=kw.get("identifier", "ENG-1"),
        linear_issue_url=kw.get("url", "http://x/ENG-1"),
    )


def _entries(*folders):
    # Non-trivial, varied folder names + dates (no all-zero fixtures).
    return [
        {"folder_path": f, "folder_name": f.split("/")[-1],
         "date_created": f.split("/")[-1][:19]}
        for f in folders
    ]


def _loader(mapping):
    def load(folder):
        if folder not in mapping:
            raise PersistenceError(f"no tasks.json in {folder}")
        return mapping[folder]
    return load


def test_registry_keeps_only_sent_with_backend_ref():
    mapping = {
        "/h/2026-05-20_10-00-00_standup": {
            "backend": "linear", "team_id": "team-A",
            "tasks": [
                _sent("Починить логин", "uuid-1"),
                Task(title="draft idea", status=TaskStatus.PENDING),  # excluded: not SENT
                Task(title="old sent no ref", status=TaskStatus.SENT, backend_ref=None),  # excluded
            ],
        },
    }
    reg = build_sent_registry(
        _entries("/h/2026-05-20_10-00-00_standup"), _loader(mapping),
    )
    assert len(reg) == 1
    s = reg[0]
    assert (s.title, s.ref, s.backend, s.container_id) == (
        "Починить логин", "uuid-1", "linear", "team-A")
    assert s.meeting_name == "2026-05-20_10-00-00_standup"
    assert s.identifier == "ENG-1"


def test_registry_excludes_current_meeting_folder():
    mapping = {
        "/h/A": {"backend": "linear", "team_id": "t", "tasks": [_sent("a", "r1")]},
        "/h/B": {"backend": "linear", "team_id": "t", "tasks": [_sent("b", "r2")]},
    }
    reg = build_sent_registry(
        _entries("/h/A", "/h/B"), _loader(mapping), exclude_folder="/h/B",
    )
    assert [s.ref for s in reg] == ["r1"]


def test_registry_defaults_backend_to_linear_and_skips_missing_tasks_json():
    mapping = {
        "/h/has": {"team_id": "t-9", "tasks": [_sent("x", "r9")]},  # no "backend" key
        # "/h/none" intentionally absent -> loader raises PersistenceError
    }
    reg = build_sent_registry(
        _entries("/h/has", "/h/none"), _loader(mapping),
    )
    assert len(reg) == 1
    assert reg[0].backend == "linear"  # defaulted
    assert reg[0].container_id == "t-9"


# ── find_candidates ──────────────────────────────────────────────────


def _reg_entry(title, ref, backend="linear", container="team-A"):
    return SentTask(
        title=title, backend=backend, container_id=container, ref=ref,
        identifier="ENG-X", url="http://x", meeting_name="m", meeting_date="d",
    )


def test_find_candidates_scope_filters_backend_and_container():
    registry = [
        _reg_entry("Починить логин", "r-match"),                       # same scope
        _reg_entry("Починить логин", "r-other-backend", backend="trello"),
        _reg_entry("Починить логин", "r-other-team", container="team-B"),
    ]
    new = Task(title="починить логин")
    out = find_candidates(new, registry, backend="linear", container_id="team-A")
    assert [s.ref for s, _ in out] == ["r-match"]


def test_find_candidates_sorted_by_score_desc_and_thresholded():
    registry = [
        _reg_entry("Купить кофе для офиса", "r-low"),       # unrelated -> below LOW
        _reg_entry("Подготовить отчёт по продажам", "r-hi"),  # near-identical
        _reg_entry("Подготовить отчет о продажах", "r-mid"),  # close variant
    ]
    new = Task(title="Подготовить отчёт по продажам за май")
    out = find_candidates(new, registry, backend="linear", container_id="team-A")
    refs = [s.ref for s, _ in out]
    assert "r-low" not in refs                 # filtered: score < FUZZY_LOW
    assert refs[0] == "r-hi"                    # best match first
    scores = [score for _, score in out]
    assert scores == sorted(scores, reverse=True)
    assert all(sc >= FUZZY_LOW for sc in scores)


def test_find_candidates_empty_new_title_returns_nothing():
    registry = [_reg_entry("anything", "r")]
    assert find_candidates(Task(title="!!!"), registry,
                           backend="linear", container_id="team-A") == []


# ── disambiguate_via_llm ─────────────────────────────────────────────


def _cands():
    return [
        _reg_entry("Подготовить отчёт по продажам", "r-1"),
        _reg_entry("Обновить документацию API", "r-2"),
    ]


def test_disambiguate_returns_matched_candidate_by_id():
    llm = MagicMock()
    llm.complete.return_value = {"content": '{"match_id": "r-1"}'}
    out = disambiguate_via_llm(
        Task(title="Сделать отчёт продаж"), _cands(), llm, "anthropic/x")
    assert out is not None and out.ref == "r-1"
    # json_mode requested on first attempt
    assert llm.complete.call_args.kwargs.get("json_mode") is True


def test_disambiguate_returns_none_on_explicit_no_match():
    llm = MagicMock()
    llm.complete.return_value = {"content": '{"match_id": null}'}
    assert disambiguate_via_llm(
        Task(title="Нечто иное"), _cands(), llm, "m") is None


def test_disambiguate_unknown_id_returns_none():
    llm = MagicMock()
    llm.complete.return_value = {"content": '{"match_id": "r-999"}'}  # not in cands
    assert disambiguate_via_llm(
        Task(title="x"), _cands(), llm, "m") is None


def test_disambiguate_malformed_json_fails_safe_to_none():
    llm = MagicMock()
    llm.complete.return_value = {"content": "sorry, no JSON here"}
    assert disambiguate_via_llm(
        Task(title="x"), _cands(), llm, "m") is None


def test_disambiguate_retries_without_json_mode_on_400():
    llm = MagicMock()
    llm.complete.side_effect = [
        OpenRouterError("OpenRouter вернул 400: response_format unsupported"),
        {"content": '{"match_id": "r-2"}'},
    ]
    out = disambiguate_via_llm(Task(title="docs"), _cands(), llm, "m")
    assert out is not None and out.ref == "r-2"
    assert llm.complete.call_count == 2
    assert llm.complete.call_args_list[1].kwargs.get("json_mode") is False


def test_disambiguate_propagates_non_400_errors():
    llm = MagicMock()
    llm.complete.side_effect = OpenRouterError("OpenRouter 429 rate-limit")
    with pytest.raises(OpenRouterError, match="429"):
        disambiguate_via_llm(Task(title="x"), _cands(), llm, "m")


def test_disambiguate_empty_candidates_short_circuits_without_llm():
    llm = MagicMock()
    assert disambiguate_via_llm(Task(title="x"), [], llm, "m") is None
    llm.complete.assert_not_called()
