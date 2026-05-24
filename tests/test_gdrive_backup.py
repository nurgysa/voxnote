"""Tests for gdrive.backup — Phase 7.1 backup orchestrator.

Mostly pure stdlib testing (zipfile, tmp_path, dict redaction). The
run_backup orchestrator test mocks DriveClient — no real Drive API.
"""
from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from unittest.mock import MagicMock, patch

from gdrive.backup import REDACTED_KEYS, REDACTION_PLACEHOLDER, redact_config, zip_history

# build_manifest gets imported INSIDE its tests below — during the
# B.3 TDD slice it doesn't exist yet, so a top-level import would
# break test collection for the B.1 (redact) + B.2 (zip_history) tests.


def test_redact_config_replaces_listed_keys_with_placeholder():
    """All keys listed in REDACTED_KEYS must be replaced with
    REDACTION_PLACEHOLDER. Keys absent from the input config are
    silently skipped (not added as new keys)."""
    config = {
        "language": "Авто-определение",
        "openrouter_api_key": "sk-or-real-key-12345",
        "linear_api_key": "lin_api_real",
        "glide_api_key": "real-glide-key",
        "assemblyai_api_key": "asm-real",
        "hf_token": "hf_real_token",
        "cloud_api_keys": {"AssemblyAI": "real", "Deepgram": "real2"},
        "gdrive_account_email": "user@example.com",  # not redacted — it's user-visible
    }
    redacted = redact_config(config)

    # Listed keys replaced.
    assert redacted["openrouter_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["linear_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["glide_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["assemblyai_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["hf_token"] == REDACTION_PLACEHOLDER
    # cloud_api_keys (nested dict of provider→key) — values redacted, keys kept.
    assert redacted["cloud_api_keys"] == {
        "AssemblyAI": REDACTION_PLACEHOLDER,
        "Deepgram": REDACTION_PLACEHOLDER,
    }
    # Non-secret keys untouched.
    assert redacted["language"] == "Авто-определение"
    assert redacted["gdrive_account_email"] == "user@example.com"
    # Input not mutated (defensive — caller might still need it).
    assert config["openrouter_api_key"] == "sk-or-real-key-12345"


def test_redact_config_handles_missing_keys_silently():
    """A config that doesn't have any of the redacted keys returns
    intact (no KeyError, no spurious new keys)."""
    config = {"language": "Русский", "model": "large-v3"}
    redacted = redact_config(config)
    assert redacted == config
    assert redacted is not config, "redact_config must return a copy"


# Audio file extensions excluded from the history.zip per spec
# (text-only backup; audio is opt-in for Phase 7.4 which we haven't shipped).
_AUDIO_EXTS = (".wav", ".mp3", ".m4a")


def test_zip_history_includes_text_files(tmp_path):
    """Plain .txt and .json files in history/ must end up in the zip."""
    src = tmp_path / "history"
    src.mkdir()
    (src / "2026-05-23_meeting").mkdir()
    (src / "2026-05-23_meeting" / "transcript.txt").write_text("Привет мир")
    (src / "2026-05-23_meeting" / "diarized.json").write_text('{"speakers": []}')

    out_zip = tmp_path / "history.zip"
    zip_history(src, out_zip)

    with zipfile.ZipFile(out_zip) as zf:
        names = sorted(zf.namelist())
    assert "2026-05-23_meeting/transcript.txt" in names
    assert "2026-05-23_meeting/diarized.json" in names


def test_zip_history_excludes_audio_files(tmp_path):
    """*.wav, *.mp3, *.m4a are stripped (spec — text-only backup).
    Verified by creating fake binary files with audio extensions
    alongside transcripts."""
    src = tmp_path / "history"
    src.mkdir()
    folder = src / "2026-05-23_meeting"
    folder.mkdir()
    (folder / "transcript.txt").write_text("text content")
    (folder / "original.wav").write_bytes(b"fake-wav-binary")
    (folder / "original.mp3").write_bytes(b"fake-mp3-binary")
    (folder / "alt.m4a").write_bytes(b"fake-m4a-binary")

    out_zip = tmp_path / "history.zip"
    zip_history(src, out_zip)

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert "2026-05-23_meeting/transcript.txt" in names
    assert not any(name.endswith(_AUDIO_EXTS) for name in names), (
        f"Audio files leaked: {[n for n in names if n.endswith(_AUDIO_EXTS)]}"
    )


def test_zip_history_empty_directory_produces_empty_archive(tmp_path):
    """An empty history/ folder must produce a valid (but empty) zip,
    not crash. Edge case: first-run user clicks Сделать backup before
    transcribing anything."""
    src = tmp_path / "history"
    src.mkdir()

    out_zip = tmp_path / "history.zip"
    zip_history(src, out_zip)

    assert out_zip.exists()
    with zipfile.ZipFile(out_zip) as zf:
        assert zf.namelist() == []
