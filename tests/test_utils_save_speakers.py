import json

from utils import load_speakers, save_speakers


def test_save_speakers_writes_forward_compatible_shape(tmp_path):
    save_speakers(str(tmp_path), "proj1", ["a", "b"])
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data == {"project_id": "proj1", "participants": ["a", "b"], "speakers": {}}


def test_save_speakers_null_project(tmp_path):
    save_speakers(str(tmp_path), None, ["a"])
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data["project_id"] is None


def test_load_speakers_roundtrip(tmp_path):
    save_speakers(str(tmp_path), "p", ["x"])
    assert load_speakers(str(tmp_path)) == {
        "project_id": "p", "participants": ["x"], "speakers": {},
    }


def test_load_speakers_missing_is_empty_dict(tmp_path):
    assert load_speakers(str(tmp_path)) == {}


def test_load_speakers_malformed_is_empty_dict(tmp_path):
    (tmp_path / "speakers.json").write_text("{not json", encoding="utf-8")
    assert load_speakers(str(tmp_path)) == {}
