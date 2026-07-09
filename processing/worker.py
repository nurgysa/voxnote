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

import hashlib
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
_TITLE_TIMESTAMP_PREFIX = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[_\s-]+"
    r"(?P<hour>\d{2})[:\-]?(?P<minute>\d{2})"
    r"(?:[:\-]?\d{2})?(?:[_\s-]+(?P<rest>.*))?$",
    re.UNICODE,
)


def _slug(text: str) -> str:
    """Filesystem-safe meeting slug from a title: Unicode letters/digits kept,
    runs of anything else → '-'. Falls back to 'meeting' when empty."""
    base = os.path.splitext(text)[0].strip().lower()
    base = _SLUG_ILLEGAL.sub("-", base).strip("-_")
    return base or "meeting"


def _sha256_file(path: str | None) -> str | None:
    """SHA-256 for provenance. None when the source is missing/unreadable."""
    if not path:
        return None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _parse_created(created_at: str) -> tuple[str, str, str]:
    """(date 'YYYY-MM-DD', time 'HH:MM', hhmm 'HHMM') from an ISO timestamp.
    Tolerant: returns ('', '', '') when unparseable."""
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return "", "", ""
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt.strftime("%H%M")


def _parse_title_timestamp(title: str) -> tuple[str, str, str] | None:
    """Return (date, time, base) when the filename already starts with a timestamp."""
    stem = os.path.splitext(title)[0].strip()
    match = _TITLE_TIMESTAMP_PREFIX.match(stem)
    if not match:
        return None
    date = match.group("date")
    hour = match.group("hour")
    minute = match.group("minute")
    rest = _slug(match.group("rest") or "") or "meeting"
    return date, f"{hour}:{minute}", f"{date}_{hour}{minute}_{rest}"


def _meeting_identity(title: str, created_at: str) -> tuple[str, str, str]:
    """Return note date, note time and collision-safe base filename/folder."""
    from_title = _parse_title_timestamp(title)
    if from_title is not None:
        date, time_str, base = from_title
        return date, time_str, base[:120]

    date, time_str, hhmm = _parse_created(created_at)
    base = "_".join(p for p in (date, hhmm, _slug(title)) if p) or title
    return date, time_str, base[:120] or "meeting"


