"""Source-slice tests for Voice-ID bind/enroll panel.

Never import the module here: ui.dialogs.voice_bind imports CustomTkinter and
audio preview helpers, which are unsafe in Linux CI. These checks pin the UI
wiring while pure behavior is covered by processing/unit tests.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VOICE_BIND = (_ROOT / "ui" / "dialogs" / "voice_bind.py").read_text(encoding="utf-8")


def test_voice_bind_dialog_uses_pr4_helpers_and_directory_store():
    for name in (
        "load_voiceid_sidecar",
        "load_segments_sidecar",
        "delete_voiceid_sidecar",
        "rerender_named_note",
        "overwrite_transcript_note",
        "Voiceprint",
        "add_voiceprint",
    ):
        assert name in _VOICE_BIND


def test_voice_bind_dialog_has_russian_bind_ui_and_playback():
    assert "class VoiceBindDialog" in _VOICE_BIND
    assert "Новые голоса" in _VOICE_BIND
    assert "— выберите —" in _VOICE_BIND
    assert "▶ Прослушать" in _VOICE_BIND
    assert "Применить" in _VOICE_BIND
    assert "Создать нового" in _VOICE_BIND
    assert "playback_window" in _VOICE_BIND
    assert "load_mono_float32" in _VOICE_BIND


def test_voice_bind_dialog_rerenders_and_refreshes_parent():
    assert "names_by_label" in _VOICE_BIND
    assert "source_meeting=self._voxnote_id" in _VOICE_BIND
    assert "delete_voiceid_sidecar(self._voxnote_id)" in _VOICE_BIND
    assert "self._on_applied()" in _VOICE_BIND


def test_voice_bind_dialog_validates_before_mutating_directory():
    assert "assignments = []" in _VOICE_BIND
    assert "new_people_by_name" in _VOICE_BIND
    assert "for row_idx, entry in enumerate(self._pending):" in _VOICE_BIND
    assert "content = rerender_named_note" in _VOICE_BIND
    assert _VOICE_BIND.index("content = rerender_named_note") < _VOICE_BIND.index(
        "vault_note.overwrite_transcript_note"
    )
    assert _VOICE_BIND.index("vault_note.overwrite_transcript_note") < _VOICE_BIND.index(
        "self._store.add_voiceprint"
    )


def test_voice_bind_dialog_stops_audio_on_close():
    assert "sd.stop()" in _VOICE_BIND
    assert "def _close" in _VOICE_BIND
