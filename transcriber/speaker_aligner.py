"""Pure speaker-alignment helpers for the diarization post-processing pass.

Maps Whisper word-level timestamps onto pyannote speaker turns, splitting
each Whisper segment along speaker boundaries so a single segment that
spans two speakers ("— Да. — Согласен.") emits one labeled sub-segment
per speaker instead of being collapsed onto whichever speaker had max
overlap.

All functions here are pure (no Whisper, no pyannote, no I/O) and tested
by ``tests/test_transcriber_pure``. Lifted out of the monolithic
``transcriber.py`` so the alignment logic can evolve independently of the
heavy ML imports.
"""
from __future__ import annotations


def _assign_speakers_word_level(
    segments: list[dict],
    speaker_turns: list[tuple[float, float, str]],
) -> list[dict]:
    """Split each Whisper segment into sub-segments along speaker-turn boundaries.

    Fixes the dominant dialogue error in segment-level max-overlap assignment:
    a single Whisper segment spanning two speakers ("— Да. — Согласен.") used
    to be labeled with one speaker (max overlap wins). Here each WORD inside
    the segment is placed on the pyannote timeline independently, and adjacent
    same-speaker words are re-grouped into output sub-segments.

    Input:  segments from :meth:`Transcriber.transcribe` — dicts with
            ``{start, end, text, words:[{start,end,word}, ...]}``.
    Output: flat list of ``{start, end, text, speaker}`` dicts in chronological
            order, ready for ``_format_diarized`` (which does the numbering and
            same-speaker merge across segments).

    Segments with empty ``words`` (Whisper DTW pass skipped them) fall back to
    whole-segment max-overlap — same behavior as before word-level path.
    """
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            # Fallback: no per-word times → keep the old behavior for this seg.
            speaker = _find_speaker_by_overlap(
                seg["start"], seg["end"], speaker_turns,
            )
            out.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": speaker,
            })
            continue

        # Group consecutive same-speaker words into emitted sub-segments.
        # We use the word midpoint as the probe time — more robust than
        # start/end at boundaries where a word straddles a speaker change.
        current_words: list[dict] = []
        current_speaker: str | None = None

        for w in words:
            mid = (w["start"] + w["end"]) / 2.0
            sp = _speaker_at_time(mid, speaker_turns)
            if sp != current_speaker and current_words:
                _flush_word_group(current_words, current_speaker, out)
                current_words = []
            current_speaker = sp
            current_words.append(w)

        _flush_word_group(current_words, current_speaker, out)

    return out


def _flush_word_group(
    words: list[dict],
    speaker: str | None,
    out: list[dict],
) -> None:
    """Append one sub-segment ({start, end, text, speaker}) for a word run.

    Module-level (not nested in :func:`_assign_speakers_word_level`'s loop)
    to avoid B023 — a closure over the loop's mutable ``current_words`` /
    ``current_speaker`` works only because callers invoke it synchronously
    in the same iteration; making the dependency explicit via parameters
    is clearer and ruff-clean.

    Skips word groups that are empty or pure-whitespace (e.g. a leading
    space token from Whisper's tokenizer that would render as "").
    """
    if not words:
        return
    text = "".join(w["word"] for w in words).strip()
    if not text:
        return
    out.append({
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": text,
        "speaker": speaker,
    })


def _speaker_at_time(
    t: float,
    speaker_turns: list[tuple[float, float, str]],
) -> str:
    """Return the speaker active at time ``t``.

    First checks for a turn whose [start, end] interval contains ``t``; if
    none (common at turn edges or in VAD gaps that pyannote didn't fill),
    falls back to the turn with the smallest edge distance. Guarantees a
    non-None return even when speaker_turns is empty (SPEAKER_00), so the
    caller never has to handle None.
    """
    best_speaker = "SPEAKER_00"
    best_dist = float("inf")
    for start, end, speaker in speaker_turns:
        if start <= t <= end:
            return speaker
        dist = t - end if t > end else start - t
        if dist < best_dist:
            best_dist = dist
            best_speaker = speaker
    return best_speaker


def _find_speaker_by_overlap(
    seg_start: float,
    seg_end: float,
    speaker_turns: list[tuple[float, float, str]],
) -> str:
    """Find which speaker has the most temporal overlap with a segment."""
    overlap_by_speaker: dict[str, float] = {}
    for start, end, speaker in speaker_turns:
        # Calculate overlap between segment and speaker turn
        overlap_start = max(seg_start, start)
        overlap_end = min(seg_end, end)
        overlap = max(0.0, overlap_end - overlap_start)
        if overlap > 0:
            overlap_by_speaker[speaker] = overlap_by_speaker.get(speaker, 0.0) + overlap

    if overlap_by_speaker:
        return max(overlap_by_speaker, key=overlap_by_speaker.get)

    # Fallback: find nearest speaker turn
    min_dist = float("inf")
    nearest = "SPEAKER_00"
    for start, end, speaker in speaker_turns:
        dist = min(abs(seg_start - end), abs(seg_end - start))
        if dist < min_dist:
            min_dist = dist
            nearest = speaker
    return nearest
