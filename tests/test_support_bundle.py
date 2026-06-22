"""Diagnostic log bundle for the "Отправить лог" button (WS-3 / D4).

support_bundle.build_log_bundle zips logs/ + a REDACTED config.json into a
single file the user sends to support manually (no telemetry backend). The
redaction reuse is load-bearing: a log bundle that leaked API keys would be
worse than the silent crash it diagnoses — so it's asserted, not assumed.
Pure (stdlib + support_bundle.redact_config), so it tests on Linux CI.
"""
from __future__ import annotations

import json
import zipfile

from support_bundle import (
    REDACTED_KEYS,
    REDACTION_PLACEHOLDER,
    build_log_bundle,
    redact_config,
)


def test_bundle_contains_logs_and_redacted_config(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("line1\nline2", encoding="utf-8")
    (logs / "app.log.1").write_text("rotated", encoding="utf-8")
    config = {"openrouter_api_key": "sk-secret", "meetings_dir": "/vault"}
    dest = tmp_path / "bundle.zip"

    summary = build_log_bundle(config, str(dest), logs_dir=str(logs))

    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        cfg = json.loads(zf.read("config.json"))
    assert "logs/app.log" in names
    assert "logs/app.log.1" in names
    assert "config.json" in names
    assert cfg["openrouter_api_key"] == "<REDACTED>"   # secret stripped
    assert cfg["meetings_dir"] == "/vault"             # non-secret survives
    assert summary["log_files"] == 2
    assert summary["dest"] == str(dest)


def test_bundle_redacts_nested_cloud_api_keys(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    config = {"cloud_api_keys": {"AssemblyAI": "aai-secret"}}
    dest = tmp_path / "b.zip"

    build_log_bundle(config, str(dest), logs_dir=str(logs))

    with zipfile.ZipFile(dest) as zf:
        cfg = json.loads(zf.read("config.json"))
    assert cfg["cloud_api_keys"]["AssemblyAI"] == "<REDACTED>"


def test_bundle_with_missing_logs_dir_still_writes_config(tmp_path):
    config = {"meetings_dir": "/x"}
    dest = tmp_path / "b.zip"

    summary = build_log_bundle(config, str(dest), logs_dir=str(tmp_path / "absent"))

    with zipfile.ZipFile(dest) as zf:
        assert "config.json" in zf.namelist()
    assert summary["log_files"] == 0


def test_bundle_does_not_mutate_input_config(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    config = {"openrouter_api_key": "sk-secret"}

    build_log_bundle(config, str(tmp_path / "b.zip"), logs_dir=str(logs))

    assert config["openrouter_api_key"] == "sk-secret"   # redact deep-copies


def test_redact_config_replaces_listed_keys_with_placeholder():
    config = {
        "language": "Авто-определение",
        "openrouter_api_key": "sk-or-real-key-12345",
        "linear_api_key": "lin_api_real",
        "glide_api_key": "real-glide-key",
        "assemblyai_api_key": "asm-real",
        "hf_token": "hf_real_token",
        "cloud_api_keys": {"AssemblyAI": "real", "Deepgram": "real2"},
        "gdrive_account_email": "user@example.com",
    }
    redacted = redact_config(config)
    assert redacted["openrouter_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["linear_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["glide_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["assemblyai_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["hf_token"] == REDACTION_PLACEHOLDER
    assert redacted["cloud_api_keys"] == {
        "AssemblyAI": REDACTION_PLACEHOLDER,
        "Deepgram": REDACTION_PLACEHOLDER,
    }
    assert redacted["language"] == "Авто-определение"
    assert redacted["gdrive_account_email"] == "user@example.com"
    assert config["openrouter_api_key"] == "sk-or-real-key-12345"


def test_redact_config_handles_missing_keys_silently():
    config = {"language": "Русский", "model": "large-v3"}
    redacted = redact_config(config)
    assert redacted == config
    assert redacted is not config


def test_redact_config_redacts_trello_credentials():
    config = {
        "trello_api_key": "trello-real-key",
        "trello_token": "trello-real-token",
        "trello_enabled": True,
    }
    redacted = redact_config(config)
    assert redacted["trello_api_key"] == REDACTION_PLACEHOLDER
    assert redacted["trello_token"] == REDACTION_PLACEHOLDER
    assert redacted["trello_enabled"] is True


def test_redact_config_redacts_unknown_secret_named_keys():
    config = {
        "some_new_api_token": "future-secret",
        "WEBHOOK_SECRET": "another-secret",
        "user_password": "hunter2",
        "gdrive_account_email": "user@example.com",
        "meetings_dir": "C:/vault",
        "speaker_count": "Авто",
    }
    redacted = redact_config(config)
    assert redacted["some_new_api_token"] == REDACTION_PLACEHOLDER
    assert redacted["WEBHOOK_SECRET"] == REDACTION_PLACEHOLDER
    assert redacted["user_password"] == REDACTION_PLACEHOLDER
    assert redacted["gdrive_account_email"] == "user@example.com"
    assert redacted["meetings_dir"] == "C:/vault"
    assert redacted["speaker_count"] == "Авто"
