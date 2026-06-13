"""Torch-free audio I/O helpers shared across the transcription pipeline.

Deliberately dependency-light: only os/subprocess/tempfile plus numpy and
soundfile. It MUST NOT import torch, ctranslate2, faster_whisper, or
pyannote — those are gone from the cloud-only build (invariant #2), and
re-adding them would drag CUDA DLLs into the import order, which breaks
ctranslate2 on Windows (see memory/windows_ctranslate2_order.md). Shared by
transcriber/, recorder, and audio_cutter.
"""

import os
import subprocess
import tempfile
import urllib.error
import urllib.request

import numpy as np
import soundfile as sf

from utils import get_ffmpeg_path

# Single source of truth for the speech pipeline's sample rate.
# Whisper, Silero VAD, and pyannote all expect 16 kHz — changing this is
# not a normal configuration knob, it's a "rewrite the pipeline" decision.
SAMPLE_RATE = 16_000

# RNNoise model URL. The GregorR/rnnoise-models repo hosts several
# community-trained models in the .rnnn binary format that ffmpeg's
# arnndn filter expects. somnolent-hogwash is a general-purpose model
# widely cited for voice cleanup (referenced in Discord/OBS plugin
# examples). License: GPL-3 per repo LICENSE — lazy-downloaded on first
# use so the binary doesn't sit in this repo, keeping the rest of our
# code license-unencumbered.
_RNNOISE_MODEL_URL = (
    "https://github.com/GregorR/rnnoise-models/raw/master/"
    "somnolent-hogwash-2018-09-01/sh.rnnn"
)
_RNNOISE_MODEL_BASENAME = "sh.rnnn"


def _escape_ffmpeg_filter_path(path: str) -> str:
    """Escape a filesystem path for safe use as an ffmpeg filter argument.

    ffmpeg's filtergraph parser runs TWO escape passes on the same
    string before reaching the filter-argument layer (see
    https://ffmpeg.org/ffmpeg-filters.html#Notes-on-filtergraph-escaping):

    1. First level — filter option value parsing. Special chars: ``\\``,
       ``'``, ``:``. To pass a literal ``:`` through this layer you
       write ``\\:``.
    2. Second level — whole filter description parsing. Special chars:
       ``\\``, ``'``, ``[``, ``]``, ``,``, ``;``. To pass a literal
       ``\\`` through this layer you write ``\\\\``.

    Both passes apply, so to embed a literal ``:`` (drive-letter colon
    on Windows) inside a filter value, the source string must contain
    ``\\\\:`` (4 chars: backslash, backslash, colon, which Python
    source ``r"\\\\:"`` produces as 3 output chars ``\\``, ``\\``, ``:``).
    Second-level unescapes ``\\\\`` → ``\\``, leaving ``\\:`` for the
    first-level layer, which unescapes to ``:``.

    Plus convert backslashes to forward slashes (ffmpeg accepts both
    on Windows for file paths) — keeps the path readable and avoids
    backslash-as-escape collision in the rest of the path.

    No-op on Unix paths (no backslashes, no colons), so cross-platform
    safe.

    History:
    - PR #56 introduced raw paths in arnndn=m=<path> — broken on
      Windows immediately.
    - PR #57 added ``\\:`` (single backslash) escape — Codex flagged
      the original raw path but my fix only handled ONE of the two
      escape levels. Real Windows users still saw "No option name
      near '/Users/...'" because ffmpeg consumed the single backslash
      at the second-level pass, leaving ``:`` as a literal separator
      at the first-level pass.
    - This commit: ``\\\\:`` (two backslashes) survives both passes.
      Verified manually against ffmpeg 6 on Windows by the test
      ``test_ensure_wav_denoise_with_real_ffmpeg`` which is gated on
      ``_FFMPEG_AVAILABLE`` AND model-cache presence.
    """
    # Forward slashes first so the colon-escape pass sees a normalized
    # path. Order matters for the colon-escape: we don't want to
    # accidentally escape a backslash-colon combination already present
    # in the source. r"\\:" in Python source is 3 chars: \, \, :.
    return path.replace("\\", "/").replace(":", r"\\:")


