"""Serial processing-queue worker — the third frontend over cli.core.

A single daemon thread carries each auto=True item through transcribe ->
protocol -> task-draft, calling the same cli.core.run_* functions the CLI and
GUI use, writing artifacts into the meeting folder, and persisting queue.json.
NO Tk: the thread mutates state under a lock and persists; the UI reads via
snapshot() and the injected on_change callback. Config and project resolution
are injected (config_loader / resolve_project) so this module stays headless
and decoupled from the directory store.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime

from cli import core
from processing import layout, store
from processing.model import QueueItem, StageStatus

logger = logging.getLogger(__name__)

_IDLE_WAIT_S = 1.0


class ProcessingQueue:
    def __init__(
        self,
        *,
        meetings_dir: str,
        config_loader: Callable[[], dict],
        resolve_project: Callable[[str | None], object | None],
        queue_path: str | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._meetings_dir = meetings_dir
        self._config_loader = config_loader
        self._resolve_project = resolve_project
        self._queue_path = queue_path
        self._on_change = on_change
        self._items: list[QueueItem] = store.load_active(queue_path)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = False

    # ── public API ──
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="processing-queue", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def enqueue(self, audio_path: str, options: dict) -> str:
        options = dict(options)
        item = QueueItem(
            id=f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}_{os.path.basename(audio_path)}",
            audio_path=audio_path,
            title=os.path.basename(audio_path),
            created_at=datetime.now().isoformat(timespec="seconds"),
            options=options,
            auto=True,
            project_id=options.get("project_id"),
        )
        with self._lock:
            self._items.append(item)
            self._persist_locked()
        self._wake.set()
        self._notify()
        return item.id

    def retry(self, item_id: str) -> None:
        with self._lock:
            for it in self._items:
                if it.id == item_id:
                    it.error_stage = None
                    it.error_message = None
                    for stage in ("transcript", "protocol", "tasks"):
                        if getattr(it, stage) == StageStatus.ERROR:
                            setattr(it, stage, StageStatus.PENDING)
                    it.auto = True
                    self._persist_locked()
                    break
        self._wake.set()
        self._notify()

    def snapshot(self) -> list[QueueItem]:
        with self._lock:
            return [QueueItem.from_dict(it.to_dict()) for it in self._items]

    # ── internals ──
    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _persist_locked(self) -> None:
        # Caller holds self._lock. queue.json carries active items only.
        store.save_active([it for it in self._items if it.auto], self._queue_path)

    def _set_stage(
        self,
        item: QueueItem,
        stage: str,
        status: StageStatus,
        *,
        error_stage: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            setattr(item, stage, status)
            item.error_stage = error_stage
            item.error_message = error_message
            self._persist_locked()
        self._notify()

    def _stage_transcribe(self, item: QueueItem) -> bool:
        """Transcribe → create meeting folder → place under project. True to
        continue, False to halt the item (stage error)."""
        self._set_stage(item, "transcript", StageStatus.RUNNING)
        try:
            import utils

            cfg = self._config_loader()
            opts = item.options
            provider = opts.get("provider") or cfg.get("cloud_provider") or "AssemblyAI"
            api_key = (cfg.get("cloud_api_keys") or {}).get(provider)
            if not api_key:
                raise ValueError(f"Нет API-ключа для провайдера {provider!r}.")
            language = opts.get("language") or None
            if language == "auto":
                language = None
            out = core.run_transcribe(
                item.audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=bool(opts.get("diarize")),
                hotwords=opts.get("hotwords") or None,
                denoise=bool(opts.get("denoise")),
            )
            folder = utils.create_history_entry(
                item.audio_path, out.text, out.language, f"cloud:{provider}",
            )
            utils.save_segments(folder, out.segments)
            project = self._resolve_project(opts.get("project_id"))
            folder = layout.assign_project(folder, project, self._meetings_dir)
            with self._lock:
                item.meeting_folder = folder
            if utils.should_delete_after_transcription(cfg, item.audio_path):
                try:
                    os.remove(item.audio_path)
                except OSError as e:
                    logger.warning("could not delete recording %s: %s", item.audio_path, e)
            self._set_stage(item, "transcript", StageStatus.DONE)
            return True
        except Exception as e:  # worker-thread boundary: any failure halts the
            # item but must never kill the daemon. Humanize for the UI; the
            # stage's ✗! is the user signal. (CLAUDE.md broad-except: justified
            # boundary, tracked in test_broad_except_ratchet.)
            from tasks.errors import humanize

            logger.exception("transcribe stage failed for item %s", item.id)
            self._set_stage(
                item, "transcript", StageStatus.ERROR,
                error_stage="transcript", error_message=humanize(e),
            )
            return False

    def _read_transcript(self, folder: str) -> str:
        for name in ("transcript.md", "transcript.txt"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    return f.read()
        raise FileNotFoundError(f"transcript not found in {folder}")

    def _stage_protocol(self, item: QueueItem) -> bool:
        self._set_stage(item, "protocol", StageStatus.RUNNING)
        try:
            cfg = self._config_loader()
            openrouter_key = cfg.get("openrouter_api_key")
            if not openrouter_key:
                raise ValueError("Нет ключа OpenRouter.")
            language = item.options.get("language") or None
            if language == "auto":
                language = None
            result = core.run_protocol(
                transcript=self._read_transcript(item.meeting_folder),
                lang=language,
                model=cfg.get("openrouter_model") or core.DEFAULT_MODEL,
                openrouter_key=openrouter_key,
            )
            with open(os.path.join(item.meeting_folder, "protocol.md"), "w", encoding="utf-8") as f:
                f.write(result.markdown)
            self._set_stage(item, "protocol", StageStatus.DONE)
            return True
        except Exception as e:  # worker-thread boundary — see _stage_transcribe.
            from tasks.errors import humanize

            logger.exception("protocol stage failed for item %s", item.id)
            self._set_stage(
                item, "protocol", StageStatus.ERROR,
                error_stage="protocol", error_message=humanize(e),
            )
            return False

    def _stage_tasks(self, item: QueueItem) -> bool:
        """Extract a task DRAFT → tasks_raw.json → AWAITING_REVIEW. No send."""
        self._set_stage(item, "tasks", StageStatus.RUNNING)
        try:
            cfg = self._config_loader()
            openrouter_key = cfg.get("openrouter_api_key")
            if not openrouter_key:
                raise ValueError("Нет ключа OpenRouter.")
            language = item.options.get("language") or None
            if language == "auto":
                language = None
            model = cfg.get("openrouter_model") or core.DEFAULT_MODEL
            result = core.run_extract_tasks(
                transcript=self._read_transcript(item.meeting_folder),
                lang=language,
                model=model,
                openrouter_key=openrouter_key,
            )
            tasks = result.get("tasks", [])
            payload = {
                "tasks": [t.to_dict() for t in tasks],
                "corrections": result.get("corrections", 0),
                "model": result.get("model", model),
            }
            target = os.path.join(item.meeting_folder, "tasks_raw.json")
            with open(target, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._set_stage(item, "tasks", StageStatus.AWAITING_REVIEW)
            return True
        except Exception as e:  # worker-thread boundary — see _stage_transcribe.
            from tasks.errors import humanize

            logger.exception("task-draft stage failed for item %s", item.id)
            self._set_stage(
                item, "tasks", StageStatus.ERROR,
                error_stage="tasks", error_message=humanize(e),
            )
            return False

    def _run(self) -> None:
        # Pipeline stages (transcribe / protocol / tasks) are wired in later tasks.
        while not self._stop:
            self._wake.wait(timeout=_IDLE_WAIT_S)
            self._wake.clear()
