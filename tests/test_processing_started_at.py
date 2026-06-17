"""started_at: carried by the model + stamped by the worker on RUNNING."""
from __future__ import annotations

from datetime import datetime

from processing.model import QueueItem, StageStatus
from processing.worker import ProcessingQueue


def _queue(tmp_path, **over):
    kwargs = dict(
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {},
        resolve_project=lambda pid: None,
        queue_path=str(tmp_path / "queue.json"),
        on_change=None,
    )
    kwargs.update(over)
    return ProcessingQueue(**kwargs)


def test_started_at_roundtrips():
    item = QueueItem(
        id="x", audio_path="", title="t", created_at="",
        started_at="2026-06-16T20:00:00",
    )
    assert item.to_dict()["started_at"] == "2026-06-16T20:00:00"
    assert QueueItem.from_dict(item.to_dict()).started_at == "2026-06-16T20:00:00"


def test_started_at_defaults_none():
    assert QueueItem.from_dict({"id": "x"}).started_at is None


def test_set_status_running_stamps_started_at(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    item = q._items[0]
    assert item.started_at is None
    q._set_status(item, StageStatus.RUNNING)
    assert item.status == StageStatus.RUNNING
    # a full ISO timestamp (parses) — not merely non-empty, so a garbage
    # value can't pass (format_elapsed downstream does fromisoformat too).
    datetime.fromisoformat(item.started_at)


def test_set_status_non_running_does_not_stamp(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    item = q._items[0]
    q._set_status(item, StageStatus.DONE)
    assert item.started_at is None
