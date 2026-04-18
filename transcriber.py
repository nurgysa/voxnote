import json
import os
import subprocess
import sys
import threading

# ctranslate2 must be imported before torch on Windows to avoid CUDA DLL conflicts.
# audio_io is torch-free (see its module docstring), so importing it here is safe.
import ctranslate2  # noqa: F401
import shutil
import tempfile

from faster_whisper import WhisperModel

from audio_io import ensure_wav, get_duration_s, split_wav_into_chunks
from logging_setup import crash_log_path, get_logger
from transcript_format import format_diarized, format_timed

logger = get_logger(__name__)


class TranscriptionCancelled(Exception):
    """Raised inside ``Transcriber.transcribe`` when the cancel event fires.

    Caught in ``app._run_transcription`` and routed to a "cancelled" UI
    state distinct from the "error" path — the user asked to stop, so
    we don't show a scary error dialog.
    """


def _check_cancelled(cancel_event) -> None:
    """Helper: raise ``TranscriptionCancelled`` if the event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled()


# Files longer than this are split into chunks before transcription. The
# threshold is set just below the empirically-observed failure point of the
# numpy contiguous-allocation bug in faster_whisper's full-file STFT
# preprocessing on Windows (verified failure on 118-min, success on 62-min).
_LONG_FILE_THRESHOLD_S = 90 * 60   # 90 minutes
# Each chunk's STFT must fit a single contiguous numpy allocation. 20 min
# at 16 kHz / hop=160 / n_fft=400 / complex128 = ~390 MB — comfortable on
# Windows fragmented heaps. Keep below ~30 min for headroom.
_CHUNK_DURATION_S = 20 * 60        # 20 minutes
# Chunk boundary overlap: each chunk after the first is extended 3 s
# backward, so boundary words are transcribed in both chunks and the caller
# can keep the chronologically-earlier version. 3 s is enough to span any
# single utterance (average speaking rate ~2.5 words/s) without materially
# inflating total inference time (3 s × N chunks ≈ 0.25% overhead on a
# 2-hour file). Dedup lives in transcribe() below — see primary_start_abs.
_CHUNK_OVERLAP_S = 3.0


# Safe upper bound on initial_prompt length. Whisper enforces a 224-token
# prompt limit; truncating at this many CHARS before tokenization keeps us
# comfortably under even in worst-case Cyrillic BPE (2-3 bytes per token).
# If the truncated string cuts a term in half the term is simply dropped
# from the prompt — hotwords= still biases for it at token level.
_MAX_PROMPT_CHARS = 400


# Language-specific prompt frames. Whisper uses these as decode-time context:
# mentioning the language and framing it as "transcript of a meeting" subtly
# biases punctuation, capitalization and word choice toward that register.
# Leaving a language out (e.g. "auto") yields None → prompt is skipped.
_PROMPT_FRAMES: dict[str, dict[str, str]] = {
    "ru": {
        "prefix": "Расшифровка разговора на русском языке.",
        "terms_label": "Упомянутые термины",
    },
    "kk": {
        "prefix": "Қазақ тіліндегі әңгіменің жазбасы.",
        "terms_label": "Аталған терминдер",
    },
    "en": {
        "prefix": "Transcript of a spoken conversation in English.",
        "terms_label": "Terms mentioned",
    },
}


def _build_initial_prompt(
    language: str | None,
    hotwords_str: str | None,
) -> str | None:
    """
    Assemble Whisper's ``initial_prompt`` from the language hint and the user's
    hotword dictionary.

    The prompt pairs two signals:
      1) A natural-language frame ("Transcript of a conversation in X...") —
         anchors stylistic register and orthography;
      2) A comma-separated list of domain terms — biases spelling of names
         and jargon (e.g. "Kubernetes" not "Kuber Netting", "Нургиса" not
         "Нур Гиса"). This is redundant with the ``hotwords=`` parameter but
         works via a different mechanism (decode context vs CTC-style biasing)
         and is more reliable for proper-noun casing.

    Returns None when neither signal is available, so the caller can pass None
    straight through to faster-whisper (which treats None as "no prompt").
    """
    frame = _PROMPT_FRAMES.get(language) if language else None
    has_terms = bool(hotwords_str and hotwords_str.strip())
    if frame is None and not has_terms:
        return None

    parts: list[str] = []
    if frame is not None:
        parts.append(frame["prefix"])
    if has_terms:
        label = frame["terms_label"] if frame is not None else "Terms"
        parts.append(f"{label}: {hotwords_str.strip()}.")

    prompt = " ".join(parts)
    if len(prompt) <= _MAX_PROMPT_CHARS:
        return prompt

    # Truncate on the last comma before the limit so we don't cut a term in
    # half. If no comma is found (shouldn't happen for multi-term prompts),
    # hard-truncate at the limit.
    head = prompt[:_MAX_PROMPT_CHARS]
    cut = head.rfind(",")
    if cut > 0:
        return head[:cut] + "."
    return head


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
    Parse one `PROGRESS\\t<step>\\t<completed>\\t<total>` line from the worker.

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
        if self._device != "auto":
            return self._device
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"

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
        worker = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "diarize_worker.py",
        )
        env = dict(os.environ)
        env["DIARIZE_WAIT"] = "1"
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
            [sys.executable, worker, audio_path, "cuda"],
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
                    raise TranscriptionCancelled()
                if elapsed >= deadline:
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(proc.args, deadline)

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

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = False,
        hf_token: str | None = None,
        hotwords: str | None = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        voice_lib_path: str | None = None,
        normalize_audio: bool = True,
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
        hotwords_str = hotwords.strip() if hotwords and hotwords.strip() else None
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
                    language=language,
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
                    transcript_segments.append({
                        "start": abs_start,
                        "end": abs_end,
                        "text": segment.text.strip(),
                        "words": seg_words,
                    })
                    if on_progress and duration > 0:
                        # Absolute position in the full file.
                        percent = min(abs_end / duration * 100, 100.0)
                        on_progress(percent * progress_weight)

            if not diarize:
                if on_progress:
                    on_progress(100.0)
                # Strip the heavy ``words`` payload before exposing — the
                # subtitle formatters only need start/end/text. Keeps the
                # public segments shape consistent across diarize/no-diarize.
                self.last_segments = [
                    {"start": s["start"], "end": s["end"], "text": s["text"]}
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


def _assign_speakers_word_level(
    segments: list[dict],
    speaker_turns: list[tuple[float, float, str]],
) -> list[dict]:
    """
    Split each Whisper segment into sub-segments along speaker-turn boundaries.

    Fixes the dominant dialogue error in segment-level max-overlap assignment:
    a single Whisper segment spanning two speakers ("— Да. — Согласен.") used
    to be labeled with one speaker (max overlap wins). Here each WORD inside
    the segment is placed on the pyannote timeline independently, and adjacent
    same-speaker words are re-grouped into output sub-segments.

    Input:  segments from Transcriber.transcribe() — dicts with
            {start, end, text, words:[{start,end,word}, ...]}.
    Output: flat list of {start, end, text, speaker} dicts in chronological
            order, ready for _format_diarized (which does the numbering and
            same-speaker merge across segments).

    Segments with empty `words` (Whisper DTW pass skipped them) fall back to
    whole-segment max-overlap — same behavior as before word-level path.
    """
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            # Fallback: no per-word times → keep the old behavior for this seg.
            speaker = _find_speaker_by_overlap(
                seg["start"], seg["end"], speaker_turns,
            )
            out.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": speaker,
            })
            continue

        # Group consecutive same-speaker words into emitted sub-segments.
        # We use the word midpoint as the probe time — more robust than
        # start/end at boundaries where a word straddles a speaker change.
        current_words: list[dict] = []
        current_speaker: str | None = None

        def _flush() -> None:
            if not current_words:
                return
            text = "".join(w["word"] for w in current_words).strip()
            if not text:
                return  # pure-whitespace (leading space tokens) — skip
            out.append({
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": text,
                "speaker": current_speaker,
            })

        for w in words:
            mid = (w["start"] + w["end"]) / 2.0
            sp = _speaker_at_time(mid, speaker_turns)
            if sp != current_speaker and current_words:
                _flush()
                current_words = []
            current_speaker = sp
            current_words.append(w)

        _flush()

    return out


def _speaker_at_time(
    t: float,
    speaker_turns: list[tuple[float, float, str]],
) -> str:
    """
    Return the speaker active at time ``t``.

    First checks for a turn whose [start, end] interval contains ``t``; if
    none (common at turn edges or in VAD gaps that pyannote didn't fill),
    falls back to the turn with the smallest edge distance. Guarantees a
    non-None return even when speaker_turns is empty (SPEAKER_00), so the
    caller never has to handle None.
    """
    best_speaker = "SPEAKER_00"
    best_dist = float("inf")
    for start, end, speaker in speaker_turns:
        if start <= t <= end:
            return speaker
        dist = t - end if t > end else start - t
        if dist < best_dist:
            best_dist = dist
            best_speaker = speaker
    return best_speaker


def _find_speaker_by_overlap(
    seg_start: float,
    seg_end: float,
    speaker_turns: list[tuple[float, float, str]],
) -> str:
    """Find which speaker has the most temporal overlap with a segment."""
    overlap_by_speaker: dict[str, float] = {}
    for start, end, speaker in speaker_turns:
        # Calculate overlap between segment and speaker turn
        overlap_start = max(seg_start, start)
        overlap_end = min(seg_end, end)
        overlap = max(0.0, overlap_end - overlap_start)
        if overlap > 0:
            overlap_by_speaker[speaker] = overlap_by_speaker.get(speaker, 0.0) + overlap

    if overlap_by_speaker:
        return max(overlap_by_speaker, key=overlap_by_speaker.get)

    # Fallback: find nearest speaker turn
    min_dist = float("inf")
    nearest = "SPEAKER_00"
    for start, end, speaker in speaker_turns:
        dist = min(abs(seg_start - end), abs(seg_end - start))
        if dist < min_dist:
            min_dist = dist
            nearest = speaker
    return nearest


