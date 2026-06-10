"""Source-text checks that local-only UI affordances are deleted.

Linux CI does not import ui/ (per memory feedback_ui_app_import_breaks_linux_ci
— sounddevice loads PortAudio at import time, missing on Ubuntu runners), so
we verify deletion by reading file content rather than instantiating widgets.
Tests are RED before Task 3 of the v5 plan runs the strips; GREEN after.
"""
from pathlib import Path


def test_voices_dialog_deleted():
    assert not Path("ui/dialogs/voices.py").exists(), (
        "ui/dialogs/voices.py must be deleted in the cloud-only build"
    )


def test_settings_has_no_voice_library_section():
    for rel in ["ui/dialogs/settings.py", "ui/dialogs/settings_builder.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        assert "Голоса" not in src, f"voice library button must be removed from {rel}"
        assert "_open_voices_dialog" not in src, (
            f"voices dialog launcher must be removed from {rel}"
        )


def test_settings_has_no_model_size_picker():
    import re

    for rel in ["ui/dialogs/settings.py", "ui/dialogs/settings_builder.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        # Whisper model size dropdown is local-only — verify the runtime bindings
        # are gone (the docstring may still mention 'whisper-model' historically).
        # _model_var is the binding the deleted dropdown used; MODELS is the
        # constants dict; _on_model_changed is the App's callback.
        assert not re.search(r"\b_model_var\b", src), (
            f"{rel} still binds to App._model_var"
        )
        assert not re.search(r"\bMODELS\b", src), (
            f"{rel} still imports MODELS constants"
        )
        assert "_on_model_changed" not in src, (
            f"{rel} still wires the Whisper model-changed callback"
        )


def test_settings_has_no_device_picker():
    for rel in ["ui/dialogs/settings.py", "ui/dialogs/settings_builder.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        # Both transcription + diarization device pickers are local-only
        assert "tr_device" not in src and "di_device" not in src, (
            f"device pickers must be removed from {rel}"
        )


def test_settings_has_no_hf_token_field():
    for rel in ["ui/dialogs/settings.py", "ui/dialogs/settings_builder.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        lower = src.lower()
        assert "hf_token" not in lower and "huggingface" not in lower, (
            f"HuggingFace token field must be removed from {rel} (pyannote is gone)"
        )


def test_builder_has_no_local_state_vars():
    import re

    src = Path("ui/app/builder.py").read_text(encoding="utf-8")
    # Word-boundary match so the regex doesn't false-positive on
    # `_openrouter_default_model_var` (which contains the substring `_model_var`
    # but is the OpenRouter model dropdown, kept).
    for marker in [r"\bapp\._model_var\b",
                   r"\bapp\._tr_device_var\b",
                   r"\bapp\._di_device_var\b"]:
        assert not re.search(marker, src), (
            f"builder.py still declares {marker!r}"
        )


def test_builder_diarize_default_is_true():
    import re

    src = Path("ui/app/builder.py").read_text(encoding="utf-8")
    # Diarization default ON for AssemblyAI MVP — clients shouldn't have to
    # toggle a checkbox to get speaker labels.
    m = re.search(r"_diar_var\s*=\s*ctk\.BooleanVar\([^)]*value\s*=\s*True", src)
    assert m, "_diar_var must default to True for AssemblyAI diarization to engage"


def test_audio_cutter_has_no_silence_remove_button():
    src = Path("audio_cutter.py").read_text(encoding="utf-8")
    # Both Russian + English markers — one of them must have been in the original
    for marker in ["remove_silences", "silence_remov", "silence_remover",
                   "Удаление тишины", "Убрать тишину"]:
        assert marker not in src, f"audio_cutter.py still references {marker!r}"


def test_transcription_mixin_has_no_local_plumbing():
    src = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")
    assert "load_model" not in src, "load_model() is gone — cloud-only Transcriber"
    assert "_transcriber.load_model" not in src
    assert "hf_token" not in src.lower(), "hf_token plumbing must be removed"
    assert "voice_lib_path" not in src, "voice_lib_path plumbing must be removed"
    assert "diarize_device" not in src, "diarize_device plumbing must be removed"
    assert "voices_from_config" not in src, "voice_library import must be removed"


def test_ui_has_no_noop_normalize_toggle():
    # The normalize checkbox was a no-op: since #103 the cloud path hardcodes
    # ensure_wav(normalize=False) by design (provider gateways apply their own
    # gain normalization), so the toggle controlled nothing while claiming to.
    # Verify the whole Var loop is gone: widget (settings.py), Var seed
    # (builder.py), persistence handler (settings_mixin.py), docstring contract
    # mentions (transcription_mixin.py).
    for rel in [
        "ui/dialogs/settings.py",
        "ui/dialogs/settings_builder.py",
        "ui/app/builder.py",
        "ui/app/settings_mixin.py",
        "ui/app/transcription_mixin.py",
    ]:
        src = Path(rel).read_text(encoding="utf-8")
        assert "_normalize_var" not in src, f"{rel} still references _normalize_var"
        assert "normalize_audio" not in src, f"{rel} still references normalize_audio"
        assert "_on_normalize_changed" not in src, (
            f"{rel} still wires the normalize handler"
        )
    settings_src = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
    assert "Нормализовать громкость" not in settings_src, (
        "Settings still shows the normalize checkbox label"
    )


def test_provider_registry_drops_local_dependent():
    import re

    src = Path("providers/__init__.py").read_text(encoding="utf-8")
    # Groq + OpenAI Whisper lacked diarization and depended on the
    # (now-deleted) hybrid path. Both provider files are gone. Check the
    # import section (top of file) rather than the whole text — the docstring/
    # comment may legitimately mention them as historical context.
    head = src.split("PROVIDERS")[0]   # everything BEFORE the dict literal
    assert not re.search(r"\bGroqProvider\b", head), (
        "Groq provider must not be imported"
    )
    assert not re.search(r"\bOpenAIWhisperProvider\b", head), (
        "OpenAI Whisper provider must not be imported"
    )

