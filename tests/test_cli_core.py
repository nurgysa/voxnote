"""cli.core orchestration with the heavy pipeline deps stubbed.

core.run_* lazy-import transcriber/tasks INSIDE the function, so we inject fake
modules via monkeypatch.setitem(sys.modules, ...) before calling — no real
network / audio deps touched.
"""
from __future__ import annotations

import sys
import types

from cli import core


def test_run_transcribe_maps_result(monkeypatch):
    fake = types.ModuleType("transcriber")

    class _FakeTranscriber:
        def __init__(self):
            self.last_segments = [{"text": "hi", "speaker": "A"}]

        def transcribe(self, audio, **kwargs):
            self.kwargs = kwargs
            return "hello world"

    fake.Transcriber = _FakeTranscriber
    monkeypatch.setitem(sys.modules, "transcriber", fake)

    out = core.run_transcribe(
        "a.mp3", provider="AssemblyAI", api_key="k", language="ru", diarize=True,
    )
    assert out.text == "hello world"
    assert out.provider == "AssemblyAI"
    assert out.diarized is True
    assert out.to_dict()["language"] == "ru"


def test_run_transcribe_without_speaker_is_not_diarized(monkeypatch):
    fake = types.ModuleType("transcriber")

    class _FakeTranscriber:
        def __init__(self):
            self.last_segments = [{"text": "hi"}]

        def transcribe(self, audio, **kwargs):
            return "x"

    fake.Transcriber = _FakeTranscriber
    monkeypatch.setitem(sys.modules, "transcriber", fake)

    out = core.run_transcribe("a.mp3", provider="P", api_key="k")
    assert out.diarized is False


def test_run_transcribe_forwards_speaker_id_and_exposes_fields(monkeypatch):
    import cli.core as core

    captured = {}

    class FakeTranscriber:
        last_segments = [{"start": 0.0, "end": 1.0, "text": "hi",
                          "speaker": "Айбек Нурланов"}]
        last_speaker_identifiers = {"Айбек Нурланов": ["id-b"]}
        last_model = "m-x"
        def transcribe(self, audio_path, **kw):
            captured.update(kw)
            return "Айбек Нурланов: hi"

    # run_transcribe does `from transcriber import Transcriber` locally.
    import transcriber
    monkeypatch.setattr(transcriber, "Transcriber", lambda: FakeTranscriber())
    # Path-confinement guard: run_transcribe calls ensure_outside_secret_store.
    monkeypatch.setattr(core, "ensure_outside_secret_store", lambda p: None)

    out = core.run_transcribe(
        "meeting.wav", provider="Speechmatics", api_key="k", diarize=True,
        enroll_speakers=True,
        known_speakers=[{"label": "Айбек Нурланов", "identifiers": ["id-b"]}],
    )
    assert captured["enroll_speakers"] is True
    assert captured["known_speakers"] == [
        {"label": "Айбек Нурланов", "identifiers": ["id-b"]}]
    assert out.speaker_identifiers == {"Айбек Нурланов": ["id-b"]}
    assert out.model == "m-x"


def test_transcribe_output_speaker_fields_default_none():
    from cli.core import TranscribeOutput
    o = TranscribeOutput(text="t", language=None, provider="P", diarized=False)
    assert o.speaker_identifiers is None
    assert o.model is None


def test_run_send_maps_statuses(monkeypatch):
    from tasks.schema import Task, TaskStatus

    backends_mod = types.ModuleType("tasks.backends")

    class _FakeBackend:
        def close(self):
            pass

    backends_mod.backend_from_name = lambda name, cfg: _FakeBackend()
    monkeypatch.setitem(sys.modules, "tasks.backends", backends_mod)

    sender_mod = types.ModuleType("tasks.sender")

    def _send_tasks_iter(tasks, *, container_id, backend, on_status_change,
                         cancel_check, retry_failed):
        for task in tasks:
            task.status = TaskStatus.SENT
            task.linear_issue_url = "http://example/1"
            yield task

    sender_mod.send_tasks_iter = _send_tasks_iter
    monkeypatch.setitem(sys.modules, "tasks.sender", sender_mod)

    results = core.run_send(
        tasks=[Task(title="T1")], backend_name="trello",
        container_id="c", config={},
    )
    assert len(results) == 1
    assert results[0].status == "sent"
    assert results[0].url == "http://example/1"
