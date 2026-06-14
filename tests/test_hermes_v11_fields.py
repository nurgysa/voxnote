# tests/test_hermes_v11_fields.py
from integrations.hermes.schema import build_audio_transcribed_event


def test_version_is_1_1():
    assert build_audio_transcribed_event(transcript_text="x")["version"] == "1.1"


def test_note_path_in_audio_block():
    p = build_audio_transcribed_event(
        transcript_text="x", note_path="C:/Vault/30 Meetings/Kitng/m/transcript.md",
    )
    assert p["audio"]["note_path"] == "C:/Vault/30 Meetings/Kitng/m/transcript.md"


def test_source_path_in_audio_block():
    p = build_audio_transcribed_event(
        transcript_text="x", source_path="G:/My Drive/sources/m.m4a",
    )
    assert p["audio"]["source_path"] == "G:/My Drive/sources/m.m4a"


def test_project_top_level():
    p = build_audio_transcribed_event(
        transcript_text="x", project={"id": "p1", "name": "Kitng"},
    )
    assert p["project"] == {"id": "p1", "name": "Kitng"}


def test_new_fields_default_none():
    p = build_audio_transcribed_event(transcript_text="x")
    assert p["audio"]["note_path"] is None
    assert p["audio"]["source_path"] is None
    assert p["project"] is None
