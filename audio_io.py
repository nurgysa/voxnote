"""Torch-free audio I/O helpers shared across the transcription pipeline.

This module is deliberately dependency-light: only os/subprocess/tempfile
plus numpy and soundfile. It MUST NOT import torch, ctranslate2,
faster_whisper, or pyannote — any of those would drag torch's CUDA DLLs
into the import order, and that breaks ctranslate2 on Windows
(see memory/windows_ctranslate2_order.md and transcriber.py line 8-10).

Callers that need torch tensors (e.g. diarize_worker.py) convert numpy
→ tensor at the call site, AFTER importing torch in their own module.
"""

import os
import subprocess
import tempfile
import urllib.error
import urllib.request

import numpy as np
import soundfile as sf

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

    ffmpeg filtergraph syntax treats ``:`` as the parameter separator
    between filter arguments and ``\\`` as an escape character. A raw
    Windows path like ``C:\\Users\\nurgisa\\sh.rnnn`` injected into
    ``arnndn=m=<path>`` is therefore parsed as the ``arnndn`` filter
    with parameter ``m=C``, followed by garbage — the filter parse
    fails and the whole ffmpeg invocation crashes. Same hazard for
    spaces in some filter contexts.

    Fix: convert backslashes to forward slashes (ffmpeg accepts both
    on Windows for file paths) and then escape any remaining colons
    with a backslash. ``C:\\Users\\foo`` becomes ``C\\:/Users/foo``,
    which ffmpeg unescapes back to the valid path ``C:/Users/foo`` at
    the filter-argument layer.

    No-op on Unix paths (no backslashes, no colons), so cross-platform
    safe.

    Background: Codex P1 finding on PR #56 — without this escaping,
    the RNNoise denoise feature is unusable on Windows. Tests cover
    both Windows-style paths and the Unix no-op case to prevent
    accidental regressions in either direction.
    """
    # Forward slashes first so the colon-escape pass sees a normalized
    # path. Order doesn't matter for correctness but keeps the result
    # readable in error messages.
    return path.replace("\\", "/").replace(":", r"\:")


def _get_rnnoise_model_path() -> str:
    """Path to the RNNoise .rnnn model file for ffmpeg's ``arnndn`` filter.

    Lazy-downloads from ``_RNNOISE_MODEL_URL`` on first call and caches in
    ``~/.audio-transcriber/models/rnnoise/sh.rnnn``. Returns the cached path
    on subsequent calls — one-time ~85 KB download per machine.

    Why lazy and not bundled in the repo: the model is GPL-3 (per
    GregorR/rnnoise-models LICENSE). Bundling would create a license-virality
    surface for the rest of this codebase; downloading on first use keeps
    the binary out of the repo while still giving the user a one-click
    experience (no manual install steps).

    Cache directory matches the existing convention used by
    ``gdrive/auth.py`` for its OAuth token cache:
    ``~/.audio-transcriber/<subsystem>/``.

    Raises:
        RuntimeError: network failure, disk full, or any other write
            problem. Message is Russian-actionable so the user can recover
            via Settings (disable denoising) or by retrying once network
            is back. The partial file (if any) is removed before raising.
    """
    cache_dir = os.path.join(
        os.path.expanduser("~"),
        ".audio-transcriber", "models", "rnnoise",
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
        "ffmpeg", "-v", "error", "-y", "-i", audio_path,
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


def ensure_16khz_mono(audio_path: str) -> tuple[str, bool]:
    """Ensure ``audio_path`` is a 16 kHz mono WAV; returns ``(path, is_temp)``.

    Short-circuits with ``is_temp=False`` when the file is already 16 kHz
    mono. Otherwise resamples via ffmpeg into a temp WAV; caller owns the
    temp and must delete it.

    Phase 2 mixed-mode passes numpy slices to ``WhisperModel.transcribe()``,
    which assumes the input is 16 kHz. Per-chunk audio loaded via
    ``load_mono_float32`` carries the file's NATIVE sample rate — non-16
    kHz inputs would be interpreted at wrong frequencies by Whisper's
    mel-filterbank, garbling text and timestamps by a sr/16000 factor.

    Why not extend ``ensure_wav`` with a force-16k flag? Because the rest
    of the pipeline (audio_cutter, silence_remover) calls
    ``ensure_wav(normalize=False)`` and EXPECTS native sample rate. This
    helper is mixed-mode-specific and skips loudness normalization (the
    chunk has already been normalized upstream by
    ``Transcriber.transcribe()``'s ffmpeg pass when
    ``normalize_audio=True``; when ``normalize_audio=False``, the user
    explicitly opted out, so we must not re-normalize either).

    Raises ``RuntimeError`` with the tail of ffmpeg's stderr if conversion
    fails. The temp file (if any) is cleaned up before raising.
    """
    # Cheap check via soundfile's header read — no audio data loaded.
    with sf.SoundFile(audio_path) as f:
        if f.samplerate == SAMPLE_RATE and f.channels == 1:
            return audio_path, False

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-i", audio_path,
                "-ar", str(SAMPLE_RATE), "-ac", "1", tmp.name,
            ],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        os.unlink(tmp.name)
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise RuntimeError(
            f"ffmpeg failed to resample {audio_path} to 16 kHz "
            f"(exit {e.returncode}):\n{stderr[-1000:]}"
        ) from e
    return tmp.name, True


def resample_to_16khz_mono(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Resample a numpy mono float32 array to 16 kHz via ffmpeg pipe.

    In-memory sibling of ``ensure_16khz_mono`` (which operates on file
    paths). Short-circuits with the original array when ``sample_rate ==
    SAMPLE_RATE`` (no ffmpeg invocation, no copy).

    Used by ``silence_remover`` so Silero VAD always receives 16 kHz input.
    Silero's neural model is trained on 16 kHz only and ``faster_whisper``
    does NOT resample non-16k input before feeding the model — formants
    land at the wrong frequencies and detection quality collapses. The
    ``sampling_rate`` kwarg on ``get_speech_timestamps`` only fixes
    ms→sample threshold arithmetic, not the underlying detection (see
    PR #34 for the partial fix history; this is the proper fix).

    Implementation: pipes raw float32 samples to ffmpeg via stdin (raw
    PCM ``f32le`` format), reads resampled float32 back from stdout. No
    temp file, no soundfile encode/decode round-trip — typically <100 ms
    for a few minutes of audio.

    Args:
        samples: 1-D float32 mono audio at ``sample_rate``.
        sample_rate: source sample rate in Hz.

    Returns:
        1-D float32 array at 16 kHz. Same array (no copy) when the input
        is already 16 kHz.

    Raises:
        ValueError: if ``samples`` is not 1-D — checked BEFORE the
            ``sample_rate == SAMPLE_RATE`` short-circuit so a caller
            mistake (e.g. forgetting to channel-average a stereo array
            before passing 16 kHz input) fails fast here with an
            actionable error, not later inside VAD with a confusing one.
        RuntimeError: with the tail of ffmpeg's stderr if the subprocess
            fails.
    """
    if samples.ndim != 1:
        raise ValueError(
            f"resample_to_16khz_mono expects 1-D mono input, "
            f"got shape {samples.shape}"
        )

    if sample_rate == SAMPLE_RATE:
        return samples

    # Ensure float32 — Python's bytes() conversion below otherwise produces
    # the wrong byte layout for other dtypes. cheap no-op when already f32.
    if samples.dtype != np.float32:
        samples = samples.astype(np.float32, copy=False)

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
                "-i", "pipe:0",
                "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "pipe:1",
            ],
            input=samples.tobytes(),
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise RuntimeError(
            f"ffmpeg failed to resample numpy samples "
            f"({sample_rate} Hz → {SAMPLE_RATE} Hz, exit {e.returncode}):\n"
            f"{stderr[-1000:]}"
        ) from e

    return np.frombuffer(result.stdout, dtype=np.float32)


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

    Raises ``subprocess.CalledProcessError`` if both paths fail. ffmpeg
    output is captured (not streamed to the terminal).
    """
    start_str = _ffmpeg_time(start_sec)
    end_str = _ffmpeg_time(end_sec)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src,
             "-ss", start_str, "-to", end_str,
             "-c", "copy", dst],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError:
        # Fallback: re-encode. Slower, but handles non-keyframe-aligned cuts
        # and codec-incompatible containers.
        subprocess.run(
            ["ffmpeg", "-y", "-i", src,
             "-ss", start_str, "-to", end_str, dst],
            capture_output=True, check=True,
        )


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


def split_wav_into_chunks(
    wav_path: str,
    chunk_duration_s: int,
    out_dir: str,
    overlap_s: float = 0.0,
) -> list[tuple[str, float, float]]:
    """Split a WAV into ≤ ``chunk_duration_s`` pieces using ffmpeg, with
    optional overlap to avoid cutting words at chunk boundaries.

    Returns a list of ``(chunk_path, chunk_start_abs, primary_start_abs)``
    triples. For files shorter than ``chunk_duration_s``, returns
    ``[(wav_path, 0.0, 0.0)]`` without copying.

    Fields:
      * ``chunk_start_abs`` — absolute position (seconds) where this chunk's
        audio starts in the original file. The caller adds this to each
        Whisper-emitted segment/word time to recover absolute timestamps.
      * ``primary_start_abs`` — absolute position from which this chunk's
        output is "authoritative". For chunks after the first this is
        ``chunk_start_abs + overlap_s``; earlier content came from the
        previous chunk and the caller should drop it to avoid duplicates.
        For chunk 0 and for all chunks when ``overlap_s == 0``, this is
        equal to ``chunk_start_abs`` (no dedup needed).

    Why overlap exists: Whisper is given a single chunk at a time and has no
    warm-up context. A word that straddles the chunk boundary typically gets
    clipped from the end of chunk N (Whisper's VAD trims the last partial
    utterance) AND from the start of chunk N+1 (insufficient context for the
    first few hundred ms). With ``overlap_s > 0``, both chunks see the full
    boundary word; the caller keeps the chunk-N version and drops chunk-N+1's
    re-transcription of the same audio.

    Why chunking exists at all: faster-whisper's WhisperModel.transcribe()
    runs a full-file STFT in numpy as preprocessing. On Windows with
    fragmented heaps, files longer than ~90 minutes fail to allocate the
    contiguous multi-GB complex128 STFT buffer (verified bug:
    logs/transcribe_crash_2026-04-14_16-14-45.log). Splitting into 20-min
    chunks keeps each STFT under ~400 MB — comfortable on Windows.

    Uses ffmpeg with ``-c copy`` (stream copy) for speed — no re-encoding.
    Each chunk is written to ``out_dir/chunk_<NNN>.wav``. The caller owns
    the chunk files and must clean them up.
    """
    duration = get_duration_s(wav_path)
    if duration <= chunk_duration_s:
        return [(wav_path, 0.0, 0.0)]

    os.makedirs(out_dir, exist_ok=True)
    chunks: list[tuple[str, float, float]] = []
    n_chunks = int(duration // chunk_duration_s) + (
        1 if duration % chunk_duration_s > 0 else 0
    )
    for i in range(n_chunks):
        # Natural chunk [natural_start, natural_start + chunk_duration_s].
        # With overlap, we ffmpeg-extract [natural_start - overlap, ...end]
        # so chunks i>0 begin overlap_s earlier. Chunk 0 is unchanged (no
        # prior content to overlap with). The primary region — the portion
        # whose transcription is canonical — always starts at natural_start.
        natural_start = i * chunk_duration_s
        chunk_start_abs = max(0.0, natural_start - overlap_s) if i > 0 else 0.0
        primary_start_abs = float(natural_start)
        chunk_end_abs = min(duration, natural_start + chunk_duration_s)
        chunk_len_s = chunk_end_abs - chunk_start_abs

        chunk_path = os.path.join(out_dir, f"chunk_{i:03d}.wav")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y",
                    "-ss", _ffmpeg_time(chunk_start_abs),
                    "-t", f"{chunk_len_s:.3f}",
                    "-i", wav_path,
                    "-c", "copy",
                    chunk_path,
                ],
                capture_output=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            # Cleanup any chunks already written before raising
            for written_path, _, _ in chunks:
                try:
                    os.unlink(written_path)
                except OSError:
                    pass
            raise RuntimeError(
                f"ffmpeg failed to split chunk {i} of {wav_path}:\n{stderr[-1000:]}"
            ) from e
        chunks.append((chunk_path, float(chunk_start_abs), primary_start_abs))
    return chunks
