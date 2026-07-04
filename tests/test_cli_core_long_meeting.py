import json
from unittest.mock import Mock, patch

from cli import core


def test_run_process_meeting_constructs_client_and_closes(tmp_path):
    note = tmp_path / "transcript.md"
    note.write_text("---\n---\n**Speaker 1:** Text", encoding="utf-8")

    fake_client = Mock()
    fake_client.complete.side_effect = [
        {"content": json.dumps({
            "topics": [],
            "decisions": [],
            "tasks": [],
            "open_questions": [],
            "uncertainties": [],
        })},
        {"content": json.dumps({
            "meeting_map": [],
            "decisions": [],
            "tasks": [],
            "open_questions": [],
            "uncertainties": [],
        })},
    ]

    with patch("tasks.openrouter_client.OpenRouterClient", return_value=fake_client):
        out = core.run_process_meeting(
            note_path=str(note),
            model="test/model",
            openrouter_key="key",
            write=False,
        )

    assert out["chunks"] == 1
    fake_client.close.assert_called_once()
