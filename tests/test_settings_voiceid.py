from pathlib import Path


def test_settings_builder_has_voiceid_section():
    src = Path("ui/dialogs/settings_builder.py").read_text(encoding="utf-8")
    assert "def build_voiceid_section(" in src
    assert "_voiceid_enabled_var" in src
    assert 'config["voiceid_enabled"]' in src
    assert "Speechmatics" in src  # the note telling users it needs Speechmatics


def test_settings_calls_build_voiceid_section():
    src = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
    assert "build_voiceid_section(self, scroll_transcription)" in src
