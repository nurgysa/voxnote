"""Unit tests for the headless «Встречи» presentation helpers."""
from __future__ import annotations

from processing.model import QueueItem, StageStatus
from ui.dialogs.meetings_view import (
    NO_PROJECT_LABEL,
    format_elapsed,
    format_status,
    group_by_project,
    queue_position,
)


def _item(id="i", status=StageStatus.PENDING, auto=True, project_id=None, started_at=None):
    return QueueItem(
        id=id, audio_path="", title=id, created_at="",
        status=status, auto=auto, project_id=project_id, started_at=started_at,
    )


def test_format_elapsed_minutes():
    assert format_elapsed("2026-06-16T20:00:00", "2026-06-16T20:01:05") == "01:05"


def test_format_elapsed_hours():
    assert format_elapsed("2026-06-16T20:00:00", "2026-06-16T21:02:03") == "1:02:03"


def test_format_elapsed_one_hour_boundary():
    assert format_elapsed("2026-06-16T20:00:00", "2026-06-16T21:00:00") == "1:00:00"


def test_format_elapsed_unparseable_or_missing():
    assert format_elapsed(None, "2026-06-16T20:00:00") == ""
    assert format_elapsed("bad", "also-bad") == ""


def test_format_elapsed_negative_clamps():
    assert format_elapsed("2026-06-16T20:00:10", "2026-06-16T20:00:00") == "00:00"


def test_queue_position_counts_active_pending_only():
    a = _item("a", StageStatus.DONE, auto=False)
    b = _item("b", StageStatus.PENDING)
    c = _item("c", StageStatus.RUNNING)
    d = _item("d", StageStatus.PENDING)
    rows = [a, b, c, d]
    assert queue_position(rows, b) == 1
    assert queue_position(rows, d) == 2
    assert queue_position(rows, c) is None
    assert queue_position(rows, a) is None


def test_format_status_running_with_elapsed():
    it = _item(status=StageStatus.RUNNING, started_at="2026-06-16T20:00:00")
    assert format_status(it, "2026-06-16T20:02:30", None) == ("идёт 02:30", "running")


def test_format_status_running_without_started_at():
    it = _item(status=StageStatus.RUNNING)
    assert format_status(it, "2026-06-16T20:00:00", None) == ("идёт…", "running")


def test_format_status_pending_positions():
    it = _item(status=StageStatus.PENDING)
    assert format_status(it, "x", None) == ("в очереди", "pending")  # not in queue / manual
    assert format_status(it, "x", 1) == ("в очереди", "pending")
    assert format_status(it, "x", 3) == ("в очереди (3-й)", "pending")


def test_format_status_done_and_error():
    assert format_status(_item(status=StageStatus.DONE), "x", None) == ("готово", "done")
    assert format_status(_item(status=StageStatus.ERROR), "x", None) == ("ошибка", "error")


def test_group_by_project_orders_no_project_last():
    def name_of(pid):
        return {"p1": "Alpha", "p2": "Beta"}.get(pid, NO_PROJECT_LABEL)
    rows = [
        _item("a", project_id="p1"),
        _item("b", project_id=None),
        _item("c", project_id="p2"),
        _item("d", project_id="p1"),
    ]
    groups = group_by_project(rows, name_of)
    assert [g[0] for g in groups] == ["Alpha", "Beta", NO_PROJECT_LABEL]
    assert [r.id for r in groups[0][1]] == ["a", "d"]
    assert [r.id for r in groups[2][1]] == ["b"]


def test_group_by_project_all_no_project():
    rows = [_item("a"), _item("b")]
    groups = group_by_project(rows, lambda pid: NO_PROJECT_LABEL)
    assert [g[0] for g in groups] == [NO_PROJECT_LABEL]
    assert [r.id for r in groups[0][1]] == ["a", "b"]


def test_group_by_project_none_no_project():
    def name_of(pid):
        return {"p1": "Alpha"}.get(pid, NO_PROJECT_LABEL)
    groups = group_by_project([_item("a", project_id="p1")], name_of)
    assert [g[0] for g in groups] == ["Alpha"]  # no «Без проекта» group when none apply
