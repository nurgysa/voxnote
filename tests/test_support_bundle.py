"""Diagnostic log bundle for the "Отправить лог" button (WS-3 / D4).

support_bundle.build_log_bundle zips logs/ + a REDACTED config.json into a
single file the user sends to support manually (no telemetry backend). The
redaction reuse is load-bearing: a log bundle that leaked API keys would be
worse than the silent crash it diagnoses — so it's asserted, not assumed.
Pure (stdlib + gdrive.redact_config), so it tests on Linux CI.
"""
from __future__ import annotations

import json
import zipfile

from support_bundle import build_log_bundle


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
