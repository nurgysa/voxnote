import json

from utils import load_segments, save_segments


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


def test_load_segments_roundtrip(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "x", "speaker": "SPEAKER_00"}]
    save_segments(str(tmp_path), segs)
    assert load_segments(str(tmp_path)) == segs


def test_load_segments_missing_is_empty_list(tmp_path):
    assert load_segments(str(tmp_path)) == []


def test_load_segments_malformed_is_empty_list(tmp_path):
    (tmp_path / "segments.json").write_text("{not json", encoding="utf-8")
    assert load_segments(str(tmp_path)) == []


def test_load_segments_non_list_json_is_empty_list(tmp_path):
    # Valid JSON but not an array (e.g. a hand-edit or future format drift) —
    # callers iterate the result, so it must still degrade to [].
    (tmp_path / "segments.json").write_text('{"key": "val"}', encoding="utf-8")
    assert load_segments(str(tmp_path)) == []
