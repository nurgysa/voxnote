"""ffmpeg-only helpers for two distinct temporary-derivative concerns.

**Cap-driven derivatives** (``compress_for_size_cap``/``split_for_size_cap``):
some cloud STT providers enforce a hard per-request upload-size ceiling
(Groq's free tier: 25 MiB — see ``providers.groq.GroqProvider.max_upload_bytes``).
These produce a temporary, speech-optimized re-encode under that ceiling:
either one file, or — when a single file can't hit the ceiling without
dropping the bitrate below an ASR-usable floor — a sequence of
trimmed+compressed chunks with their ``[start, end)`` offsets in the
ORIGINAL timeline.

**Quiet-rescue derivative** (``measure_volume_stats`` +
``should_rescue_quiet_audio`` + ``prepare_quiet_audio_derivative``): source
audio too quiet for cloud ASR VAD to find usable speech gets a conservative
loudness-normalized FLAC derivative instead. This is orthogonal to the
cap-driven path — it runs first, independent of upload size — and uses a
different filter chain (no forced resample/downmix, no highpass, a more
conservative loudnorm target).

The source file passed in is NEVER modified, moved, or deleted here — every
function returns brand-new temp file(s) that the CALLER owns and must clean
up (``cleanup_paths`` is provided for that). This module has no HTTP and no
provider-specific knowledge; any future provider that sets
``max_upload_bytes`` can reuse the cap-driven helpers unchanged.
"""
from __future__ import annotations

import os
import re
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

# EBU R128 single-pass loudness normalization for the CAP-DRIVEN derivative
# (compress_for_size_cap / split_for_size_cap) — a technical re-encode whose
# job is fitting under a provider's byte ceiling, not rescuing quiet speech.
# -16 LUFS integrated / 11 LU loudness range / -1.5 dBTP true-peak ceiling is
# a reasonable default for that re-encode. Single-pass (not the two-pass
# measure-then-normalize variant) is deliberately used: it's a temporary
# preprocessing derivative, not a mastering step, and single-pass avoids a
# second ffmpeg invocation. Not to be confused with
# ``_QUIET_RESCUE_LOUDNORM_FILTER`` below, which is a separate, more
# conservative derivative applied only to clearly-too-quiet source audio.
_LOUDNORM_FILTER = "loudnorm=I=-16:LRA=11:TP=-1.5"

# EBU R128 loudness normalization for the QUIET-RESCUE derivative
# (prepare_quiet_audio_derivative) — applied only to source audio too quiet
# for cloud ASR VAD to find usable speech. -18 LUFS is a conservative
# VoxNote rescue default pending an A/B evaluation against alternatives; it
# is NOT a claim that -18 LUFS is universally ASR-optimal.
_QUIET_RESCUE_LOUDNORM_FILTER = "loudnorm=I=-18:LRA=11:TP=-1.5"

# Upper mean-volume boundary of the quiet-rescue decision band — see
# ``should_rescue_quiet_audio``. Files at or above this mean volume are left
# untouched outright; files below it are evaluated against the mean/max
# policy in ``should_rescue_quiet_audio``.
QUIET_MEAN_VOLUME_DB_THRESHOLD = -40.0

# Below this mean volume, source audio is rescued unconditionally —
# regardless of max_volume — because it's too consistently quiet for a
# transient-based exception to matter.
_QUIET_RESCUE_MEAN_FLOOR_DB = -45.0

# In the transition band (mean floor < mean <= threshold), only rescue when
# there's no loud transient (max_volume) already present — a loud max
# alongside a quiet mean usually means normal speech with silence gaps, not
# uniformly-too-quiet audio, so boosting it risks clipping the transients.
_QUIET_RESCUE_MAX_VOLUME_GUARD_DB = -6.0

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


_AUDIO_SAMPLE_RATE_RE = re.compile(r"Audio:.*?(\d+)\s*Hz", flags=re.IGNORECASE)


def _source_sample_rate_hz(audio_path: str) -> int:
    """Input sample rate parsed from ffmpeg stream metadata.

    ``loudnorm`` may dynamically upsample audio to 192 kHz for true-peak
    analysis. Quiet-rescue FLAC must restore the source rate explicitly, so
    normal cloud-provider inputs retain their native rate after the filter.
    """
    ffmpeg = _require_ffmpeg()
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", audio_path],
            capture_output=True,
            check=False,
        )
    except OSError as e:
        raise AudioPrepError(f"Не удалось определить частоту аудио: {e}") from e
    stderr = result.stderr.decode("utf-8", errors="replace")
    match = _AUDIO_SAMPLE_RATE_RE.search(stderr)
    if match is None:
        raise AudioPrepError(
            "Не удалось определить исходную частоту аудио для временной подготовки."
        )
    return int(match.group(1))


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
        "-af", _LOUDNORM_FILTER,
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
            "-af", _LOUDNORM_FILTER,
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


