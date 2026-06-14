import json
import os
import types

from directory.schema import Project
from processing.model import StageStatus
from processing.worker import ProcessingQueue


def _queue(tmp_path, **over):
    kwargs = dict(
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {},
        resolve_project=lambda pid: None,
        queue_path=str(tmp_path / "queue.json"),
        on_change=None,
    )
    kwargs.update(over)
    return ProcessingQueue(**kwargs)


class _Out:
    def __init__(self, text="hello", language="ru", segments=None):
        self.text = text
        self.language = language
        self.segments = segments if segments is not None else [
            {"speaker": "A", "text": "hi"}
        ]


def _patch_happy(monkeypatch, *, duration_s=60.0, size_bytes=1000, capture=None):
    """Patch preflight.probe + cli.core.run_transcribe for a happy run. When
    ``capture`` is a dict, run_transcribe records its kwargs there."""
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": duration_s, "size_bytes": size_bytes},
    )

    def _fake_transcribe(*a, **k):
        if capture is not None:
            capture.update(k)
        return _Out()

    monkeypatch.setattr("cli.core.run_transcribe", _fake_transcribe)


def _sandbox_home(tmp_path, monkeypatch):
    """Keep the segments sidecar (~/.voxnote/segments) inside tmp_path."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _audio(tmp_path, name="rec.m4a"):
    p = tmp_path / name
    p.write_bytes(b"\x00\x00")
    return str(p)


# ── enqueue / persistence (no processing) ──

def test_enqueue_appends_and_persists(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {"provider": "AssemblyAI", "project_id": "p1"})
    snap = q.snapshot()
    assert len(snap) == 1
    assert snap[0].id == item_id
    assert snap[0].audio_path == "/audio/a.m4a"
    assert snap[0].auto is True
    assert snap[0].project_id == "p1"
    assert snap[0].source == "pick"
    assert snap[0].status == StageStatus.PENDING
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["items"][0]["id"] == item_id


def test_enqueue_captures_source(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {"source": "record"})
    assert q.snapshot()[0].source == "record"


def test_snapshot_is_a_deep_copy(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    snap = q.snapshot()
    snap[0].status = StageStatus.DONE
    assert q.snapshot()[0].status == StageStatus.PENDING


def test_on_change_fires_on_enqueue(tmp_path):
    calls = []
    q = _queue(tmp_path, on_change=lambda: calls.append(1))
    q.enqueue("/audio/a.m4a", {})
    assert calls == [1]


def test_loads_existing_active_items(tmp_path):
    q1 = _queue(tmp_path)
    q1.enqueue("/audio/a.m4a", {})
    q2 = _queue(tmp_path)
    assert len(q2.snapshot()) == 1


# ── _process_item: happy path + archive variants ──

def test_process_item_writes_note_and_copies_for_pick(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    from utils import load_segments_sidecar

    meetings = tmp_path / "meetings"
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path)
    proj = Project(name="Kitng", id="p1")
    q = _queue(
        tmp_path,
        meetings_dir=str(meetings),
        resolve_project=lambda pid: proj if pid == "p1" else None,
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "project_id": "p1", "source": "pick"})
    q._process_item(q._items[0])

    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.meeting_folder and os.path.isdir(live.meeting_folder)
    assert os.path.basename(os.path.dirname(live.meeting_folder)) == "Kitng"
    note = os.path.join(live.meeting_folder, "transcript.md")
    assert os.path.isfile(note)
    with open(note, encoding="utf-8") as f:
        body = f.read()
    assert "hi" in body
    assert os.path.isfile(os.path.join(live.meeting_folder, "speakers.json"))
    assert os.path.isfile(audio)
    assert live.source_path and os.path.isfile(live.source_path)
    assert os.path.dirname(live.source_path) == str(sources_dir)
    assert load_segments_sidecar(live.id) == [{"speaker": "A", "text": "hi"}]
    assert not os.path.isfile(os.path.join(live.meeting_folder, "segments.json"))


def test_process_item_moves_audio_for_record(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path, "rec.wav")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert not os.path.exists(audio)
    assert live.source_path and os.path.isfile(live.source_path)


def test_process_item_without_sources_dir_keeps_audio(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert os.path.isfile(audio)
    assert live.source_path is None
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        assert "source_path:" in f.read()


# ── _process_item: guards + errors ──

def test_process_item_missing_key_errors_and_halts(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(tmp_path, meetings_dir=str(tmp_path / "meetings"), config_loader=lambda: {})
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.ERROR
    assert live.error_message
    assert live.meeting_folder is None


def test_process_item_provider_cap_blocks_before_upload(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": None, "size_bytes": 5 * 1024**3},
    )
    called = []
    monkeypatch.setattr("cli.core.run_transcribe", lambda *a, **k: called.append(1))
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    assert q.snapshot()[0].status == StageStatus.ERROR
    assert called == []


def test_process_item_denoise_auto_off_for_long_audio(tmp_path, monkeypatch):
    cap = {}
    _patch_happy(monkeypatch, duration_s=46 * 60, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "denoise": True})
    q._process_item(q._items[0])
    assert cap["denoise"] is False
    assert q.snapshot()[0].status == StageStatus.DONE


def test_process_item_denoise_kept_for_short_audio(tmp_path, monkeypatch):
    cap = {}
    _patch_happy(monkeypatch, duration_s=600, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "denoise": True})
    q._process_item(q._items[0])
    assert cap["denoise"] is True


def test_process_item_transcribe_error_halts_and_leaves_audio(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "processing.preflight.probe",
        lambda p: {"duration_s": 60.0, "size_bytes": 1000},
    )

    def _boom(*a, **k):
        raise RuntimeError("AssemblyAI вернул 401")

    monkeypatch.setattr("cli.core.run_transcribe", _boom)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.ERROR
    assert live.error_message
    assert os.path.isfile(audio)


# ── _process_item: Hermes nudge ──

def test_process_item_nudge_enabled_marks_delivered(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sent = {}

    def _emit(**k):
        sent.update(k)
        return types.SimpleNamespace(sent=True)

    monkeypatch.setattr(
        "integrations.hermes.client.emit_audio_transcribed_event", _emit
    )
    audio = _audio(tmp_path)
    proj = Project(name="P", id="p1")
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        resolve_project=lambda pid: proj,
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "hermes_webhook_enabled": True,
            "hermes_webhook_secret": "s",
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "project_id": "p1"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.nudge_delivered is True
    assert sent["note_path"].endswith("transcript.md")
    assert sent["project"] == {"id": "p1", "name": "P"}
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        assert "nudged: true" in f.read()


def test_process_item_nudge_failure_still_done(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "integrations.hermes.client.emit_audio_transcribed_event",
        lambda **k: types.SimpleNamespace(sent=False),
    )
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"},
            "hermes_webhook_enabled": True,
            "hermes_webhook_secret": "s",
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert live.nudge_delivered is False


# ── retry / scheduling ──

def test_retry_resets_errored_item_to_pending(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    it = q._items[0]
    it.status = StageStatus.ERROR
    it.error_message = "boom"
    it.auto = False
    q.retry(item_id)
    live = q.snapshot()[0]
    assert live.status == StageStatus.PENDING
    assert live.error_message is None
    assert live.auto is True


def test_retry_ignores_non_errored(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._items[0].status = StageStatus.DONE
    q.retry(item_id)
    assert q.snapshot()[0].status == StageStatus.DONE


def test_retry_unknown_id_is_noop(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q.retry("nope")
    assert len(q.snapshot()) == 1


def test_next_auto_item_skips_auto_false(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q._items[0].auto = False
    assert q._next_auto_item() is None


def test_next_auto_item_skips_settled(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q._items[0].status = StageStatus.DONE
    assert q._next_auto_item() is None


def test_started_thread_drains_to_done(tmp_path, monkeypatch):
    import time

    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path, meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.start()
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "record"})
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if q.snapshot()[0].status == StageStatus.DONE:
            break
        time.sleep(0.02)
    q.stop()
    assert q.snapshot()[0].status == StageStatus.DONE
