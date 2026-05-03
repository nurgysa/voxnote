"""Diarization progress-line parser.

The diarize_worker subprocess emits ``PROGRESS\\t<step>\\t<completed>\\t<total>``
lines on stderr. This module turns each such line into a 0..100 % position
inside the 70-90 % GUI progress band (post-Whisper, pre-formatting).

Pure module — no I/O. Tested by ``tests/test_transcriber_pure``.
"""
from __future__ import annotations

# Weight of each pyannote step within the 70-90% GUI progress band.
# Embeddings (ECAPA-TDNN per VAD chunk) dominates wall time, so it gets the
# largest sub-range. "startup" is a synthetic step the worker emits during
# subprocess cold start so the bar crawls forward instead of freezing at 70%
# for ~20s while Python/torch/pyannote import.
_DIARIZATION_STEP_RANGES = {
    "startup":              (0.00, 0.10),
    "segmentation":         (0.10, 0.25),
    "embeddings":           (0.25, 0.85),
    "discrete_diarization": (0.85, 1.00),
}


def _parse_progress_line(line: str) -> float | None:
    """
    Parse one ``PROGRESS\\t<step>\\t<completed>\\t<total>`` line from the worker.

    Returns the overall percent in the 70-90% range, or None if the line is
    malformed or refers to an unknown step (unknown steps are skipped so a
    future pyannote version with new stages can't accidentally jump the bar).
    """
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 4 or parts[0] != "PROGRESS":
        return None
    step = parts[1]
    if step not in _DIARIZATION_STEP_RANGES:
        return None
    try:
        completed = int(parts[2])
        total = int(parts[3])
    except ValueError:
        return None
    sub_start, sub_end = _DIARIZATION_STEP_RANGES[step]
    ratio = min(1.0, completed / total) if total > 0 else 0.0
    sub_percent = sub_start + (sub_end - sub_start) * ratio
    # Map 0..1 into the 70..90 GUI band, leaving 90..100 for post-processing.
    return 70.0 + 20.0 * sub_percent
