"""Serial processing-queue worker — the third frontend over cli.core.

A single daemon thread carries each auto=True item through ONE stage:
transcribe → archive audio to Drive sources → write transcript.md into the
Obsidian vault → persist a segments sidecar → fire a best-effort Hermes nudge.
VoxNote is transcribe-only; Hermes owns protocol/tasks/approve/send downstream
(spec: docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md).

NO Tk: the thread mutates state under a lock and persists; the UI reads via
snapshot() and the injected on_change callback. Config and project resolution
are injected (config_loader / resolve_project) so this module stays headless
and decoupled from the directory store.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime

from cli import core
from processing import preflight, sources, store, vault_note
from processing.model import QueueItem, StageStatus

logger = logging.getLogger(__name__)

_IDLE_WAIT_S = 1.0
_SLUG_ILLEGAL = re.compile(r"[^\w]+", re.UNICODE)


def _slug(text: str) -> str:
    """Filesystem-safe meeting slug from a title: Unicode letters/digits kept,
    runs of anything else → '-'. Falls back to 'meeting' when empty."""
    base = os.path.splitext(text)[0].strip().lower()
    base = _SLUG_ILLEGAL.sub("-", base).strip("-_")
    return base or "meeting"


def _parse_created(created_at: str) -> tuple[str, str, str]:
    """(date 'YYYY-MM-DD', time 'HH:MM', hhmm 'HHMM') from an ISO timestamp.
    Tolerant: returns ('', '', '') when unparseable."""
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return "", "", ""
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt.strftime("%H%M")


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
            source=options.get("source") or "pick",
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
                if it.id == item_id and it.status == StageStatus.ERROR:
                    it.status = StageStatus.PENDING
                    it.error_message = None
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

    def _set_status(
        self, item: QueueItem, status: StageStatus, *, error_message: str | None = None
    ) -> None:
        with self._lock:
            item.status = status
            item.error_message = error_message
            self._persist_locked()
        self._notify()

    def _process_item(self, item: QueueItem) -> None:
        """Transcribe → archive audio (sources) → write transcript.md (vault) →
        speakers.json + segments sidecar → best-effort Hermes nudge → DONE. Any
        failure halts THIS item (ERROR) but never kills the daemon."""
        self._set_status(item, StageStatus.RUNNING)
        try:
            import utils
            from integrations.hermes.client import (
                emit_audio_transcribed_event,
                get_hermes_webhook_config,
            )

            cfg = self._config_loader()
            opts = item.options
            provider = opts.get("provider") or cfg.get("cloud_provider") or "AssemblyAI"
            api_key = (cfg.get("cloud_api_keys") or {}).get(provider)
            if not api_key:
                raise ValueError(f"Нет API-ключа для провайдера {provider!r}.")
            language = opts.get("language") or None
            if language == "auto":
                language = None

            info = preflight.probe(item.audio_path)
            duration_s = info.get("duration_s")
            size_bytes = info.get("size_bytes", 0)
            ok, reason = preflight.provider_limit_ok(provider, duration_s, size_bytes)
            if not ok:
                raise ValueError(reason)
            denoise = preflight.should_denoise(duration_s, bool(opts.get("denoise")))

            out = core.run_transcribe(
                item.audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=bool(opts.get("diarize")),
                hotwords=opts.get("hotwords") or None,
                denoise=denoise,
                num_speakers=opts.get("num_speakers"),
                min_speakers=opts.get("min_speakers"),
                max_speakers=opts.get("max_speakers"),
            )

            date, time_str, hhmm = _parse_created(item.created_at)
            base = "_".join(p for p in (date, hhmm, _slug(item.title)) if p) or item.id
            project = self._resolve_project(opts.get("project_id"))

            # Archive only AFTER a successful transcribe, so a failure never
            # strands or loses audio (spec §Failure-handling, ordering). The
            # note then records the FINAL Drive path in a single write — no
            # second pass over transcript.md.
            sources_dir = (cfg.get("sources_dir") or "").strip()
            source_path: str | None = None
            if sources_dir:
                try:
                    source_path = sources.archive_audio(
                        item.audio_path, sources_dir, base,
                        move=item.source in ("record", "inbox"),
                    )
                except OSError as e:
                    # Archiving is non-fatal: the note records the original path
                    # instead (spec §Failure-handling). Audio stays put.
                    logger.warning("audio archive failed for %s: %s", item.id, e)

            hermes_cfg = get_hermes_webhook_config(cfg)
            content = vault_note.render_transcript_note(
                segments=out.segments,
                title=item.title,
                project_name=getattr(project, "name", None),
                date=date,
                time=time_str,
                participants=[],
                provider=provider,
                language=out.language,
                voxnote_id=item.id,
                source_path=source_path or item.audio_path,
                nudged=hermes_cfg.enabled,
            )
            note_path = vault_note.write_transcript_note(
                self._meetings_dir, project, base, content
            )
            folder = os.path.dirname(note_path)
            with self._lock:
                item.meeting_folder = folder
                item.source_path = source_path
            # Keep speakers.json for «Извлечь задачи» + directory compat, and so
            # store.build_view reads the project back from disk.
            utils.save_speakers(folder, opts.get("project_id"), [], {})
            utils.save_segments_sidecar(item.id, out.segments)

            if hermes_cfg.enabled:
                result = emit_audio_transcribed_event(
                    config=hermes_cfg,
                    transcript_text=out.text,
                    audio_path=item.audio_path,
                    history_folder=folder,
                    note_path=note_path,
                    source_path=source_path,
                    project=(
                        {"id": project.id, "name": project.name} if project else None
                    ),
                    provider=provider,
                    language=out.language,
                )
                with self._lock:
                    item.nudge_delivered = bool(result.sent)

            self._set_status(item, StageStatus.DONE)
        except Exception as e:  # worker-thread boundary: any failure halts THIS
            # item but must never kill the daemon. Humanize for the UI; the
            # ERROR status is the user signal. (CLAUDE.md broad-except: justified
            # boundary, tracked in test_broad_except_ratchet.)
            from tasks.errors import humanize

            logger.exception("processing failed for item %s", item.id)
            self._set_status(item, StageStatus.ERROR, error_message=humanize(e))

    def _next_auto_item(self) -> QueueItem | None:
        with self._lock:
            for it in self._items:
                if it.auto and it.status == StageStatus.PENDING:
                    return it
        return None

    def _run(self) -> None:
        while not self._stop:
            item = self._next_auto_item()
            if item is None:
                self._wake.wait(timeout=_IDLE_WAIT_S)
                self._wake.clear()
                continue
            self._process_item(item)
