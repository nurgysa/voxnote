"""Pure helpers for PR-4 retroactive re-render + preview playback window.

Tk-free / network-free — exercises processing.voiceid + vault_note + utils
directly so the bind panel's logic is proven without importing any UI.
"""
from __future__ import annotations

import os

from processing import vault_note
from processing.voiceid import (
    participants_that_spoke,
    playback_window,
    rename_segment_speakers,
    rerender_named_note,
)
from utils import delete_voiceid_sidecar, load_voiceid_sidecar, save_voiceid_sidecar

_SEGMENTS = [
    {"start": 0.0, "end": 1.0, "text": "привет", "speaker": "SPEAKER_1"},
    {"start": 1.0, "end": 2.0, "text": "здравствуйте", "speaker": "SPEAKER_2"},
    {"start": 2.0, "end": 3.0, "text": "как дела", "speaker": "SPEAKER_1"},
]

_NOTE_META = {
    "title": "Планёрка",
    "project_name": "Проект Альфа",
    "date": "2026-06-29",
    "time": "10:15",
    "provider": "Speechmatics",
    "language": "ru",
    "voxnote_id": "vid-1",
    "source_path": "C:/audio/planerka.m4a",
    "nudged": False,
}


def test_participants_that_spoke_excludes_anonymous_and_sorts():
    segs = [
        {"speaker": "SPEAKER_2", "text": "a"},
        {"speaker": "Борис Ким", "text": "b"},
        {"speaker": "Айбек Нурланов", "text": "c"},
        {"speaker": "Айбек Нурланов", "text": "d"},  # dup ignored
        {"speaker": "", "text": "e"},                # blank ignored
    ]
    assert participants_that_spoke(segs) == ["Айбек Нурланов", "Борис Ким"]


def test_rename_segment_speakers_is_nondestructive():
    out = rename_segment_speakers(_SEGMENTS, {"SPEAKER_1": "Айбек Нурланов"})
    assert out[0]["speaker"] == "Айбек Нурланов"
    assert out[1]["speaker"] == "SPEAKER_2"      # untouched
    assert _SEGMENTS[0]["speaker"] == "SPEAKER_1"  # original not mutated


def test_rerender_named_note_partial_naming():
    content = rerender_named_note(
        _SEGMENTS, {"SPEAKER_1": "Айбек Нурланов"}, _NOTE_META
    )
    # frontmatter participants = only the named person who spoke
    assert 'participants: ["Айбек Нурланов"]' in content
    # body: named speaker verbatim, remaining anonymous renumbered to «Спикер 1»
    assert "**Айбек Нурланов:** привет" in content
    assert "**Спикер 1:** здравствуйте" in content
    assert "**Айбек Нурланов:** как дела" in content
    # «Связи» links project + the named participant
    assert "## Связи" in content
    assert "[[Айбек Нурланов]]" in content
    assert "[[Проект Альфа]]" in content


def test_rerender_named_note_all_named():
    content = rerender_named_note(
        _SEGMENTS,
        {"SPEAKER_1": "Айбек Нурланов", "SPEAKER_2": "Борис Ким"},
        _NOTE_META,
    )
    assert 'participants: ["Айбек Нурланов", "Борис Ким"]' in content
    assert "SPEAKER_" not in content
    assert "Спикер" not in content  # no anonymous left


def test_playback_window_clamps():
    sr = 16000
    # window inside the audio
    assert playback_window(160000, sr, 1.0, window_s=2.0) == (16000, 48000)
    # start past the end → empty slice
    assert playback_window(16000, sr, 100.0, window_s=2.0) == (16000, 16000)
    # window tail clamps to n_samples
    assert playback_window(20000, sr, 1.0, window_s=10.0) == (16000, 20000)
    # degenerate inputs
    assert playback_window(0, sr, 1.0) == (0, 0)
    assert playback_window(16000, 0, 1.0) == (0, 0)


def test_overwrite_transcript_note_replaces_in_place(tmp_path):
    folder = tmp_path / "meeting"
    folder.mkdir()
    note = folder / "transcript.md"
    note.write_text("OLD", encoding="utf-8")
    path = vault_note.overwrite_transcript_note(str(folder), "НОВЫЙ текст")
    assert os.path.normpath(path) == os.path.normpath(str(note))
    assert note.read_text(encoding="utf-8") == "НОВЫЙ текст"
    # no stray temp file left behind
    assert not (folder / "transcript.md.tmp").exists()


def test_delete_voiceid_sidecar(tmp_path):
    save_voiceid_sidecar("vid-x", {"pending": [{"label": "SPEAKER_1"}]}, base_dir=str(tmp_path))
    assert load_voiceid_sidecar("vid-x", base_dir=str(tmp_path)) is not None
    delete_voiceid_sidecar("vid-x", base_dir=str(tmp_path))
    assert load_voiceid_sidecar("vid-x", base_dir=str(tmp_path)) is None
    # idempotent — deleting an absent sidecar is a no-op, not an error
    delete_voiceid_sidecar("vid-x", base_dir=str(tmp_path))
