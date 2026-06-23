from utils import load_voiceid_sidecar, save_voiceid_sidecar


def test_save_then_load_roundtrip(tmp_path):
    payload = {"model": "m-x", "pending": [{"label": "SPEAKER_1"}], "note_meta": {}}
    path = save_voiceid_sidecar("vid-1", payload, base_dir=str(tmp_path))
    assert path.endswith("vid-1.voiceid.json")
    assert load_voiceid_sidecar("vid-1", base_dir=str(tmp_path)) == payload


def test_load_absent_returns_none(tmp_path):
    assert load_voiceid_sidecar("nope", base_dir=str(tmp_path)) is None


def test_load_malformed_returns_none(tmp_path):
    import os
    os.makedirs(tmp_path, exist_ok=True)
    (tmp_path / "bad.voiceid.json").write_text("{ not json", encoding="utf-8")
    assert load_voiceid_sidecar("bad", base_dir=str(tmp_path)) is None


def test_save_unicode_is_utf8(tmp_path):
    save_voiceid_sidecar("vid-2", {"pending": [{"sample_text": "Привет"}]},
                         base_dir=str(tmp_path))
    raw = (tmp_path / "vid-2.voiceid.json").read_text(encoding="utf-8")
    assert "Привет" in raw  # ensure_ascii=False
