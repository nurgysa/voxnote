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

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime

from processing import store
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

    def _run(self) -> None:
        # Pipeline stages (transcribe / protocol / tasks) are wired in later tasks.
        while not self._stop:
            self._wake.wait(timeout=_IDLE_WAIT_S)
            self._wake.clear()
