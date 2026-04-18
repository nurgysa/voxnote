"""
Isolated pyannote diarization worker.

Runs in a fresh Python subprocess spawned by transcriber.Transcriber so that
pyannote's CUDA state never collides with ctranslate2's (which caused
`Fatal Python error: Aborted` when the main process tried to host both).

Usage:
    python diarize_worker.py <audio_path> <device>

Env:
    HF_TOKEN — Hugging Face token for pyannote/speaker-diarization-3.1
    DIARIZE_NUM_SPEAKERS / _MIN_SPEAKERS / _MAX_SPEAKERS — optional speaker
        count hints forwarded to pyannote's pipeline kwargs.
    DIARIZE_VOICE_LIB — optional path to a JSON file with enrolled voices
        (format: [{name, dim, embedding_b64}, ...]). When set, detected
        SPEAKER_XX labels are matched against enrolled voices via cosine
        similarity + Hungarian assignment and renamed to real names. Unmatched
        speakers keep their SPEAKER_XX label.

Exit codes:
    0 — success; stdout contains one JSON array of [start, end, speaker] triples
    non-zero — failure; stderr contains the traceback

All human-readable output (warnings, progress, errors) goes to stderr. stdout
is reserved for the single JSON line that the parent process parses.
"""

import inspect
import json
import os
import sys
from contextlib import contextmanager

# Make stdout UTF-8 so non-ASCII speaker labels (unlikely, but possible) survive
# Windows' default cp1251 pipe encoding.
sys.stdout.reconfigure(encoding="utf-8")

import soundfile as sf  # noqa: E402
import torch  # noqa: E402

# cuDNN on GTX 1650 Ti (4 GB) crashes pyannote with HOST_ALLOCATION_FAILED.
# Native CUDA kernels are slower in theory but actually complete and are 7x
# faster than CPU for this workload. See memory/diarization_gpu_tricks.md.
torch.backends.cudnn.enabled = False


@contextmanager
def _suppress_inspect_stack():
    """
    Work around speechbrain 1.1 + lightning 2.6 LazyModule crash.

    Lightning's _restricted_classmethod wrapper eagerly calls inspect.stack()
    on every classmethod invocation, which walks sys.modules and triggers
    speechbrain LazyModule resolution for modules with missing deps (k2_fsa
    without k2 installed). The stack() result is only used to detect
    torch.jit.script context, which never applies here, so returning [] is safe.
    """
    original = inspect.stack
    inspect.stack = lambda *a, **kw: []
    try:
        yield
    finally:
        inspect.stack = original


class _StderrProgressHook:
    """
    pyannote Hook that forwards step progress to the parent process via stderr.

    The pipeline calls this on each internal step (segmentation, embeddings,
    discrete_diarization, ...) with `completed`/`total` counters. We emit one
    tab-separated line per call; transcriber.py's _run_diarization_subprocess
    parses these and drives the GUI progress bar in real time.

    Lines are emitted to stderr (not stdout) so they don't mix with the final
    JSON result. Format: "PROGRESS\\t<step>\\t<completed>\\t<total>\\n".
    """

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __call__(self, step_name, step_artifact, file=None, total=None, completed=None):
        if total is None or completed is None:
            return
        sys.stderr.write(f"PROGRESS\t{step_name}\t{completed}\t{total}\n")
        sys.stderr.flush()


def _load_audio(audio_path: str) -> tuple[torch.Tensor, int]:
    """
    Load a WAV file into a pyannote-ready ``(1, time) float32`` tensor using
    a chunked, pre-allocated strategy.

    Background: on Windows with 16 GB single-channel RAM and torch + pyannote +
    ctranslate2 all loaded, a plain ``sf.read(path, dtype="float32")`` for a
    60-minute 16 kHz file tries to grab ~230 MB of CONTIGUOUS virtual address
    space via numpy's PyMem allocator. That allocator struggles with fragmented
    subprocess heaps and fails with ``_ArrayMemoryError: Unable to allocate``
    even when total free RAM is several GB.

    Workaround: pre-allocate the final tensor via ``torch.empty`` (torch's
    caching allocator is more tolerant of fragmentation because it retries
    multiple times and groups allocations into larger pools), then stream the
    file into it with small soundfile reads. Each chunk is a short-lived
    numpy buffer (~512 KB at 64 K frames of float32) that is copied into the
    pre-allocated tensor and immediately freed, so peak extra RAM is minimal.

    pyannote expects ``(channel, time)`` with ``channel=1`` for mono sources.
    """
    with sf.SoundFile(audio_path) as f:
        total_frames = len(f)
        sample_rate = f.samplerate
        channels = f.channels

        # Pre-allocate the final mono tensor through torch's allocator.
        # If this fails we get a clean RuntimeError (torch OOM) instead of
        # numpy's obscure _ArrayMemoryError — and it's more likely to succeed
        # because torch tries harder.
        waveform = torch.empty((1, total_frames), dtype=torch.float32)

        chunk_frames = 65_536  # 64 K frames ≈ 4 seconds of 16 kHz audio
        pos = 0
        while pos < total_frames:
            n = min(chunk_frames, total_frames - pos)
            block = f.read(frames=n, dtype="float32", always_2d=False)
            if channels > 1 and block.ndim > 1:
                block = block.mean(axis=1)
            waveform[0, pos:pos + len(block)] = torch.from_numpy(block)
            pos += len(block)

    return waveform, sample_rate


