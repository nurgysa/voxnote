# Code-switching KZ+RU+EN Phase 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a VAD pre-pass + per-segment language detection on the LOCAL Whisper path so `language == "mixed"` (the Phase 1 sentinel) produces true code-switching for KZ+RU+EN audio instead of the Phase 1 prompt-only band-aid.

**Architecture:** New `transcriber/segmenter.py` wraps Silero VAD (already used by `silence_remover.py`) with parameters tuned for language detection. `Transcriber.transcribe()` grows a branch inside its per-chunk loop: when `language == "mixed"`, the chunk is VAD-split and each speech segment is fed to `model.transcribe(audio_slice, language=None, vad_filter=False, ...)` separately — Whisper's internal `detect_language()` then runs per slice. Every other code path (single-language, all 5 cloud providers, diarization, speaker alignment, formatting) is physically untouched.

**Tech Stack:** Python 3.10, faster-whisper (Silero VAD via `faster_whisper.vad`), soundfile (already a dep via `audio_io.py`), pytest, ruff. No new packages.

**Spec:** `docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md` (commit `fddb3da` on `docs/code-switching-phase-2-spec`).

---

## Pre-flight (do once before starting)

- [ ] Confirm spec is committed: `git log --oneline -1 docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md` → should show commit `fddb3da`.
- [ ] Confirm baseline tests green: `pytest -q` → should report `285 passed` (per CLAUDE.md baseline).
- [ ] Confirm ruff clean: `python -m ruff check .` → exit code 0.
- [ ] Ship spec + plan PR first (current branch `docs/code-switching-phase-2-spec` carries both files; same pattern as PR #20 for Phase 1):
      ```
      git push -u origin docs/code-switching-phase-2-spec
      gh pr create --title "docs: code-switching KZ+RU+EN Phase 2 spec + implementation plan" --body "Per docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md and docs/superpowers/plans/2026-05-22-code-switching-kz-ru-en-phase-2.md. Docs-only PR; implementation follows in PR-A, PR-B, and optional PR-C."
      ```
- [ ] Wait for spec+plan PR to merge to `main` before starting PR-A (per `feedback_stacked_pr_squash_merge.md` — no stacking).

## File map

| PR | File | Change | Estimated LOC |
|---|---|---|---|
| A | `transcriber/segmenter.py` | **NEW** — `vad_split()` helper | ~50 |
| A | `tests/test_segmenter.py` | **NEW** — 5 unit tests for the wrapper | ~50 |
| B | `transcriber/__init__.py` | Per-chunk `if language == "mixed":` branch; extract existing inline logic to `_decode_chunk_single`; add `_decode_chunk_mixed` | ~120 |
| B | `tests/test_transcriber_mixed.py` | **NEW** — 8 mock-based integration tests | ~120 |
| B | `tests/test_transcriber_pure.py` | +1 regression test (language="ru" doesn't trigger mixed path) | ~15 |
| C | `transcriber/segmenter.py` | Possibly tweak VadOptions after real-world signal | 0-5 |

**Total**: ~360 LOC across 3 PRs (manual A/B QA in PR-B description; PR-C may have zero diff if VAD defaults are fine).

## Branch strategy

One topic branch per PR. Each branch is created off `main` AFTER the previous PR has merged. No stacking.

```
main (after spec PR merge)
 ├── feat/code-switching-phase-2-segmenter   → PR-A
 │
 main (after PR-A merge)
 ├── feat/code-switching-phase-2-integration → PR-B
 │
 main (after PR-B merge)
 ├── feat/code-switching-phase-2-tuning      → PR-C  (if any tuning needed)
```

Optional post-PR-C: `docs/claude-md-after-code-switching-phase-2` for the CLAUDE.md "Active work" update.

---

## PR-A: Segmenter foundation

**Branch:** `feat/code-switching-phase-2-segmenter` (created from `main` after spec PR merges).

**Goal:** Ship the `transcriber/segmenter.py` module as a pure, tested helper. No integration with `Transcriber.transcribe()` yet — that's PR-B.

**Pre-task:**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/code-switching-phase-2-segmenter
```

---

### Task A.1: Create `transcriber/segmenter.py` + first test (empty audio)

**Files:**
- Create: `transcriber/segmenter.py`
- Create: `tests/test_segmenter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_segmenter.py`:

```python
"""Tests for transcriber.segmenter.vad_split — VAD wrapper used by the
Phase 2 mixed-language code path.

Pure module — no Whisper, no GPU. Faster-whisper's Silero VAD is
imported lazily inside vad_split() (matching silence_remover.py's
pattern), so the test process pays the import cost only when these
tests run, not at collection.
"""
from __future__ import annotations

import numpy as np

from transcriber.segmenter import vad_split


def test_vad_split_empty_audio_returns_empty_list():
    """Defensive: a zero-length array must not crash get_speech_timestamps
    and must not produce phantom segments. Matches silence_remover.py's
    empty-input contract."""
    samples = np.array([], dtype=np.float32)
    result = vad_split(samples, sample_rate=16_000)
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_segmenter.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'transcriber.segmenter'`.

- [ ] **Step 3: Create `transcriber/segmenter.py` with the minimal `vad_split`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_segmenter.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add transcriber/segmenter.py tests/test_segmenter.py
git commit -m "feat(transcriber/segmenter): VAD wrapper for Phase 2 mixed mode

New transcriber.segmenter.vad_split() wraps faster_whisper.vad.get_speech_timestamps
with parameters tuned for language detection (longer min_speech_duration_ms
than silence_remover.py because Whisper's detect_language needs ~0.5s
of audio to be reliable). Empty input returns [] — first defensive test
covers this contract.

Pure helper for now; integration into Transcriber.transcribe() lands
in PR-B."
```

---

### Task A.2: Add remaining VAD tests (all-silence, all-speech, alternating, micro-blip)

**Files:**
- Modify: `tests/test_segmenter.py`
- Modify (only if a test fails): `transcriber/segmenter.py`

- [ ] **Step 1: Add the four remaining tests**

Append to `tests/test_segmenter.py`:

```python
def test_vad_split_all_silence_returns_empty_list():
    """5 seconds of literal zeros must not yield any speech regions.
    Silero VAD's threshold defaults are well above the noise floor, so
    a zero-amplitude signal should never cross it."""
    samples = np.zeros(16_000 * 5, dtype=np.float32)
    result = vad_split(samples, sample_rate=16_000)
    assert result == []


def test_vad_split_all_speech_returns_one_group():
    """Synthetic noise > threshold across the entire input should yield
    a single speech region spanning most of the input. Exact boundaries
    depend on VAD's internal frame alignment; we assert structural
    properties, not byte-exact start/end."""
    # Pink-ish noise: random normal scaled to roughly normal speech level.
    rng = np.random.default_rng(seed=42)
    samples = (rng.standard_normal(16_000 * 5).astype(np.float32) * 0.2).clip(-1.0, 1.0)
    result = vad_split(samples, sample_rate=16_000)
    assert len(result) == 1, f"Expected 1 group, got {len(result)}: {result}"
    # Should cover most of the input (allow some VAD frame padding).
    seg = result[0]
    assert seg["start"] < 16_000  # starts within first second
    assert seg["end"] > 16_000 * 4  # ends past 4 seconds


def test_vad_split_alternating_returns_two_groups():
    """Speech-silence-speech pattern (each block 2s) must yield exactly
    two speech regions with a gap between them. Tests that VAD's
    min_silence_duration_ms=500 doesn't merge regions separated by
    longer silence."""
    rng = np.random.default_rng(seed=7)
    block_samples = 16_000 * 2  # 2 seconds at 16 kHz
    speech1 = (rng.standard_normal(block_samples).astype(np.float32) * 0.2).clip(-1.0, 1.0)
    silence = np.zeros(block_samples, dtype=np.float32)
    speech2 = (rng.standard_normal(block_samples).astype(np.float32) * 0.2).clip(-1.0, 1.0)
    samples = np.concatenate([speech1, silence, speech2])

    result = vad_split(samples, sample_rate=16_000)
    assert len(result) == 2, f"Expected 2 groups, got {len(result)}: {result}"
    # First group entirely within first 2 seconds (allow small padding).
    assert result[0]["end"] <= block_samples + 16_000 // 2  # +0.5s padding tolerance
    # Second group starts after the silence block.
    assert result[1]["start"] >= block_samples * 2 - 16_000 // 2


def test_vad_split_micro_blips_merged():
    """Speech blocks separated by silence shorter than
    min_silence_duration_ms=500 must be MERGED into a single region.
    Validates that we picked the right min_silence param for Phase 2's
    language-detection use case."""
    rng = np.random.default_rng(seed=13)
    block_samples = 16_000 * 2  # 2s speech
    micro_silence = np.zeros(int(16_000 * 0.2), dtype=np.float32)  # 200ms — < 500ms
    speech1 = (rng.standard_normal(block_samples).astype(np.float32) * 0.2).clip(-1.0, 1.0)
    speech2 = (rng.standard_normal(block_samples).astype(np.float32) * 0.2).clip(-1.0, 1.0)
    samples = np.concatenate([speech1, micro_silence, speech2])

    result = vad_split(samples, sample_rate=16_000)
    # Single region because the micro-silence is below the min_silence threshold.
    assert len(result) == 1, f"Expected 1 group (micro-silence merged), got {len(result)}: {result}"
```

- [ ] **Step 2: Run all segmenter tests**

```
pytest tests/test_segmenter.py -v
```
Expected: all 5 PASS. If any fails:

- **`all_speech` fails with 0 groups**: noise level too low — bump the `0.2` multiplier to `0.3` in the test (real human speech is typically louder).
- **`alternating` fails with 1 group**: the gap was below `min_silence_duration_ms` after VAD's padding accounting — increase the silence block in the test to `block_samples * 3 // 2` (3 seconds).
- **`micro_blips_merged` fails with 2 groups**: VAD's `min_silence_duration_ms=500` isn't being honoured — verify segmenter.py uses the constant. If still failing, the test's micro-silence (200ms) may be straddling a VAD frame boundary; bump it down to 100ms.

These adjustments should NOT touch `transcriber/segmenter.py` — they're tuning of the test inputs, not the production parameters. If you find yourself wanting to change `_MIN_SPEECH_MS` etc., **stop and re-read the spec** — those values are deliberate.

- [ ] **Step 3: Commit**

```bash
git add tests/test_segmenter.py
git commit -m "test(segmenter): cover silence, speech, alternating, micro-blip cases

Four additional tests verify vad_split's behavior on the edge cases
that matter for Phase 2:
- all silence (no false-positive speech regions)
- all speech (one continuous region)
- speech-silence-speech (2 regions, correct boundary)
- speech-micro_silence-speech (1 region, merging behavior)

Validates that min_silence_duration_ms=500 is honoured (Phase 2
needs longer silences than silence_remover.py's 250 ms to avoid
fragmenting cross-language utterances)."
```

---

### Task A.3: PR-A wrap-up

- [ ] **Step 1: Run full suite + lint**

```
pytest -q
python -m ruff check .
```
Expected: 285 baseline + 5 new = 290 tests PASS, ruff clean.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/code-switching-phase-2-segmenter
gh pr create --title "feat(code-switching): VAD segmenter for Phase 2 [PR-A]" --body "$(cat <<'EOF'
## Summary

Foundation for Phase 2 of the KZ+RU+EN code-switching feature (PR-A of 3).

- New `transcriber/segmenter.py` module: `vad_split(samples, sample_rate)` wraps `faster_whisper.vad.get_speech_timestamps` with VAD parameters tuned for language detection (`min_speech_duration_ms=500`, `min_silence_duration_ms=500`).
- `tests/test_segmenter.py` covers 5 cases: empty, all-silence, all-speech, alternating, micro-blip-merged.

Pure helper — no integration with `Transcriber.transcribe()` yet. PR-B wires it in.

See [Phase 2 spec](docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md) for the full design.

## Test plan

- [x] `pytest -q` — 285 baseline + 5 new = 290 green
- [x] `python -m ruff check .` — clean
- [x] No behaviour change for any existing code path; this PR only adds a new module that nothing imports yet
EOF
)"
```

- [ ] **Step 3: Wait for review + merge before starting PR-B.** Per `feedback_stacked_pr_squash_merge.md`.

---

## PR-B: Integration

**Branch:** `feat/code-switching-phase-2-integration` (created from `main` after PR-A merges).

**Goal:** Wire `vad_split` into `Transcriber.transcribe()` via a branch on `language == "mixed"`. Extract the existing per-chunk decode logic into `_decode_chunk_single` so the new `_decode_chunk_mixed` is a parallel helper.

**Pre-task:**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/code-switching-phase-2-integration
```

---

### Task B.1: Extract `_decode_chunk_single` (refactor only, no behaviour change)

**Files:**
- Modify: `transcriber/__init__.py` (the per-chunk loop body at lines 732-825)

This task is a pure refactor: lift the inline per-chunk loop body into a private method. No new tests, no behaviour change. The point is to isolate the existing logic so PR-B's later tasks can drop a sibling `_decode_chunk_mixed` next to it without weaving into a long inline block.

- [ ] **Step 1: Read the current loop body**

```
sed -n '730,830p' transcriber/__init__.py
```

Confirm the loop body covers: per-chunk status update, the `model.transcribe(chunk_path, ...)` call, the inner `for segment in segments:` loop with `_check_cancelled`, dedup-via-midpoint check, word extraction, `transcript_segments.append(...)`, progress callback.

- [ ] **Step 2: Add `_decode_chunk_single` method**

In `transcriber/__init__.py`, insert this method on the `Transcriber` class. Pick a location just before `transcribe()` (after `_transcribe_via_cloud`, which sits around line 477-557):

```python
def _decode_chunk_single(
    self,
    chunk_path: str,
    chunk_start_abs: float,
    primary_start_abs: float,
    *,
    effective_language: str | None,
    initial_prompt: str | None,
    hotwords_str: str | None,
    cancel_event,
) -> list[dict]:
    """Single-language per-chunk decode — the pre-Phase-2 code path.

    Extracted verbatim from the inline body of ``transcribe()`` so the
    parallel mixed-mode helper (``_decode_chunk_mixed``) can sit next
    to it. Behaviour is byte-identical to pre-refactor; see the moved
    comments for rationale on each transcribe() parameter.

    Returns a list of transcript-segment dicts with keys
    ``{"start", "end", "text", "words"}`` — same shape callers already
    consume from the inline loop.
    """
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
        _check_cancelled(cancel_event)
        abs_start = segment.start + chunk_start_abs
        abs_end = segment.end + chunk_start_abs
        seg_mid = (abs_start + abs_end) / 2.0
        if seg_mid < primary_start_abs:
            continue
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
```

- [ ] **Step 3: Replace the inline body in `transcribe()` with a call to `_decode_chunk_single`**

Locate the per-chunk loop in `transcribe()` (around line 732-826). Replace the body so the loop becomes:

```python
for chunk_idx, (chunk_path, chunk_start_abs, primary_start_abs) in enumerate(chunks):
    if on_status and len(chunks) > 1:
        on_status(
            f"Транскрипция части {chunk_idx + 1}/{len(chunks)}..."
        )
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
```

The progress-callback fire stays in the outer loop (not inside the helper) so the helper has a single-responsibility "decode this chunk" shape, free of side effects.

- [ ] **Step 4: Verify nothing broke**

```
pytest -q
python -m ruff check .
```
Expected: all 285 existing tests still PASS, ruff clean. If a test fails, the refactor has a typo — diff against `main`:

```
git diff main -- transcriber/__init__.py
```
and check that the only changes are:
- New method `_decode_chunk_single` added
- Loop body replaced with one call + a small `for seg in chunk_segments:` aggregator
- No accidental parameter renames inside the new method

- [ ] **Step 5: Commit**

```bash
git add transcriber/__init__.py
git commit -m "refactor(transcriber): extract _decode_chunk_single from transcribe()

Pure refactor — no behaviour change. The per-chunk decode body (was
inline in transcribe()'s for-loop) becomes a private method on the
Transcriber class. PR-B's next task adds a parallel _decode_chunk_mixed
sibling, so the loop body shrinks to a one-line dispatch.

All 285 existing tests stay green."
```

---

### Task B.2: Add `_decode_chunk_mixed` + first integration test (routing)

**Files:**
- Modify: `transcriber/__init__.py` (add helper, add branch in the chunk loop, add import)
- Create: `tests/test_transcriber_mixed.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcriber_mixed.py`:

```python
"""Tests for the language='mixed' path in Transcriber.transcribe().

Mock-based — no real Whisper model, no GPU. Stubs:
  - WhisperModel via MagicMock on the Transcriber._model attribute
  - faster_whisper.vad's get_speech_timestamps via patching segmenter.vad_split
  - audio loading via patching audio_io.load_mono_float32
  - ensure_wav / diarize subprocess via patching at the import site
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from transcriber import Transcriber


def _make_fake_model(per_call_results):
    """Build a MagicMock that mimics faster_whisper.WhisperModel.

    ``per_call_results`` is a list of (segments_iter, info) tuples; each
    successive ``model.transcribe()`` invocation pops one and returns it.
    """
    model = MagicMock()
    model.model = MagicMock()  # for unload_model() / load_model() during offload
    calls = iter(per_call_results)

    def fake_transcribe(audio, **kwargs):
        return next(calls)

    model.transcribe.side_effect = fake_transcribe
    return model


def _make_segment(start, end, text, words=None):
    """Build a faster_whisper segment stand-in (duck-typed)."""
    seg = MagicMock()
    seg.start = start
    seg.end = end
    seg.text = text
    seg.words = words
    return seg


def _make_info(language="ru"):
    info = MagicMock()
    info.language = language
    return info


def test_mixed_routes_to_per_segment_path():
    """When language='mixed', the chunk-loop dispatches to the VAD-pre-pass
    branch and model.transcribe() is called once PER VAD segment, not
    once per chunk."""
    t = Transcriber(model_size="tiny")  # size irrelevant — model is mocked
    # Three VAD segments → three transcribe() calls.
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "Сәлеметсіз бе")]), _make_info("kk")),
        (iter([_make_segment(0.0, 2.0, "Окей, давайте")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.5, "Slack deployment")]), _make_info("en")),
    ])

    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)  # 30s of "audio"
    vad_segments = [
        {"start": 0, "end": 16_000 * 5},
        {"start": 16_000 * 10, "end": 16_000 * 20},
        {"start": 16_000 * 22, "end": 16_000 * 28},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="trilingual frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # One model.transcribe call per VAD segment.
    assert t._model.transcribe.call_count == 3
    # Three transcript segments out (one per call's single Whisper segment).
    assert len(out) == 3
    # Texts preserved.
    assert [s["text"] for s in out] == [
        "Сәлеметсіз бе", "Окей, давайте", "Slack deployment",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_transcriber_mixed.py -v
```
Expected: FAIL with `AttributeError: 'Transcriber' object has no attribute '_decode_chunk_mixed'`.

- [ ] **Step 3: Add `_decode_chunk_mixed` to `Transcriber`**

In `transcriber/__init__.py`:

**Imports** — add at the top of the file (with the other relative imports around lines 28-36):

```python
from .segmenter import vad_split
```

Also add the audio_io import (it's already there for `ensure_wav` / `split_wav_into_chunks` etc. — verify and add `load_mono_float32` if not already imported):

```python
from audio_io import ensure_wav, get_duration_s, load_mono_float32, split_wav_into_chunks
```

(The existing line on `transcriber/__init__.py:24` says `from audio_io import ensure_wav, get_duration_s, split_wav_into_chunks` — extend it to include `load_mono_float32`.)

**Method** — add `_decode_chunk_mixed` to the `Transcriber` class, right after `_decode_chunk_single` (from B.1):

```python
def _decode_chunk_mixed(
    self,
    chunk_path: str,
    chunk_start_abs: float,
    primary_start_abs: float,
    *,
    initial_prompt: str | None,
    hotwords_str: str | None,
    cancel_event,
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
    samples, sample_rate = load_mono_float32(chunk_path)
    speech_timestamps = vad_split(samples, sample_rate)
    logger.info(
        "Transcribe: mixed mode, vad_segments=%d",
        len(speech_timestamps),
    )

    out: list[dict] = []
    for seg_idx, ts in enumerate(speech_timestamps):
        _check_cancelled(cancel_event)
        seg_audio = samples[ts["start"]:ts["end"]]
        seg_start_s = ts["start"] / sample_rate

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

        for segment in segments:
            _check_cancelled(cancel_event)
            abs_start = chunk_start_abs + seg_start_s + segment.start
            abs_end = chunk_start_abs + seg_start_s + segment.end
            seg_mid = (abs_start + abs_end) / 2.0
            if seg_mid < primary_start_abs:
                continue
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
                "language": info.language,
            })
        logger.debug(
            "vad_seg %d: %.2fs-%.2fs, lang=%s, whisper_segments=%d",
            seg_idx,
            seg_start_s,
            seg_start_s + (ts["end"] - ts["start"]) / sample_rate,
            info.language,
            sum(1 for _ in [None]),  # placeholder; can be removed if too noisy
        )
    return out
```

**Note**: the last `logger.debug(...)` call is a "nice to have" diagnostic. If it becomes annoying in production logs, the line can be dropped — it's not load-bearing. (The `sum(1 for _ in [None])` placeholder for whisper_segments count is intentionally weird; replace with actual count tracking if you want — the simpler form: track `whisper_segs_count` in a local var inside the inner loop.)

**Branch** — modify the per-chunk loop in `transcribe()` to dispatch based on `language`. Replace the loop body added in B.1:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_transcriber_mixed.py -v
```
Expected: PASS.

If the test fails with `ImportError` on `transcriber.load_mono_float32` or `transcriber.vad_split`: the patches reference these as MODULE-level names inside `transcriber/__init__.py`. Because both are imported at module top-level (Step 3), `transcriber.load_mono_float32` and `transcriber.vad_split` are valid module attributes — the patches should resolve. If not, the imports were placed inside a function instead of at module level; fix the import location.

- [ ] **Step 5: Commit**

```bash
git add transcriber/__init__.py tests/test_transcriber_mixed.py
git commit -m "feat(transcriber): VAD per-segment branch for language='mixed'

New _decode_chunk_mixed() method runs vad_split() on the chunk audio
then calls model.transcribe(seg_audio, language=None, vad_filter=False)
once per speech region. Whisper's internal detect_language runs per
slice, producing true per-segment code-switching.

Per-chunk loop in transcribe() now dispatches based on language —
'mixed' → _decode_chunk_mixed, anything else → _decode_chunk_single
(unchanged behaviour for kk/ru/en/None).

First integration test asserts routing (3 VAD segments → 3 transcribe
calls, 3 transcript segments out). More tests follow."
```

---

### Task B.3: Mixed-path parameter assertions (language=None, vad_filter=False, prompt, language field)

**Files:**
- Modify: `tests/test_transcriber_mixed.py`

- [ ] **Step 1: Add four assertion-tests**

Append to `tests/test_transcriber_mixed.py`:

```python
def test_mixed_passes_language_none_and_vad_filter_false():
    """Critical: each per-segment transcribe call must pass language=None
    (so Whisper auto-detects this slice's language) and vad_filter=False
    (we already filtered upstream)."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "x")]), _make_info("kk")),
        (iter([_make_segment(0.0, 1.0, "y")]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 5, "end": 16_000 * 8},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # Every transcribe() call must have language=None + vad_filter=False.
    for call in t._model.transcribe.call_args_list:
        kwargs = call.kwargs
        assert kwargs["language"] is None, f"Expected language=None, got {kwargs.get('language')!r}"
        assert kwargs["vad_filter"] is False, f"Expected vad_filter=False, got {kwargs.get('vad_filter')!r}"


def test_mixed_passes_trilingual_prompt_through():
    """The initial_prompt passed to _decode_chunk_mixed must reach every
    per-segment transcribe call verbatim. In real usage this is the
    trilingual frame from _build_initial_prompt('mixed', ...)."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "x")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "y")]), _make_info("kk")),
    ])
    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 5, "end": 16_000 * 8},
    ]

    expected_prompt = "Расшифровка трилингвальной речи..."

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt=expected_prompt,
            hotwords_str=None,
            cancel_event=None,
        )

    for call in t._model.transcribe.call_args_list:
        assert call.kwargs["initial_prompt"] == expected_prompt


