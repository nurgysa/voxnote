# tests/test_segments_sidecar.py
import os

import utils


def test_sidecar_round_trip(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "привет", "speaker": "SPEAKER_00"}]
    path = utils.save_segments_sidecar("abc123", segs, base_dir=str(tmp_path))
    assert path == os.path.join(str(tmp_path), "abc123.json")
    assert os.path.isfile(path)
    assert utils.load_segments_sidecar("abc123", base_dir=str(tmp_path)) == segs


def test_load_missing_returns_none(tmp_path):
    assert utils.load_segments_sidecar("nope", base_dir=str(tmp_path)) is None


def test_default_dir_is_voxnote_segments(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    utils.save_segments_sidecar("v1", [{"start": 0, "end": 1, "text": "x"}])
    assert os.path.isfile(tmp_path / ".voxnote" / "segments" / "v1.json")
