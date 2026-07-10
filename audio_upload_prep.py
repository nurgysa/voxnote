"""ffmpeg-only helpers to keep a single upload under a provider's hard cap.

Some cloud STT providers enforce a hard per-request upload-size ceiling
(Groq's free tier: 25 MiB — see ``providers.groq.GroqProvider.max_upload_bytes``).
This module produces a temporary, speech-optimized derivative under that
ceiling: either one re-encoded file, or — when a single file can't hit the
ceiling without dropping the bitrate below an ASR-usable floor — a sequence
of trimmed+compressed chunks with their ``[start, end)`` offsets in the
ORIGINAL timeline.

The source file passed in is NEVER modified, moved, or deleted here — every
function returns brand-new temp file(s) that the CALLER owns and must clean
up (``cleanup_paths`` is provided for that). This module has no HTTP and no
provider-specific knowledge; any future provider that sets
``max_upload_bytes`` can reuse it unchanged.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable

from utils import get_ffmpeg_path

# Speech-only mono compression range. These are ASR inputs, not for human
# listening — Whisper-family models tolerate quite aggressive compression,
# and mono 16 kHz output above ~24 kbps preserves the phonetic content that
# matters for transcription. 64 kbps is a practical ceiling: short/dense
# recordings don't need more and it keeps the encode fast.
_MIN_BITRATE_BPS = 24_000
_MAX_BITRATE_BPS = 64_000
_SAMPLE_RATE_HZ = 16_000

# Target below the hard cap, not at it: CBR mp3 size is close to
# bitrate*duration/8 but container/frame overhead adds a small, hard-to-
# predict slack. 8% headroom comfortably absorbs that without meaningfully
# hurting quality (it shifts the chosen bitrate down by the same 8%).
_SAFETY_MARGIN = 0.92


class AudioPrepError(RuntimeError):
    """ffmpeg is unavailable, or a compress/chunk pass failed.

    Message is Russian and user-actionable; ``transcriber._run_cloud_stt``
    re-wraps this as a ``providers.ProviderError`` so it surfaces through
    the existing UI error-dialog contract.
    """


def target_bytes_for_cap(max_bytes: int) -> int:
    """Byte budget to aim for, leaving headroom below the hard ``max_bytes``
    cap for CBR encoder/container overhead (see ``_SAFETY_MARGIN``)."""
    return int(max_bytes * _SAFETY_MARGIN)


def _bitrate_for_target(duration_s: float, target_bytes: int) -> int | None:
    """Bitrate (bps) that fits ``duration_s`` seconds of mono audio into
    ``target_bytes``, clamped to ``[_MIN_BITRATE_BPS, _MAX_BITRATE_BPS]``.

    Returns None when even the floor bitrate would exceed ``target_bytes``
    — the recording is too long for a single compressed file; the caller
    must chunk instead via ``split_for_size_cap``.
    """
    if duration_s <= 0:
        return _MAX_BITRATE_BPS
    ideal = int((target_bytes * 8) / duration_s)
    if ideal < _MIN_BITRATE_BPS:
        return None
    return max(_MIN_BITRATE_BPS, min(_MAX_BITRATE_BPS, ideal))


def _require_ffmpeg() -> str:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise AudioPrepError(
            "ffmpeg не найден — не удалось подготовить длинную запись для "
            "лимита загрузки Groq (25 МиБ). Установи ffmpeg или выбери "
            "другого провайдера в Настройках → Облако."
        )
    return ffmpeg


def compress_for_size_cap(
    audio_path: str, duration_s: float, target_bytes: int,
) -> tuple[str, bool] | None:
    """Single-file speech-optimized re-encode aimed under ``target_bytes``.

    Returns ``(temp_mp3_path, True)`` on success, or ``None`` when
    ``duration_s`` is too long to hit ``target_bytes`` at a usable bitrate
    (caller should chunk instead). ffmpeg is only invoked once a viable
    bitrate is known — a too-long recording never spawns a doomed ffmpeg
    call.

    Raises AudioPrepError if ffmpeg is missing or the encode fails; the
    partial output (if any) is removed before raising.
    """
    bitrate = _bitrate_for_target(duration_s, target_bytes)
    if bitrate is None:
        return None
    ffmpeg = _require_ffmpeg()
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    cmd = [
        ffmpeg, "-v", "error", "-y", "-i", audio_path,
        "-ar", str(_SAMPLE_RATE_HZ), "-ac", "1",
        "-c:a", "libmp3lame", "-b:a", f"{bitrate // 1000}k",
        tmp.name,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        stderr = (
            e.stderr.decode("utf-8", errors="replace")
            if isinstance(e, subprocess.CalledProcessError) and e.stderr
            else str(e)
        )
        raise AudioPrepError(
            f"Не удалось сжать аудио для лимита загрузки Groq (25 МиБ): "
            f"{stderr[-500:]}"
        ) from e
    return tmp.name, True


def split_for_size_cap(
    audio_path: str, duration_s: float, target_bytes: int,
) -> list[tuple[str, float, float]]:
    """Trim + compress ``audio_path`` into consecutive chunks that each fit
    under ``target_bytes`` at the ASR-usable floor bitrate.

    Returns ``[(chunk_temp_path, start_s, end_s), ...]`` in original-
    timeline order; every ``chunk_temp_path`` is a temp file the caller
    owns. Chunks are NOT overlapped — a word that straddles a cut point may
    be split between two chunks, an accepted limitation of splitting long
    audio for ASR (the alternative, overlap+dedup reconciliation, is out of
    scope here).

    Raises AudioPrepError if ffmpeg is missing or any chunk encode fails —
    on a mid-loop failure, every chunk produced so far is cleaned up before
    raising (no orphaned temp files).
    """
    ffmpeg = _require_ffmpeg()
    chunk_duration_s = (target_bytes * 8) / _MIN_BITRATE_BPS
    if chunk_duration_s <= 0:
        chunk_duration_s = duration_s or 1.0

    chunks: list[tuple[str, float, float]] = []
    start = 0.0
    while start < duration_s:
        end = min(start + chunk_duration_s, duration_s)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        cmd = [
            ffmpeg, "-v", "error", "-y", "-i", audio_path,
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-ar", str(_SAMPLE_RATE_HZ), "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", f"{_MIN_BITRATE_BPS // 1000}k",
            tmp.name,
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            cleanup_paths(p for p, _s, _e in chunks)
            stderr = (
                e.stderr.decode("utf-8", errors="replace")
                if isinstance(e, subprocess.CalledProcessError) and e.stderr
                else str(e)
            )
            raise AudioPrepError(
                f"Не удалось нарезать длинную запись для лимита загрузки "
                f"Groq (25 МиБ): {stderr[-500:]}"
            ) from e
        chunks.append((tmp.name, start, end))
        start = end
    return chunks


def cleanup_paths(paths: Iterable[str]) -> None:
    """Best-effort unlink of every path — missing files are ignored."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass
