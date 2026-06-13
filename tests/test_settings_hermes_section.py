"""Hermes webhook section in Settings (spec 2026-06-13).

Source-text checks — no ui imports on Linux CI (sounddevice/PortAudio).
The webhook client itself is covered by tests/test_hermes_webhook_client.py;
these pin the Settings GUI surface (enable + URL + masked secret + test).
"""
from pathlib import Path

BUILDER = Path("ui/dialogs/settings_builder.py").read_text(encoding="utf-8")
SETTINGS = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")


def _section_block() -> str:
    start = BUILDER.index("def build_hermes_section")
    nxt = BUILDER.find("\ndef ", start + 1)
    return BUILDER[start:nxt if nxt != -1 else len(BUILDER)]


def test_builder_has_hermes_section():
    assert "def build_hermes_section" in BUILDER


def test_hermes_section_binds_three_config_keys_and_saves():
    block = _section_block()
    assert "CTkCheckBox" in block
    assert '"hermes_webhook_enabled"' in block
    assert '"hermes_webhook_url"' in block
    assert '"hermes_webhook_secret"' in block
    assert "save_config" in block


def test_hermes_enabled_default_is_false_opt_in():
    # Webhook is opt-in: a missing key means OFF (unlike dedup, which is ON).
    assert 'get("hermes_webhook_enabled", False)' in _section_block()


def test_hermes_secret_uses_api_key_row():
    # Secret is masked + validated through the shared api_key_row helper.
    assert "api_key_row" in _section_block()


def test_hermes_test_button_calls_webhook_client():
    # «Проверить» delivers a real (marked) event via the shipped client.
    block = _section_block()
    assert "on_validate" in block
    assert "emit_audio_transcribed_event" in block


def test_settings_wires_hermes_section_on_integrations_tab():
    assert "build_hermes_section" in SETTINGS
