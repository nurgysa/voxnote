"""Tests for the task-dedup engine (PR-2). Pure logic — no FS/network."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tasks.dedup import (
    FUZZY_HIGH,
    FUZZY_LOW,
    SentTask,
    normalize_title,
)


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