class ProcessingQueue:

    def __init__(
        self,
        *,
        meetings_dir: str,
        config_loader: Callable[[], dict],
        resolve_project: Callable[[str | None], object | None],
        resolve_participants: Callable[[str | None], list[str]] | None = None,
        resolve_known_speakers: Callable[[], list[tuple[str, list[str]]]] | None = None,
        queue_path: str | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._meetings_dir = meetings_dir
        self._config_loader = config_loader
        self._resolve_project = resolve_project
        self._resolve_participants = resolve_participants or (lambda _pid: [])
        self._resolve_known_speakers = resolve_known_speakers or (lambda: [])
        self._queue_path = queue_path
        self._on_change = on_change
        self._items: list[QueueItem] = store.load_active(queue_path)
        # A RUNNING item in a freshly-loaded queue means a prior session
        # crashed mid-transcribe. Don't silently auto-resume — re-running a
        # 2–3 h cloud job costs real money (spec: no auto-retry). Surface it as
        # ERROR so the user can decide to «Повторить». (__init__ is
        # single-threaded; no lock needed yet.)
        interrupted = [it for it in self._items if it.status == StageStatus.RUNNING]
        for it in interrupted:
            it.status = StageStatus.ERROR
            it.error_message = (
                "Обработка прервана (приложение было перезапущено). "
                "Нажми «Повторить», чтобы запустить заново."
            )
        # DONE items in a loaded queue are legacy (pre-pruning): a finished
        # meeting belongs to disk, not the active queue. Drop them so the active
        # list and queue.json hold active work only and no stale audio_path
        # survives a restart.
        had_done = any(it.status == StageStatus.DONE for it in self._items)
        if had_done:
            self._items = [it for it in self._items if it.status != StageStatus.DONE]
        if interrupted or had_done:
            # Same predicate as _persist_locked: persist active work only, so
            # both save sites enforce the queue.json invariant at the call site.
            store.save_active(
                [it for it in self._items if it.auto and it.status != StageStatus.DONE],
                queue_path,
            )
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

    def forget(self, item_id: str) -> None:
        """Drop an item from the active queue — e.g. its meeting folder was
        deleted from «Встречи». No-op if the id is absent or the item is
        currently RUNNING (never evict a live job)."""
        with self._lock:
            keep = [
                it for it in self._items
                if not (it.id == item_id and it.status != StageStatus.RUNNING)
            ]
            changed = len(keep) != len(self._items)
            self._items = keep
            if changed:
                self._persist_locked()
        self._notify()

    def snapshot(self) -> list[QueueItem]:
        with self._lock:
            return [QueueItem.from_dict(it.to_dict()) for it in self._items]

    # ── internals ──
    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _persist_locked(self) -> None:
        # Caller holds self._lock. queue.json carries ACTIVE items only — a
        # finished meeting lives on disk (its transcript.md); persisting DONE
        # here would grow queue.json without bound and leak a stale audio_path
        # into the inbox dedup across restarts. build_view re-reads finished
        # meetings from their folders for «Встречи».
        store.save_active(
            [it for it in self._items if it.auto and it.status != StageStatus.DONE],
            self._queue_path,
        )

    def _set_status(
        self, item: QueueItem, status: StageStatus, *, error_message: str | None = None
    ) -> None:
        with self._lock:
            item.status = status
            item.error_message = error_message
            if status == StageStatus.RUNNING:
                item.started_at = datetime.now().isoformat(timespec="seconds")
            self._persist_locked()
        self._notify()

    def _process_item(self, item: QueueItem) -> None:
        """Transcribe → archive audio (sources) → write transcript.md (vault) →
        speakers.json + segments sidecar → best-effort Hermes nudge → DONE. Any
        failure halts THIS item (ERROR) but never kills the daemon."""
        self._set_status(item, StageStatus.RUNNING)
        try:
            # Lazy imports keep this module's import cost low and mirror
            # cli.core's lazy pattern; an ImportError surfacing as a per-item
            # ERROR (not a dead daemon) is acceptable at this boundary.
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

            # Resume-safe: if a prior attempt already archived the audio
            # (source_path recorded) and the original is gone, work from the
            # archived copy so a retry after a mid-run failure isn't a dead end.
            audio_path = item.audio_path
            already_archived = bool(item.source_path) and os.path.exists(item.source_path)
            if already_archived and not os.path.exists(audio_path):
                audio_path = item.source_path

            info = preflight.probe(audio_path)
            duration_s = info.get("duration_s")
            size_bytes = info.get("size_bytes", 0)
            ok, reason = preflight.provider_limit_ok(provider, duration_s, size_bytes)
            if not ok:
                raise ValueError(reason)
            denoise = preflight.should_denoise(duration_s, bool(opts.get("denoise")))
            provider_supports_diarization = True
            try:
                from providers import PROVIDERS
                provider_cls = PROVIDERS.get(provider)
                if provider_cls is not None:
                    provider_supports_diarization = provider_cls.supports_diarization
            except (ImportError, AttributeError):
                # Provider construction still enforces its own capabilities; this
                # branch keeps stale queue normalization best-effort only.
                pass
            asr_only = (
                opts.get("transcription_mode") == "asr_only"
                or not provider_supports_diarization
            )
            diarize = bool(opts.get("diarize")) and not asr_only

            voiceid_on = (
                bool(cfg.get("voiceid_enabled"))
                and provider == "Speechmatics"
                and diarize
                and not asr_only
            )
            known_speakers = [
                {"label": name, "identifiers": ids}
                for name, ids in (self._resolve_known_speakers() if voiceid_on else [])
            ]

            out = core.run_transcribe(
                audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=diarize,
                hotwords=opts.get("hotwords") or None,
                denoise=denoise,
                num_speakers=None if asr_only else opts.get("num_speakers"),
                min_speakers=None if asr_only else opts.get("min_speakers"),
                max_speakers=None if asr_only else opts.get("max_speakers"),
                enroll_speakers=voiceid_on,
                known_speakers=known_speakers or None,
            )

            voiceid_pending: list[dict] = []
            identified: list[str] = []
            if voiceid_on:
                from processing.voiceid import partition_speakers
                known_names = {s["label"] for s in known_speakers}
                identified, voiceid_pending = partition_speakers(
                    out.segments, out.speaker_identifiers or {}, known_names,
                )

            date, time_str, base = _meeting_identity(item.title, item.created_at)
            project = self._resolve_project(opts.get("project_id"))

            # Archive only AFTER a successful transcribe, so a failure never
            # strands or loses audio (spec §Failure-handling, ordering). Record
            # source_path immediately so a MOVED original is never lost track of
            # if a later step fails — a retry then resumes from it. Skip when a
            # prior attempt already archived. The note records the FINAL path in
            # a single write.
            sources_dir = (cfg.get("sources_dir") or "").strip()
            source_path: str | None = item.source_path
            if sources_dir and not already_archived:
                try:
                    source_path = sources.archive_audio(
                        audio_path, sources_dir, base,
                        move=item.source in ("record", "inbox"),
                    )
                except OSError as e:
                    # Archiving is non-fatal: the note records the original path
                    # instead (spec §Failure-handling). Audio stays put.
                    logger.warning("audio archive failed for %s: %s", item.id, e)
                    source_path = item.source_path
                if source_path:
                    with self._lock:
                        item.source_path = source_path
                        self._persist_locked()

            hermes_cfg = get_hermes_webhook_config(cfg)
            final_source_path = source_path or audio_path
            output_diarized = getattr(
                out, "diarized", any(s.get("speaker") for s in out.segments)
            )
            content = vault_note.render_transcript_note(
                segments=out.segments,
                title=item.title,
                project_name=getattr(project, "name", None),
                date=date,
                time=time_str,
                participants=(identified or self._resolve_participants(item.project_id)),
                provider=provider,
                language=out.language,
                voxnote_id=item.id,
                source_path=final_source_path,
                nudged=hermes_cfg.enabled,
                model=out.model,
                diarized=output_diarized,
                duration_s=duration_s,
                cost_estimate_usd=preflight.estimate_cost(provider, duration_s),
                source_sha256=_sha256_file(final_source_path),
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

            if voiceid_on and voiceid_pending:
                utils.save_voiceid_sidecar(item.id, {
                    "model": out.model,
                    "pending": voiceid_pending,
                    "note_meta": {
                        "title": item.title,
                        "project_name": getattr(project, "name", None),
                        "date": date,
                        "time": time_str,
                        "provider": provider,
                        "language": out.language,
                        "voxnote_id": item.id,
                        "source_path": source_path or audio_path,
                        "nudged": hermes_cfg.enabled,
                    },
                })

            if hermes_cfg.enabled:
                result = emit_audio_transcribed_event(
                    config=hermes_cfg,
                    transcript_text=out.text,
                    audio_path=audio_path,
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
