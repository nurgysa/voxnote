"""Tests for tasks.persistence — disk I/O via pytest tmp_path, no real history."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.persistence import (
    PersistenceError, load_tasks_raw, save_tasks_raw, RAW_FILENAME,
    MUTABLE_FILENAME, load_tasks, save_tasks,
)
from tasks.schema import Priority, Task, TaskStatus


def _sample_tasks() -> list[Task]:
    return [
        Task(title="A", priority=Priority.HIGH, assignee_id="u1", assignee_name="Айдар"),
        Task(title="B", description="Multi\nline", label_ids=["l1"], label_names=["bug"]),
    ]


def _sample_meta() -> dict:
    return {
        "extracted_at": "2026-04-28T15:30:00",
        "model": "anthropic/claude-sonnet-4.5",
        "team_id": "team-uuid",
        "team_name": "Engineering",
        "transcript_lang": "ru",
    }


# ── save_tasks_raw ─────────────────────────────────────────────────────


def test_save_writes_tasks_raw_json_to_folder(tmp_path: Path):
    save_tasks_raw(str(tmp_path), _sample_tasks(), _sample_meta())
    raw = tmp_path / RAW_FILENAME
    assert raw.is_file()
    data = json.loads(raw.read_text(encoding="utf-8"))
    assert data["model"] == "anthropic/claude-sonnet-4.5"
    assert data["team_id"] == "team-uuid"
    assert data["transcript_lang"] == "ru"
    assert isinstance(data["tasks"], list)
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["title"] == "A"
    assert data["tasks"][0]["priority"] == "high"   # enum-as-string


def test_save_does_not_include_local_send_state_in_raw(tmp_path: Path):
    """tasks_raw.json is the LLM's output as-extracted — no selected/status/linear_*.

    Those are user/local-only and belong in tasks.json (Phase 6.2)."""
    save_tasks_raw(str(tmp_path), _sample_tasks(), _sample_meta())
    data = json.loads((tmp_path / RAW_FILENAME).read_text(encoding="utf-8"))
    sample = data["tasks"][0]
    assert "selected" not in sample
    assert "status" not in sample
    assert "linear_issue_id" not in sample
    assert "linear_issue_url" not in sample
    assert "send_error" not in sample
    # local_id IS preserved — it's the durable handle the editor uses.
    assert "local_id" in sample


def test_save_creates_folder_if_missing(tmp_path: Path):
    target = tmp_path / "new-history-entry"
    assert not target.exists()
    save_tasks_raw(str(target), _sample_tasks(), _sample_meta())
    assert (target / RAW_FILENAME).is_file()


def test_save_is_atomic_via_temp_file_rename(tmp_path: Path, monkeypatch):
    """If json.dumps somehow fails midway, no partial tasks_raw.json is left."""
    # Pre-populate so we can verify atomicity:
    save_tasks_raw(str(tmp_path), _sample_tasks(), _sample_meta())
    original = (tmp_path / RAW_FILENAME).read_text(encoding="utf-8")

    # Now poison json.dumps and try a "second save" — original file must be intact.
    import tasks.persistence as P
    original_dumps = P.json.dumps

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-encode")

    monkeypatch.setattr(P.json, "dumps", boom)
    with pytest.raises(RuntimeError):
        save_tasks_raw(str(tmp_path), [Task(title="C")], _sample_meta())

    monkeypatch.setattr(P.json, "dumps", original_dumps)
    # Original file untouched:
    assert (tmp_path / RAW_FILENAME).read_text(encoding="utf-8") == original


# ── load_tasks_raw ─────────────────────────────────────────────────────


def test_load_round_trips_save(tmp_path: Path):
    tasks_in = _sample_tasks()
    save_tasks_raw(str(tmp_path), tasks_in, _sample_meta())
    loaded = load_tasks_raw(str(tmp_path))
    assert loaded["model"] == "anthropic/claude-sonnet-4.5"
    out = loaded["tasks"]
    assert len(out) == 2
    assert out[0].title == "A"
    assert out[0].priority is Priority.HIGH
    assert out[0].assignee_name == "Айдар"
    assert out[1].label_names == ["bug"]


def test_load_raises_persistence_error_on_missing_file(tmp_path: Path):
    with pytest.raises(PersistenceError, match="not found"):
        load_tasks_raw(str(tmp_path))


def test_load_raises_persistence_error_on_malformed_json(tmp_path: Path):
    (tmp_path / RAW_FILENAME).write_text("not json at all", encoding="utf-8")
    with pytest.raises(PersistenceError, match="malformed"):
        load_tasks_raw(str(tmp_path))


# ── save_tasks / load_tasks ──────────────────────────────────────────


def _full_state_tasks() -> list[Task]:
    return [
        Task(
            title="A", priority=Priority.HIGH, assignee_id="u1",
            assignee_name="Айдар", label_ids=["l1"], label_names=["bug"],
            selected=True, status=TaskStatus.SENT,
            linear_issue_id="ENG-101", linear_issue_url="https://linear.app/x/ENG-101",
        ),
        Task(
            title="B", description="multi\nline",
            selected=False, status=TaskStatus.SKIPPED,
        ),
    ]


def test_save_tasks_writes_full_state(tmp_path: Path):
    """tasks.json includes user-state fields (selected, status, linear_*) — unlike tasks_raw.json."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    data = json.loads((tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8"))
    sample = data["tasks"][0]
    # Full state present:
    assert sample["selected"] is True
    assert sample["status"] == "sent"
    assert sample["linear_issue_id"] == "ENG-101"
    assert sample["linear_issue_url"] == "https://linear.app/x/ENG-101"
    # Same meta keys as raw:
    assert data["model"] == "anthropic/claude-sonnet-4.5"
    assert data["team_id"] == "team-uuid"


def test_save_tasks_includes_edited_at_timestamp(tmp_path: Path):
    """tasks.json adds an `edited_at` field separate from `extracted_at`."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    data = json.loads((tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8"))
    assert "edited_at" in data
    assert isinstance(data["edited_at"], str)
    # Should be ISO-8601-ish:
    assert "T" in data["edited_at"]


def test_save_tasks_is_atomic(tmp_path: Path, monkeypatch):
    """Same atomic-write invariant as save_tasks_raw."""
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    original = (tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8")

    import tasks.persistence as P

    def boom(*args, **kwargs):
        raise RuntimeError("simulated mid-encode failure")

    monkeypatch.setattr(P.json, "dumps", boom)
    with pytest.raises(RuntimeError):
        save_tasks(str(tmp_path), [Task(title="X")], _sample_meta())

    # Original tasks.json untouched:
    assert (tmp_path / MUTABLE_FILENAME).read_text(encoding="utf-8") == original


def test_load_tasks_round_trips_full_state(tmp_path: Path):
    save_tasks(str(tmp_path), _full_state_tasks(), _sample_meta())
    loaded = load_tasks(str(tmp_path))
    assert loaded["model"] == "anthropic/claude-sonnet-4.5"
    out = loaded["tasks"]
    assert len(out) == 2
    assert out[0].selected is True
    assert out[0].status is TaskStatus.SENT
    assert out[0].linear_issue_id == "ENG-101"
    assert out[1].selected is False
    assert out[1].status is TaskStatus.SKIPPED


def test_load_tasks_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(PersistenceError, match="not found"):
        load_tasks(str(tmp_path))


def test_load_tasks_raises_on_malformed_json(tmp_path: Path):
    (tmp_path / MUTABLE_FILENAME).write_text("not json", encoding="utf-8")
    with pytest.raises(PersistenceError, match="malformed"):
        load_tasks(str(tmp_path))
