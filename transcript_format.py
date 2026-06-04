"""Formatters for transcript segments.

Pure functions over a list of segment dicts of the shape:

    {"start": float, "end": float, "text": str, "speaker"?: str}

The optional ``speaker`` key carries the cloud provider's speaker label
(``SPEAKER_XX`` — gets renamed to ``Спикер N``) or a name already
substituted in the UI (kept verbatim). Functions degrade gracefully when
the key is absent — the same module handles both diarized and
plain-transcription paths.

Kept torch-free and side-effect-free so it can be unit-tested without
loading any model.
"""

from __future__ import annotations


def _fmt_time_human(seconds: float) -> str:
    """Format seconds as ``[MM:SS]`` or ``[H:MM:SS]`` for the inline viewer."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def _fmt_time_srt(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS,mmm`` per the SubRip spec."""
    total_ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_time_vtt(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` per the WebVTT spec."""
    total_ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _build_speaker_map(segments: list[dict]) -> dict[str, str]:
    """Map raw provider speaker labels to friendly names, preserving any
    already-substituted names.

    SPEAKER_XX → Спикер 1/2/... in first-seen order. Labels that don't
    match the SPEAKER_ prefix (e.g. names already filled in by the user)
    are kept verbatim.
    """
    mapping: dict[str, str] = {}
    counter = 1
    for seg in segments:
        raw = seg.get("speaker")
        if raw is None or raw in mapping:
            continue
        if str(raw).startswith("SPEAKER_"):
            mapping[raw] = f"Спикер {counter}"
            counter += 1
        else:
            mapping[raw] = str(raw)
    return mapping


def format_timed(segments: list[dict]) -> str:
    """Plain text with ``[MM:SS]`` prefix per segment, no diarization."""
    if not segments:
        return ""
    return "\n".join(
        f"{_fmt_time_human(seg['start'])} {seg['text']}" for seg in segments
    )


def format_diarized(segments: list[dict]) -> str:
    """Speaker-labeled text, merging consecutive same-speaker segments.

    Format: ``[MM:SS] [Спикер N]: text...`` separated by blank lines.
    """
    if not segments:
        return ""

    speaker_map = _build_speaker_map(segments)

    lines: list[str] = []
    prev_speaker: str | None = None
    current_texts: list[str] = []
    block_start = 0.0

    for seg in segments:
        raw = seg.get("speaker")
        speaker = speaker_map.get(raw, str(raw)) if raw else None

        if speaker == prev_speaker:
            current_texts.append(seg["text"])
            continue

        if current_texts and prev_speaker:
            lines.append(
                f"{_fmt_time_human(block_start)} [{prev_speaker}]: "
                f"{' '.join(current_texts)}"
            )
        current_texts = [seg["text"]]
        block_start = seg["start"]
        prev_speaker = speaker

    if current_texts and prev_speaker:
        lines.append(
            f"{_fmt_time_human(block_start)} [{prev_speaker}]: "
            f"{' '.join(current_texts)}"
        )

    return "\n\n".join(lines)


def format_srt(segments: list[dict]) -> str:
    """SubRip subtitle format. One block per segment.

    Speaker labels (when present) are inlined as ``Спикер N: text`` so
    they survive in players that ignore styling. Blank ``text`` segments
    are skipped — players treat zero-text cues as flicker.
    """
    if not segments:
        return ""

    speaker_map = _build_speaker_map(segments)
    blocks: list[str] = []
    idx = 1
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker_raw = seg.get("speaker")
        speaker = speaker_map.get(speaker_raw) if speaker_raw else None
        body = f"{speaker}: {text}" if speaker else text
        blocks.append(
            f"{idx}\n"
            f"{_fmt_time_srt(seg['start'])} --> {_fmt_time_srt(seg['end'])}\n"
            f"{body}"
        )
        idx += 1
    # SRT spec: blocks separated by a blank line; trailing newline is fine.
    return "\n\n".join(blocks) + "\n"


def apply_speaker_names(text: str, name_by_label: dict[str, str]) -> str:
    """Replace bracketed friendly speaker labels with real names.

    ``name_by_label`` maps a friendly label ("Спикер 1") to a person's ФИО.
    Only bound labels are replaced; unbound labels stay "Спикер N". The
    bracketed token "[Спикер 1]" is replaced as a unit (both brackets
    included) so "Спикер 1" never matches inside "Спикер 11". Identity
    when the map is empty.
    """
    for label_text, name in name_by_label.items():
        text = text.replace(f"[{label_text}]", f"[{name}]")
    return text


def format_vtt(segments: list[dict]) -> str:
    """WebVTT subtitle format. Same conventions as SRT but with WEBVTT header."""
    if not segments:
        return "WEBVTT\n"

    speaker_map = _build_speaker_map(segments)
    cues: list[str] = ["WEBVTT", ""]
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker_raw = seg.get("speaker")
        speaker = speaker_map.get(speaker_raw) if speaker_raw else None
        body = f"{speaker}: {text}" if speaker else text
        cues.append(
            f"{_fmt_time_vtt(seg['start'])} --> {_fmt_time_vtt(seg['end'])}\n"
            f"{body}\n"
        )
    return "\n".join(cues)
