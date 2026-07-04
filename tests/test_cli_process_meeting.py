import json
from unittest.mock import patch

import pytest

from cli.app import build_parser, main


def test_process_meeting_requires_note_path():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["process-meeting"])


def test_process_meeting_prints_json(tmp_path, capsys):
    note = tmp_path / "transcript.md"
    note.write_text("---\n---\nbody", encoding="utf-8")
    fake = {
        "note_path": str(note),
        "history_folder": str(tmp_path),
        "model": "test/model",
        "chunks": 1,
        "result": {
            "meeting_map": [],
            "decisions": [],
            "tasks": [],
            "open_questions": [],
            "uncertainties": [],
        },
        "protocol_markdown": "# P",
        "tasks_markdown": "# T",
        "written": [],
    }

    with patch("cli.config.merged_config", return_value={"openrouter_api_key": "key"}), \
         patch("cli.core.run_process_meeting", return_value=fake) as run:
        code = main([
            "process-meeting",
            "--note-path",
            str(note),
            "--model",
            "test/model",
            "--json",
        ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["chunks"] == 1
    run.assert_called_once()
    assert run.call_args.kwargs["write"] is False


def test_process_meeting_write_flag_is_passed(tmp_path):
    note = tmp_path / "transcript.md"
    note.write_text("---\n---\nbody", encoding="utf-8")

    with patch("cli.config.merged_config", return_value={"openrouter_api_key": "key"}), \
         patch("cli.core.run_process_meeting", return_value={"written": ["protocol.md"]}) as run:
        code = main(["process-meeting", "--note-path", str(note), "--write", "--json"])

    assert code == 0
    assert run.call_args.kwargs["write"] is True
