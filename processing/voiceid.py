"""Pure helpers for the Voice-ID queue path — split a Speechmatics speaker-ID
result into identified participants and unknown ("new") voices awaiting naming.

Tk-free and side-effect-free so it unit-tests without any UI or network.
"""
from __future__ import annotations

import re

from processing import vault_note

_ANON_RE = re.compile(r"^SPEAKER_")


def _normalise_raw_label(raw: str) -> str:
    """Mirror providers.speechmatics._normalise_speaker for anonymous labels:
    ``S1`` -> ``SPEAKER_1``; anything else -> ``SPEAKER_<raw>`` (e.g. ``UU`` ->
    ``SPEAKER_UU``). Identified real names never reach this (they are filtered by
    known_names first), so we only ever map anonymous Speechmatics labels here."""
    if raw.startswith("S") and raw[1:].isdigit():
        return f"SPEAKER_{raw[1:]}"
    return f"SPEAKER_{raw}"


def _first_sample(segments: list[dict], label: str) -> tuple[str, float]:
    """(text, start) of the first segment spoken by ``label``; ("", 0.0) if none."""
    for seg in segments:
        if seg.get("speaker") == label:
            return (seg.get("text") or "").strip(), float(seg.get("start", 0.0))
    return "", 0.0


def partition_speakers(
    segments: list[dict],
    speaker_identifiers: dict[str, list[str]],
    known_names: set[str],
) -> tuple[list[str], list[dict]]:
    """Split a diarized speaker-ID result.

    participants: sorted unique identified real names that actually spoke
        (segment labels that are neither anonymous ``SPEAKER_N`` nor blank).
    pending: one dict per unknown returned voice (a response label not in
        ``known_names``) carrying its identifier + a sample for recognition.
    """
    participants = participants_that_spoke(segments)

    pending: list[dict] = []
    for raw_label, ids in (speaker_identifiers or {}).items():
        if raw_label in known_names:
            continue          # an identified person, not a new voice
        if not ids:
            continue          # no identifier → cannot enroll → don't surface
        label = _normalise_raw_label(raw_label)
        sample_text, first_start = _first_sample(segments, label)
        pending.append({
            "label": label,
            "identifier": ids[0],
            "sample_text": sample_text,
            "first_start": first_start,
        })
    pending.sort(key=lambda p: p["first_start"])
    return participants, pending


def participants_that_spoke(segments: list[dict]) -> list[str]:
    """Sorted unique non-anonymous speaker names that actually appear in
    ``segments`` (labels that are neither ``SPEAKER_*`` nor blank). Shared by
    partition_speakers (the identified set) and the retroactive re-render."""
    spoke: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp and not _ANON_RE.match(sp) and sp not in seen:
            seen.add(sp)
            spoke.append(sp)
    return sorted(spoke)


def rename_segment_speakers(
    segments: list[dict], names_by_label: dict[str, str]
) -> list[dict]:
    """A shallow copy of ``segments`` with every ``speaker`` label present in
    ``names_by_label`` replaced by the chosen ФИО; other labels untouched.
    Non-destructive — the inputs are the persisted sidecar and must not mutate."""
    out: list[dict] = []
    for seg in segments:
        s = dict(seg)
        label = s.get("speaker")
        if label in names_by_label:
            s["speaker"] = names_by_label[label]
        out.append(s)
    return out


def rerender_named_note(
    segments: list[dict], names_by_label: dict[str, str], note_meta: dict
) -> str:
    """Re-render a meeting's transcript.md content after naming some voices.

    Applies ``names_by_label`` (``SPEAKER_n`` -> ФИО) to the segments, recomputes
    ``participants`` from the renamed segments (newly named + already-identified
    people who spoke; still-anonymous ``SPEAKER_*`` excluded), and renders via the
    canonical vault_note formatter. No ``speaker_map`` is passed, so any voices
    left unnamed renumber cleanly to «Спикер N». ``note_meta`` carries the
    non-segment render kwargs persisted in the sidecar; its keys MUST match
    render_transcript_note's remaining keyword params."""
    renamed = rename_segment_speakers(segments, names_by_label)
    participants = participants_that_spoke(renamed)
    return vault_note.render_transcript_note(
        segments=renamed,
        participants=participants,
        **note_meta,
    )


def playback_window(
    n_samples: int, sample_rate: int, first_start: float, window_s: float = 6.0
) -> tuple[int, int]:
    """[start_idx, end_idx) sample slice for a preview ``window_s`` seconds long
    starting at ``first_start`` (clamped to the audio). Returns an empty slice
    (start == end) for empty audio, a non-positive sample rate, or a start past
    the end."""
    if n_samples <= 0 or sample_rate <= 0:
        return 0, 0
    start = max(0, min(int(first_start * sample_rate), n_samples))
    end = min(n_samples, start + int(window_s * sample_rate))
    return start, end
