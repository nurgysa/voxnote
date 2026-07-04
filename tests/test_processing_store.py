import json

from processing.model import QueueItem, StageStatus
from processing.store import (
    build_view,
    hermes_badges_from_folder,
    is_meeting_folder,
    load_active,
    meeting_status_from_folder,
    pending_voices_count_from_folder,
    read_voxnote_id,
    save_active,
)
from utils import save_voiceid_sidecar


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "queue.json"
    items = [
        QueueItem(id="a", audio_path="/x.wav", title="x", created_at="t",
                  auto=True, source="record", status=StageStatus.DONE),
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


def test_meeting_status_pending_empty_folder(tmp_path):
    assert meeting_status_from_folder(str(tmp_path)) is StageStatus.PENDING


def test_meeting_status_done_with_transcript(tmp_path):
    _touch(tmp_path, "transcript.md")
    assert meeting_status_from_folder(str(tmp_path)) is StageStatus.DONE


def test_hermes_badges_reflect_files(tmp_path):
    _touch(tmp_path, "transcript.md")
    assert hermes_badges_from_folder(str(tmp_path)) == {
        "has_protocol": False, "has_tasks": False,
    }
    _touch(tmp_path, "protocol.md")
    _touch(tmp_path, "tasks.md")
    assert hermes_badges_from_folder(str(tmp_path)) == {
        "has_protocol": True, "has_tasks": True,
    }


def test_read_voxnote_id_from_transcript_frontmatter(tmp_path):
    (tmp_path / "transcript.md").write_text(
        "---\n"
        "type: meeting\n"
        "voxnote_id: smoke-123\n"
        "---\n\n"
        "body",
        encoding="utf-8",
    )
    assert read_voxnote_id(str(tmp_path)) == "smoke-123"


def test_read_voxnote_id_rejects_path_traversal(tmp_path):
    (tmp_path / "transcript.md").write_text(
        "---\nvoxnote_id: ../../outside\n---\n\nbody",
        encoding="utf-8",
    )
    assert read_voxnote_id(str(tmp_path)) is None


def test_pending_voices_count_from_voiceid_sidecar(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    folder = tmp_path / "meeting"
    folder.mkdir()
    (folder / "transcript.md").write_text(
        "---\nvoxnote_id: vid-pr5\n---\n\nbody", encoding="utf-8"
    )
    save_voiceid_sidecar(
        "vid-pr5",
        {"pending": [{"label": "SPEAKER_1"}, {"label": "SPEAKER_2"}]},
    )
    assert pending_voices_count_from_folder(str(folder)) == 2


def test_is_meeting_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    meeting = tmp_path / "m"
    _touch(meeting, "transcript.md")
    assert is_meeting_folder(str(meeting)) is True
    assert is_meeting_folder(str(empty)) is False


def _meeting(folder, *, transcript=True, project_id=None, protocol=False, tasks=False):
    folder.mkdir(parents=True, exist_ok=True)
    if transcript:
        (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if protocol:
        (folder / "protocol.md").write_text("p", encoding="utf-8")
    if tasks:
        (folder / "tasks.md").write_text("t", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def test_build_view_finds_root_and_project_meetings(tmp_path):
    _meeting(tmp_path / "2026-06-01_root_meeting")
    _meeting(tmp_path / "Kitng" / "2026-06-02_kitng", project_id="p1", protocol=True)
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")

    rows = build_view(str(tmp_path), active=[])
    titles = {r.title for r in rows}
    assert titles == {"2026-06-01_root_meeting", "2026-06-02_kitng"}
    by_title = {r.title: r for r in rows}
    assert by_title["2026-06-01_root_meeting"].project_id is None
    assert by_title["2026-06-01_root_meeting"].status is StageStatus.DONE
    assert by_title["2026-06-02_kitng"].project_id == "p1"
    assert by_title["2026-06-02_kitng"].has_protocol is True
    assert by_title["2026-06-02_kitng"].has_tasks is False
    assert all(r.auto is False for r in rows)


def test_build_view_marks_pending_voice_badge_count(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    folder = tmp_path / "Kitng" / "2026-07-03_voiceid"
    _meeting(folder)
    (folder / "transcript.md").write_text(
        "---\nvoxnote_id: vid-pr5\n---\n\nhi", encoding="utf-8"
    )
    save_voiceid_sidecar("vid-pr5", {"pending": [{"label": "SPEAKER_1"}]})

    rows = build_view(str(tmp_path), active=[])
    assert len(rows) == 1
    assert rows[0].pending_voices_count == 1


def test_build_view_merges_pending_voice_badge_into_active_row(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    folder = tmp_path / "2026-07-03_voiceid"
    _meeting(folder)
    (folder / "transcript.md").write_text(
        "---\nvoxnote_id: vid-active\n---\n\nhi", encoding="utf-8"
    )
    save_voiceid_sidecar("vid-active", {"pending": [{"label": "SPEAKER_1"}]})
    active = [QueueItem(
        id="active",
        audio_path="",
        title="active",
        created_at="2026-07-03T10:00:00",
        meeting_folder=str(folder),
    )]

    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].pending_voices_count == 1


def test_build_view_skips_recordings_dir(tmp_path):
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "rec.wav").write_text("x", encoding="utf-8")
    assert build_view(str(tmp_path), active=[]) == []


def test_build_view_active_item_overrides_disk_row(tmp_path):
    folder = tmp_path / "2026-06-02_live"
    _meeting(folder)
    active = [QueueItem(id="live", audio_path="/a.wav", title="2026-06-02_live",
                        created_at="t", meeting_folder=str(folder), auto=True,
                        status=StageStatus.RUNNING)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].auto is True
    assert rows[0].status is StageStatus.RUNNING


def test_build_view_active_without_folder_is_appended(tmp_path):
    active = [QueueItem(id="new", audio_path="/a.wav", title="pending one",
                        created_at="t", auto=True)]
    rows = build_view(str(tmp_path), active=active)
    assert len(rows) == 1
    assert rows[0].id == "new"
