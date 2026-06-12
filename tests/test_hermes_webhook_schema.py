"""Tests for the Hermes webhook event payload builder.

All eight spec §11.2 schema behaviors are covered here.
Pure unit tests — no network, no filesystem I/O beyond module import.
"""
from __future__ import annotations

import pytest

from integrations.hermes.schema import build_audio_transcribed_event

# ── 1. Required top-level fields ──────────────────────────────────────

def test_required_top_level_fields_present():
    payload = build_audio_transcribed_event(transcript_text="hello")
    for key in ("event_type", "version", "source", "routing_hint",
                "audio", "transcript", "analysis", "meta"):
        assert key in payload, f"Missing top-level field: {key!r}"


# ── 2. event_type is exactly "audio.transcribed" ──────────────────────

def test_event_type_value():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["event_type"] == "audio.transcribed"


# ── 3. version is exactly "1.0" ───────────────────────────────────────

def test_version_value():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["version"] == "1.0"


# ── source is exactly "audio-transcriber" ────────────────────────────

def test_source_value():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["source"] == "audio-transcriber"


# ── 4. Filename is extracted from audio_path ──────────────────────────

def test_filename_extracted_from_audio_path():
    payload = build_audio_transcribed_event(
        transcript_text="Привет мир",
        audio_path="C:/tmp/meeting.m4a",
        provider="AssemblyAI",
        language="ru",
        created_at="2026-06-11T12:00:00Z",
    )
    assert payload["audio"]["filename"] == "meeting.m4a"
    assert payload["audio"]["path"] == "C:/tmp/meeting.m4a"


def test_filename_extracted_from_windows_backslash_path():
    payload = build_audio_transcribed_event(
        transcript_text="x",
        audio_path=r"C:\Users\nurgisa\Documents\recording.wav",
    )
    assert payload["audio"]["filename"] == "recording.wav"


# ── 5. Optional arrays default to [] ─────────────────────────────────

def test_optional_arrays_default_to_empty():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["transcript"]["segments"] == []
    assert payload["analysis"]["tasks"] == []
    assert payload["analysis"]["ideas"] == []
    assert payload["analysis"]["decisions"] == []


def test_none_arrays_normalize_to_empty():
    payload = build_audio_transcribed_event(
        transcript_text="x",
        segments=None,
        tasks=None,
        ideas=None,
        decisions=None,
    )
    assert payload["transcript"]["segments"] == []
    assert payload["analysis"]["tasks"] == []
    assert payload["analysis"]["ideas"] == []
    assert payload["analysis"]["decisions"] == []


# ── 6. created_at override is respected ──────────────────────────────

def test_created_at_override_used():
    payload = build_audio_transcribed_event(
        transcript_text="x",
        created_at="2026-06-11T12:00:00Z",
    )
    assert payload["meta"]["created_at"] == "2026-06-11T12:00:00Z"


def test_created_at_auto_generated_when_not_provided():
    import re
    payload = build_audio_transcribed_event(transcript_text="x")
    ts = payload["meta"]["created_at"]
    # Must match YYYY-MM-DDTHH:MM:SSZ
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), (
        f"Unexpected timestamp format: {ts!r}"
    )


# ── 7. Missing audio path → filename and path are None ────────────────

def test_missing_audio_path_gives_null_filename():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["audio"]["filename"] is None
    assert payload["audio"]["path"] is None


# ── 8. Unicode transcript text is preserved ──────────────────────────

def test_unicode_transcript_preserved():
    text = "Привет мир 你好世界 مرحبا"
    payload = build_audio_transcribed_event(transcript_text=text)
    assert payload["transcript"]["raw"] == text


# ── Full spec example from §11.2 ─────────────────────────────────────

def test_full_spec_example():
    payload = build_audio_transcribed_event(
        transcript_text="Привет мир",
        audio_path="C:/tmp/meeting.m4a",
        provider="AssemblyAI",
        language="ru",
        created_at="2026-06-11T12:00:00Z",
    )
    assert payload["event_type"] == "audio.transcribed"
    assert payload["version"] == "1.0"
    assert payload["source"] == "audio-transcriber"
    assert payload["audio"]["filename"] == "meeting.m4a"
    assert payload["transcript"]["raw"] == "Привет мир"
    assert payload["transcript"]["segments"] == []
    assert payload["meta"]["provider"] == "AssemblyAI"
    assert payload["meta"]["language"] == "ru"


# ── routing_hint default and override ────────────────────────────────

def test_routing_hint_default():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["routing_hint"] == "obsidian_inbox"


def test_routing_hint_override():
    payload = build_audio_transcribed_event(
        transcript_text="x",
        routing_hint="telegram",
    )
    assert payload["routing_hint"] == "telegram"


# ── analysis optional scalars default to None ────────────────────────

def test_analysis_optional_scalars_default_none():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["analysis"]["summary"] is None
    assert payload["analysis"]["protocol"] is None


# ── history_folder forwarded ──────────────────────────────────────────

def test_history_folder_forwarded():
    payload = build_audio_transcribed_event(
        transcript_text="x",
        history_folder="C:/vault/2026-06-11",
    )
    assert payload["audio"]["history_folder"] == "C:/vault/2026-06-11"


def test_history_folder_defaults_to_none():
    payload = build_audio_transcribed_event(transcript_text="x")
    assert payload["audio"]["history_folder"] is None
