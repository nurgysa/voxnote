import json

from processing.model import QueueItem, StageStatus
from processing.store import (
    build_view,
    is_meeting_folder,
    load_active,
    save_active,
    stage_status_from_folder,
)


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "queue.json"
    items = [
        QueueItem(id="a", audio_path="/x.wav", title="x", created_at="t",
                  auto=True, transcript=StageStatus.DONE),
    ]
    save_active(items, path=p)
    loaded = load_active(path=p)
    assert loaded == items


def test_load_missing_file_returns_empty(tmp_path):
    assert load_active(path=tmp_path / "nope.json") == []


def test_load_malformed_returns_empty(tmp_path):
    p = tmp_path / "queue.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_active(path=p) == []


def test_save_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "queue.json"
    save_active([], path=p)
    assert p.is_file()
    assert not (tmp_path / ".queue.json.tmp").exists()


def _touch(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("x", encoding="utf-8")


def test_stage_status_all_pending_empty_folder(tmp_path):
    s = stage_status_from_folder(str(tmp_path))
    assert s == {
        "transcript": StageStatus.PENDING,
        "protocol": StageStatus.PENDING,
        "tasks": StageStatus.PENDING,
    }


def test_stage_status_full_meeting(tmp_path):
    for name in ("transcript.md", "protocol.md", "tasks.json"):
        _touch(tmp_path, name)
    s = stage_status_from_folder(str(tmp_path))
    assert s["transcript"] is StageStatus.DONE
    assert s["protocol"] is StageStatus.DONE
    assert s["tasks"] is StageStatus.DONE


def test_stage_status_draft_only_is_awaiting_review(tmp_path):
    _touch(tmp_path, "transcript.md")
    _touch(tmp_path, "tasks_raw.json")
    s = stage_status_from_folder(str(tmp_path))
    assert s["tasks"] is StageStatus.AWAITING_REVIEW


def test_is_meeting_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    meeting = tmp_path / "m"
    _touch(meeting, "transcript.md")
    assert is_meeting_folder(str(meeting)) is True
    assert is_meeting_folder(str(empty)) is False


def _meeting(folder, *, transcript=True, project_id=None):
    folder.mkdir(parents=True, exist_ok=True)
    if transcript:
        (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def test_build_view_finds_root_and_project_meetings(tmp_path):
    _meeting(tmp_path / "2026-06-01_root_meeting")
    _meeting(tmp_path / "Kitng" / "2026-06-02_kitng", project_id="p1")
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")

    rows = build_view(str(tmp_path), active=[])
    titles = {r.title for r in rows}
    assert titles == {"2026-06-01_root_meeting", "2026-06-02_kitng"}
    by_title = {r.title: r for r in rows}
    assert by_title["2026-06-01_root_meeting"].project_id is None
    assert by_title["2026-06-02_kitng"].project_id == "p1"
    assert all(r.auto is False for r in rows)


def test_build_view_skips_recordings_dir(tmp_path):
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")
    assert build_view(str(tmp_path), active=[]) == []


def test_build_view_active_item_overrides_disk_row(tmp_path):
    folder = tmp_path / "2026-06-02_live"
    _meeting(folder)
    active = [QueueItem(id="live", audio_path="/a.wav", title="2026-06-02_live",
                        created_at="t", meeting_folder=str(folder), auto=True,
                        protocol=StageStatus.RUNNING)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].auto is True
    assert rows[0].protocol is StageStatus.RUNNING


def test_build_view_active_without_folder_is_appended(tmp_path):
    active = [QueueItem(id="new", audio_path="/a.wav", title="pending one",
                        created_at="t", auto=True)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].id == "new"
