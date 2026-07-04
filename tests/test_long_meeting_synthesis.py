import json

from tasks.long_meeting import build_synthesis_messages, parse_synthesis_response


def test_build_synthesis_messages_contains_chunk_outputs_not_full_transcript():
    chunk_outputs = [
        {
            "topics": [{"title": "A", "evidence": "B"}],
            "tasks": [],
            "decisions": [],
            "open_questions": [],
            "uncertainties": [],
        }
    ]

    messages = build_synthesis_messages(chunk_outputs, meta={"date": "2026-07-04"})

    assert len(messages) == 2
    assert "consolidate" in messages[0]["content"].lower()
    assert "2026-07-04" in messages[1]["content"]
    assert "topics" in messages[1]["content"]


def test_parse_synthesis_response_accepts_schema():
    raw = json.dumps(
        {
            "meeting_map": [
                {"topic": "Sensor", "summary": "Discussed water heavy metals"}
            ],
            "decisions": [
                {
                    "text": "Draft concept",
                    "confidence": "high",
                    "evidence": "we want to",
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
            "open_questions": ["Lab access?"],
            "uncertainties": ["No deadlines confirmed"],
        }
    )

    out = parse_synthesis_response(raw)

    assert out["meeting_map"][0]["topic"] == "Sensor"
    assert out["tasks"][0]["title"] == "Write one-page concept"
