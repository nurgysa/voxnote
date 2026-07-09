from processing.vault_note import render_transcript_note


def test_render_transcript_note_includes_provenance_frontmatter():
    note = render_transcript_note(
        segments=[{"start": 0.0, "end": 1.0, "speaker": "A", "text": "Hi"}],
        title="meeting",
        project_name="Mini-AGI",
        date="2026-07-09",
        time="10:30",
        participants=["Айбек"],
        provider="AssemblyAI",
        language="mixed",
        voxnote_id="q-1",
        source_path="C:/audio/meeting.m4a",
        nudged=True,
        model="universal-2",
        diarized=True,
        duration_s=3723.5,
        cost_estimate_usd=0.1754321,
        source_sha256="abc123",
    )

    assert "provider: AssemblyAI" in note
    assert "model: universal-2" in note
    assert "language: mixed" in note
    assert "diarized: true" in note
    assert "duration_sec: 3723.5" in note
    assert "cost_estimate_usd: 0.175432" in note
    assert "source_sha256: abc123" in note
    assert "source_path: \"C:/audio/meeting.m4a\"" in note


def test_render_transcript_note_uses_null_for_unknown_numeric_provenance():
    note = render_transcript_note(
        segments=[{"text": "Hi"}],
        title="meeting",
        project_name=None,
        date="2026-07-09",
        time="10:30",
        participants=[],
        provider="Groq",
        language=None,
        voxnote_id="q-2",
        source_path=None,
        nudged=False,
    )

    assert "model: " in note
    assert "diarized: false" in note
    assert "duration_sec: null" in note
    assert "cost_estimate_usd: null" in note
    assert "source_sha256: " in note
