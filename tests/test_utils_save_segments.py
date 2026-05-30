import json

from utils import save_segments


def test_save_segments_writes_json(tmp_path):
    segs = [{"start": 0.0, "end": 1.5, "text": "hi", "speaker": "SPEAKER_00"}]
    save_segments(str(tmp_path), segs)
    data = json.loads((tmp_path / "segments.json").read_text(encoding="utf-8"))
    assert data == segs


def test_save_segments_none_is_noop(tmp_path):
    save_segments(str(tmp_path), None)
    assert not (tmp_path / "segments.json").exists()


def test_save_segments_empty_list_writes_empty(tmp_path):
    save_segments(str(tmp_path), [])
    data = json.loads((tmp_path / "segments.json").read_text(encoding="utf-8"))
    assert data == []
