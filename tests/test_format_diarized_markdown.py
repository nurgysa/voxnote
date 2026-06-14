# tests/test_format_diarized_markdown.py
from transcript_format import format_diarized_markdown


def _seg(start, text, speaker=None):
    return {"start": start, "end": start + 1, "text": text, "speaker": speaker}


def test_groups_consecutive_same_speaker():
    segs = [
        _seg(0, "привет", "SPEAKER_00"),
        _seg(1, "как дела", "SPEAKER_00"),
        _seg(2, "норм", "SPEAKER_01"),
    ]
    assert format_diarized_markdown(segs) == (
        "**Спикер 1:** привет как дела\n\n**Спикер 2:** норм"
    )


def test_no_speakers_plain_paragraphs():
    segs = [_seg(0, "первый"), _seg(1, "второй")]
    assert format_diarized_markdown(segs) == "первый\n\nвторой"


def test_empty_returns_empty():
    assert format_diarized_markdown([]) == ""


def test_speaker_map_override():
    segs = [_seg(0, "да", "SPEAKER_00")]
    assert format_diarized_markdown(
        segs, speaker_map={"SPEAKER_00": "Айгерим"}
    ) == "**Айгерим:** да"
