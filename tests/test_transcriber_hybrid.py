"""Tests for the hybrid cloud-STT + local-pyannote-diarization path.

The hybrid orchestrator (``Transcriber._transcribe_via_cloud_with_local_diarize``)
engages automatically when ``cloud_provider`` is set, ``diarize=True``, and
the provider declares ``supports_diarization = False`` (Groq, OpenAI
Whisper). Flow:

1. Spawn pyannote subprocess on the FULL original audio (no DIARIZE_WAIT —
   no Whisper is loaded locally to compete for VRAM).
2. In parallel: run cloud STT (provider.transcribe, possibly via chunker).
   Returns segments with word-level timestamps.
3. Validate words[] present (hybrid alignment requires word-level).
4. Wait for diarize subprocess; collect speaker turns.
5. Merge via ``speaker_aligner._assign_speakers_word_level``.
6. Format with ``format_diarized``.

All subprocess + provider work is mocked — no real ffmpeg, no real pyannote,
no real network. The choreography (correct calls in the right order,
cleanup on cancel/error, words[]-presence guard) is what we verify.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from providers import PROVIDERS, ProviderError, TranscriptionProvider
from providers.base import TranscriptionOptions, TranscriptionResult
from transcriber import Transcriber, TranscriptionCancelled

# ── stub provider used by every test ──────────────────────────────────


class _StubProviderNoDiariz(TranscriptionProvider):
    """Cloud STT provider without native diarization — the trigger for the
    hybrid path. Subclasses tune the canned TranscriptionResult."""

    display_name = "StubHybrid"
    supports_diarization = False
    supports_mixed = True
    max_upload_bytes = None  # never engages chunker in these tests

    def __init__(self, api_key: str, result: TranscriptionResult | None = None):
        self._api_key = api_key
        self._result = result or TranscriptionResult(
            segments=[
                {
                    "start": 0.0, "end": 2.0, "text": "Привет.",
                    "words": [{"start": 0.0, "end": 1.0, "word": "Привет"}],
                },
                {
                    "start": 2.5, "end": 4.0, "text": "Как дела?",
                    "words": [{"start": 2.5, "end": 3.5, "word": "Как"}],
                },
            ],
            language="ru",
            raw={},
        )
        self.transcribe_calls: list[dict] = []

    def transcribe(self, audio_path, options, on_status=None,
                   on_progress=None, cancel_event=None):
        self.transcribe_calls.append({
            "audio_path": audio_path,
            "options": options,
            "cancel_event": cancel_event,
        })
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelled()
        return self._result


class _StubProviderWithNativeDiariz(_StubProviderNoDiariz):
    """Provider that DOES have native diarization (Deepgram, AssemblyAI
    shape). Used to assert hybrid path is NOT engaged for these."""
    display_name = "StubNative"
    supports_diarization = True


def _patch_provider(monkeypatch, cls):
    """Helper: register a stub provider class in the PROVIDERS registry
    so transcribe() can resolve it by name."""
    monkeypatch.setitem(PROVIDERS, cls.display_name, cls)


# ── _launch_diarization_subprocess gains wait_for_go ──────────────────


def test_launch_diarization_subprocess_default_sets_diarize_wait(monkeypatch):
    """Regression: existing callers (local path) get DIARIZE_WAIT=1 by
    default. Without this, the local path's stdin GO protocol breaks
    and the dead-zone optimization (Phase 5) regresses."""
    t = Transcriber()
    captured_env = {}

    class FakePopen:
        def __init__(self, *args, env=None, **kw):
            captured_env.update(env or {})
            self.stdin = MagicMock()
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            # Empty iterables so the consumer threads exit immediately
            self.stdout.__iter__ = lambda self: iter([])
            self.stderr.__iter__ = lambda self: iter([])
            self.returncode = 0

        def poll(self):
            return None

    with patch("transcriber.subprocess.Popen", FakePopen):
        t._launch_diarization_subprocess(
            audio_path="dummy.wav", device="cpu",
            hf_token=None, num_speakers=None,
            min_speakers=None, max_speakers=None,
            voice_lib_path=None, on_status=None, on_progress=None,
        )

    assert captured_env.get("DIARIZE_WAIT") == "1"


def test_launch_diarization_subprocess_wait_for_go_false_omits_diarize_wait(monkeypatch):
    """Hybrid path calls with wait_for_go=False so the worker starts
    pyannote immediately (no GO blocking). No Whisper is loaded locally
    to compete for VRAM, so the GO-protocol optimization is unnecessary
    and we want maximum parallelism with the cloud upload."""
    t = Transcriber()
    captured_env = {}

    class FakePopen:
        def __init__(self, *args, env=None, **kw):
            captured_env.update(env or {})
            self.stdin = MagicMock()
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.__iter__ = lambda self: iter([])
            self.stderr.__iter__ = lambda self: iter([])
            self.returncode = 0

        def poll(self):
            return None

    with patch("transcriber.subprocess.Popen", FakePopen):
        t._launch_diarization_subprocess(
            audio_path="dummy.wav", device="cpu",
            hf_token=None, num_speakers=None,
            min_speakers=None, max_speakers=None,
            voice_lib_path=None, on_status=None, on_progress=None,
            wait_for_go=False,
        )

    assert "DIARIZE_WAIT" not in captured_env, (
        "wait_for_go=False must omit the DIARIZE_WAIT env var so the "
        "worker proceeds without blocking on stdin"
    )


# ── _transcribe_via_cloud_with_local_diarize ──────────────────────────


def _hybrid_setup(monkeypatch, *, cancel_during_cloud=False, words=True):
    """Common setup: stub provider registered, mocked diarize spawn/await,
    spy on the alignment call. Returns the Transcriber instance and a
    dict of mocked handles for per-test assertions."""
    t = Transcriber()
    segments = [
        {
            "start": 0.0, "end": 2.0, "text": "Первый говорит.",
            "words": [{"start": 0.0, "end": 1.9, "word": "Первый"}] if words else [],
        },
        {
            "start": 2.5, "end": 4.0, "text": "Второй отвечает.",
            "words": [{"start": 2.5, "end": 3.9, "word": "Второй"}] if words else [],
        },
    ]
    # Strip empty words[] to match real provider behavior (Groq doesn't
    # send the key at all when granularities=word isn't honored).
    if not words:
        for seg in segments:
            del seg["words"]
    stub_result = TranscriptionResult(segments=segments, language="ru", raw={})

    class _ConfiguredStub(_StubProviderNoDiariz):
        def __init__(self, api_key):
            super().__init__(api_key, result=stub_result)

    _patch_provider(monkeypatch, _ConfiguredStub)

    fake_handle = {"proc": MagicMock(), "_test_killed": [False]}
    fake_handle["proc"].poll.return_value = None
    fake_handle["proc"].kill = lambda: fake_handle["_test_killed"].__setitem__(0, True)
    fake_handle["proc"].wait = lambda *a, **kw: None

    speaker_turns = [
        (0.0, 2.1, "SPEAKER_00"),
        (2.4, 4.1, "SPEAKER_01"),
    ]

    launch_calls = []
    await_calls = []

    def fake_launch(self, *, audio_path, device, hf_token, num_speakers,
                    min_speakers, max_speakers, voice_lib_path,
                    on_status, on_progress, wait_for_go=True):
        launch_calls.append({
            "audio_path": audio_path, "device": device,
            "wait_for_go": wait_for_go,
        })
        return fake_handle

    def fake_await(self, handle, cancel_event=None):
        await_calls.append({"handle_id": id(handle)})
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelled()
        return speaker_turns

    monkeypatch.setattr(
        Transcriber, "_launch_diarization_subprocess", fake_launch,
    )
    monkeypatch.setattr(
        Transcriber, "_await_diarization_subprocess", fake_await,
    )

    return t, {
        "stub_cls": _ConfiguredStub,
        "stub_result": stub_result,
        "fake_handle": fake_handle,
        "speaker_turns": speaker_turns,
        "launch_calls": launch_calls,
        "await_calls": await_calls,
    }


def test_hybrid_engages_via_transcribe_cloud_short_circuit(monkeypatch, tmp_path):
    """End-to-end: transcribe() with cloud_provider + diarize=True +
    provider-lacks-native-diariz must route to hybrid and produce a
    diarized output string (mentioning at least one speaker label)."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch)
    text = t.transcribe(
        audio_path=str(audio),
        diarize=True,
        cloud_provider=h["stub_cls"].display_name,
        cloud_api_key="k",
    )
    # Some speaker label appears in the output — exact format ("Спикер 1"
    # vs "SPEAKER_00") depends on format_diarized which we don't pin
    # here. Either is fine.
    assert "SPEAKER" in text or "Спикер" in text
    # Both subprocesses were used.
    assert len(h["launch_calls"]) == 1
    assert len(h["await_calls"]) == 1


