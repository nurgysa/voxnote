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
    def __init__(
        self,
        text="hello",
        language="ru",
        segments=None,
        model="universal-2",
        diarized=None,
    ):
        self.text = text
        self.language = language
        self.segments = segments if segments is not None else [
            {"speaker": "A", "text": "hi"}
        ]
        self.model = model
        self.diarized = (
            any(s.get("speaker") for s in self.segments)
            if diarized is None else diarized
        )
        self.speaker_identifiers = None


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
            if a:
                capture["audio_path"] = a[0]
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


def test_done_item_not_persisted_but_kept_in_memory(tmp_path):
    """A completed item is dropped from queue.json (queue.json = active work
    only) but stays in the in-memory snapshot for the session's live view."""
    q = _queue(tmp_path)
    done_id = q.enqueue("/audio/done.m4a", {})
    pend_id = q.enqueue("/audio/pend.m4a", {})
    q._set_status(q._items[0], StageStatus.DONE)

    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        persisted_ids = [it["id"] for it in json.load(f)["items"]]
    assert done_id not in persisted_ids   # DONE not written
    assert pend_id in persisted_ids       # active item still written

    # in-memory overlay preserved (live «Встречи» shows "just finished")
    statuses = {it.id: it.status for it in q.snapshot()}
    assert statuses[done_id] == StageStatus.DONE
    assert statuses[pend_id] == StageStatus.PENDING


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
    assert "provider: AssemblyAI" in body
    assert "model: universal-2" in body
    assert "diarized: true" in body
    assert "duration_sec: 60.0" in body
    assert "cost_estimate_usd: 0.002833" in body
    assert "source_sha256:" in body
    assert os.path.isfile(os.path.join(live.meeting_folder, "speakers.json"))
    assert os.path.isfile(audio)
    assert live.source_path and os.path.isfile(live.source_path)
    expected_archive_root = os.path.join(str(sources_dir), "Audio", "VoxNote", "Meetings")
    assert os.path.dirname(os.path.dirname(live.source_path)) == expected_archive_root
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


def test_process_item_asr_only_disables_diarization_speaker_hints_and_voiceid(
    tmp_path, monkeypatch
):
    cap = {}
    _patch_happy(monkeypatch, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    resolved_known = []

    def _resolve_known():
        resolved_known.append(True)
        return [("Айбек", ["voice-1"])]

    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"Speechmatics": "k"},
            "voiceid_enabled": True,
        },
        resolve_known_speakers=_resolve_known,
    )
    q.enqueue(
        audio,
        {
            "provider": "Speechmatics",
            "transcription_mode": "asr_only",
            "diarize": True,
            "num_speakers": 2,
            "min_speakers": 2,
            "max_speakers": 4,
        },
    )

    q._process_item(q._items[0])

    assert q.snapshot()[0].status == StageStatus.DONE
    assert cap["diarize"] is False
    assert cap["num_speakers"] is None
    assert cap["min_speakers"] is None
    assert cap["max_speakers"] is None
    assert cap["enroll_speakers"] is False
    assert cap["known_speakers"] is None
    assert resolved_known == []


def test_process_item_forces_asr_only_for_provider_without_diarization(
    tmp_path, monkeypatch
):
    cap = {}
    _patch_happy(monkeypatch, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    audio = _audio(tmp_path)
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"Groq": "k"}},
    )
    q.enqueue(
        audio,
        {
            "provider": "Groq",
            "transcription_mode": "meeting",
            "diarize": True,
            "num_speakers": 2,
        },
    )

    q._process_item(q._items[0])

    assert q.snapshot()[0].status == StageStatus.DONE
    assert cap["diarize"] is False
    assert cap["num_speakers"] is None


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


def test_loads_reconciles_interrupted_running_to_error(tmp_path):
    from processing.model import QueueItem
    from processing.store import save_active

    qp = tmp_path / "queue.json"
    save_active(
        [QueueItem(id="x", audio_path="/a.m4a", title="a", created_at="t",
                   auto=True, status=StageStatus.RUNNING)],
        path=qp,
    )
    q = _queue(tmp_path, queue_path=str(qp))
    live = q.snapshot()[0]
    assert live.status == StageStatus.ERROR
    assert live.error_message
    q.retry("x")
    assert q.snapshot()[0].status == StageStatus.PENDING


def test_load_drops_legacy_done_and_rewrites(tmp_path):
    """A queue.json written before pruning may carry DONE items; loading drops
    them (active list = active work only) and rewrites the file without them."""
    from processing.model import QueueItem
    from processing.store import save_active

    qp = tmp_path / "queue.json"
    save_active(
        [
            QueueItem(id="d", audio_path="/a.m4a", title="a", created_at="t",
                      auto=True, status=StageStatus.DONE),
            QueueItem(id="p", audio_path="/b.m4a", title="b", created_at="t",
                      auto=True, status=StageStatus.PENDING),
        ],
        path=qp,
    )
    q = _queue(tmp_path, queue_path=str(qp))

    assert [it.id for it in q.snapshot()] == ["p"]          # DONE dropped in memory
    with open(qp, encoding="utf-8") as f:                   # file rewritten without it
        assert [it["id"] for it in json.load(f)["items"]] == ["p"]