def test_mixed_output_segments_carry_language_field():
    """Each output transcript dict must include a 'language' key set
    from info.language. This is the metadata downstream consumers
    (SRT/VTT export, future features) read."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "kz text")]), _make_info("kk")),
        (iter([_make_segment(0.0, 1.0, "ru text")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "en text")]), _make_info("en")),
    ])
    fake_samples = np.zeros(16_000 * 15, dtype=np.float32)
    vad_segments = [
        {"start": 0, "end": 16_000 * 3},
        {"start": 16_000 * 4, "end": 16_000 * 7},
        {"start": 16_000 * 9, "end": 16_000 * 12},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert [s["language"] for s in out] == ["kk", "ru", "en"]


def test_mixed_segment_timestamps_offset_correctly():
    """A Whisper-emitted segment at local time t inside VAD slice
    starting at seg_start_s inside chunk starting at chunk_start_abs
    must produce abs_start = chunk_start_abs + seg_start_s + t."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        # Whisper sees a 5-second slice and emits a segment from 1.0 to 3.5 within it.
        (iter([_make_segment(1.0, 3.5, "in slice", words=None)]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 60, dtype=np.float32)
    # VAD says slice runs from 10s to 15s within the chunk.
    vad_segments = [{"start": 16_000 * 10, "end": 16_000 * 15}]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=900.0,   # chunk starts at 15-min mark in original file
            primary_start_abs=900.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert len(out) == 1
    seg = out[0]
    # abs_start = 900 (chunk) + 10 (vad slice start in chunk) + 1.0 (whisper local) = 911.0
    assert seg["start"] == pytest.approx(911.0, abs=0.01)
    # abs_end = 900 + 10 + 3.5 = 913.5
    assert seg["end"] == pytest.approx(913.5, abs=0.01)
```

- [ ] **Step 2: Run new tests**

```
pytest tests/test_transcriber_mixed.py -v
```
Expected: all 5 PASS (the original `test_mixed_routes_to_per_segment_path` from B.2 plus the 4 new ones).

- [ ] **Step 3: Commit**

```bash
git add tests/test_transcriber_mixed.py
git commit -m "test(transcriber/mixed): parameter & metadata assertions

Four more mock-based tests:
- language=None and vad_filter=False on every per-segment call
- initial_prompt passes through verbatim
- info.language reaches transcript_segments[].language
- chunk_start_abs + seg_start_s + segment.start math is correct"
```

---

### Task B.4: Edge-case tests (empty VAD, progress monotonicity, cancellation, dedup)

**Files:**
- Modify: `tests/test_transcriber_mixed.py`

- [ ] **Step 1: Add four edge-case tests**

Append to `tests/test_transcriber_mixed.py`:

```python
import threading


def test_mixed_empty_vad_yields_empty_transcript():
    """If vad_split returns [], _decode_chunk_mixed returns [] without
    calling model.transcribe at all. Important for chunks that are
    entirely silent — they should contribute nothing, not crash."""
    t = Transcriber(model_size="tiny")
    t._model = MagicMock()

    fake_samples = np.zeros(16_000 * 10, dtype=np.float32)
    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=[]):
        out = t._decode_chunk_mixed(
            chunk_path="silent.wav",
            chunk_start_abs=0.0,
            primary_start_abs=0.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert out == []
    assert t._model.transcribe.call_count == 0


def test_mixed_dedup_drops_segments_before_primary_start():
    """For overlap chunks (chunk_start_abs < primary_start_abs), the same
    midpoint-based dedup as _decode_chunk_single must apply: segments
    whose midpoint is before primary_start_abs are dropped."""
    t = Transcriber(model_size="tiny")
    t._model = _make_fake_model([
        # First VAD slice contributes a segment whose absolute midpoint
        # is BEFORE primary_start_abs — should be dropped.
        # Second VAD slice produces a segment past primary_start_abs — kept.
        (iter([_make_segment(0.0, 1.0, "dropped")]), _make_info("ru")),
        (iter([_make_segment(0.0, 1.0, "kept")]), _make_info("ru")),
    ])
    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [
        {"start": 0,            "end": 16_000 * 2},    # 0-2s in chunk
        {"start": 16_000 * 5,   "end": 16_000 * 8},    # 5-8s in chunk
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=100.0,
            primary_start_abs=103.0,   # primary starts 3s into this chunk
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    # Slice 1 segment midpoint = 100 + 0 + 0.5 = 100.5 < 103 → DROPPED
    # Slice 2 segment midpoint = 100 + 5 + 0.5 = 105.5 ≥ 103 → KEPT
    assert [s["text"] for s in out] == ["kept"]


def test_mixed_cancel_event_breaks_inner_loop():
    """Setting cancel_event mid-loop must raise TranscriptionCancelled
    on the next _check_cancelled, before processing more segments."""
    from transcriber import TranscriptionCancelled

    cancel = threading.Event()
    t = Transcriber(model_size="tiny")

    call_count = {"n": 0}

    def fake_transcribe(audio, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            cancel.set()  # cancel after second segment is being processed
        return (iter([_make_segment(0.0, 1.0, f"seg{call_count['n']}")]), _make_info("ru"))

    t._model = MagicMock()
    t._model.transcribe.side_effect = fake_transcribe

    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [
        {"start": 0,            "end": 16_000 * 3},
        {"start": 16_000 * 5,   "end": 16_000 * 8},
        {"start": 16_000 * 10,  "end": 16_000 * 13},
    ]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        with pytest.raises(TranscriptionCancelled):
            t._decode_chunk_mixed(
                chunk_path="fake.wav",
                chunk_start_abs=0.0,
                primary_start_abs=0.0,
                initial_prompt="frame",
                hotwords_str=None,
                cancel_event=cancel,
            )

    # Should have called transcribe at most 2 times (one before cancel,
    # one during which cancel was set). The third VAD segment must NOT
    # have been processed.
    assert t._model.transcribe.call_count <= 2


def test_mixed_word_timestamps_offset_correctly():
    """When Whisper emits per-word timestamps within a VAD slice, the
    word abs times must include both chunk_start_abs AND seg_start_s.
    Diarization downstream (speaker_aligner) indexes by word times, so
    a missing offset would mis-align speakers."""
    t = Transcriber(model_size="tiny")
    fake_word = MagicMock()
    fake_word.start = 0.5
    fake_word.end = 1.0
    fake_word.word = "Hello"

    t._model = _make_fake_model([
        (iter([_make_segment(0.0, 1.0, "Hello", words=[fake_word])]), _make_info("en")),
    ])
    fake_samples = np.zeros(16_000 * 30, dtype=np.float32)
    vad_segments = [{"start": 16_000 * 10, "end": 16_000 * 15}]

    with patch("transcriber.load_mono_float32", return_value=(fake_samples, 16_000)), \
         patch("transcriber.vad_split", return_value=vad_segments):
        out = t._decode_chunk_mixed(
            chunk_path="fake.wav",
            chunk_start_abs=600.0,    # chunk starts at 10-min mark
            primary_start_abs=600.0,
            initial_prompt="frame",
            hotwords_str=None,
            cancel_event=None,
        )

    assert len(out) == 1
    words = out[0]["words"]
    assert len(words) == 1
    # word abs_start = 600 (chunk) + 10 (vad slice start) + 0.5 (whisper local) = 610.5
    assert words[0]["start"] == pytest.approx(610.5, abs=0.01)
    assert words[0]["end"] == pytest.approx(611.0, abs=0.01)
```

- [ ] **Step 2: Run all mixed tests**

```
pytest tests/test_transcriber_mixed.py -v
```
Expected: 9 tests PASS (5 from earlier tasks + 4 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_transcriber_mixed.py
git commit -m "test(transcriber/mixed): edge cases — empty VAD, dedup, cancel, words

Four edge cases:
- vad_split returns [] → empty output, no transcribe calls
- midpoint dedup respects primary_start_abs (overlap-chunk behaviour
  identical to _decode_chunk_single)
- cancel_event mid-loop raises TranscriptionCancelled before next segment
- per-word timestamps include both chunk_start_abs AND seg_start_s offsets
  (critical for diarization speaker_aligner downstream)"
```

---

### Task B.5: Regression test in `test_transcriber_pure.py`

**Files:**
- Modify: `tests/test_transcriber_pure.py`

- [ ] **Step 1: Write the regression test**

Append to `tests/test_transcriber_pure.py` (after the existing `_effective_whisper_language` block from Phase 1):

```python
# ── Phase 2 regression: language="ru" must NOT trigger the mixed branch ──


def test_single_language_skips_vad_pre_pass():
    """When language is a real ISO code (not 'mixed'), the per-chunk loop
    must take the single-language branch (_decode_chunk_single). The VAD
    pre-pass (vad_split) MUST NOT be called — that's reserved for mixed.

    Regression guard against accidentally widening the mixed branch's
    trigger condition (e.g. `if language is None or language == 'mixed'`).
    """
    from unittest.mock import MagicMock, patch

    from transcriber import Transcriber

    t = Transcriber(model_size="tiny")
    t._model = MagicMock()
    # Make model.transcribe return one empty segment iter so transcribe()
    # can complete; we only care about routing.
    t._model.transcribe.return_value = (iter([]), MagicMock(language="ru"))

    # Patch vad_split as a tripwire — if the mixed branch is wrongly taken,
    # this would be called.
    vad_tripwire = MagicMock()
    with patch("transcriber.vad_split", vad_tripwire), \
         patch("transcriber.ensure_wav", return_value=("fake.wav", False)), \
         patch("transcriber.get_duration_s", return_value=30.0), \
         patch("transcriber.split_wav_into_chunks", return_value=[("fake.wav", 0.0, 0.0)]):
        # diarize=False so we skip the subprocess + offload code paths
        t.transcribe(
            audio_path="fake.wav",
            language="ru",
            diarize=False,
        )

    vad_tripwire.assert_not_called()
    # And the model.transcribe was called with language="ru" (effective_language
    # is the same for non-mixed inputs).
    assert t._model.transcribe.call_count >= 1
    first_call = t._model.transcribe.call_args_list[0]
    assert first_call.kwargs.get("language") == "ru"
```

- [ ] **Step 2: Run regression test**

```
pytest tests/test_transcriber_pure.py -v -k single_language_skips_vad_pre_pass
```
Expected: PASS.

- [ ] **Step 3: Run full suite**

```
pytest -q
python -m ruff check .
```
Expected: 285 baseline + 5 segmenter (PR-A) + 9 mixed (B.2-B.4) + 1 regression = ~300 tests PASS, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_transcriber_pure.py
git commit -m "test(transcriber): regression guard — single-lang skips VAD pre-pass

When language='ru' (or any non-'mixed' value), transcribe() must take
the _decode_chunk_single branch. vad_split is patched as a tripwire;
the test fails if the mixed branch's trigger condition gets widened
(e.g. by accidentally checking 'language is None').

Also verifies the model.transcribe call uses language='ru' verbatim
(the existing single-language behaviour preserved by the B.1 refactor)."
```

---

### Task B.6: PR-B wrap-up

- [ ] **Step 1: Final pytest + lint**

```
pytest -q
python -m ruff check .
```
Expected: 285 baseline + 5 segmenter + 9 mixed + 1 regression = ~300 green; ruff clean.

- [ ] **Step 2: Manual smoke test**

Run the app once on any audio you have lying around, just to confirm import order didn't break anything:

```
python app.py
```

Open the file, run a short single-language (e.g. `Русский`) transcription, confirm it works. This is a sanity check, not the manual A/B — that's PR-C.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/code-switching-phase-2-integration
gh pr create --title "feat(code-switching): local Whisper per-segment language detection [PR-B]" --body "$(cat <<'EOF'
## Summary

Integration step for Phase 2 (PR-B of 3).

- New `_decode_chunk_mixed()` method on `Transcriber` runs `vad_split` (from PR-A) on each chunk, then calls `model.transcribe(seg_audio, language=None, vad_filter=False)` once per VAD-detected speech region. Whisper's internal `detect_language()` runs per slice, producing true per-segment code-switching.
- Existing inline per-chunk decode logic extracted to `_decode_chunk_single()` (pure refactor in commit `<B.1 commit hash>`).
- `transcribe()` per-chunk loop dispatches: `language == "mixed"` → mixed branch; anything else → single-language branch (physically unchanged behaviour).
- New `transcript_segments[].language` field carries Whisper's detected language code for downstream consumers (SRT/VTT export, future features). Not surfaced in user-facing text.
- 10 new tests: 9 mock-based integration tests + 1 regression test in `test_transcriber_pure.py`.

Builds on PR-A's `transcriber/segmenter.py`. PR-C reports manual A/B QA against real meeting recordings.

## Test plan

- [x] `pytest -q` — ~300 tests green (285 baseline + 14 from PR-A and PR-B)
- [x] `python -m ruff check .` — clean
- [x] Manual smoke: short pure-RU clip + `Русский` language — works as before (regression check)
- [ ] Manual A/B (in PR-C) — real trilingual meetings, Phase 1 vs Phase 2 side-by-side

## Files changed

- `transcriber/__init__.py` — `_decode_chunk_single` extraction + `_decode_chunk_mixed` addition + branch dispatch (~120 LOC)
- `tests/test_transcriber_mixed.py` — NEW, 9 tests (~280 LOC)
- `tests/test_transcriber_pure.py` — +1 regression test (~30 LOC)
EOF
)"
```

- [ ] **Step 4: Wait for review + merge before starting PR-C.**

---

## PR-C: Manual A/B QA + optional VAD tuning

**Branch:** `feat/code-switching-phase-2-tuning` (created from `main` after PR-B merges).

**Goal:** Execute the manual A/B test plan from the spec on real trilingual meetings, record results in the PR description, and ship VAD-parameter tuning ONLY if real-world signal proves the Phase-2-default VadOptions need adjustment. If results are good, PR-C may have a zero-diff PR description (just the QA evidence) — that's a valid outcome.

**Pre-task:**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/code-switching-phase-2-tuning
```

---

### Task C.1: Prepare 2-3 real trilingual recordings

This task has no code. You need 2-3 meeting recordings (10-30 min each)
with genuine KZ+RU+EN code-switching content. Real work meetings from
the past 1-2 weeks are ideal.

- [ ] **Step 1: Pick the recordings**

Choose 2-3 files. Ideal candidates:
- One with a KZ greeting + RU body + scattered EN tech terms (typical Kazakhstan workplace conversation)
- One with longer KZ stretches (so per-segment detection has a real chance to lock onto KZ)
- One with multiple EN brand/tool names in the middle of RU sentences (Slack, Kubernetes, deployment, etc.)

If you don't have suitable recordings, record one quickly with the app's own recorder — read aloud a prepared trilingual script.

- [ ] **Step 2: Stash them outside the repo**

Pick a local directory OUTSIDE the repo (e.g. `~/Documents/qa_audio/`). **Do not commit audio files** — see `.gitignore`. Record the file paths in a temporary local notes file (e.g. `~/Documents/qa_notes-phase-2.txt`).

---

### Task C.2: Run Phase 1 baseline on `main`

- [ ] **Step 1: Check out main**

```bash
git checkout main && git pull --ff-only origin main
```

- [ ] **Step 2: For each recording, run with `Смешанный (KZ+RU+EN)` and capture the result**

In the app:
1. Open the recording.
2. Set language to `Смешанный (KZ+RU+EN)`.
3. Run transcription (local Whisper). Diarization OFF for the A/B baseline (turn it on later for the diarization-compat check).
4. Save the resulting `.txt` to `~/Documents/qa_notes-phase-2/<recording>_phase1.txt`.
5. Record wall-clock time (the app's status bar shows it; otherwise watch the clock).

If diarization is desired for the diarization-compat check too, run a second pass with diarization ON and save as `<recording>_phase1_diarized.txt`.

- [ ] **Step 3: Snapshot transcripts**

You should now have for each recording (2-3 of them):
- `<recording>_phase1.txt` — plain transcription
- `<recording>_phase1_diarized.txt` — optional, with speaker labels
- A noted wall-clock time

---

### Task C.3: Run Phase 2 on the new branch

- [ ] **Step 1: Check out the tuning branch (already created in Pre-task)**

```bash
git checkout feat/code-switching-phase-2-tuning
```

(This branch is identical to `main` right now — PR-A and PR-B have merged. The branch exists just so any later tuning commits go on it instead of `main`.)

- [ ] **Step 2: Re-run each recording with `Смешанный (KZ+RU+EN)` on this branch**

Same procedure as C.2 Step 2, saving to `<recording>_phase2.txt` and `<recording>_phase2_diarized.txt`.

Record wall-clock times — expect ~2× the Phase 1 baseline. If the slowdown is >3× there might be an inefficiency worth investigating before opening PR-C.

---

### Task C.4: Compose side-by-side comparison + decide on tuning

- [ ] **Step 1: Side-by-side diff for each recording**

For each `<recording>` (in `~/Documents/qa_notes-phase-2/`), produce a comparison snippet showing the most informative 3-5 utterances. Format:

```markdown
### Recording: <name> (~<minutes> min)

| | Phase 1 (main) | Phase 2 (this branch) |
|---|---|---|
| KZ phrase | `<phase 1 text>` | `<phase 2 text>` |
| EN tech term | `<phase 1 text>` | `<phase 2 text>` |
| RU body | `<phase 1 text>` | `<phase 2 text>` |

Wall time: <phase 1> → <phase 2> (~<N>× slowdown)
```

Save this to `~/Documents/qa_notes-phase-2/comparison.md` — you'll paste it into the PR description.

- [ ] **Step 2: Decide if VAD tuning is needed**

Read your comparisons. Symptoms that suggest tuning:

- **Phase 2 is worse than Phase 1 on KZ phrases** (or comparable, no win): VAD might be cutting KZ utterances mid-word. Try increasing `_MIN_SILENCE_MS` from 500 to 700 ms in `transcriber/segmenter.py` (silences need to be longer to trigger a split — keeps multi-sentence KZ stretches together).
- **Too many tiny segments in the logs** (`logger.info` reports `vad_segments=<huge number>`): bump `_MIN_SPEECH_MS` from 500 to 700 ms (filters out micro-speech blips that aren't real utterance starts).
- **English tech terms still get cyrillicized**: Phase 2 working as intended for EN but English speakers' utterances are too short for VAD to isolate. Lower `_MIN_SPEECH_MS` from 500 to 300 ms (gives shorter utterances their own segment) and accept slightly worse stability.
- **Diarization mis-aligns speakers in the mixed transcript**: speaker_aligner.py uses word-level times; check that word abs times in `last_segments[].words[]` look reasonable. If they're wrong, the offset arithmetic in `_decode_chunk_mixed` is broken — verify against the `test_mixed_word_timestamps_offset_correctly` unit test.

If you don't see any of these symptoms, **skip Step 3** and go straight to Task C.5.

- [ ] **Step 3: If tuning is needed — change one parameter at a time, re-run, commit**

For each parameter change:

```bash
# Edit transcriber/segmenter.py, change ONE constant
# E.g.: _MIN_SILENCE_MS = 500  →  _MIN_SILENCE_MS = 700

# Re-run the failing case from C.3
# If the test suite has assertions about specific numeric boundaries
# (test_segmenter.py), they may need updating — re-run pytest first:
pytest tests/test_segmenter.py -v

# If pytest fails, the test that locked in 500 ms behaviour needs to
# be updated to match the new value. Adjust the test rather than the
# production code — the test was a starting-point fixture.

git add transcriber/segmenter.py tests/test_segmenter.py
git commit -m "tune(segmenter): _MIN_SILENCE_MS 500 → 700 based on real-world A/B

Observed: <symptom from your comparison, e.g. KZ utterances split
mid-sentence in 3 of 3 test recordings>. Longer min_silence stops
the over-fragmentation."
```

---

### Task C.5: PR-C wrap-up

- [ ] **Step 1: Final pytest + lint**

```
pytest -q
python -m ruff check .
```

- [ ] **Step 2: Push (only if there are any commits beyond the branch point)**

```bash
git status   # confirm whether there are commits ahead of main
```

If no commits (no tuning needed), PR-C is "QA evidence only" — you can either:

(a) **Skip the PR entirely** and post the comparison results as a comment on PR-B or in a Slack/email message to whoever needs to know.
(b) **Open a zero-diff PR** (allowed but unusual) with the comparison results in the body — useful for record-keeping.

If there are commits (tuning landed):

```bash
git push -u origin feat/code-switching-phase-2-tuning
gh pr create --title "tune(code-switching): VAD params from real-world A/B [PR-C]" --body "$(cat <<'EOF'
## Summary

Phase 2 manual A/B QA on real trilingual meeting recordings + VAD-parameter tuning based on observed behaviour.

## Manual A/B results

<paste contents of ~/Documents/qa_notes-phase-2/comparison.md here>

## Tuning applied

<for each commit ahead of main, summarize: what changed, why>

E.g.: "_MIN_SILENCE_MS 500 → 700 ms — observed KZ utterances being split mid-sentence in 3 of 3 test recordings. Longer min_silence stops the over-fragmentation."

## Test plan

- [x] `pytest -q` — all green
- [x] `python -m ruff check .` — clean
- [x] Real-world A/B per spec — see Manual A/B results above
- [x] Diarization compatibility — speaker labels accurate in the mixed transcript

## Closes

Phase 2 of code-switching KZ+RU+EN per [spec](docs/superpowers/specs/2026-05-22-code-switching-kz-ru-en-phase-2-design.md).
EOF
)"
```

- [ ] **Step 3: After PR-C merges (or after the comparison comment is posted): update CLAUDE.md**

Open a small docs follow-up PR (modeled on the Phase 1 closure pattern):

```bash
git checkout main && git pull --ff-only origin main
git checkout -b docs/claude-md-after-code-switching-phase-2
# Edit CLAUDE.md → Active work / context:
#   Replace the "Code-switching KZ+RU+EN Phase 1 (May 2026)" bullet with
#   a "Code-switching KZ+RU+EN (May 2026, Phase 1+2 both shipped)" bullet
#   summarising both phases.
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md after code-switching Phase 2 ships"
git push -u origin docs/claude-md-after-code-switching-phase-2
gh pr create --title "docs: update CLAUDE.md after code-switching Phase 2" --body "Records Phase 2 closure (per-segment local language detection + manual A/B QA). Both phases of the code-switching feature have now shipped."
```

---

## Plan self-review (already done by author)

**Spec coverage** — every spec section has a corresponding task:

- Architecture (branch dispatch + VAD pre-pass) → Tasks A.1 (VAD wrapper), B.1 (single-language extraction), B.2 (mixed branch + dispatch)
- Components table → all 7 file changes mapped (segmenter.py + test_segmenter.py in PR-A; transcriber/__init__.py + test_transcriber_mixed.py + test_transcriber_pure.py regression in PR-B; possible segmenter.py tuning in PR-C)
- Data flow (UI → transcribe → branch → VAD → per-segment decode → diarization-unchanged) → covered by Tasks B.1-B.4 with explicit timestamp-offset assertions
- Error handling (empty VAD, cancellation, dedup) → Tasks B.4
- Testing (12-14 new tests + Manual QA) → 5 in A.2 + 9 in B.2-B.4 + 1 in B.5 = 15 tests, slightly above estimate; manual QA in Tasks C.1-C.4
- Open questions (VadOptions exact values, load_chunk_samples location, info.language field) → resolved in A.1 (param values) and Task B.2 (load_mono_float32 already exists; info.language verified during impl)
- Implementation phases (PR-A foundation, PR-B integration, PR-C tuning) → matches plan structure

**Placeholder scan** — no `TBD`/`TODO`/`fill in later` left in code steps. Task C.4 has prose about "if tuning is needed" which is intentional decision-point text, not an unaddressed gap; the action is fully specified per symptom.

**Type consistency** —
- `_decode_chunk_single` and `_decode_chunk_mixed` have parallel signatures (same positional args + matching kwargs minus `effective_language` which only the single helper takes); used consistently in Tasks B.1, B.2, and the branch in B.2.
- `vad_split` returns `list[dict]` with `{"start": int, "end": int}` keys throughout Tasks A.1, A.2, and B.2-B.5 (matches faster-whisper's `get_speech_timestamps` shape).
- `info.language` is the attribute name on the Whisper info object in B.2-B.5 (matches faster-whisper's actual API).
- `load_mono_float32` (from `audio_io.py`) is referenced consistently across Tasks B.2-B.4 — both in the production import and the patch target.

---

## Glossary

- **Foundation PR (PR-A)** — Ships the `transcriber/segmenter.py` module + tests as a pure helper. Zero integration — nothing imports it yet on this branch. Allows the integration PR to focus purely on transcribe()'s plumbing.
- **Integration PR (PR-B)** — Wires the segmenter into `Transcriber.transcribe()`. Includes the parallel refactor (`_decode_chunk_single` extraction) so the new `_decode_chunk_mixed` doesn't grow the inline body further.
- **Tuning PR (PR-C)** — Optional. Lands VAD-parameter adjustments only if the real-world A/B exposes a defect. May be a zero-diff PR description if Phase 2 defaults work well out of the box.
- **Mixed branch** — The `if language == "mixed":` arm inside the per-chunk loop in `transcribe()`. Routes to `_decode_chunk_mixed`. Everything else routes to `_decode_chunk_single` (unchanged from pre-Phase-2 behaviour).
- **`vad_split`** — The `transcriber.segmenter.vad_split` function. Takes float32 mono samples + sample rate, returns `[{"start": sample_idx, "end": sample_idx}]`. Pure wrapper over Silero VAD via `faster_whisper.vad.get_speech_timestamps`.
- **Per-segment decode** — Calling `model.transcribe(seg_audio, language=None, ...)` once per VAD-detected speech region inside a chunk. `language=None` triggers Whisper's internal `detect_language()` on that slice, so each region is decoded in its own detected language.
- **Manual A/B** — The Phase 2 quality gate: run real trilingual meeting recordings through both `main` (Phase 1) and the integration branch (Phase 2), compare side-by-side in the PR description. No WER corpus; documented in the spec as the explicit quality gate.
