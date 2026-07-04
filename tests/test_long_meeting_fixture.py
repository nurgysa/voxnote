import json
from pathlib import Path
from unittest.mock import Mock

from tasks.long_meeting import process_meeting_note


def test_synthetic_long_fixture_processes_without_network():
    note = Path("tests/fixtures/long_meeting_transcript.md")
    client = Mock()
    chunk_response = json.dumps({
        "topics": [{"title": "Water sensor", "evidence": "modular water sensor"}],
        "decisions": [],
        "tasks": [{"title": "Draft one-page concept", "owner": None, "deadline": None, "evidence": "one-page concept"}],
        "open_questions": ["Who owns lab validation?"],
        "uncertainties": ["No deadline confirmed"],
    })
    synthesis_response = json.dumps({
        "meeting_map": [{"topic": "Water sensor", "summary": "Concept, validation, field testing"}],
        "decisions": [],
        "tasks": [{"title": "Draft one-page concept", "owner": None, "deadline": None, "evidence": "one-page concept"}],
        "open_questions": ["Who owns lab validation?"],
        "uncertainties": ["No deadline confirmed"],
    })

    def fake_complete(*, messages, **kwargs):
        system = messages[0]["content"].lower()
        if "consolidate" in system:
            return {"content": synthesis_response}
        return {"content": chunk_response}

    client.complete.side_effect = fake_complete

    result = process_meeting_note(note, model="test/model", openrouter_client=client, max_chars=1200)

    assert result["chunks"] >= 3
    assert "Meeting Protocol Draft" in result["protocol_markdown"]
    assert "Candidate Tasks" in result["tasks_markdown"]
