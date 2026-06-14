# tests/test_processing_vault_note.py
import os

from directory.schema import Project
from processing import vault_note


def test_render_has_frontmatter_and_diarized_body():
    md = vault_note.render_transcript_note(
        segments=[{"start": 0, "end": 1, "text": "привет", "speaker": "SPEAKER_00"}],
        title="call", project_name="Kitng", date="2026-06-14", time="10:00",
        participants=[], provider="AssemblyAI", language="ru",
        voxnote_id="vid1", source_path="G:/My Drive/sources/call.m4a", nudged=True,
    )
    assert md.startswith("---\n")
    assert "type: meeting" in md
    assert "project: Kitng" in md
    assert 'source_path: "G:/My Drive/sources/call.m4a"' in md
    assert "nudged: true" in md
    assert "**Спикер 1:** привет" in md


def test_render_no_source_path_and_no_project():
    md = vault_note.render_transcript_note(
        segments=[], title="x", project_name=None, date="2026-06-14", time="09:00",
        participants=[], provider="Deepgram", language=None,
        voxnote_id="v", source_path=None, nudged=False,
    )
    assert 'source_path: ""' in md
    assert "project: \n" in md
    assert "nudged: false" in md


def test_write_creates_folder_and_transcript(tmp_path):
    p = vault_note.write_transcript_note(
        str(tmp_path), Project(name="Kitng", id="p1"),
        "2026-06-14_1000_call", "---\ntype: meeting\n---\nbody\n",
    )
    assert p == os.path.join(
        str(tmp_path), "Kitng", "2026-06-14_1000_call", "transcript.md"
    )
    assert os.path.isfile(p)


def test_write_no_project_uses_root(tmp_path):
    p = vault_note.write_transcript_note(str(tmp_path), None, "m", "x")
    assert p == os.path.join(str(tmp_path), "m", "transcript.md")


def test_write_collision_safe(tmp_path):
    vault_note.write_transcript_note(str(tmp_path), None, "m", "first")
    p2 = vault_note.write_transcript_note(str(tmp_path), None, "m", "second")
    assert p2 == os.path.join(str(tmp_path), "m-2", "transcript.md")
    assert open(p2, encoding="utf-8").read() == "second"
