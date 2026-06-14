"""Pure payload builder for the Hermes ``audio.transcribed`` webhook event.

No network calls, no side effects — only data construction.
All inputs are optional except ``transcript_text``; missing values yield safe
defaults so the caller never needs to guard before calling.
"""
from __future__ import annotations

import ntpath
from datetime import datetime, timezone


def build_audio_transcribed_event(
    *,
    transcript_text: str,
    audio_path: str | None = None,
    history_folder: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    segments: list | None = None,
    routing_hint: str = "obsidian_inbox",
    summary: str | None = None,
    tasks: list | None = None,
    ideas: list | None = None,
    decisions: list | None = None,
    protocol: str | None = None,
    note_path: str | None = None,
    source_path: str | None = None,
    project: dict | None = None,
    created_at: str | None = None,
) -> dict:
    """Build a JSON-serializable ``audio.transcribed`` event dict.

    Args:
        transcript_text: Full transcript string. Never audio bytes.
        audio_path: Absolute or relative path to the source audio file.
            ``audio.filename`` is its basename (both ``/`` and ``\\``
            recognized as separators on any OS).
        history_folder: Path to the meeting history folder for this run.
        provider: Cloud STT provider name (e.g. ``"AssemblyAI"``).
        language: BCP-47 language code (e.g. ``"ru"``) or ``"mixed"``.
        segments: Speaker-segmented transcript items. Defaults to ``[]``.
        routing_hint: Hermes routing target. Defaults to ``"obsidian_inbox"``.
        summary: Optional extracted summary text.
        tasks: Extracted task list. Defaults to ``[]``.
        ideas: Extracted idea list. Defaults to ``[]``.
        decisions: Extracted decision list. Defaults to ``[]``.
        protocol: Optional generated protocol text.
        note_path: Path to the meeting transcript.md (populated by the
            processing-queue worker in PR-B2).
        source_path: Path to the audio file in Google Drive sources
            (populated by the processing-queue worker after archive_audio).
        project: Dict with ``id`` and ``name`` keys identifying the
            project this meeting belongs to. ``None`` when not in a queue.
        created_at: UTC timestamp string ``YYYY-MM-DDTHH:MM:SSZ``. When
            omitted, current timezone-aware UTC time is used.

    Returns:
        A ``dict`` that is safe to pass to ``json.dumps``.
    """
    if created_at is None:
        now = datetime.now(tz=timezone.utc)
        created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    filename: str | None = None
    path_str: str | None = None
    if audio_path is not None:
        path_str = audio_path
        # ntpath.basename splits on BOTH / and \ regardless of host OS:
        # the producer is usually the Windows app, but the CLI may run on
        # a Linux Hermes host — the payload must not depend on where the
        # event was built (PosixPath.name treats \ as a literal char).
        filename = ntpath.basename(audio_path) or None

    return {
        "event_type": "audio.transcribed",
        "version": "1.1",
        "source": "voxnote",
        "routing_hint": routing_hint,
        "audio": {
            "filename": filename,
            "path": path_str,
            "history_folder": history_folder,
            "note_path": note_path,
            "source_path": source_path,
        },
        "project": project,
        "transcript": {
            "raw": transcript_text,
            "segments": segments if segments is not None else [],
        },
        "analysis": {
            "summary": summary,
            "tasks": tasks if tasks is not None else [],
            "ideas": ideas if ideas is not None else [],
            "decisions": decisions if decisions is not None else [],
            "protocol": protocol,
        },
        "meta": {
            "provider": provider,
            "language": language,
            "created_at": created_at,
        },
    }
