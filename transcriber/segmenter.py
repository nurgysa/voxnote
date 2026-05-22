"""VAD pre-pass for the Phase 2 mixed-language code path.

Wraps faster_whisper.vad.get_speech_timestamps with parameters tuned
for language detection (longer minimum speech duration than
silence_remover.py because Whisper's internal detect_language needs
~0.5s+ of audio to be reliable).

Used by transcriber.Transcriber.transcribe() when language == "mixed":
each chunk is split into speech regions here, then each region is fed
to model.transcribe(language=None, ...) separately so Whisper's
internal language detection runs per region instead of once per file.

Pure module — no I/O, no GPU. Tested via tests/test_segmenter.py.
"""
from __future__ import annotations

import numpy as np

# VAD parameters tuned for language detection (NOT silence removal).
# Differs from silence_remover.py's defaults in two ways:
#   - min_speech_duration_ms=500 (vs 250): Whisper's detect_language
#     needs roughly half a second of audio to lock onto a language;
#     shorter speech blips lead to high-variance detection.
#   - speech_pad_ms=100 (vs 200): we don't need word-ending padding
#     here because each segment will be re-transcribed independently
#     and Whisper handles its own boundary handling internally.
_VAD_THRESHOLD = 0.5
_MIN_SPEECH_MS = 500
_MIN_SILENCE_MS = 500
_SPEECH_PAD_MS = 100


def vad_split(samples: np.ndarray, sample_rate: int) -> list[dict]:
    """Detect speech in ``samples`` and return per-region sample-index ranges.

    Args:
        samples: 1-D float32 mono audio, values in [-1, 1].
        sample_rate: Sample rate of ``samples``, typically 16_000.

    Returns:
        List of ``{"start": int, "end": int}`` dicts where start/end are
        sample indices into ``samples`` (inclusive start, exclusive end —
        matches faster_whisper.vad.get_speech_timestamps's contract).
        Empty list if no speech detected or input is empty.
    """
    if samples is None or len(samples) == 0:
        return []

    # Lazy import — same pattern as silence_remover.py. Keeps cold-start
    # cost out of test collection and out of callers that only need the
    # module's symbols for type hints.
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    vad_options = VadOptions(
        threshold=_VAD_THRESHOLD,
        min_speech_duration_ms=_MIN_SPEECH_MS,
        min_silence_duration_ms=_MIN_SILENCE_MS,
        speech_pad_ms=_SPEECH_PAD_MS,
    )
    return list(get_speech_timestamps(samples, vad_options))
