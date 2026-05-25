"""Provider-agnostic chunker for long audio uploads.

When a cloud STT provider has a small hard upload cap (Groq Free tier
= 25 MB, OpenAI whisper-1 = 25 MB), files larger than what fits even
after opus 32 kbps compression cannot be sent as a single request.
Typical pain point: user records a 2-5 hour meeting, file is 230 MB
WAV → 28 MB opus (still over 25 MB cap).

This module splits oversized audio at silence boundaries, uploads
each chunk via the chosen provider, and stitches the results back
with offset-corrected timestamps.

Public surface:

- :func:`needs_chunking` — predicate: does this (audio, provider)
  combination require splitting?
- :func:`transcribe_chunked` — full orchestrator: split → upload each
  → merge.

Internals (mockable, testable in isolation):

- :func:`_audio_duration` — read total duration in seconds.
- :func:`_find_silence_boundaries` — ffmpeg silencedetect → silences.
- :func:`_pick_split_points` — pure: pick best splits near targets.
- :func:`_extract_chunk` — ffmpeg slice + opus encode → tempfile.
- :func:`_merge_chunk_results` — pure: stitch + offset timestamps.
- :func:`_ensure_wav_for_chunking` — normalize input to WAV if needed.

Architectural notes:

- Diarization (when added by the hybrid path) runs on the FULL
  original audio, not per-chunk. Per-chunk diarization would reset
  speaker labels at every boundary. The chunker is text-only.
- Per-chunk prompt continuity: each chunk after the first receives
  ``options.hotwords`` augmented with the tail of the previous
  chunk's text. This is what OpenAI's Whisper docs recommend for
  cross-chunk accuracy on named entities and mid-sentence anaphora.
- Tempfiles are tracked in a list and cleaned in a single finally
  block regardless of success / cancel / error — no leaked disk.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import replace
from threading import Event

import soundfile as sf

from audio_io import ensure_wav
from providers.base import (
    ProviderError,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
)

# Target chunk length in seconds. 90 minutes = 5400s. At opus 32 kbps mono
# 16 kHz, one chunk holds ~21 MB worth — safely under the 25 MB cap with
# headroom for container overhead and slight bitrate variance.
_TARGET_CHUNK_SECONDS = 5400.0

# Tolerance window: how far from the target offset we'll look for a
# natural silence to split at. ±5 min keeps splits close to the target
# while making it likely we find a real pause.
_SILENCE_TOLERANCE_SECONDS = 300.0

# ffmpeg silencedetect parameters:
# - noise floor -30 dB: typical for room recordings (mic self-noise +
#   HVAC). Lower (more negative) misses real pauses; higher (less
#   negative) flags speech as silence.
# - min duration 0.5s: shorter than 500 ms is mid-word breathing, not
#   a structural pause. We want sentence-level breaks.
_SILENCE_NOISE_DB = "-30dB"
_SILENCE_MIN_DURATION = "0.5"

# Conservative trigger: a file 2× the cap raw definitely needs chunking
# even after opus 8× compression. Files just over the cap (1.0-2.0×) are
# borderline — opus might save them, but the chunker handles them
# uniformly via the same path. This threshold biases toward chunking
# whenever there's any real risk of exceeding the cap.
_CHUNK_TRIGGER_RATIO = 1.0

# Opus encoding for chunks. Same parameters as
# providers/groq.py::_shrink_for_upload — Whisper downsamples to 16 kHz
# internally, so 16k mono opus 32 kbps is transparent for speech.
_OPUS_BITRATE = "32k"
_OPUS_SAMPLE_RATE = "16000"

# Tail-of-previous-chunk length passed as Whisper prompt for continuity.
# 200 chars ≈ ~40 words, fits comfortably in Whisper's 224-token prompt
# budget while giving enough context for named-entity / topic anchoring.
_CONTINUITY_PROMPT_CHARS = 200


# ─────────────────────── public API ──────────────────────────────────


def needs_chunking(audio_path: str, provider: TranscriptionProvider) -> bool:
    """True when this audio + provider combination requires splitting.

    Logic:
    - If provider has no documented cap (``max_upload_bytes is None``)
      → never chunk based on provider cap.
    - If raw file size ≥ cap × _CHUNK_TRIGGER_RATIO → chunk.

    The trigger ratio is intentionally conservative: a file just over
    the cap might fit after opus compression alone, but the chunker
    handles all oversized files uniformly. Better to over-chunk than
    fail mid-upload on a borderline case.
    """
    cap = provider.max_upload_bytes
    if cap is None:
        return False
    size = os.path.getsize(audio_path)
    return size >= cap * _CHUNK_TRIGGER_RATIO


def transcribe_chunked(
    audio_path: str,
    provider: TranscriptionProvider,
    options: TranscriptionOptions,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: Event | None = None,
) -> TranscriptionResult:
    """Split audio at silence boundaries, transcribe each chunk via
    ``provider``, stitch results with offset-corrected timestamps.

    Tempfiles created for each chunk are tracked and cleaned in a
    single finally block. If a chunk transcription fails or is
    cancelled, prior chunks' results are discarded — partial-result
    recovery is intentionally out of scope (see plan).
    """
    from transcriber import TranscriptionCancelled  # late: cyclical safe

    def _check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelled()

    if on_status:
        on_status("Подготовка аудио для чанкинга...")
    _check_cancel()

    wav_path, wav_is_temp = _ensure_wav_for_chunking(audio_path)
    chunk_paths: list[str] = []
    try:
        if on_status:
            on_status("Анализ тишин для чанкинга...")
        _check_cancel()

        duration = _audio_duration(wav_path)
        silences = _find_silence_boundaries(wav_path)
        split_points = _pick_split_points(
            silences=silences,
            total_duration=duration,
            target_chunk_seconds=_TARGET_CHUNK_SECONDS,
            tolerance=_SILENCE_TOLERANCE_SECONDS,
        )
        # Convert split points into [start, end] ranges. Start of first
        # chunk = 0; end of last chunk = total duration.
        boundaries: list[tuple[float, float]] = []
        prev = 0.0
        for split in split_points:
            boundaries.append((prev, split))
            prev = split
        boundaries.append((prev, duration))

        total_chunks = len(boundaries)
        results: list[TranscriptionResult] = []
        offsets: list[float] = []

        for idx, (start, end) in enumerate(boundaries):
            _check_cancel()
            if on_status:
                on_status(f"Чанк {idx + 1}/{total_chunks} (загрузка)...")
            if on_progress:
                # Pre-chunk progress: linear in chunk index, leaving 5%
                # at the end for the merge step. Each chunk's own upload
                # progress would require provider-internal callbacks
                # that aren't part of the ABC — keep it simple.
                on_progress((idx / total_chunks) * 95.0)

            chunk_path = _extract_chunk(wav_path, start, end)
            chunk_paths.append(chunk_path)

            chunk_options = _build_chunk_options(
                base=options,
                prior_results=results,
                is_first_chunk=(idx == 0),
            )

            try:
                chunk_result = provider.transcribe(
                    chunk_path,
                    chunk_options,
                    on_status=None,  # chunk-level status handled here
                    on_progress=None,
                    cancel_event=cancel_event,
                )
            except TranscriptionCancelled:
                raise
            # Other provider errors propagate as-is to the caller; the
            # finally block below still cleans tempfiles.

            results.append(chunk_result)
            offsets.append(start)

        _check_cancel()
        if on_status:
            on_status("Объединение результатов...")
        if on_progress:
            on_progress(98.0)

        merged = _merge_chunk_results(results=results, offsets=offsets)

        if on_progress:
            on_progress(100.0)
        if on_status:
            on_status("Готово.")
        return merged

    finally:
        # Always clean every chunk tempfile, even on cancel/error.
        for p in chunk_paths:
            _safe_unlink(p)
        if wav_is_temp:
            _safe_unlink(wav_path)


# ─────────────────────── pure helpers ────────────────────────────────


def _pick_split_points(
    silences: list[tuple[float, float]],
    total_duration: float,
    target_chunk_seconds: float,
    tolerance: float,
) -> list[float]:
    """Pick chunk split offsets given detected silences + total duration.

    For each target offset (target_chunk_seconds, 2×target, 3×target, ...):
    - Look for silences whose midpoint lies within ±tolerance of the target.
    - If any qualify, pick the LONGEST one and use its midpoint as the
      split point.
    - If none qualify, fall back to a hard cut at the exact target.

    Returns a strictly-increasing list of split offsets (in seconds).
    Empty list means "no chunking needed" — caller treats the whole
    audio as one chunk.
    """
    if total_duration <= target_chunk_seconds:
        return []

    points: list[float] = []
    target = target_chunk_seconds
    while target < total_duration:
        # Candidate silences whose MIDPOINT falls in [target - tol, target + tol]
        # AND lies strictly after any previously picked split point (to keep
        # output monotonic).
        prior = points[-1] if points else 0.0
        candidates = [
            (s_start, s_end)
            for s_start, s_end in silences
            if (
                target - tolerance <= (s_start + s_end) / 2.0 <= target + tolerance
                and (s_start + s_end) / 2.0 > prior
            )
        ]
        if candidates:
            # Pick the LONGEST silence in the window — most likely a real
            # structural pause (paragraph break, speaker switch).
            chosen = max(candidates, key=lambda r: r[1] - r[0])
            split = (chosen[0] + chosen[1]) / 2.0
        else:
            # No usable silence — hard cut at the target offset.
            split = target
        points.append(split)
        target += target_chunk_seconds

    return points


def _merge_chunk_results(
    results: list[TranscriptionResult],
    offsets: list[float],
) -> TranscriptionResult:
    """Concatenate per-chunk results, shifting each chunk's timestamps
    by its audio offset. Picks the first non-None language. The raw
    payload becomes ``{"chunks": [...]}`` for debugging."""
    if len(results) != len(offsets):
        raise ValueError(
            f"results/offsets length mismatch: {len(results)} vs {len(offsets)}"
        )

    merged_segments: list[dict] = []
    merged_language: str | None = None
    raw_chunks: list[dict] = []

    for result, offset in zip(results, offsets, strict=True):
        if merged_language is None and result.language:
            merged_language = result.language
        for seg in result.segments:
            shifted: dict = {
                **seg,
                "start": seg["start"] + offset,
                "end": seg["end"] + offset,
            }
            words = seg.get("words")
            if isinstance(words, list) and words:
                shifted["words"] = [
                    {
                        **w,
                        "start": w["start"] + offset,
                        "end": w["end"] + offset,
                    }
                    for w in words
                ]
            merged_segments.append(shifted)
        raw_chunks.append({
            "offset": offset,
            "raw": result.raw,
        })

    return TranscriptionResult(
        segments=merged_segments,
        language=merged_language,
        raw={"chunks": raw_chunks},
    )


def _build_chunk_options(
    base: TranscriptionOptions,
    prior_results: list[TranscriptionResult],
    is_first_chunk: bool,
) -> TranscriptionOptions:
    """Build the per-chunk TranscriptionOptions. For chunks after the
    first, append a continuity prompt (tail of previous chunk's text)
    to the hotwords list — providers use hotwords as the Whisper
    prompt, so this gives chunk N context from chunk N-1.

    Why hotwords and not a dedicated prompt field: the
    TranscriptionOptions dataclass doesn't expose ``prompt`` directly.
    Providers (Groq, OpenAI Whisper) already concatenate hotwords into
    the prompt form field — see providers/groq.py:130-134 — so this
    piggybacks on existing wiring without an ABC change.
    """
    if is_first_chunk or not prior_results:
        return base

    last_text = " ".join(
        seg["text"] for seg in prior_results[-1].segments if seg.get("text")
    )
    if not last_text:
        return base

    # Tail of previous chunk → continuity hint. 200 chars ≈ 40 words,
    # fits comfortably under Whisper's 224-token prompt budget.
    continuity = last_text[-_CONTINUITY_PROMPT_CHARS:]
    augmented_hotwords = list(base.hotwords) + [continuity]
    return replace(base, hotwords=augmented_hotwords)


# ─────────────────────── I/O helpers ─────────────────────────────────


def _ensure_wav_for_chunking(audio_path: str) -> tuple[str, bool]:
    """Normalize input to a WAV file so we can probe duration and run
    silencedetect on it. Skips conversion when input is already WAV
    (and we don't need the loudness normalization — that's the local
    pipeline's concern, not ours).
    """
    if os.path.splitext(audio_path)[1].lower() == ".wav":
        return audio_path, False
    # For non-WAV input, route through ensure_wav with normalize=False
    # — we just want a readable WAV, not the EBU R128 loudness pass.
    return ensure_wav(audio_path, normalize=False)


def _audio_duration(wav_path: str) -> float:
    """Total duration in seconds. soundfile.info reads the WAV header
    without decoding the whole file."""
    info = sf.info(wav_path)
    return float(info.frames) / float(info.samplerate)


def _find_silence_boundaries(wav_path: str) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect on the entire file. Returns silence
    ranges as ``[(start_sec, end_sec), ...]``.

    Output format (ffmpeg writes silencedetect events to stderr):

        [silencedetect @ 0x...] silence_start: 89.524
        [silencedetect @ 0x...] silence_end: 92.137 | silence_duration: 2.613

    Both lines must appear for a complete silence range. We pair them
    in order. If the file ends during silence, ffmpeg may emit only the
    start — we drop the incomplete entry.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-i", wav_path,
        "-af", f"silencedetect=noise={_SILENCE_NOISE_DB}:d={_SILENCE_MIN_DURATION}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise ProviderError(
            "ffmpeg не найден в PATH — нужен для анализа тишин при "
            "чанкинге длинного аудио."
        ) from e

    if proc.returncode != 0:
        # ffmpeg with -f null - typically exits 0 even with -af warnings.
        # Non-zero usually means input file is unreadable.
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-300:]
        raise ProviderError(
            f"ffmpeg silencedetect завершился с кодом {proc.returncode}. "
            f"Аудио файл может быть повреждён.\n{stderr_tail}"
        )

    stderr = proc.stderr.decode("utf-8", errors="replace")
    starts = [
        float(m.group(1))
        for m in re.finditer(r"silence_start:\s*([\d.]+)", stderr)
    ]
    ends = [
        float(m.group(1))
        for m in re.finditer(r"silence_end:\s*([\d.]+)", stderr)
    ]
    # Pair start/end in order; drop incomplete trailing start (file
    # ended mid-silence).
    paired_count = min(len(starts), len(ends))
    return list(zip(starts[:paired_count], ends[:paired_count], strict=True))


def _extract_chunk(wav_path: str, start_sec: float, end_sec: float) -> str:
    """Slice ``wav_path`` from ``start_sec`` to ``end_sec`` and encode
    to opus 32 kbps mono 16 kHz in a tempfile. Returns the tempfile
    path. Caller cleans up.

    ffmpeg invocation:
      ffmpeg -ss <start> -to <end> -i <input> \\
             -c:a libopus -b:a 32k -ac 1 -ar 16000 <output.opus>
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".opus", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{start_sec:.3f}",
        "-to", f"{end_sec:.3f}",
        "-i", wav_path,
        "-c:a", "libopus",
        "-b:a", _OPUS_BITRATE,
        "-ac", "1",
        "-ar", _OPUS_SAMPLE_RATE,
        tmp.name,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        _safe_unlink(tmp.name)
        raise ProviderError(
            "ffmpeg не найден в PATH — нужен для нарезки аудио."
        ) from e
    except subprocess.CalledProcessError as e:
        _safe_unlink(tmp.name)
        stderr_tail = (e.stderr.decode("utf-8", errors="replace") if e.stderr else "")[-300:]
        raise ProviderError(
            f"ffmpeg не смог вырезать чанк [{start_sec:.1f}..{end_sec:.1f}s] "
            f"(код {e.returncode}).\n{stderr_tail}"
        ) from e
    return tmp.name


def _safe_unlink(path: str) -> None:
    """``os.unlink`` that swallows OSError. Used in finally / error
    paths where surfacing the cleanup failure would mask the real
    user-actionable error from the main flow."""
    try:
        os.unlink(path)
    except OSError:
        pass
