import json
from unittest.mock import Mock

from tasks.long_meeting import process_meeting_note


def _note(tmp_path):
    p = tmp_path / "transcript.md"
    p.write_text(
        """---
date: 2026-07-04
provider: AssemblyAI
language: mixed
source_path: "G:/Drive/source.m4a"
---
**Speaker 1:** We should draft the concept.

**Speaker 2:** Who owns lab validation?
""",
        encoding="utf-8",
    )
    return p


def test_process_meeting_note_calls_llm_for_chunks_and_synthesis(tmp_path):
    client = Mock()
    client.complete.side_effect = [
        {
            "content": json.dumps(
                {
                    "topics": [{"title": "Concept", "evidence": "draft the concept"}],
                    "decisions": [],
                    "tasks": [
                        {
                            "title": "Draft concept",
                            "owner": None,
                            "deadline": None,
                            "evidence": "draft the concept",
                        }
                    ],
                    "open_questions": ["Who owns lab validation?"],
                    "uncertainties": [],
                }
            )
        },
        {
            "content": json.dumps(
                {
                    "meeting_map": [
                        {
                            "topic": "Concept",
                            "summary": "Discussed concept drafting",
                        }
                    ],
                    "decisions": [],
                    "tasks": [
                        {
                            "title": "Draft concept",
                            "owner": None,
                            "deadline": None,
                            "evidence": "draft the concept",
                        }
                    ],
                    "open_questions": ["Who owns lab validation?"],
                    "uncertainties": [],
                }
            )
        },
    ]

    out = process_meeting_note(
        _note(tmp_path),
        model="test/model",
        openrouter_client=client,
        max_chars=4000,
    )

    assert out["chunks"] == 1
    assert "protocol_markdown" in out
    assert "tasks_markdown" in out
    assert out["result"]["tasks"][0]["title"] == "Draft concept"
    assert client.complete.call_count == 2