def _get_rnnoise_model_path() -> str:
    """Path to the RNNoise .rnnn model file for ffmpeg's ``arnndn`` filter.

    Lazy-downloads from ``_RNNOISE_MODEL_URL`` on first call and caches in
    ``~/.voxnote/models/rnnoise/sh.rnnn``. Returns the cached path
    on subsequent calls — one-time ~85 KB download per machine.

    Why lazy and not bundled in the repo: the model is GPL-3 (per
    GregorR/rnnoise-models LICENSE). Bundling would create a license-virality
    surface for the rest of this codebase; downloading on first use keeps
    the binary out of the repo while still giving the user a one-click
    experience (no manual install steps).

    Cache directory matches the existing convention used by
    ``gdrive/auth.py`` for its OAuth token cache:
    ``~/.voxnote/<subsystem>/``.

    Raises:
        RuntimeError: network failure, disk full, or any other write
            problem. Message is Russian-actionable so the user can recover
            via Settings (disable denoising) or by retrying once network
            is back. The partial file (if any) is removed before raising.
    """
    cache_dir = os.path.join(
        os.path.expanduser("~"),
        ".voxnote", "models", "rnnoise",
    )
    cache_path = os.path.join(cache_dir, _RNNOISE_MODEL_BASENAME)
    if os.path.isfile(cache_path):
        return cache_path

    try:
        os.makedirs(cache_dir, exist_ok=True)
        with urllib.request.urlopen(_RNNOISE_MODEL_URL, timeout=30) as resp:
            data = resp.read()
        # Write to a temp file in the same dir then atomically rename, so a
        # crash mid-write doesn't leave a half-cached file that later passes
        # the os.path.isfile check but breaks ffmpeg with cryptic errors.
        tmp_path = cache_path + ".partial"
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, cache_path)
    except (OSError, urllib.error.URLError) as e:
        # Best-effort cleanup of the .partial file before raising.
        for path in (cache_path + ".partial", cache_path):
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        raise RuntimeError(
            f"Не удалось загрузить RNNoise модель для подавления шума: {e}. "
            f"Отключи «Подавлять шум» в Настройках или проверь сетевое "
            f"соединение и попробуй снова."
        ) from e

    return cache_path


def ensure_wav(
    audio_path: str,
    normalize: bool = True,
    denoise: bool = False,
) -> tuple[str, bool]:
    """Ensure ``audio_path`` points at a 16 kHz mono WAV readable end-to-end.

    Returns ``(path, is_temp)``. If ``is_temp`` is True, the caller owns the
    file and must delete it when done.

    Args:
        audio_path: source audio (any format ffmpeg can decode).
        normalize: if True (default), apply EBU R128 loudness normalization
            after the (always-on) highpass. Pass False for code paths
            that need the raw waveform — e.g. silence-based cutting, where
            normalization would invalidate the threshold.
        denoise: if True, insert an ``arnndn`` (RNNoise) denoise stage
            between the highpass and loudnorm. Default False — opt-in
            because RNNoise can over-aggressively cut consonants on
            already-clean recordings. The .rnnn model is lazy-downloaded
            on first use via ``_get_rnnoise_model_path``.

    Why this exists at all: faster-whisper decodes audio via pyav (FFmpeg
    bindings). Some MP3 files with broken headers cause pyav to silently
    truncate — a 62-minute MP3 was once decoded as only 744 seconds, making
    Whisper transcribe just the first ~12 minutes. The ffmpeg CLI is more
    tolerant: it logs bad frames and continues, producing a full-length
    WAV. So for non-WAV inputs we route through ffmpeg first.

    Why normalization: speech ASR quality is a function of signal-level
    consistency across the file. Meetings/phone calls typically have quiet
    and loud speakers in the same recording; Whisper handles the loud ones
    better and under-transcribes the quiet ones. EBU R128 loudness
    normalization equalizes them to a target integrated loudness, which
    empirically cuts WER on mixed-speaker recordings. A highpass at 80 Hz
    additionally strips AC hum, mic rumble, and table thumps below speech
    fundamentals (~85 Hz) — effectively free.

    Why optional denoising: RNNoise (Mozilla/Xiph) is a neural denoiser
    trained on hundreds of hours of speech-vs-noise pairs. On recordings
    with keyboard clicks, fan hum, paper rustling, or breath noise on a
    close mic, it gives a clear quality lift for downstream Whisper WER.
    On already-clean studio-style recordings it can introduce subtle
    artifacts (clipped consonants, "musical noise") that slightly hurt
    quality. Hence opt-in via Settings.

    Filter chain ordering (any combination is supported):
      * ``highpass=f=80`` — always on; eliminates sub-speech rumble before
        downstream stages can waste cycles on it.
      * ``arnndn=m=<model_path>`` — only when ``denoise=True``. Runs on
        the high-passed signal so the noise model isn't confused by
        infrasonic energy.
      * ``loudnorm=I=-16:TP=-1.5:LRA=11`` — only when ``normalize=True``.
        Runs LAST so its measurement reflects the cleaned signal, not
        the noise floor. Single-pass: faster than the two-pass mastering
        form and quality-equivalent for speech.

    Short-circuit: when input is already ``.wav`` AND ``normalize=False``
    AND ``denoise=False``, returns the input path as-is (no ffmpeg
    invocation, no copy). Any non-default flag forces a re-encode.

    Raises ``RuntimeError`` with the tail of ffmpeg's stderr if conversion
    fails, or a Russian-actionable message if the RNNoise model download
    fails on first ``denoise=True`` call. The temp file (if any) is
    cleaned up before raising.
    """
    is_wav = os.path.splitext(audio_path)[1].lower() == ".wav"
    # Short-circuit only when nothing needs to happen: input is already a
    # WAV AND the caller opted out of all filtering. Any other combination
    # — normalize, denoise, or non-WAV input — requires an ffmpeg pass.
    if is_wav and not normalize and not denoise:
        return audio_path, False

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    cmd: list[str] = [
        get_ffmpeg_path(), "-v", "error", "-y", "-i", audio_path,
    ]
    # Build the filter chain in the documented order. highpass is always
    # present because (a) it's effectively free, (b) it improves both
    # downstream stages, and (c) it preserves existing behavior for callers
    # that pass only normalize=True.
    filters: list[str] = ["highpass=f=80"]
    if denoise:
        # Model fetch happens here, not at module load — so users who
        # never enable denoising never pay the download cost. Path is
        # escaped for ffmpeg filtergraph syntax — see
        # _escape_ffmpeg_filter_path for the why (Windows paths with
        # `:` and `\` break filter parsing without escaping).
        model_path = _escape_ffmpeg_filter_path(_get_rnnoise_model_path())
        filters.append(f"arnndn=m={model_path}")
    if normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    cmd += ["-af", ",".join(filters)]
    cmd += ["-ar", str(SAMPLE_RATE), "-ac", "1", tmp.name]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        os.unlink(tmp.name)
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise RuntimeError(
            f"ffmpeg failed to decode {audio_path} (exit {e.returncode}):\n"
            f"{stderr[-1000:]}"
        ) from e
    return tmp.name, True


