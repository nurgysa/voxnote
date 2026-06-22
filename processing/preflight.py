"""Pre-upload checks for the transcription queue.

Pure, cheap guards run before spending a (possibly multi-hour, paid) cloud
upload: probe the file's duration + size, reject over-cap files with a Russian
message, auto-disable denoise on long files (the denoise path forces a
multi-hundred-MB temp WAV — spec §Long-audio), and estimate STT cost.

Duration probing: soundfile reads WAV/FLAC/OGG headers cheaply; phone audio is
usually .m4a/.mp3, which soundfile can't read, so we fall back to parsing
``ffmpeg -i`` stderr. ffprobe is NOT bundled (utils.get_ffmpeg_path may even
return None) — both paths degrade to ``None`` duration, and callers size-gate
only. Catch classes stay narrow so this module adds no broad-except handlers.
"""
from __future__ import annotations

import os
import re
import subprocess

# ~2 GB upload body cap — documented for AssemblyAI / Speechmatics / Deepgram;
# applied uniformly (Gladia's real cap is tighter but unpublished, so a 2 GB
# gate is a safe conservative ceiling whose job is to catch the obvious
# "this 5 GB file will 413" case before an upload is attempted).
_SIZE_CAP_BYTES = 2 * 1024**3

# Denoise forces ensure_wav → a huge temp WAV + hours of ffmpeg on long audio.
_DENOISE_MAX_S = 45 * 60

# Rough $/hour WITH speaker diarization, from each provider module's header
# comment (providers/{assemblyai,deepgram,gladia,speechmatics}.py). Estimate
# only — for an at-enqueue cost hint, not billing.
_COST_PER_HOUR = {
    "AssemblyAI": 0.17,
    "Deepgram": 0.43,
    "Gladia": 0.61,
    "Speechmatics": 1.04,
}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_ffmpeg_duration(stderr: str) -> float | None:
    """Extract seconds from an ``ffmpeg -i`` stderr ``Duration: HH:MM:SS.ss``
    line. None when no duration line is present."""
    m = _DURATION_RE.search(stderr)
    if not m:
        return None
    hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _duration_via_soundfile(audio_path: str) -> float | None:
    """Duration via soundfile header (WAV/FLAC/OGG). None on any read failure
    (e.g. .m4a/.mp3, which soundfile can't decode)."""
    import soundfile

    try:
        from audio_io import get_duration_s

        return get_duration_s(audio_path)
    except (RuntimeError, OSError, soundfile.SoundFileError):
        # soundfile.SoundFileError is the base for libsndfile decode failures.
        # LibsndfileError already subclasses RuntimeError, but catching the base
        # honors the "any soundfile probe failure → fall back to ffmpeg" contract.
        return None


def _duration_via_ffmpeg(audio_path: str) -> float | None:
    """Duration by parsing ``ffmpeg -i`` stderr. None when ffmpeg is absent or
    the output has no Duration line."""
    from utils import get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", audio_path],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Probe runs on the worker thread before anything else, and inbox files
        # live on a Google Drive-synced path — a stalled mount must not hang the
        # queue. A header read is sub-second; 30 s is a generous ceiling.
        return None
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    return _parse_ffmpeg_duration(stderr)


def probe(audio_path: str) -> dict:
    """Return ``{"duration_s": float | None, "size_bytes": int}``.

    Size from the filesystem (0 if unreadable). Duration tries soundfile first,
    then the ffmpeg-stderr fallback; ``None`` when both fail.
    """
    try:
        size_bytes = os.path.getsize(audio_path)
    except OSError:
        size_bytes = 0
    duration_s = _duration_via_soundfile(audio_path)
    if duration_s is None:
        duration_s = _duration_via_ffmpeg(audio_path)
    return {"duration_s": duration_s, "size_bytes": size_bytes}


def provider_limit_ok(
    provider: str, duration_s: float | None, size_bytes: int
) -> tuple[bool, str]:
    """``(ok, reason)``. False with a Russian message when the file exceeds the
    provider's upload cap. An unreadable file (``size_bytes == 0``) passes —
    we can't reject what we couldn't measure. ``duration_s`` is reserved for
    future per-provider duration caps; the live gate is size."""
    if size_bytes and size_bytes > _SIZE_CAP_BYTES:
        gb = size_bytes / 1024**3
        return (
            False,
            f"Файл {gb:.1f} ГБ превышает лимит провайдера {provider} (~2 ГБ). "
            f"Сократи запись или сожми аудио и попробуй снова.",
        )
    return True, ""


def should_denoise(duration_s: float | None, requested: bool) -> bool:
    """Honor the user's denoise request, but force it off above the long-audio
    threshold (the denoise path is too heavy there). Unknown duration → honor
    the request."""
    if not requested:
        return False
    if duration_s is not None and duration_s > _DENOISE_MAX_S:
        return False
    return True


def estimate_cost(provider: str, duration_s: float | None) -> float | None:
    """Rough STT cost in USD for ``duration_s`` at ``provider``'s with-diarization
    rate. None when the duration is unknown or the provider isn't in the table."""
    if duration_s is None:
        return None
    rate = _COST_PER_HOUR.get(provider)
    if rate is None:
        return None
    return rate * (duration_s / 3600.0)


def cost_hint_suffix(provider: str, duration_s: float | None) -> str:
    """' · ~$X.XX' for an at-enqueue status-line hint, or '' when the cost is
    unknown (duration unmeasurable or provider not in the rate table)."""
    cost = estimate_cost(provider, duration_s)
    if cost is None:
        return ""
    return f" · ~${cost:.2f}"
