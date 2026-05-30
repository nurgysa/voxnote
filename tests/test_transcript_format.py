"""Tests for the pure transcript-formatting helpers."""
import pytest

from transcript_format import (
    _build_speaker_map,
    _fmt_time_human,
    _fmt_time_srt,
    _fmt_time_vtt,
    apply_speaker_names,
    format_diarized,
    format_srt,
    format_timed,
    format_vtt,
)

# ── time formatters ────────────────────────────────────────────────


@pytest.mark.parametrize("seconds,expected", [
    (0, "[00:00]"),
    (5, "[00:05]"),
    (65, "[01:05]"),
    (3661, "[1:01:01]"),
    (7200, "[2:00:00]"),
])
def test_fmt_time_human(seconds, expected):
    assert _fmt_time_human(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0.0, "00:00:00,000"),
    (1.5, "00:00:01,500"),
    (61.123, "00:01:01,123"),
    (3725.999, "01:02:05,999"),
    # Negative inputs should clamp to 00:00:00,000 (defensive — pyannote
    # has emitted slightly-negative starts on rare boundary segments).
    (-1.0, "00:00:00,000"),
])
def test_fmt_time_srt(seconds, expected):
    assert _fmt_time_srt(seconds) == expected


def test_fmt_time_vtt_uses_dot_separator():
    # The only difference vs SRT is comma → dot for the millisecond split.
    assert _fmt_time_vtt(1.5) == "00:00:01.500"
    assert _fmt_time_vtt(3725.999) == "01:02:05.999"


# ── speaker map ────────────────────────────────────────────────────


def test_build_speaker_map_renames_in_order():
    segs = [
        {"speaker": "SPEAKER_02"},
        {"speaker": "SPEAKER_00"},
        {"speaker": "SPEAKER_02"},  # repeat — no new entry
        {"speaker": "SPEAKER_01"},
    ]
    # First-seen order: SPEAKER_02 → "Спикер 1", SPEAKER_00 → "Спикер 2", ...
    assert _build_speaker_map(segs) == {
        "SPEAKER_02": "Спикер 1",
        "SPEAKER_00": "Спикер 2",
        "SPEAKER_01": "Спикер 3",
    }


def test_build_speaker_map_keeps_enrolled_names():
    segs = [
        {"speaker": "Нургиса"},
        {"speaker": "SPEAKER_00"},
    ]
    assert _build_speaker_map(segs) == {
        "Нургиса": "Нургиса",
        "SPEAKER_00": "Спикер 1",
    }


# ── format_timed / format_diarized ─────────────────────────────────


def test_format_timed_empty_returns_empty_string():
    assert format_timed([]) == ""


def test_format_timed_one_segment():
    segs = [{"start": 0.0, "end": 1.0, "text": "Привет"}]
    assert format_timed(segs) == "[00:00] Привет"


def test_format_diarized_merges_consecutive_same_speaker():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "А", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "Б", "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 3.0, "text": "В", "speaker": "SPEAKER_01"},
    ]
    out = format_diarized(segs)
    # First block merges A+B; second is a separate speaker.
    assert out == "[00:00] [Спикер 1]: А Б\n\n[00:02] [Спикер 2]: В"


# ── format_srt ─────────────────────────────────────────────────────


def test_format_srt_empty_returns_empty_string():
    assert format_srt([]) == ""


def test_format_srt_skips_blank_text_segments():
    # A blank cue would render as flicker in players — must be skipped, and
    # the remaining cue must be re-numbered as #1, not #2.
    segs = [
        {"start": 0.0, "end": 1.0, "text": "  "},
        {"start": 1.0, "end": 2.0, "text": "Real"},
    ]
    out = format_srt(segs)
    assert out.startswith("1\n00:00:01,000 --> 00:00:02,000\nReal\n")


def test_format_srt_inlines_speaker_label():
    segs = [{"start": 0.0, "end": 1.0, "text": "Привет", "speaker": "SPEAKER_00"}]
    out = format_srt(segs)
    assert "Спикер 1: Привет" in out


# ── format_vtt ─────────────────────────────────────────────────────


def test_format_vtt_emits_header_even_when_empty():
    assert format_vtt([]) == "WEBVTT\n"


def test_format_vtt_includes_header_and_dot_timestamps():
    segs = [{"start": 0.0, "end": 1.5, "text": "Привет"}]
    out = format_vtt(segs)
    assert out.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.500" in out


# ── apply_speaker_names ────────────────────────────────────────────


def test_apply_speaker_names_replaces_bound_labels():
    text = "[00:05] [Спикер 1]: привет\n\n[00:12] [Спикер 2]: пока"
    out = apply_speaker_names(text, {"Спикер 1": "Айбек Нурланов"})
    assert "[Айбек Нурланов]: привет" in out
    assert "[Спикер 2]: пока" in out  # unbound label untouched


def test_apply_speaker_names_empty_map_is_identity():
    text = "[00:05] [Спикер 1]: привет"
    assert apply_speaker_names(text, {}) == text


def test_apply_speaker_names_no_collision_1_vs_11():
    text = "[Спикер 1]: a\n[Спикер 11]: b"
    out = apply_speaker_names(text, {"Спикер 1": "Сара"})
    assert "[Сара]: a" in out
    assert "[Спикер 11]: b" in out  # 11 must NOT be rewritten by the "1" rule
