"""The cloud-STT key row must wire the «Проверить» button.

Source-text checks window-sliced to _build_cloud_section (Linux CI can't
import ui/ — sounddevice loads PortAudio at import time). Before this
feature the row was built with on_validate=None («deferred to a follow-up
PR per the spec») — a wrong key stayed silent until the first transcription.
"""
from pathlib import Path

_SETTINGS = Path("ui/dialogs/settings.py")


def _cloud_section_body() -> str:
    src = _SETTINGS.read_text(encoding="utf-8")
    start = src.index("def _build_cloud_section(")
    end = src.index("def _refresh_meetings_stats(", start)
    return src[start:end]


def test_cloud_key_row_wires_validate():
    body = _cloud_section_body()
    assert "on_validate=_on_validate" in body, (
        "the cloud API-key row must wire the Проверить button"
    )
    assert "validate_key()" in body, (
        "validation must call the provider's validate_key()"
    )
    assert "on_validate=None" not in body, (
        "the deferred on_validate=None placeholder must be gone"
    )


def test_cloud_validate_dispatches_on_selected_provider():
    body = _cloud_section_body()
    assert "get_provider(" in body and "_cloud_provider_var.get()" in body, (
        "validation must target the currently selected provider"
    )


def test_cloud_validate_persists_per_provider_key():
    body = _cloud_section_body()
    assert "on_key_persisted=_persist" in body
    assert "_cloud_api_keys[" in body, (
        "a validated key must be persisted into the per-provider dict"
    )