def test_load_keeps_error_drops_done(tmp_path):
    """ERROR items survive a reload (retry/crash-resume intact); DONE does not."""
    from processing.model import QueueItem
    from processing.store import save_active

    qp = tmp_path / "queue.json"
    save_active(
        [
            QueueItem(id="e", audio_path="/a.m4a", title="a", created_at="t",
                      auto=True, status=StageStatus.ERROR, error_message="boom"),
            QueueItem(id="d", audio_path="/b.m4a", title="b", created_at="t",
                      auto=True, status=StageStatus.DONE),
        ],
        path=qp,
    )
    q = _queue(tmp_path, queue_path=str(qp))

    live = q.snapshot()
    assert [it.id for it in live] == ["e"]
    assert live[0].status == StageStatus.ERROR
    assert live[0].error_message == "boom"


def test_process_item_moves_audio_for_inbox(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path, "phone.m4a")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "inbox"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    assert not os.path.exists(audio)  # inbox is drained by the move
    assert live.source_path and os.path.isfile(live.source_path)


def test_process_item_preserves_dated_audio_filename_in_archive(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    sources_dir = tmp_path / "sources"
    audio = _audio(tmp_path, "2026-07-04_1009_запись-автосохранение.m4a")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "sources_dir": str(sources_dir),
        },
    )
    q.enqueue(audio, {"provider": "AssemblyAI", "source": "inbox"})
    q._items[0].created_at = "2026-07-04T14:29:13"
    q._process_item(q._items[0])

    live = q.snapshot()[0]
    expected = os.path.join(
        str(sources_dir),
        "Audio",
        "VoxNote",
        "Meetings",
        "2026-07-04",
        "2026-07-04_1009_запись-автосохранение.m4a",
    )
    assert live.status == StageStatus.DONE
    assert live.source_path == expected
    assert os.path.isfile(expected)
    assert not os.path.exists(audio)
    assert os.path.basename(live.meeting_folder) == "2026-07-04_1009_запись-автосохранение"
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        body = f.read()
    assert 'time: "10:09"' in body


def test_process_item_archive_failure_is_nonfatal(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("processing.sources.archive_audio", _boom)
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
    assert live.status == StageStatus.DONE  # archiving is non-fatal
    assert live.source_path is None
    assert os.path.isfile(audio)  # original left in place when archive failed
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        assert "source_path:" in f.read()  # note records the original path


def test_process_item_resumes_from_source_path_when_original_gone(tmp_path, monkeypatch):
    cap = {}
    _patch_happy(monkeypatch, capture=cap)
    _sandbox_home(tmp_path, monkeypatch)
    archived = _audio(tmp_path, "archived.m4a")  # the prior attempt's archived copy
    sources_dir = tmp_path / "sources"
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {
            "cloud_api_keys": {"AssemblyAI": "k"}, "sources_dir": str(sources_dir),
        },
    )
    q.enqueue("/gone/original.m4a", {"provider": "AssemblyAI", "source": "record"})
    it = q._items[0]
    it.source_path = archived  # a prior attempt already archived (and moved) it
    q._process_item(it)
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    # transcribed from the archived copy, not the missing original
    assert cap["audio_path"] == archived
    # already-archived → not re-archived; the copy stays put
    assert os.path.isfile(archived)
    assert live.source_path == archived


# ── forget (evict an item, e.g. its meeting was deleted) ──