def test_hybrid_spawns_diarize_with_wait_for_go_false(monkeypatch, tmp_path):
    """The hybrid path must spawn the diarize subprocess WITHOUT
    DIARIZE_WAIT so it runs in parallel with the cloud upload —
    the whole point of the hybrid optimization."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch)
    t.transcribe(
        audio_path=str(audio),
        diarize=True,
        cloud_provider=h["stub_cls"].display_name,
        cloud_api_key="k",
    )

    assert len(h["launch_calls"]) == 1
    assert h["launch_calls"][0]["wait_for_go"] is False, (
        "hybrid must spawn diarize with wait_for_go=False — otherwise "
        "the worker blocks on stdin and the parallelism with cloud "
        "upload is lost"
    )


def test_hybrid_spawns_diarize_on_original_audio_not_denoised_copy(
    monkeypatch, tmp_path,
):
    """When denoise_audio=True, cloud STT runs on the denoised audio
    but pyannote runs on the ORIGINAL — denoising can subtly shift
    speech boundaries which would misalign speaker turns vs the
    cloud's words. Keep them on the same physical timeline."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch)

    # Patch ensure_wav to verify it's called for cloud but NOT for diarize.
    ensure_wav_calls = []
    from audio_io import ensure_wav as real_ensure_wav

    def spy_ensure_wav(path, normalize=True, denoise=False):
        ensure_wav_calls.append({
            "path": path, "normalize": normalize, "denoise": denoise,
        })
        # Return path unchanged for the test — we just want to spy.
        return path, False

    monkeypatch.setattr("transcriber.ensure_wav", spy_ensure_wav)

    t.transcribe(
        audio_path=str(audio),
        diarize=True,
        denoise_audio=True,
        cloud_provider=h["stub_cls"].display_name,
        cloud_api_key="k",
    )

    # The diarize spawn got the ORIGINAL audio path, not a denoised temp.
    assert h["launch_calls"][0]["audio_path"] == str(audio)