def _emit_status(msg: str) -> None:
    """Emit a STATUS line for the parent process to forward to the GUI."""
    sys.stderr.write(f"STATUS\t{msg}\n")
    sys.stderr.flush()


def _emit_progress(step: str, completed: int, total: int) -> None:
    """Emit a synthetic PROGRESS line (for lifecycle checkpoints without a natural counter)."""
    sys.stderr.write(f"PROGRESS\t{step}\t{completed}\t{total}\n")
    sys.stderr.flush()


# Post-processing thresholds. Tuned on typical meeting/interview audio:
#   _MIN_TURN_S — turns shorter than this are usually VAD artifacts (breaths,
#     short "mm", or boundary mis-detection). Dropping them before merging
#     makes the pass below more effective.
#   _FLIP_FLOP_S — if speaker A is sandwiched inside speaker B for less than
#     this duration, A is relabeled as B. Addresses the classic pattern
#     B(long) → A(0.4s) → B(long) where A is an embedding-noise error.
_MIN_TURN_S = 0.3
_FLIP_FLOP_S = 0.8


def _postprocess_turns(
    turns: list[list],
) -> list[list]:
    """
    Clean up raw pyannote turns to reduce visible diarization errors.

    Two-pass, order-preserving:
      1) Drop turns shorter than _MIN_TURN_S.
      2) Merge adjacent same-speaker turns (they can become adjacent after
         step 1 even if they weren't originally).
      3) Collapse short flip-flop turns surrounded by a single other speaker.

    Returns a new list; does not mutate the input.
    """
    if not turns:
        return turns

    # Pass 1: filter micro-turns.
    kept = [t for t in turns if (t[1] - t[0]) >= _MIN_TURN_S]
    if not kept:
        return turns  # nothing survives — bail rather than return empty

    # Pass 2: merge consecutive same-speaker turns.
    merged: list[list] = [list(kept[0])]
    for start, end, speaker in kept[1:]:
        last = merged[-1]
        if speaker == last[2]:
            last[1] = end
        else:
            merged.append([start, end, speaker])

    # Pass 3: flip-flop suppression. Walk windows of 3; if the middle is short
    # AND surrounded by the same speaker on both sides, absorb it.
    cleaned: list[list] = []
    i = 0
    while i < len(merged):
        if (
            i + 2 < len(merged)
            and merged[i][2] == merged[i + 2][2]
            and merged[i][2] != merged[i + 1][2]
            and (merged[i + 1][1] - merged[i + 1][0]) < _FLIP_FLOP_S
        ):
            # Absorb: extend i's end to i+2's end, skip i+1 and i+2.
            cleaned.append([merged[i][0], merged[i + 2][1], merged[i][2]])
            i += 3
        else:
            cleaned.append(list(merged[i]))
            i += 1

    return cleaned


# Cosine-similarity floor below which a detected speaker is considered
# "nobody in the library". Tuned for pyannote/embedding (classic ECAPA
# x-vectors, 512-dim). With L2-normalized embeddings on both sides, same-
# speaker pairs usually score 0.65-0.90; different-speaker pairs cluster
# around 0.15-0.40. A threshold of 0.5 gives comfortable margin.
_ENROLL_MATCH_THRESHOLD = 0.5


