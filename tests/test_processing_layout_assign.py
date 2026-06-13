import json
import os

from directory.schema import Project
from processing import layout


def _meeting(tmp_path, name="2026-06-13_10-00-00_call", project_id=None,
             participants=("p1",), speakers=None):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    payload = {
        "project_id": project_id,
        "participants": list(participants),
        "speakers": speakers or {"SPEAKER_00": "p1"},
    }
    (folder / "speakers.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return str(folder)


def test_assign_project_writes_id_and_moves(tmp_path):
    meetings = tmp_path
    folder = _meeting(meetings)
    project = Project(name="Kitng", id="proj-123")

    new_path = layout.assign_project(folder, project, str(meetings))

    assert os.path.basename(os.path.dirname(new_path)) == "Kitng"
    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] == "proj-123"
    assert sp["participants"] == ["p1"]
    assert sp["speakers"] == {"SPEAKER_00": "p1"}
    assert not os.path.exists(folder)


def test_assign_project_none_keeps_root_and_clears_id(tmp_path):
    meetings = tmp_path
    folder = _meeting(meetings, project_id="old-proj")

    new_path = layout.assign_project(folder, None, str(meetings))

    assert os.path.normpath(os.path.dirname(new_path)) == os.path.normpath(str(meetings))
    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] is None
    assert sp["participants"] == ["p1"]


def test_assign_project_no_speakers_file_creates_one(tmp_path):
    meetings = tmp_path
    folder = tmp_path / "2026-06-13_11-00-00_x"
    folder.mkdir()
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    project = Project(name="Beta", id="b-1")

    new_path = layout.assign_project(str(folder), project, str(meetings))

    with open(os.path.join(new_path, "speakers.json"), encoding="utf-8") as f:
        sp = json.load(f)
    assert sp["project_id"] == "b-1"
    assert sp["participants"] == []
    assert sp["speakers"] == {}
