"""Pure helpers for the Voice-ID queue path — split a Speechmatics speaker-ID
result into identified participants and unknown ("new") voices awaiting naming.

Tk-free and side-effect-free so it unit-tests without any UI or network.
"""
from __future__ import annotations

import re

_ANON_RE = re.compile(r"^SPEAKER_\d+$")


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
    spoke: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp and not _ANON_RE.match(sp) and sp not in seen:
            seen.add(sp)
            spoke.append(sp)
    participants = sorted(spoke)

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
