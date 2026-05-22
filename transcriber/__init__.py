# isort: off
# Order-sensitive bootstrap: cuda_utils loads ctranslate2, which MUST be
# imported before torch on Windows (CUDA DLL conflict — see cuda_utils
# docstring + logs/transcribe_crash_*.log for the STATUS_DLL_INIT_FAILED
# trail). faster_whisper below pulls torch transitively, so cuda_utils
# has to win the race. Do NOT let ruff/isort reorder this block.
from .cuda_utils import (
    TranscriptionCancelled,
    _check_cancelled,
    _cuda_is_available,
)
# isort: on

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from faster_whisper import WhisperModel

from audio_io import (
    ensure_16khz_mono,
    ensure_wav,
    get_duration_s,
    load_mono_float32,
    split_wav_into_chunks,
)
from logging_setup import crash_log_path, get_logger
from transcript_format import format_diarized, format_timed

from .progress import _parse_progress_line
from .prompt import _build_initial_prompt, _effective_whisper_language
from .segmenter import vad_split
from .speaker_aligner import (
    _assign_speakers_word_level,
    _find_speaker_by_overlap,
    _flush_word_group,
    _speaker_at_time,
)

logger = get_logger(__name__)


# Path to the diarize_worker.py script spawned as a subprocess (see
# Transcriber.diarize). The worker lives at the project root, ONE level
# above this package — hence the double dirname. Pre-F4, when this code
# was a flat ``transcriber.py`` on the root, a single dirname sufficed;
# the package split shifted ``__file__`` one level deeper, so the path
# has to climb one extra level. Computed once at import — covered by
# tests/test_transcriber_paths.py to catch any future repackaging that
# would silently break diarization.
_DIARIZE_WORKER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "diarize_worker.py",
)


# Public API. Most names are re-exported from helper submodules so existing
# call sites (`from transcriber import Transcriber`, `from transcriber import
# _build_initial_prompt`, etc.) keep working unchanged after the F4 split.
# Tests in ``tests/test_transcriber_pure`` import the underscore-prefixed
# helpers directly from this package.
__all__ = [
    "Transcriber",
    "TranscriptionCancelled",
    "_assign_speakers_word_level",
    "_build_initial_prompt",
    "_check_cancelled",
    "_cuda_is_available",
    "_effective_whisper_language",
    "_find_speaker_by_overlap",
    "_flush_word_group",
    "_parse_progress_line",
    "_speaker_at_time",
]


# Files longer than this are split into chunks before transcription. The
# threshold protects against a numpy contiguous-allocation bug in
# faster_whisper's full-file STFT on Windows: even when the system has
# plenty of free RAM, np.fft.rfft can't always find a contiguous block
# big enough for the STFT output.
#
# History:
#   - Initially 90 min, based on empirical testing (62 min worked,
#     118 min failed).
#   - Lowered to 25 min after a 32-min file failed in production
#     (logs/transcribe_crash_2026-04-27_14-37-18.log) once the process
#     started carrying psutil, pynvml, requests, providers package and
#     the live-monitor dialog — more long-lived Python objects =
#     more heap fragmentation = lower contiguous-allocation ceiling.
# At this threshold a 32-min file (which was the failing case) gets
# split into two ~16-min chunks of ~280 MB STFT each, well clear of
# the fragmentation ceiling on a 16 GB Windows machine.
_LONG_FILE_THRESHOLD_S = 25 * 60   # 25 minutes
# Each chunk's STFT must fit a single contiguous numpy allocation. 15 min
# at 16 kHz / hop=160 / n_fft=400 / complex128 = ~280 MB — comfortable on
# fragmented Windows heaps even after long-running app sessions.
_CHUNK_DURATION_S = 15 * 60        # 15 minutes
# Chunk boundary overlap: each chunk after the first is extended 3 s
# backward, so boundary words are transcribed in both chunks and the caller
# can keep the chronologically-earlier version. 3 s is enough to span any
# single utterance (average speaking rate ~2.5 words/s) without materially
# inflating total inference time (3 s × N chunks ≈ 0.25% overhead on a
# 2-hour file). Dedup lives in transcribe() below — see primary_start_abs.
_CHUNK_OVERLAP_S = 3.0