def _match_to_voice_library(
    speaker_turns: list[list],
    waveform,   # torch.Tensor (1, time)
    sample_rate: int,
    voice_lib_path: str,
    hf_token: str | None,
) -> list[list]:
    """
    Replace auto ``SPEAKER_XX`` labels with enrolled names where cosine
    similarity exceeds ``_ENROLL_MATCH_THRESHOLD``.

    Algorithm:
      1) For each unique detected speaker, pick their longest single turn.
         (Longest gives the cleanest embedding with the least VAD noise.
         Concatenating all turns is more robust in principle but adds I/O
         complexity for marginal gain on normal meeting audio.)
      2) Slice the waveform and run pyannote/embedding on each slice.
         One forward pass per detected speaker — cheap.
      3) Build a (N_detected × N_enrolled) cosine similarity matrix.
         Both sides are L2-normalized so this is a plain matmul.
      4) Solve 1-to-1 assignment via Hungarian (scipy.optimize). Enforces
         that one enrolled voice can't claim two detected clusters even
         if similarities tie — exactly what we want in a 2-person call
         where pyannote split one speaker into two clusters.
      5) Apply the threshold AFTER assignment, not inside the cost matrix.
         This lets Hungarian pair low-confidence matches and then we drop
         them — rather than the optimizer preferring a junk off-diagonal
         match over a mid-quality on-diagonal one.

    Returns a new list with the rename map applied. Never raises: any
    failure (missing lib file, dim mismatch, model load error) logs to
    stderr and returns the input unchanged, so diarization degrades
    gracefully to the auto-labels case.
    """
    try:
        with open(voice_lib_path, "r", encoding="utf-8") as f:
            voices_raw = json.load(f)
    except Exception as e:
        print(f"voice library load failed: {e}", file=sys.stderr, flush=True)
        return speaker_turns

    if not voices_raw or not speaker_turns:
        return speaker_turns

    import base64
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from pyannote.audio import Inference, Model

    # Parse enrolled voices. Skip malformed entries silently; an isolated
    # bad row in config shouldn't kill the entire matching pass.
    enrolled_names: list[str] = []
    enrolled_embs: list[np.ndarray] = []
    for v in voices_raw:
        name = v.get("name")
        enc = v.get("embedding_b64")
        if not name or not enc:
            continue
        try:
            vec = np.frombuffer(base64.b64decode(enc), dtype=np.float32)
        except Exception:
            continue
        norm = float(np.linalg.norm(vec)) + 1e-10
        enrolled_embs.append((vec / norm).astype(np.float32))
        enrolled_names.append(str(name))
    if not enrolled_embs:
        return speaker_turns

    # Find each speaker's longest turn.
    longest_by_speaker: dict[str, tuple[float, float]] = {}
    for start, end, spk in speaker_turns:
        dur = float(end) - float(start)
        cur = longest_by_speaker.get(spk)
        if cur is None or dur > (cur[1] - cur[0]):
            longest_by_speaker[spk] = (float(start), float(end))

    # Load embedding model once. Small (~30 MB VRAM for pyannote/embedding).
    try:
        with _suppress_inspect_stack():
            emb_model = Model.from_pretrained(
                "pyannote/embedding", token=hf_token,
            )
            inference = Inference(emb_model, window="whole")
        inference.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    except Exception as e:
        print(f"embedding model load failed: {e}", file=sys.stderr, flush=True)
        return speaker_turns

    detected_names: list[str] = []
    detected_embs: list[np.ndarray] = []
    total_frames = waveform.shape[1]
    for spk, (start, end) in longest_by_speaker.items():
        s_frame = max(0, int(start * sample_rate))
        e_frame = min(total_frames, int(end * sample_rate))
        if e_frame - s_frame < sample_rate:  # < 1 second → unusable
            continue
        slice_wf = waveform[:, s_frame:e_frame]
        try:
            emb = inference({"waveform": slice_wf, "sample_rate": sample_rate})
        except Exception as e:
            print(f"embedding extraction failed for {spk}: {e}",
                  file=sys.stderr, flush=True)
            continue
        if isinstance(emb, torch.Tensor):
            emb = emb.detach().cpu().numpy()
        vec = np.asarray(emb, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec)) + 1e-10
        detected_embs.append((vec / norm).astype(np.float32))
        detected_names.append(spk)

    if not detected_embs:
        return speaker_turns

    D = np.stack(detected_embs)
    E = np.stack(enrolled_embs)
    if D.shape[1] != E.shape[1]:
        print(
            f"embedding dim mismatch (detected {D.shape[1]} vs enrolled "
            f"{E.shape[1]}). Re-enroll voices with the current model.",
            file=sys.stderr, flush=True,
        )
        return speaker_turns

    sim = D @ E.T  # cosine since both are unit-norm
    det_idx, enr_idx = linear_sum_assignment(-sim)  # max-cost via negation

    rename: dict[str, str] = {}
    for di, ei in zip(det_idx, enr_idx):
        if sim[di, ei] >= _ENROLL_MATCH_THRESHOLD:
            rename[detected_names[di]] = enrolled_names[ei]
            print(
                f"match: {detected_names[di]} -> {enrolled_names[ei]} "
                f"(cos={sim[di, ei]:.3f})",
                file=sys.stderr, flush=True,
            )
        else:
            print(
                f"no match: {detected_names[di]} (best cos={sim[di, ei]:.3f} "
                f"< threshold {_ENROLL_MATCH_THRESHOLD})",
                file=sys.stderr, flush=True,
            )

    if not rename:
        return speaker_turns
    return [[s, e, rename.get(spk, spk)] for s, e, spk in speaker_turns]


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: diarize_worker.py <audio_path> <device>", file=sys.stderr)
        return 2

    audio_path = sys.argv[1]
    # sys.argv[2] (device) is accepted for CLI-compat with transcriber.py but
    # ignored — diarization is GPU-only now.
    hf_token = os.environ.get("HF_TOKEN") or None

    if not os.path.isfile(audio_path):
        print(f"audio file not found: {audio_path}", file=sys.stderr)
        return 2

    # When DIARIZE_WAIT=1 is set, the parent will run Whisper transcription
    # AFTER spawning us. We do all CPU-side setup (pyannote import, weights
    # download/load to CPU, audio decode) in parallel with that transcription,
    # then block on a "GO\n" line on stdin. This collapses the 10-15 s
    # dead-zone the user sees at 70 % between Whisper finishing and
    # pipeline.to(cuda) returning.
    wait_mode = bool(os.environ.get("DIARIZE_WAIT"))

    # Cheap preflight: surface "no GPU at all" before importing pyannote,
    # so the user gets a fast, actionable error if they're on a CPU-only
    # machine. The meaningful VRAM check happens AFTER GO (see below) when
    # Whisper has had a chance to free its weights.
    if not torch.cuda.is_available():
        print("CUDA недоступна — для диаризации нужен Nvidia GPU",
              file=sys.stderr, flush=True)
        return 3

    # Import pyannote lazily so the import-cost is only paid in the subprocess.
    from pyannote.audio import Pipeline

    # Pyannote weights load to CPU memory (~700 MB), not VRAM, so this is
    # safe to run while the parent's Whisper still owns the GPU.
    with _suppress_inspect_stack():
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )

    # Pyannote's built-in file loader (Audio() class) requires torchcodec,
    # which is broken on this Windows machine (libtorchcodec_core*.dll not
    # loadable). The supported fallback is preloaded waveform as a dict.
    # Loading is CPU+RAM only — runs in parallel with Whisper inference.
    waveform, sample_rate = _load_audio(audio_path)
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}

    # Block until parent has freed VRAM (offloaded Whisper to CPU). Until
    # GO, no STATUS/PROGRESS messages have been emitted — the parent's UI
    # is showing Whisper progress and would be visually disturbed by a
    # diarization status overwriting the label. After GO we surface the
    # full lifecycle as before.
    if wait_mode:
        line = sys.stdin.readline()
        if not line:
            # stdin closed before GO — parent died or cancelled; abort cleanly.
            print("parent closed stdin before GO; aborting", file=sys.stderr, flush=True)
            return 4
        if line.strip() != "GO":
            print(f"unexpected stdin line {line!r}; expected 'GO'",
                  file=sys.stderr, flush=True)
            return 4

    _emit_status("Запуск диаризации...")

    # Re-check VRAM now that the parent has offloaded Whisper. Done AFTER
    # the GO signal because the pre-GO check would see Whisper's weights
    # still resident and fail the launch unnecessarily.
    _MIN_FREE_VRAM_GB = 1.2
    free_bytes, _ = torch.cuda.mem_get_info()
    free_gb = free_bytes / 1024**3
    if free_gb < _MIN_FREE_VRAM_GB:
        print(
            f"Недостаточно VRAM для диаризации: {free_gb:.2f} GB свободно, "
            f"нужно >= {_MIN_FREE_VRAM_GB} GB. Закройте другие приложения, "
            f"использующие GPU (браузер, Discord), или выберите меньшую "
            f"модель Whisper.",
            file=sys.stderr, flush=True,
        )
        return 3

    target = torch.device("cuda")
    pipeline.to(target)
    print(f"pipeline on {target}", file=sys.stderr, flush=True)

    # Fixed batch=16: safe on 4 GB VRAM, close to max throughput on Turing.
    # pyannote default (32) is tuned for 8+ GB GPUs and has been observed to
    # OOM on GTX 1650 Ti. We set both the public attribute and the private
    # _segmentation/_embedding mirrors as cheap insurance against version drift.
    _BATCH_SIZE = 16
    for attr in ("segmentation_batch_size", "embedding_batch_size"):
        if hasattr(pipeline, attr):
            setattr(pipeline, attr, _BATCH_SIZE)
    for obj_attr in ("_segmentation", "_embedding"):
        obj = getattr(pipeline, obj_attr, None)
        if obj is not None and hasattr(obj, "batch_size"):
            obj.batch_size = _BATCH_SIZE

    mode = f"cuda/batch={_BATCH_SIZE} (free {free_gb:.1f} GB)"
    print(f"diarization mode: {mode}", file=sys.stderr, flush=True)
    _emit_status(f"Режим: {mode}")

    # Speaker-count hints (optional, env-supplied by transcriber.py).
    # Pyannote accepts num_speakers=K OR (min_speakers, max_speakers); passing
    # a known value cuts DER ~2× vs. its internal auto-detection. Values are
    # forwarded only if present, so an empty/unset env leaves pyannote's
    # default heuristic in place.
    pipeline_kwargs: dict = {"hook": None}  # placeholder; hook set below
    def _int_env(name: str) -> int | None:
        raw = os.environ.get(name)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    num_spk = _int_env("DIARIZE_NUM_SPEAKERS")
    min_spk = _int_env("DIARIZE_MIN_SPEAKERS")
    max_spk = _int_env("DIARIZE_MAX_SPEAKERS")
    if num_spk is not None:
        pipeline_kwargs["num_speakers"] = num_spk
        print(f"speaker hint: num_speakers={num_spk}", file=sys.stderr, flush=True)
    else:
        if min_spk is not None:
            pipeline_kwargs["min_speakers"] = min_spk
        if max_spk is not None:
            pipeline_kwargs["max_speakers"] = max_spk
        if min_spk is not None or max_spk is not None:
            print(
                f"speaker hint: min={min_spk} max={max_spk}",
                file=sys.stderr, flush=True,
            )

    # GPU-only inference. OOM, cuBLAS init failures and any other errors
    # propagate out of this subprocess as a non-zero exit — the parent turns
    # them into a RuntimeError with the stderr tail so the user sees the real
    # cause instead of a silent CPU slowdown.
    _emit_status("Анализ речи...")
    with _StderrProgressHook() as hook:
        pipeline_kwargs["hook"] = hook
        diarization = pipeline(audio_input, **pipeline_kwargs)

    # pyannote doesn't emit progress for discrete_diarization + our own post-
    # processing below; bump the bar to the end of our allotted band so the
    # UI doesn't freeze at 87% during the last few seconds of work.
    _emit_status("Сборка результата...")
    _emit_progress("discrete_diarization", 1, 1)

    # pyannote 4.x returns a DiarizationResult with exclusive_speaker_diarization
    # (no overlaps — better for transcription); fall back to speaker_diarization
    # or the object itself for 3.x compatibility.
    annotation = getattr(
        diarization, "exclusive_speaker_diarization",
        getattr(diarization, "speaker_diarization", diarization),
    )

    speaker_turns: list[list] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        speaker_turns.append([float(turn.start), float(turn.end), str(speaker)])

    speaker_turns = _postprocess_turns(speaker_turns)

    # Optional enrollment matching: replace SPEAKER_XX labels with enrolled
    # names when a match exceeds the similarity threshold. waveform is the
    # already-loaded tensor from _load_audio; no second audio I/O needed.
    voice_lib_path = os.environ.get("DIARIZE_VOICE_LIB")
    if voice_lib_path and os.path.isfile(voice_lib_path):
        _emit_status("Распознавание голосов...")
        speaker_turns = _match_to_voice_library(
            speaker_turns, waveform, sample_rate, voice_lib_path, hf_token,
        )

    print(f"done: {len(speaker_turns)} turns", file=sys.stderr, flush=True)
    sys.stdout.write(json.dumps(speaker_turns))
    sys.stdout.flush()
    sys.stderr.flush()
    # Fast-exit via os._exit to skip ~8s of torch/pyannote CUDA teardown at
    # Python interpreter shutdown. The OS reclaims the CUDA context on process
    # exit anyway, and we've already flushed all output. Normal `return 0`
    # leaves the GUI progress bar frozen at 90% while cleanup runs.
    os._exit(0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
