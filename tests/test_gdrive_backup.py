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

from gdrive.backup import (
    REDACTED_KEYS,
    REDACTION_PLACEHOLDER,
    build_manifest,
    redact_config,
    zip_history,
)


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


def test_iso_timestamp_format_matches_spec(monkeypatch):
    """_iso_timestamp returns the folder-name-safe ISO 8601 used in
    the spec's example (`2026-04-30T12-30-00`). The two : separators
    between hours/minutes/seconds are replaced with `-` because Drive
    folder names tolerate them but Windows paths don't (matters for
    restore flow's local extraction in Phase 7.2)."""
    import datetime as dt

    from gdrive.backup import _iso_timestamp

    class _FakeDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 23, 22, 30, 45, tzinfo=tz)

    monkeypatch.setattr("gdrive.backup.datetime", _FakeDateTime)
    assert _iso_timestamp() == "2026-05-23T22-30-45"


def test_build_manifest_computes_sha256_and_size_for_each_file(tmp_path):
    """build_manifest computes SHA-256 + byte-size for each file in
    the files dict, plus carries through the structural fields
    (version, created_at, app_version, host, transcripts_count,
    audio_included)."""
    config_file = tmp_path / "config.json"
    config_file.write_bytes(b'{"language": "ru"}')   # 18 bytes
    history_zip = tmp_path / "history.zip"
    history_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # 104 bytes

    expected_config_sha = hashlib.sha256(b'{"language": "ru"}').hexdigest()
    expected_zip_sha = hashlib.sha256(b"PK\x03\x04" + b"\x00" * 100).hexdigest()

    manifest = build_manifest(
        files={"config.json": config_file, "history.zip": history_zip},
        transcripts_count=42,
        app_version="phase-7.1",
        host="TEST-HOST",
        created_at="2026-05-23T22-30-45",
    )

    assert manifest["version"] == 1
    assert manifest["created_at"] == "2026-05-23T22-30-45"
    assert manifest["app_version"] == "phase-7.1"
    assert manifest["host"] == "TEST-HOST"
    assert manifest["transcripts_count"] == 42
    assert manifest["audio_included"] is False
    assert manifest["files"] == {
        "config.json": {"size": 18, "sha256": expected_config_sha},
        "history.zip": {"size": 104, "sha256": expected_zip_sha},
    }


def test_build_manifest_serializable_to_json(tmp_path):
    """The returned dict must round-trip through json.dumps/loads with
    no special encoders. Smoke for "did I use a Path object where I
    should have str'd it" kinds of bugs."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    history_zip = tmp_path / "history.zip"
    history_zip.write_bytes(b"PK")

    manifest = build_manifest(
        files={"config.json": config_file, "history.zip": history_zip},
        transcripts_count=0,
        app_version="phase-7.1",
        host="HOST",
        created_at="2026-05-23T22-30-45",
    )

    serialised = json.dumps(manifest)
    assert json.loads(serialised) == manifest


def test_run_backup_calls_client_in_correct_order(tmp_path):
    """run_backup must call DriveClient in this order:
      1. find_or_create_folder("audio-transcriber-backup")  → root_id
      2. create_folder(<iso-timestamp>, parent_id=root_id)  → snap_id
      3. upload_file(manifest.json, ..., parent_id=snap_id) → manifest_id
      4. upload_file(config.json,   ..., parent_id=snap_id) → config_id
      5. upload_file(history.zip,   ..., parent_id=snap_id) → zip_id

    Returns a dict with root_id + snapshot_id + uploaded file ids."""
    from gdrive.backup import run_backup

    # Set up a real on-disk history dir + config so zip_history and
    # build_manifest do real work (lighter-weight than mocking them).
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "meeting").mkdir()
    (history_dir / "meeting" / "transcript.txt").write_text("hello")

    config = {"language": "ru", "openrouter_api_key": "secret"}

    # Mock the auth + client.
    fake_auth = MagicMock()
    fake_auth.ensure_valid_credentials.return_value = None
    fake_auth.get_credentials.return_value = MagicMock()

    fake_client = MagicMock()
    fake_client.find_or_create_folder.return_value = "root-folder-id"
    fake_client.create_folder.return_value = "snapshot-folder-id"
    fake_client.upload_file.side_effect = ["manifest-id", "config-id", "zip-id"]

    with patch("gdrive.backup.DriveClient", return_value=fake_client), \
         patch("gdrive.backup._iso_timestamp", return_value="2026-05-23T22-30-45"):
        result = run_backup(
            auth=fake_auth,
            config=config,
            history_dir=history_dir,
            work_dir=tmp_path / "work",
        )

    # ensure_valid_credentials was called before any Drive op.
    fake_auth.ensure_valid_credentials.assert_called_once()
    fake_auth.get_credentials.assert_called_once()

    # Folder creation in the right order.
    fake_client.find_or_create_folder.assert_called_once_with("audio-transcriber-backup")
    fake_client.create_folder.assert_called_once_with(
        "2026-05-23T22-30-45", parent_id="root-folder-id",
    )

    # 3 uploads to the snapshot folder.
    assert fake_client.upload_file.call_count == 3
    upload_names = [c.kwargs["drive_name"] for c in fake_client.upload_file.call_args_list]
    assert upload_names == ["manifest.json", "config.json", "history.zip"]
    for call in fake_client.upload_file.call_args_list:
        assert call.kwargs["parent_id"] == "snapshot-folder-id"

    # Result dict shape.
    assert result == {
        "root_folder_id": "root-folder-id",
        "snapshot_folder_id": "snapshot-folder-id",
        "snapshot_name": "2026-05-23T22-30-45",
        "uploaded": {
            "manifest.json": "manifest-id",
            "config.json": "config-id",
            "history.zip": "zip-id",
        },
    }


def test_run_backup_redacts_uploaded_config(tmp_path):
    """Variant of the redaction test that captures uploaded file
    CONTENT during the upload call (run_backup cleans up work_dir
    on success, so paths recorded by side_effect can't be read
    after-the-fact)."""
    from gdrive.backup import run_backup

    history_dir = tmp_path / "history"
    history_dir.mkdir()

    config = {
        "language": "ru",
        "openrouter_api_key": "real-secret",
        "cloud_api_keys": {"AssemblyAI": "real-asm-key"},
    }
    config_before = copy.deepcopy(config)

    fake_auth = MagicMock()
    fake_auth.get_credentials.return_value = MagicMock()
    fake_client = MagicMock()
    fake_client.find_or_create_folder.return_value = "root"
    fake_client.create_folder.return_value = "snap"

    uploaded_content = {}
    def capture_upload(*, local_path, drive_name, **_):
        # Read while file still exists (before run_backup's rmtree).
        uploaded_content[drive_name] = open(local_path, encoding="utf-8").read()
        return "fake-id"
    fake_client.upload_file.side_effect = capture_upload

    work_dir = tmp_path / "work"
    with patch("gdrive.backup.DriveClient", return_value=fake_client), \
         patch("gdrive.backup._iso_timestamp", return_value="2026-05-23T22-30-45"):
        run_backup(auth=fake_auth, config=config, history_dir=history_dir, work_dir=work_dir)

    # Local config unmodified.
    assert config == config_before

    # Uploaded config.json has <REDACTED> placeholders.
    on_disk = json.loads(uploaded_content["config.json"])
    assert on_disk["openrouter_api_key"] == REDACTION_PLACEHOLDER
    assert on_disk["cloud_api_keys"] == {"AssemblyAI": REDACTION_PLACEHOLDER}
    assert on_disk["language"] == "ru"
