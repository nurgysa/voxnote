from tasks.long_meeting import write_meeting_outputs


def test_write_meeting_outputs_creates_protocol_and_tasks(tmp_path):
    note = tmp_path / "transcript.md"
    note.write_text("transcript stays unchanged", encoding="utf-8")
    result = {
        "history_folder": str(tmp_path),
        "protocol_markdown": "# Protocol\n",
        "tasks_markdown": "# Tasks\n",
        "written": [],
    }

    out = write_meeting_outputs(result)

    assert (tmp_path / "protocol.md").read_text(encoding="utf-8") == "# Protocol\n"
    assert (tmp_path / "tasks.md").read_text(encoding="utf-8") == "# Tasks\n"
    assert note.read_text(encoding="utf-8") == "transcript stays unchanged"
    assert str(tmp_path / "protocol.md") in out["written"]
    assert str(tmp_path / "tasks.md") in out["written"]
