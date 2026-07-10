"""CLI behavior for the Stage-0 ASR evaluation entry point.

Loads the script by path (it lives in scripts/, not a package), matching the
convention in tests/test_move_recordings_script.py.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

_PATH = pathlib.Path("scripts/asr_stage0_evaluate.py")
_spec = importlib.util.spec_from_file_location("asr_stage0_evaluate", _PATH)
asr_stage0_evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asr_stage0_evaluate)


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _manifest_path(tmp_path):
    return _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "items": [
                {"id": "a", "gold_text": "hello world", "languages": ["en"], "tags": []},
                {"id": "b", "gold_text": "goodnight moon", "languages": ["en"], "tags": []},
            ],
        },
    )


def _result_path(tmp_path):
    return _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [
                {"id": "a", "text": "hello world"},
                {"id": "b", "text": "goodnight moon"},
            ],
        },
    )


def test_main_writes_json_and_markdown_reports(tmp_path, capsys):
    manifest_path = _manifest_path(tmp_path)
    result_path = _result_path(tmp_path)
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"

    exit_code = asr_stage0_evaluate.main(
        [
            "--manifest",
            str(manifest_path),
            "--result",
            str(result_path),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["item_count"] == 2
    assert report["mean_wer"] == 0.0

    markdown = out_md.read_text(encoding="utf-8")
    assert "# ASR Stage-0 Evaluation Report" in markdown

    captured = capsys.readouterr()
    assert "groq" in captured.out


def test_main_prints_json_to_stdout_without_out_json(tmp_path, capsys):
    manifest_path = _manifest_path(tmp_path)
    result_path = _result_path(tmp_path)

    exit_code = asr_stage0_evaluate.main(
        ["--manifest", str(manifest_path), "--result", str(result_path)]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "groq" in captured.out


def test_main_returns_nonzero_and_prints_error_on_validation_failure(tmp_path, capsys):
    manifest_path = _manifest_path(tmp_path)
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "m",
            "items": [{"id": "a", "text": "hello world"}],
        },
    )

    exit_code = asr_stage0_evaluate.main(
        ["--manifest", str(manifest_path), "--result", str(result_path)]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.strip()


def test_main_returns_nonzero_for_missing_manifest_file(tmp_path, capsys):
    result_path = _result_path(tmp_path)

    exit_code = asr_stage0_evaluate.main(
        ["--manifest", str(tmp_path / "missing.json"), "--result", str(result_path)]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.strip()