class Transcriber:
    """Wrapper around faster-whisper for audio transcription."""

    def __init__(
        self,
        model_size: str = "medium",
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 5,
    ):
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._model = None
        self._on_cpu = False   # True if Whisper weights are offloaded to CPU memory
        # Last transcription's per-segment results, for callers that want
        # subtitle exports (SRT/VTT). List of {start, end, text, speaker?}.
        # None until transcribe() runs at least once.
        self.last_segments: list[dict] | None = None

    @property
    def model_size(self) -> str:
        return self._model_size

    def _get_device(self) -> str:
        # Explicit "cpu" — honour without question.
        if self._device == "cpu":
            return "cpu"
        # Explicit "cuda" — hard-fail if no GPU. We do NOT silently fall back
        # to CPU because the user picked GPU on purpose; a silent CPU run
        # would be 5-10× slower with no warning. "auto" exists for the
        # silent-fallback case.
        if self._device == "cuda":
            if _cuda_is_available():
                return "cuda"
            raise RuntimeError(
                "GPU выбран как устройство, но CUDA недоступна. "
                "Выбери CPU или Авто в настройках, либо проверь, что "
                "NVIDIA-драйвер установлен и видеокарта поддерживается."
            )
        # "auto" (and any unknown value) — best-effort.
        return "cuda" if _cuda_is_available() else "cpu"

    def _get_compute_type(self, device: str) -> str:
        """
        Decide which ctranslate2 compute type to use for the loaded model.

        Trade-offs on CUDA (GTX 1650 Ti, compute 7.5, 4 GB VRAM):
          - "float16":       ~reference quality, highest VRAM (~3.1 GB for large-v3),
                             safest for accuracy but tight on 4 GB cards.
          - "int8_float16":  int8 weights + fp16 activations. ~50% VRAM savings,
                             usually *faster* on Turing Tensor Cores, minimal
                             quality loss (~0.1-0.3% WER). Best default here.
          - "int8":          int8 weights + int8 activations. Smallest VRAM,
                             slight additional quality loss. Useful if OOM.

        On CPU, "int8" is by far the fastest option (ctranslate2 AVX2 kernels).

        If the user passed an explicit compute_type (not "auto"), honour it.

        Returns one of:
            "float16" | "int8_float16" | "int8_float32" | "int8" | "float32"
        """
        if self._compute_type != "auto":
            return self._compute_type
        return "int8_float16" if device == "cuda" else "int8"

    @property
    def device(self) -> str | None:
        """Return the device the model is loaded on, or None if not loaded."""
        if self._model is None:
            return None
        return self._get_device()

    def load_model(self) -> None:
        """Download (if needed) and load the Whisper model.

        If the model is already loaded but offloaded to CPU memory (via
        offload_to_cpu()), restore it to GPU. This is the fast path used
        between consecutive transcribe() calls — no re-download, no re-init.
        """
        if self._model is not None:
            if self._on_cpu:
                # Resume from CPU offload: weights move back to GPU using the
                # runtime context kept alive by ctranslate2's unload_model.
                self._model.model.load_model()
                self._on_cpu = False
            return
        device = self._get_device()
        compute_type = self._get_compute_type(device)
        self._model = WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute_type,
        )

    def offload_to_cpu(self) -> None:
        """
        Move Whisper weights from GPU VRAM to CPU memory without destroying
        the model object. Used to free VRAM for the diarization subprocess.

        Why not just unload_model() (full destruction): on Windows + GTX 1650 Ti
        + Whisper "medium", calling `del self._model` triggers a Fatal Python
        error: Aborted in ctranslate2's native destructor (verified via
        faulthandler.log). ctranslate2's `unload_model(to_cpu=True)` is the
        official escape hatch — it moves weights to CPU and keeps the runtime
        context alive, avoiding the destructor entirely.

        Subsequent load_model() restores the weights to GPU via ctranslate2's
        `load_model()` — fast (~hundreds of ms) because the runtime context
        is already initialized.

        Safe to call multiple times — no-op if model is None or already offloaded.
        """
        if self._model is None or self._on_cpu:
            return
        # No-op when Whisper was loaded on CPU to begin with — there is no
        # VRAM to free, and ctranslate2's unload_model(to_cpu=True) on a
        # CPU-resident model can confuse its internal device tracking.
        if self._get_device() == "cpu":
            return
        self._model.model.unload_model(to_cpu=True)
        self._on_cpu = True

    def _write_crash_log(
        self,
        audio_path: str,
        exit_code: int,
        stderr_text: str,
        stdout_text: str,
    ) -> str | None:
        """Persist a diarization subprocess crash dump for post-mortem.

        The rotating ``logs/app.log`` carries an indexed reference; the dump
        file holds the full subprocess stderr/stdout (potentially many KB)
        that doesn't fit cleanly in a single log line. Returns the dump path
        or None if writing failed (never raises — diagnostics must not mask
        the original error).
        """
        try:
            path = crash_log_path("diarize_crash")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"audio_path: {audio_path}\n")
                f.write(f"exit_code: {exit_code}\n")
                f.write(f"model: {self._model_size}\n")
                f.write("=" * 60 + "\nSTDERR:\n")
                f.write(stderr_text)
                f.write("\n" + "=" * 60 + "\nSTDOUT:\n")
                f.write(stdout_text)
            return path
        except Exception:
            logger.exception("failed to write diarize crash dump")
            return None

    def _launch_diarization_subprocess(
        self,
        audio_path: str,
        hf_token: str | None,
        device: str = "auto",
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        voice_lib_path: str | None = None,
        on_progress=None,
        on_status=None,
    ) -> dict:
        """
        Spawn the diarization subprocess in WAIT mode and return a handle.

        The subprocess starts immediately but blocks on stdin for a "GO\\n"
        line before doing any GPU work, so it can be launched in parallel
        with Whisper transcription. While waiting it imports pyannote,
        downloads/loads weights to CPU, and decodes the audio file — all
        CPU+RAM only. Once we send GO it does VRAM preflight, moves the
        pipeline to CUDA, and runs inference.

        Why subprocess at all: ctranslate2's WhisperModel and pyannote's
        CUDA state conflict on destruction, crashing the main process with
        a C-level abort. Running pyannote in a fresh interpreter sidesteps
        the interaction — the OS cleans up all CUDA resources when the
        subprocess exits.

        Returns a handle dict consumed by ``_await_diarization_subprocess``:
        proc, audio_path, stdout/stderr buffers, consumer threads.
        """
        # Resolve device ahead of spawn. "auto" picks GPU when available,
        # CPU otherwise — no surprise to the user. Explicit "cuda" with no
        # GPU is a hard error here (Q2.a): we don't want to silently spawn
        # a subprocess that will exit 3 ten seconds later — better to fail
        # immediately with a message that points to the device picker.
        if device == "cuda" and not _cuda_is_available():
            raise RuntimeError(
                "GPU выбран для диаризации, но CUDA недоступна. "
                "Выбери CPU или Авто в настройках, либо проверь, что "
                "NVIDIA-драйвер установлен."
            )
        if device == "auto":
            device = "cuda" if _cuda_is_available() else "cpu"
        # device is now one of {"cuda", "cpu"} — passed to worker via argv.

        worker = _DIARIZE_WORKER_PATH
        env = dict(os.environ)
        env["DIARIZE_WAIT"] = "1"
        # Reduce CUDA allocator fragmentation. On 4 GB cards, pyannote's
        # segmentation pass hits "reserved but unallocated" OOM that surfaces
        # as cuBLAS_NOT_INITIALIZED on the first matmul. Expandable segments
        # let the allocator grow contiguous regions instead of leaving holes.
        # Harmless on CPU — torch ignores it when CUDA isn't engaged.
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        if hf_token:
            env["HF_TOKEN"] = hf_token
        # Speaker-count hints travel as env vars (optional, each independent).
        # Env rather than argv keeps the worker CLI stable and makes absent
        # hints indistinguishable from unset (what pyannote's API expects).
        if num_speakers is not None:
            env["DIARIZE_NUM_SPEAKERS"] = str(num_speakers)
        if min_speakers is not None:
            env["DIARIZE_MIN_SPEAKERS"] = str(min_speakers)
        if max_speakers is not None:
            env["DIARIZE_MAX_SPEAKERS"] = str(max_speakers)
        if voice_lib_path:
            env["DIARIZE_VOICE_LIB"] = voice_lib_path

        proc = subprocess.Popen(
            [sys.executable, worker, audio_path, device],
            env=env,
            stdin=subprocess.PIPE,    # for the GO signal
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        last_percent = [70.0]  # monotonic guard — percent only moves forward

        def _consume_stdout():
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_chunks.append(line)

        def _consume_stderr():
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)
                if line.startswith("STATUS\t"):
                    if on_status is not None:
                        msg = line[len("STATUS\t"):].rstrip("\n")
                        try:
                            on_status(msg)
                        except Exception:
                            pass
                    continue
                if not line.startswith("PROGRESS\t") or on_progress is None:
                    continue
                percent = _parse_progress_line(line)
                if percent is None or percent <= last_percent[0]:
                    continue
                last_percent[0] = percent
                try:
                    on_progress(percent)
                except Exception:
                    pass  # GUI callback errors must not crash diarization

        t_out = threading.Thread(target=_consume_stdout, daemon=True)
        t_err = threading.Thread(target=_consume_stderr, daemon=True)
        t_out.start()
        t_err.start()

        return {
            "proc": proc,
            "audio_path": audio_path,
            "stdout_chunks": stdout_chunks,
            "stderr_chunks": stderr_chunks,
            "t_out": t_out,
            "t_err": t_err,
        }

    def _await_diarization_subprocess(
        self,
        handle: dict,
        cancel_event=None,
    ) -> list[tuple[float, float, str]]:
        """
        Send the GO signal, wait for completion, return parsed speaker turns.

        Polls in 0.25s ticks so a cancel_event set from the GUI thread is
        acted on within ~250 ms instead of after the diarization subprocess
        finishes (5+ min on a 60-min file). On cancel we kill the subprocess
        and raise TranscriptionCancelled — the OS reclaims its CUDA context.

        If the subprocess has already exited at GO time (crashed during
        pyannote load), we skip the write and fall through to the exit-code
        handling below — its stderr already explains why.
        """
        proc = handle["proc"]
        stdout_chunks = handle["stdout_chunks"]
        stderr_chunks = handle["stderr_chunks"]
        audio_path = handle["audio_path"]

        if proc.poll() is None and proc.stdin is not None:
            try:
                proc.stdin.write("GO\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                # Subprocess died between our poll and our write — its stderr
                # carries the cause; fall through to the exit-code handler.
                pass
        # Close stdin so a stuck readline() in the worker (shouldn't happen
        # but defensive) gets EOF. Safe — we never write again.
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass

        deadline = 3600.0
        elapsed = 0.0
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                elapsed += 0.25
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    handle["t_out"].join()
                    handle["t_err"].join()
                    # Cancel is a user-initiated signal, not a wrapped timeout
                    # from the per-poll wait — `from None` keeps the chain clean.
                    raise TranscriptionCancelled() from None
                if elapsed >= deadline:
                    proc.kill()
                    proc.wait()
                    # The outer "we exhausted the 1h deadline" timeout is
                    # semantically distinct from any single 0.25s poll timeout
                    # we caught — chain would just confuse downstream readers.
                    raise subprocess.TimeoutExpired(proc.args, deadline) from None

        handle["t_out"].join()
        handle["t_err"].join()

        if proc.returncode != 0:
            # Persist full stderr/stdout to disk for post-mortem before
            # raising — the RuntimeError only carries a tail, and the tk
            # dialog disappears once the user dismisses it.
            stderr_text = "".join(stderr_chunks)
            stdout_text = "".join(stdout_chunks)
            log_path = self._write_crash_log(
                audio_path, proc.returncode, stderr_text, stdout_text,
            )
            log_hint = f"\n\nПолный лог: {log_path}" if log_path else ""

            if proc.returncode == 3:
                # Preflight failure (no CUDA or insufficient VRAM). Worker's
                # stderr last line is a user-friendly Russian message.
                stderr_stripped = stderr_text.strip()
                last_line = stderr_stripped.splitlines()[-1] \
                    if stderr_stripped else "Диаризация на GPU недоступна."
                raise RuntimeError(last_line + log_hint)

            raise RuntimeError(
                f"diarize_worker failed (exit {proc.returncode}):\n"
                f"{stderr_text[-2000:]}"
                f"{log_hint}"
            )
        return [tuple(row) for row in json.loads("".join(stdout_chunks).strip())]

    def _transcribe_via_cloud(
        self,
        audio_path: str,
        *,
        language: str | None,
        diarize: bool,
        hotwords: str | None,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
        cloud_provider: str,
        cloud_api_key: str,
        on_progress,
        on_status,
        cancel_event,
    ) -> str:
        """
        Cloud branch — delegate to a managed transcription API.

        Skips the local pipeline entirely (no ffmpeg normalize, no Whisper,
        no pyannote subprocess). The provider returns segments in the same
        shape the local path produces, so the same TXT/SRT/VTT formatters
        downstream work without modification.

        ``voice_lib_path`` is intentionally ignored here: matching enrolled
        voices to clusters needs the raw embeddings, which managed APIs
        don't expose. The user keeps the SPEAKER_A/B/... → «Спикер 1/2/...»
        rename via the standard ``_build_speaker_map`` path.
        """
        # Local imports keep providers/ off the import path of CLI tools
        # like ``audio_cutter`` that don't need it. Also avoids paying the
        # ``requests`` import cost at module load.
        from providers import ProviderError, TranscriptionOptions, get_provider

        try:
            provider = get_provider(cloud_provider, cloud_api_key)
        except ProviderError as e:
            raise RuntimeError(str(e)) from e

        hotword_list: list[str] = []
        if hotwords and hotwords.strip():
            # Same comma-split rule the local prompt builder uses.
            hotword_list = [
                h.strip() for h in hotwords.split(",") if h.strip()
            ]

        opts = TranscriptionOptions(
            language=language,
            diarize=diarize,
            hotwords=hotword_list,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

        try:
            result = provider.transcribe(
                audio_path,
                opts,
                on_status=on_status,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        except ProviderError as e:
            # Surface the user-facing message verbatim, preserving the
            # original cause for the crash log via __cause__.
            raise RuntimeError(str(e)) from e

        # Cache for SRT/VTT export (mirrors the local path's
        # ``self.last_segments = transcript_segments`` line below).
        self.last_segments = result.segments

        if on_progress:
            on_progress(100.0)

        # Pick the same formatter the local path uses, based on whether
        # any segment carries a speaker label.
        has_speakers = any("speaker" in seg for seg in result.segments)
        if diarize and has_speakers:
            return format_diarized(result.segments)
        return format_timed(result.segments)

    def _decode_chunk_single(
        self,
        chunk_path: str,
        chunk_start_abs: float,
        primary_start_abs: float,
        *,
        effective_language: str | None,
        initial_prompt: str | None,
        hotwords_str: str | None,
        cancel_event: threading.Event | None,
    ) -> list[dict]:
        """Single-language per-chunk decode — the pre-Phase-2 code path.

        Extracted verbatim from the inline body of ``transcribe()`` so the
        parallel mixed-mode helper (``_decode_chunk_mixed``) can sit next
        to it. Behaviour is byte-identical to pre-refactor. Rationale for
        each parameter is documented inline.

        Returns a list of transcript-segment dicts with keys
        ``{"start", "end", "text", "words"}`` — same shape callers already
        consume from the inline loop.
        """
        # Sequential WhisperModel.transcribe() per chunk. We do NOT
        # use BatchedInferencePipeline because its parallel batched
        # inference exceeds VRAM on a 4 GB GPU with Whisper medium
        # (verified OOM at batch=4: logs/transcribe_crash_2026-04-14_16-42-41.log).
        # Sequential per-segment inference uses ~1× chunk activations,
        # which fits comfortably alongside the loaded weights.
        # Quality-focused defaults (Phase 1 tuning):
        #   condition_on_previous_text=False — disables the feedback
        #     loop that causes Whisper to emit runaway repeats like
        #     "Спасибо. Спасибо. Спасибо." on long quiet stretches.
        #     Well-known failure mode; standard production fix.
        #   vad_parameters — keep a bit of silence around speech so
        #     word endings aren't clipped; ignore micro-pauses so we
        #     don't fragment utterances mid-word.
        #   no_speech_threshold / log_prob_threshold /
        #     compression_ratio_threshold — anti-hallucination gates
        #     for the temperature-fallback ladder. Values are the
        #     faster-whisper recommended anti-hallucination tuple.
        # word_timestamps=True:
        #   Enables word-level diarization (see _assign_speakers_word_level).
        #   Cost: ~10-15% more wall time for transcription — this is the
        #   cross-attention DTW alignment pass Whisper runs after the
        #   beam search. Worth it: without per-word times, a single
        #   Whisper segment that spans two speakers' turns ("— Да.
        #   — Согласен.") gets labeled with a single speaker, which
        #   is the dominant visible diarization error in dialogue.
        #   Paid even when diarize=False because it's harmless and
        #   branching would just add flakiness.
        segments, _info = self._model.transcribe(
            chunk_path,
            language=effective_language,
            beam_size=self._beam_size,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            word_timestamps=True,
            initial_prompt=initial_prompt,
            hotwords=hotwords_str,
        )

        out: list[dict] = []
        for segment in segments:
            # Cancel checkpoint inside the hot loop. On a 90-minute
            # file this fires several thousand times — a single
            # is_set() call is sub-microsecond, so the overhead is
            # invisible compared to per-segment Whisper inference.
            _check_cancelled(cancel_event)
            abs_start = segment.start + chunk_start_abs
            abs_end = segment.end + chunk_start_abs
            # Dedup overlap zone. A chunk (N>0) begins _CHUNK_OVERLAP_S
            # seconds before its primary_start_abs; segments whose
            # midpoint falls before primary_start_abs describe audio
            # already transcribed by the previous chunk. Keeping the
            # earlier chunk's version is arbitrary but consistent —
            # both transcriptions of the same audio should match.
            # Using the midpoint (not start or end) is robust to
            # boundary words that straddle the line.
            seg_mid = (abs_start + abs_end) / 2.0
            if seg_mid < primary_start_abs:
                continue
            # Words are optional in faster-whisper: if the DTW
            # alignment pass was skipped (silent segment, or
            # pathological beam output), segment.words is None. We
            # store the list anyway — an empty list triggers the
            # segment-level speaker-overlap fallback downstream.
            seg_words: list[dict] = []
            if segment.words:
                for w in segment.words:
                    seg_words.append({
                        "start": w.start + chunk_start_abs,
                        "end": w.end + chunk_start_abs,
                        "word": w.word,
                    })
            out.append({
                "start": abs_start,
                "end": abs_end,
                "text": segment.text.strip(),
                "words": seg_words,
            })
        return out

    def _decode_chunk_mixed(
        self,
        chunk_path: str,
        chunk_start_abs: float,
        primary_start_abs: float,
        *,
        initial_prompt: str | None,
        hotwords_str: str | None,
        cancel_event: threading.Event | None,
    ) -> list[dict]:
        """Per-segment mixed-language decode for the Phase 2 'mixed' path.

        Loads the chunk into memory, VAD-splits it into speech regions, and
        runs ``model.transcribe(seg_audio, language=None, ...)`` once per
        region. ``language=None`` triggers Whisper's internal
        ``detect_language()`` on that slice, so each region is decoded in
        its own detected language (KZ / RU / EN / sister-language false
        positive) without re-encoding the audio twice.

        Returns transcript-segment dicts with the same shape
        ``_decode_chunk_single`` produces, plus a new ``"language"`` key
        carrying ``info.language`` (the per-segment detection result).
        """
        # Mixed mode slices the chunk into numpy arrays per VAD region and
        # passes them to model.transcribe(seg_audio, ...). faster-whisper
        # treats numpy input as 16 kHz mono unconditionally — if the chunk
        # arrived at a different rate (normalize_audio=False AND input is a
        # non-16 kHz WAV), the slice would be interpreted at the wrong
        # frequency, garbling text and skewing timestamps. Resample first;
        # ensure_16khz_mono short-circuits when the chunk is already 16k mono.
        chunk_16k_path, _is_temp = ensure_16khz_mono(chunk_path)
        try:
            # Load the chunk into memory once. Unlike _decode_chunk_single,
            # which streams via the WAV path, the mixed path needs the raw
            # samples to feed numpy slices to model.transcribe() per VAD
            # region — Whisper accepts both a path and an ndarray.
            samples, sample_rate = load_mono_float32(chunk_16k_path)
            # VAD pre-pass: split the chunk into speech regions so each can
            # be decoded with its own language detection. Parameters tuned in
            # segmenter.py for language detection (min 500 ms speech, etc.),
            # not silence removal.
            speech_timestamps = vad_split(samples, sample_rate)
            logger.info(
                "Transcribe: mixed mode, vad_segments=%d",
                len(speech_timestamps),
            )

            out: list[dict] = []
            for seg_idx, ts in enumerate(speech_timestamps):
                # Cancel checkpoint between VAD segments. Each model.transcribe()
                # call below can take several seconds on a long region; checking
                # here keeps cancel latency bounded to one region's decode time.
                _check_cancelled(cancel_event)
                seg_audio = samples[ts["start"]:ts["end"]]
                seg_start_s = ts["start"] / sample_rate

                # Per-segment Whisper decode. Key differences from the single-
                # language path (_decode_chunk_single above):
                #   language=None — let Whisper detect_language() run on this
                #     slice. This is the whole point of the mixed path: each
                #     VAD region gets its own language tag.
                #   vad_filter=False — we already filtered above, no need to
                #     run the VAD pass again inside Whisper.
                # The anti-hallucination tuple, word_timestamps, initial_prompt,
                # hotwords mirror the single-language path — same rationale
                # applies (see _decode_chunk_single for full commentary).
                segments, info = self._model.transcribe(
                    seg_audio,
                    language=None,                    # Whisper auto-detects per slice
                    beam_size=self._beam_size,
                    vad_filter=False,                 # already filtered
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                    word_timestamps=True,
                    initial_prompt=initial_prompt,
                    hotwords=hotwords_str,
                )

                whisper_segs_count = 0
                for segment in segments:
                    # Cancel checkpoint inside the inner Whisper segment loop —
                    # mirrors _decode_chunk_single's pattern. Sub-microsecond
                    # overhead.
                    _check_cancelled(cancel_event)
                    # Count Whisper-emitted segments BEFORE the dedup continue
                    # below — the diagnostic answers "did Whisper find speech
                    # in this VAD region?", which is more useful than "how many
                    # segments survived dedup against the previous chunk".
                    whisper_segs_count += 1
                    # Convert Whisper-segment-local times to absolute times by
                    # adding the VAD region's offset (seg_start_s) AND the
                    # chunk's absolute offset (chunk_start_abs). Two-level
                    # arithmetic because Whisper sees only the sliced audio.
                    abs_start = chunk_start_abs + seg_start_s + segment.start
                    abs_end = chunk_start_abs + seg_start_s + segment.end
                    # Midpoint dedup against the previous chunk's overlap zone —
                    # identical strategy to _decode_chunk_single. Drops segments
                    # whose midpoint falls before primary_start_abs because the
                    # earlier chunk already transcribed that audio.
                    seg_mid = (abs_start + abs_end) / 2.0
                    if seg_mid < primary_start_abs:
                        continue
                    # Word-level times: same two-level offset as segment times.
                    # ``segment.words`` is optional (None if DTW alignment was
                    # skipped); an empty list downstream triggers the segment-
                    # level speaker-overlap fallback during diarization.
                    seg_words: list[dict] = []
                    if segment.words:
                        for w in segment.words:
                            seg_words.append({
                                "start": w.start + chunk_start_abs + seg_start_s,
                                "end": w.end + chunk_start_abs + seg_start_s,
                                "word": w.word,
                            })
                    out.append({
                        "start": abs_start,
                        "end": abs_end,
                        "text": segment.text.strip(),
                        "words": seg_words,
                        # Per-segment detected language — the load-bearing
                        # addition over _decode_chunk_single's dict shape.
                        # Downstream consumers (subtitle formatters, future
                        # per-segment-language UI) read this key.
                        "language": info.language,
                    })
                logger.debug(
                    "vad_seg %d: %.2fs-%.2fs, lang=%s, whisper_segments=%d",
                    seg_idx,
                    seg_start_s,
                    seg_start_s + (ts["end"] - ts["start"]) / sample_rate,
                    info.language,
                    whisper_segs_count,
                )
            return out
        finally:
            # Best-effort cleanup of the resample temp. Runs on normal
            # return, cancellation (TranscriptionCancelled), and any
            # ffmpeg/Whisper exception — without it we'd leak temp WAVs
            # on every mixed-mode chunk when the source isn't 16 kHz mono.
            if _is_temp:
                try:
                    os.unlink(chunk_16k_path)
                except OSError:
                    pass  # best-effort cleanup

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = False,
        diarize_device: str = "auto",
        hf_token: str | None = None,
        hotwords: str | None = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        voice_lib_path: str | None = None,
        normalize_audio: bool = True,
        cloud_provider: str | None = None,
        cloud_api_key: str | None = None,
        on_progress=None,
        on_status=None,
        cancel_event=None,
    ) -> str:
        """
        Transcribe an audio file and return the full text.

        Args:
            audio_path: Path to an MP3, WAV, or M4A file.
            language: Language code ("kk", "ru", "en") or None for auto-detect.
            diarize: If True, identify speakers in the audio.
            hf_token: Hugging Face token for pyannote models.
            hotwords: Comma-separated terms/names to improve recognition.
            num_speakers: Exact number of speakers, if known. Dramatic DER
                improvement when correct. Mutually exclusive with min/max.
            min_speakers: Lower bound for speaker count (inclusive).
            max_speakers: Upper bound for speaker count (inclusive).
            voice_lib_path: Optional path to a JSON file with enrolled voice
                embeddings. When present, detected SPEAKER_XX clusters are
                matched to real names via cosine similarity + Hungarian
                assignment. Unmatched clusters keep their SPEAKER_XX label
                (formatted as "Спикер N" in the output).
            normalize_audio: If True (default), pass the source through an
                EBU R128 loudness normalizer and 80 Hz high-pass before
                transcription. Disable for already-mastered material.
            on_progress: Optional callback(percent: float) called per segment.
            on_status: Optional callback(text: str) for status updates.

        Returns:
            The transcribed text, with speaker labels if diarize=True.
        """
        # Cloud short-circuit. When cloud_provider is set, skip the entire
        # local pipeline (ffmpeg normalize / Whisper / pyannote subprocess)
        # and delegate to the chosen managed API. Returns formatted text in
        # the same shape as the local path so the UI doesn't need to branch.
        if cloud_provider:
            # Guard: fail fast with a Russian ProviderError before any HTTP
            # work when the chosen provider hasn't opted in to mixed-mode.
            # Without this, language="mixed" leaks to the vendor API as a
            # literal language code and produces a confusing vendor 400.
            # Providers opt in by setting supports_mixed = True once their
            # _submit() has a mixed-aware branch (see PR-B/PR-C task series).
            if language == "mixed":
                from providers import PROVIDERS, ProviderError
                provider_cls = PROVIDERS.get(cloud_provider)
                if provider_cls is not None and not provider_cls.supports_mixed:
                    raise ProviderError(
                        f"{cloud_provider} ещё не поддерживает «Смешанный (KZ+RU+EN)». "
                        "Выбери другой язык или провайдер."
                    )
            return self._transcribe_via_cloud(
                audio_path,
                language=language,
                diarize=diarize,
                hotwords=hotwords,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                cloud_provider=cloud_provider,
                cloud_api_key=cloud_api_key or "",
                on_progress=on_progress,
                on_status=on_status,
                cancel_event=cancel_event,
            )

        hotwords_str = hotwords.strip() if hotwords and hotwords.strip() else None
        # Translate the UI-level sentinel for the Whisper API. "mixed" means
        # «KZ+RU+EN decode» for our layer but Whisper only accepts ISO codes
        # or None (auto-detect). Keep the original `language` for the prompt-
        # frame lookup below so the trilingual initial_prompt is still built.
        effective_language = _effective_whisper_language(language)
        # initial_prompt works through Whisper's decode context (stylistic
        # framing + proper-noun spelling), while hotwords= biases the
        # CTC-style token scoring. Using both in tandem gives the most
        # reliable recognition of domain names; redundancy is a feature.
        initial_prompt = _build_initial_prompt(language, hotwords_str)

        # IMPORTANT ordering: ensure_wav() BEFORE load_model().
        #
        # ensure_wav launches ffmpeg as a subprocess. ffmpeg dynamically loads
        # a long list of GPU-related DLLs on startup (cuda-llvm, cuvid,
        # ffnvcodec, nvenc, nvdec, dxva2, d3d11/12va, vaapi, amf, vulkan).
        # If the Python process has already loaded ctranslate2 + CUDA runtime
        # via load_model(), some of those DLLs are locked/initialized in a
        # way that conflicts with ffmpeg's GPU probe, and ffmpeg fails to
        # start with Windows STATUS_DLL_INIT_FAILED (exit 3221225794) before
        # writing anything to stderr. Verified:
        # logs/transcribe_crash_2026-04-14_20-09-27.log.
        #
        # Running ffmpeg FIRST — while Python still only has customtkinter
        # and our light imports — keeps the CUDA DLLs untouched, and ffmpeg
        # probes/loads them cleanly. We then load Whisper after the WAV is
        # ready. Total user-visible time is the same; only the order changed.
        if on_status:
            on_status(
                "Подготовка аудио (нормализация громкости)..."
                if normalize_audio
                else "Подготовка аудио (ffmpeg)..."
            )
        wav_path, wav_is_temp = ensure_wav(audio_path, normalize=normalize_audio)
        chunks_dir = None
        diarize_handle: dict | None = None
        try:
            _check_cancelled(cancel_event)
            # Now safe to load Whisper — ffmpeg has already done any DLL
            # initialization it needs and exited.
            if on_status:
                on_status("Загрузка модели...")
            self.load_model()
            _check_cancelled(cancel_event)

            # Launch the diarization subprocess in WAIT mode RIGHT NOW so it
            # can import pyannote, load weights to CPU, and decode the audio
            # in parallel with Whisper inference. We send "GO\n" later (after
            # offload_to_cpu) and it then takes only ~1 s to move the
            # pipeline to CUDA. Without this, the user sees a 10-15 s dead
            # zone at 70 % between Whisper finishing and the first pyannote
            # progress line.
            if diarize:
                diarize_handle = self._launch_diarization_subprocess(
                    wav_path, hf_token,
                    device=diarize_device,
                    num_speakers=num_speakers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                    voice_lib_path=voice_lib_path,
                    on_progress=on_progress, on_status=on_status,
                )

            if on_status:
                on_status("Транскрипция...")

            # Long files are split into chunks before transcription, then the
            # results are concatenated with timestamp offsets. See
            # _LONG_FILE_THRESHOLD_S / _CHUNK_DURATION_S at top of file for
            # the rationale (numpy contiguous-allocation failure on Windows
            # for full-file STFT of >~90 min audio in faster-whisper's
            # feature_extractor). Files shorter than the threshold pass
            # through unchanged — split_wav_into_chunks returns
            # [(wav_path, 0.0)] in that case.
            duration = get_duration_s(wav_path)
            if duration > _LONG_FILE_THRESHOLD_S:
                chunks_dir = tempfile.mkdtemp(prefix="whisper_chunks_")
                if on_status:
                    on_status(f"Длинный файл ({int(duration//60)} мин) — нарезаю на части...")
                chunks = split_wav_into_chunks(
                    wav_path, _CHUNK_DURATION_S, chunks_dir,
                    overlap_s=_CHUNK_OVERLAP_S,
                )
            else:
                # Short-file sentinel: (path, chunk_start_abs, primary_start_abs).
                # Matching the 3-tuple shape of split_wav_into_chunks avoids a
                # branch in the per-chunk loop below.
                chunks = [(wav_path, 0.0, 0.0)]

            transcript_segments: list[dict] = []
            progress_weight = 0.7 if diarize else 1.0

            for chunk_idx, (chunk_path, chunk_start_abs, primary_start_abs) in enumerate(chunks):
                if on_status and len(chunks) > 1:
                    on_status(
                        f"Транскрипция части {chunk_idx + 1}/{len(chunks)}..."
                    )
                if language == "mixed":
                    chunk_segments = self._decode_chunk_mixed(
                        chunk_path,
                        chunk_start_abs,
                        primary_start_abs,
                        initial_prompt=initial_prompt,
                        hotwords_str=hotwords_str,
                        cancel_event=cancel_event,
                    )
                else:
                    chunk_segments = self._decode_chunk_single(
                        chunk_path,
                        chunk_start_abs,
                        primary_start_abs,
                        effective_language=effective_language,
                        initial_prompt=initial_prompt,
                        hotwords_str=hotwords_str,
                        cancel_event=cancel_event,
                    )
                for seg in chunk_segments:
                    transcript_segments.append(seg)
                    if on_progress and duration > 0:
                        percent = min(seg["end"] / duration * 100, 100.0)
                        on_progress(percent * progress_weight)

            if not diarize:
                if on_progress:
                    on_progress(100.0)
                # Strip the heavy ``words`` payload before exposing — the
                # subtitle formatters only need start/end/text. Keeps the
                # public segments shape consistent across diarize/no-diarize.
                # Mixed-mode (Phase 2) attaches a per-segment ``language``
                # tag the spec promises to forward to SRT/VTT exporters and
                # future features; preserve it when present. Single-mode
                # has no language key — the conditional spread keeps the
                # dict shape byte-identical to pre-Phase-2.
                self.last_segments = [
                    {
                        "start": s["start"],
                        "end": s["end"],
                        "text": s["text"],
                        **({"language": s["language"]} if "language" in s else {}),
                    }
                    for s in transcript_segments
                ]
                return format_timed(transcript_segments)

            # --- Diarization ---
            # Move Whisper weights from GPU VRAM to CPU memory so the
            # diarization subprocess gets the full GPU. Uses ctranslate2's
            # unload_model(to_cpu=True) — keeps the model object alive (no
            # destructor → no Fatal Python error: Aborted on Windows). Next
            # transcribe() call restores via load_model() in ~hundreds of ms.
            #
            # Without this, Whisper medium holds ~1086 MB VRAM and the
            # pyannote subprocess can't even initialize its CUDA context
            # (verified: logs/diarize_crash_2026-04-14_16-33-25.log shows
            # OOM at the very first torch.cuda.mem_get_info() call).
            logger.debug("phase=before_offload_to_cpu")
            self.offload_to_cpu()
            logger.debug("phase=after_offload_to_cpu")

            # Progress first, then status: app.py._on_progress overwrites the
            # label on every call, so the status update must come *after* to
            # survive.
            if on_progress:
                on_progress(70.0)
            if on_status:
                on_status("Диаризация (определение спикеров)...")

            # Send GO to the already-running subprocess and wait for it to
            # finish. The subprocess has been loading pyannote in parallel
            # with our Whisper transcription; after GO it does the GPU-only
            # work (preflight, pipeline.to(cuda), inference).
            logger.debug("phase=before_subprocess_go")
            _check_cancelled(cancel_event)
            assert diarize_handle is not None  # set above when diarize=True
            speaker_turns = self._await_diarization_subprocess(
                diarize_handle, cancel_event=cancel_event,
            )
            diarize_handle = None  # consumed; suppress finally-cleanup

            if on_progress:
                on_progress(90.0)

            labeled = _assign_speakers_word_level(transcript_segments, speaker_turns)

            if on_progress:
                on_progress(100.0)

            self.last_segments = labeled
            return format_diarized(labeled)
        finally:
            # If we launched the diarization subprocess but never sent GO
            # (transcription failed mid-loop, or cancel fired before await),
            # kill it so it doesn't outlive the parent waiting on stdin.
            if diarize_handle is not None:
                proc = diarize_handle["proc"]
                if proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        logger.exception("failed to clean up diarize subprocess")
            if wav_is_temp:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass  # best-effort cleanup
            if chunks_dir is not None:
                # Clean up all chunk WAVs + the temp dir. shutil.rmtree handles
                # the case where individual unlink calls fail.
                try:
                    shutil.rmtree(chunks_dir, ignore_errors=True)
                except Exception:
                    pass


