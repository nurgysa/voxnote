from pathlib import Path

from tasks.long_meeting import MeetingNote, read_meeting_note


def test_read_meeting_note_parses_frontmatter_and_body(tmp_path):
    note = tmp_path / "transcript.md"
    note.write_text(
        """---
type: meeting
date: 2026-07-04
time: "10:09"
provider: AssemblyAI
language: mixed
voxnote_id: test-id
source_path: "G:/Drive/Sources/meeting.m4a"
nudged: false
---
**Speaker 1:** First point.

**Speaker 2:** Second point.
""",
        encoding="utf-8",
    )

    out = read_meeting_note(note)

    assert isinstance(out, MeetingNote)
    assert out.note_path == note
    assert out.history_folder == note.parent
    assert out.meta["provider"] == "AssemblyAI"
    assert out.meta["language"] == "mixed"
    assert out.meta["source_path"] == "G:/Drive/Sources/meeting.m4a"
    assert "First point" in out.body
    assert "---" not in out.body


def test_read_meeting_note_rejects_missing_file(tmp_path):
    missing = tmp_path / "missing.md"

    try:
        read_meeting_note(missing)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