def load_mono_float32(audio_path: str) -> tuple[np.ndarray, int]:
    """Load an audio file as a 1-D float32 mono numpy array + sample rate.

    For WAV inputs, reads directly via soundfile. For other formats, routes
    through ffmpeg to a temporary 16 kHz mono WAV first (the temp file is
    deleted before returning).

    Returns ``(samples, sample_rate)`` where ``samples.ndim == 1``. Stereo
    or multichannel sources are downmixed by channel-averaging.

    Uses ``normalize=False``: callers of this helper (silence detection,
    manual cutting) need the raw waveform. Loudness normalization would
    rescale the RMS/peak levels and invalidate any amplitude-based silence
    threshold downstream.
    """
    wav_path, is_temp = ensure_wav(audio_path, normalize=False)
    try:
        data, sample_rate = sf.read(wav_path, dtype="float32")
    finally:
        if is_temp:
            try:
                os.unlink(wav_path)
            except OSError:
                pass  # best-effort cleanup — ensure_wav owns the temp file

    # Downmix to mono if we got a multichannel WAV (e.g. a stereo WAV passed
    # directly without going through ensure_wav's ffmpeg pass, which would
    # already have forced -ac 1).
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.float32, copy=False)
    return data, int(sample_rate)


def ffmpeg_trim(src: str, start_sec: float, end_sec: float, dst: str) -> None:
    """Trim ``[start_sec, end_sec]`` out of ``src`` into ``dst``.

    First tries stream-copy (``-c copy``) which is fast and lossless but
    only works when the cut points align with keyframes and the container
    supports it. If that fails, falls back to re-encoding.

    Raises ``subprocess.CalledProcessError`` if both paths fail. On a double
    failure the partial ``dst`` (whatever the copy pass wrote) is removed
    before re-raising, so a caller can never mistake a half-written file for a
    successful trim. ffmpeg output is captured (not streamed to the terminal).
    """
    start_str = _ffmpeg_time(start_sec)
    end_str = _ffmpeg_time(end_sec)
    try:
        subprocess.run(
            [get_ffmpeg_path(), "-y", "-i", src,
             "-ss", start_str, "-to", end_str,
             "-c", "copy", dst],
            capture_output=True, check=True,
        )
        return
    except subprocess.CalledProcessError:
        # Stream-copy failed (non-keyframe-aligned cut / incompatible
        # container) — fall through to a re-encode pass.
        pass

    # Drop any partial output the copy pass may have written so the re-encode
    # starts clean AND a second failure can't leave a corrupt dst the caller
    # would treat as a valid trim.
    try:
        os.unlink(dst)
    except OSError:
        pass
    try:
        subprocess.run(
            [get_ffmpeg_path(), "-y", "-i", src,
             "-ss", start_str, "-to", end_str, dst],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise


def _ffmpeg_time(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` for ffmpeg ``-ss``/``-to``."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def get_duration_s(wav_path: str) -> float:
    """Return the duration of a WAV file in seconds.

    Uses soundfile's lazy header read — no audio data is loaded into RAM.
    """
    with sf.SoundFile(wav_path) as f:
        return len(f) / f.samplerate
