from tasks.long_meeting import (
    render_protocol_markdown,
    render_tasks_markdown,
)

RESULT = {
    "meeting_map": [{"topic": "Sensor", "summary": "Discussed modular water sensor."}],
    "decisions": [
        {
            "text": "Draft concept",
            "confidence": "high",
            "evidence": "we want to build",
        }
    ],
    "tasks": [
        {
            "title": "Write one-page concept",
            "owner": "Dias",
            "deadline": None,
            "evidence": "need concept",
        }
    ],
    "open_questions": ["Who owns lab validation?"],
    "uncertainties": ["Speaker names are generic."],
}

META = {
    "date": "2026-07-04",
    "provider": "AssemblyAI",
    "source_path": "G:/Drive/source.m4a",
}


def test_render_protocol_markdown_has_expected_sections():
    md = render_protocol_markdown(RESULT, meta=META)

    assert md.startswith("# Meeting Protocol Draft")
    assert "## Meeting Map" in md
    assert "## Decisions" in md
    assert "Draft concept" in md
    assert "source.m4a" in md


def test_render_tasks_markdown_is_approval_safe():
    md = render_tasks_markdown(RESULT, meta=META)

    assert md.startswith("# Candidate Tasks")
    assert "Draft - not sent" in md
    assert "Write one-page concept" in md
    assert "Who owns lab validation?" not in md
