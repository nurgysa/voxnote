from tasks.long_meeting import chunk_transcript


def test_chunk_transcript_keeps_short_transcript_as_one_chunk():
    body = "**Speaker 1:** Hello.\n\n**Speaker 2:** Hi."

    chunks = chunk_transcript(body, max_chars=1000)

    assert len(chunks) == 1
    assert chunks[0].index == 1
    assert chunks[0].total == 1
    assert chunks[0].text == body


def test_chunk_transcript_splits_on_blank_line_between_turns():
    turns = [f"**Speaker 1:** Turn {i} " + ("x" * 220) for i in range(12)]
    body = "\n\n".join(turns)

    chunks = chunk_transcript(body, max_chars=1000)

    assert len(chunks) > 1
    assert all(len(c.text) <= 1100 for c in chunks)
    assert all(c.text.startswith("**Speaker") for c in chunks)
    assert "Turn 0" in chunks[0].text
    assert "Turn 11" in chunks[-1].text


def test_chunk_transcript_rejects_empty_body():
    try:
        chunk_transcript("   ")
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