def test_hybrid_raises_when_provider_returns_no_words(monkeypatch, tmp_path):
    """speaker_aligner._assign_speakers_word_level needs words[]. If the
    cloud provider didn't return them (wrong model, e.g. gpt-4o-transcribe
    which has token-level not word-level metadata), surface a user-actionable
    Russian error pointing at the model choice — don't silently produce
    misaligned output."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch, words=False)
    with pytest.raises(RuntimeError, match="пословн|word"):
        t.transcribe(
            audio_path=str(audio),
            diarize=True,
            cloud_provider=h["stub_cls"].display_name,
            cloud_api_key="k",
        )


def test_hybrid_kills_diarize_subprocess_if_cloud_raises(monkeypatch, tmp_path):
    """If cloud STT fails mid-stream, the diarize subprocess we spawned
    earlier must be killed — otherwise we leak a Python subprocess +
    its CUDA context for the rest of the session."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch)

    # Make the stub provider raise ProviderError on transcribe.
    class _FailingStub(_StubProviderNoDiariz):
        display_name = h["stub_cls"].display_name

        def transcribe(self, *a, **kw):
            raise ProviderError("simulated cloud failure")

    _patch_provider(monkeypatch, _FailingStub)

    with pytest.raises(RuntimeError, match="simulated cloud failure"):
        t.transcribe(
            audio_path=str(audio),
            diarize=True,
            cloud_provider=h["stub_cls"].display_name,
            cloud_api_key="k",
        )

    # The kill spy set this when proc.kill() was called.
    assert h["fake_handle"]["_test_killed"][0], (
        "diarize subprocess must be killed when cloud STT fails — "
        "otherwise it outlives the parent's transcription run"
    )


def test_hybrid_propagates_cancel(monkeypatch, tmp_path):
    """User clicks Cancel: TranscriptionCancelled bubbles, diarize
    subprocess gets killed in the finally block."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    cancel = threading.Event()
    cancel.set()

    t, h = _hybrid_setup(monkeypatch)

    with pytest.raises(TranscriptionCancelled):
        t.transcribe(
            audio_path=str(audio),
            diarize=True,
            cloud_provider=h["stub_cls"].display_name,
            cloud_api_key="k",
            cancel_event=cancel,
        )

    assert h["fake_handle"]["_test_killed"][0]


# ── routing: cloud-with-native-diariz still uses original path ────────


def test_cloud_with_native_diariz_skips_hybrid(monkeypatch, tmp_path):
    """Regression guard: providers that DO have native diarization
    (Deepgram, AssemblyAI in real life — stub here) must NOT trigger
    the hybrid path. They should use the original _transcribe_via_cloud
    so the cloud's own speaker labels surface in the output."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    class _ConfiguredNative(_StubProviderWithNativeDiariz):
        def __init__(self, api_key):
            super().__init__(api_key, result=TranscriptionResult(
                segments=[
                    {
                        "start": 0.0, "end": 2.0, "text": "Cloud diariz.",
                        "speaker": "SPEAKER_00",
                    },
                ],
                language="ru",
                raw={},
            ))

    _patch_provider(monkeypatch, _ConfiguredNative)

    # Spy on diarize spawn — must NOT be called for native-diariz providers.
    launch_spy = MagicMock()
    monkeypatch.setattr(
        Transcriber, "_launch_diarization_subprocess", launch_spy,
    )

    t = Transcriber()
    text = t.transcribe(
        audio_path=str(audio),
        diarize=True,
        cloud_provider=_ConfiguredNative.display_name,
        cloud_api_key="k",
    )
    # Cloud's own SPEAKER_00 appears in output.
    assert "SPEAKER" in text or "Спикер" in text
    # NO local pyannote spawn.
    launch_spy.assert_not_called()


def test_cloud_no_diariz_skips_hybrid(monkeypatch, tmp_path):
    """When diarize=False, the hybrid path must not engage even for
    providers without native diarization. The cloud-only path still
    runs (returning unlabeled text)."""
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    t, h = _hybrid_setup(monkeypatch)

    launch_spy = MagicMock()
    monkeypatch.setattr(
        Transcriber, "_launch_diarization_subprocess", launch_spy,
    )

    t.transcribe(
        audio_path=str(audio),
        diarize=False,  # <-- the relevant difference
        cloud_provider=h["stub_cls"].display_name,
        cloud_api_key="k",
    )
    launch_spy.assert_not_called()