def test_forget_drops_item_and_persists(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._set_status(q._items[0], StageStatus.DONE)
    q.forget(item_id)
    assert q.snapshot() == []
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        assert json.load(f)["items"] == []


def test_forget_drops_errored_item(tmp_path):
    """«✕ Убрать» in «Встречи» relies on forget evicting an ERROR item (not
    only DONE). Pins the backend contract the UI dismiss depends on."""
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._set_status(q._items[0], StageStatus.ERROR, error_message="boom")
    q.forget(item_id)
    assert q.snapshot() == []
    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        assert json.load(f)["items"] == []


def test_forget_ignores_unknown_id(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    q.forget("does-not-exist")
    assert len(q.snapshot()) == 1


def test_forget_refuses_to_evict_running(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {})
    q._set_status(q._items[0], StageStatus.RUNNING)
    q.forget(item_id)
    assert len(q.snapshot()) == 1  # a live job must not be evicted


def test_process_item_links_roster_participants(tmp_path, monkeypatch):
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    proj = Project(name="AI Auditor", id="p1")
    q = _queue(
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        resolve_project=lambda pid: proj if pid == "p1" else None,
        resolve_participants=lambda pid: (
            ["Алмас Нурлан", "Данияр Сатыбалды"] if pid == "p1" else []
        ),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(_audio(tmp_path), {"provider": "AssemblyAI", "project_id": "p1"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "## Связи" in note
    assert "- **Проект:** [[AI Auditor]]" in note
    assert "[[Алмас Нурлан]], [[Данияр Сатыбалды]]" in note
    assert 'participants: ["Алмас Нурлан", "Данияр Сатыбалды"]' in note


def test_process_item_defaults_to_no_participants(tmp_path, monkeypatch):
    """Without an injected resolve_participants the worker renders an empty roster
    — backward compatibility for existing construction sites."""
    _patch_happy(monkeypatch)
    _sandbox_home(tmp_path, monkeypatch)
    q = _queue(  # _queue() does NOT pass resolve_participants → default applies
        tmp_path,
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(_audio(tmp_path), {"provider": "AssemblyAI", "source": "record"})
    q._process_item(q._items[0])
    live = q.snapshot()[0]
    assert live.status == StageStatus.DONE
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "participants: []" in note
    assert "**Участники:**" not in note


# ── Voice-ID integration ──

class _VOut:
    """run_transcribe output carrying speaker-ID fields (PR-1)."""
    text = "hi"
    language = "ru"
    def __init__(self, segments, speaker_identifiers, model="m-x"):
        self.segments = segments
        self.speaker_identifiers = speaker_identifiers
        self.model = model


def test_voiceid_on_sets_participants_and_writes_sidecar(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr("processing.preflight.probe",
                        lambda p: {"duration_s": 60.0, "size_bytes": 1000})
    capture = {}

    def _fake(*a, **k):
        capture.update(k)
        return _VOut(
            segments=[{"speaker": "Айбек Нурланов", "text": "привет", "start": 0.0},
                      {"speaker": "SPEAKER_1", "text": "кто", "start": 2.0}],
            speaker_identifiers={"Айбек Нурланов": ["known"], "S1": ["new-id"]},
        )
    monkeypatch.setattr("cli.core.run_transcribe", _fake)

    q = _queue(
        tmp_path,
        config_loader=lambda: {"cloud_provider": "Speechmatics",
                               "cloud_api_keys": {"Speechmatics": "k"},
                               "voiceid_enabled": True, "meetings_dir": str(tmp_path / "m")},
        resolve_known_speakers=lambda: [("Айбек Нурланов", ["known"])],
    )
    item_id = q.enqueue(_audio(tmp_path), {"provider": "Speechmatics", "diarize": True})
    q._process_item(q._items[0])

    # known speakers + enroll passed to the job
    assert capture["enroll_speakers"] is True
    assert capture["known_speakers"] == [
        {"label": "Айбек Нурланов", "identifiers": ["known"]}]
    # sidecar holds the pending new voice + model
    from utils import load_voiceid_sidecar
    sc = load_voiceid_sidecar(item_id, base_dir=str(tmp_path / ".voxnote" / "segments"))
    assert sc["model"] == "m-x"
    assert sc["pending"] == [{
        "label": "SPEAKER_1", "identifier": "new-id",
        "sample_text": "кто", "first_start": 2.0}]
    assert sc["note_meta"]["voxnote_id"] == item_id
    # participants rendered in transcript.md (voice-ID name present)
    live = q.snapshot()[0]
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "Айбек Нурланов" in note


def test_voiceid_off_uses_roster_and_no_sidecar(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr("processing.preflight.probe",
                        lambda p: {"duration_s": 60.0, "size_bytes": 1000})
    capture = {}

    def _fake(*a, **k):
        capture.update(k)
        return _VOut(segments=[{"speaker": "SPEAKER_1", "text": "x", "start": 0.0}],
                     speaker_identifiers={"S1": ["i"]})
    monkeypatch.setattr("cli.core.run_transcribe", _fake)

    q = _queue(
        tmp_path,
        config_loader=lambda: {"cloud_provider": "Speechmatics",
                               "cloud_api_keys": {"Speechmatics": "k"},
                               "voiceid_enabled": False, "meetings_dir": str(tmp_path / "m")},
        resolve_participants=lambda pid: ["Ростер Человек"],
        resolve_known_speakers=lambda: [("X", ["i"])],
    )
    item_id = q.enqueue(_audio(tmp_path), {"provider": "Speechmatics", "diarize": True})
    q._process_item(q._items[0])

    assert capture.get("enroll_speakers") is False
    from utils import load_voiceid_sidecar
    assert load_voiceid_sidecar(item_id, base_dir=str(tmp_path / ".voxnote" / "segments")) is None
    # roster fallback rendered in transcript.md (when voiceid off)
    live = q.snapshot()[0]
    with open(os.path.join(live.meeting_folder, "transcript.md"), encoding="utf-8") as f:
        note = f.read()
    assert "Ростер Человек" in note