_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB")


def measure_volume_stats(audio_path: str) -> tuple[float, float]:
    """``(mean_volume_db, max_volume_db)`` of ``audio_path``, via ffmpeg's
    ``volumedetect`` — the provider-neutral pair the quiet-rescue gate
    (``should_rescue_quiet_audio``) needs to tell "uniformly quiet" apart
    from "quiet on average but with loud transients".

    Raises AudioPrepError if ffmpeg is missing, the probe fails, or its
    output doesn't contain parseable ``mean_volume``/``max_volume`` lines.
    """
    ffmpeg = _require_ffmpeg()
    devnull = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [ffmpeg, "-i", audio_path, "-af", "volumedetect", "-f", "null", devnull]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        stderr = (
            e.stderr.decode("utf-8", errors="replace")
            if isinstance(e, subprocess.CalledProcessError) and e.stderr
            else str(e)
        )
        raise AudioPrepError(
            f"Не удалось измерить громкость аудио: {stderr[-500:]}"
        ) from e
    stderr = result.stderr.decode("utf-8", errors="replace")
    mean_match = _MEAN_VOLUME_RE.search(stderr)
    max_match = _MAX_VOLUME_RE.search(stderr)
    if mean_match is None or max_match is None:
        raise AudioPrepError(
            "Не удалось измерить громкость аудио: ffmpeg не вернул "
            "mean_volume/max_volume."
        )
    # Most ffmpeg builds floor true digital silence at a finite noise-floor
    # value (e.g. -91.0 dB for 16-bit PCM), but some report the literal
    # "-inf dB" — treat that as a real, comparable float rather than an
    # unparseable value; ``should_rescue_quiet_audio`` explicitly special-
    # cases it so pure silence never gets an opaque parse error.
    return float(mean_match.group(1)), float(max_match.group(1))


def measure_mean_volume_db(audio_path: str) -> float:
    """Mean loudness of ``audio_path`` in dB — thin wrapper over
    ``measure_volume_stats`` kept for callers that only need the mean.
    """
    mean_db, _max_db = measure_volume_stats(audio_path)
    return mean_db


def should_rescue_quiet_audio(mean_db: float, max_db: float) -> bool:
    """Adaptive quiet-rescue decision from ``measure_volume_stats``' output.

    Policy:
    - ``mean_db <= -45.0`` -> rescue unconditionally (uniformly too quiet).
    - ``-45.0 < mean_db <= -40.0`` -> rescue only when ``max_db <= -6.0``
      (loud transients alongside a quiet mean usually mean normal speech
      with silence gaps, not audio that needs boosting).
    - ``mean_db > -40.0`` -> leave the original untouched.
    - Pure digital silence (``-inf`` on either metric) never auto-rescues —
      there is no signal to boost, and boosting a noise floor risks
      amplifying artifacts for no ASR benefit.
    """
    if mean_db == float("-inf") or max_db == float("-inf"):
        return False
    if mean_db <= _QUIET_RESCUE_MEAN_FLOOR_DB:
        return True
    if mean_db <= QUIET_MEAN_VOLUME_DB_THRESHOLD:
        return max_db <= _QUIET_RESCUE_MAX_VOLUME_GUARD_DB
    return False


def prepare_quiet_audio_derivative(audio_path: str) -> str:
    """Temp lossless FLAC derivative of ``audio_path`` with a conservative
    EBU R128 loudnorm pass, for quiet source audio that would otherwise
    reach a cloud STT provider too faint to transcribe.

    Deliberately distinct from the CAP-DRIVEN mp3 derivative
    (``compress_for_size_cap``/``split_for_size_cap``): this is a
    quality-preserving rescue, not a byte-budget re-encode, so it keeps the
    source sample rate and channel count (no ``-ar``/``-ac``) and applies
    no highpass filter by default. Returns the temp file path — the caller
    owns it and must delete it (this function never touches ``audio_path``
    itself). Raises AudioPrepError if ffmpeg is missing or the encode
    fails; any partial output is removed before raising.
    """
    ffmpeg = _require_ffmpeg()
    source_sample_rate_hz = _source_sample_rate_hz(audio_path)
    tmp = tempfile.NamedTemporaryFile(suffix=".flac", delete=False)
    tmp.close()
    cmd = [
        ffmpeg, "-v", "error", "-y", "-i", audio_path,
        "-af", _QUIET_RESCUE_LOUDNORM_FILTER,
        "-ar", str(source_sample_rate_hz),
        "-c:a", "flac",
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
            f"Не удалось подготовить тихое аудио для отправки: {stderr[-500:]}"
        ) from e
    return tmp.name


def cleanup_paths(paths: Iterable[str]) -> None:
    """Best-effort unlink of every path — missing files are ignored."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass
