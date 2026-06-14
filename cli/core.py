"""Reusable pipeline orchestration for the CLI (and the future MCP shim).

Pure functions: NO argparse, NO Tk, NO printing. Each wraps an existing
pipeline entry point and returns plain data (dataclasses / dicts) or raises
the pipeline's own exceptions. All heavy imports (``transcriber`` / ``tasks``
/ ``providers``) are LAZY — done inside the functions — so ``import cli.core``
stays cheap and never pulls CustomTkinter / sounddevice. This is the headless
guarantee Hermes Agent relies on (enforced by the import-guard test).

This module is the seam a future stdio MCP server imports directly: register
each ``run_*`` function as an MCP tool, no CLI involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cli._paths import ensure_outside_secret_store

# Mirror the GUI's task-extraction default model (extract_tasks autofill path).
DEFAULT_MODEL = "google/gemini-3.5-flash"


@dataclass
class TranscribeOutput:
    """Result of a transcription run, serialisable for ``--json`` output."""

    text: str
    language: str | None
    provider: str
    diarized: bool
    segments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "provider": self.provider,
            "diarized": self.diarized,
            "segments": self.segments,
        }


@dataclass
class SendResult:
    """Per-task outcome from ``run_send``."""

    title: str
    status: str
    url: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status,
            "url": self.url,
            "error": self.error,
        }


def run_transcribe(
    audio_path: str,
    *,
    provider: str,
    api_key: str,
    language: str | None = None,
    diarize: bool = False,
    hotwords: str | None = None,
    denoise: bool = False,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    on_status=None,
) -> TranscribeOutput:
    """Transcribe one audio file via a cloud provider.

    ``language`` is already a code (``ru`` / ``kk`` / ``en`` / ``mixed``) or
    None — the caller maps ``auto`` → None. Raises ``ValueError`` (missing
    provider/key, or an ``audio_path`` inside the secret store — see
    ``cli._paths``), ``providers.ProviderError`` / ``RuntimeError`` (provider
    failure) or ``transcriber.TranscriptionCancelled``.
    """
    # Confine the model-/user-supplied path before any read (audit WS-5 P1):
    # reject ~/.voxnote/* so a transcribe call can't exfiltrate keys.
    ensure_outside_secret_store(audio_path)

    from transcriber import Transcriber

    transcriber = Transcriber()
    text = transcriber.transcribe(
        audio_path,
        language=language,
        diarize=diarize,
        hotwords=hotwords,
        denoise_audio=denoise,
        cloud_provider=provider,
        cloud_api_key=api_key,
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        on_status=on_status,
    )
    segments = transcriber.last_segments or []
    diarized = any(s.get("speaker") for s in segments)
    return TranscribeOutput(
        text=text,
        language=language,
        provider=provider,
        diarized=diarized,
        segments=segments,
    )


def run_extract_tasks(
    *,
    transcript: str,
    lang: str | None,
    model: str,
    openrouter_key: str,
    backend_name: str | None = None,
    container_id: str | None = None,
    config: dict | None = None,
) -> dict:
    """Extract tasks from a transcript via OpenRouter.

    When ``backend_name`` + ``container_id`` are given, fetches that backend's
    members/labels for LLM grounding (Linear); other backends return empty
    context. Returns ``tasks.extractor.extract``'s dict (``tasks`` are ``Task``
    objects). Raises ``OpenRouterError`` / ``ExtractionError`` / backend errors.
    """
    from tasks.extractor import extract
    from tasks.openrouter_client import OpenRouterClient

    members: list = []
    labels: list = []
    backend = None
    if backend_name and container_id:
        from tasks.backends import backend_from_name

        backend = backend_from_name(backend_name, config or {})
        ctx = backend.context(container_id)
        members = ctx.get("members") or []
        labels = ctx.get("labels") or []

    openrouter = OpenRouterClient(openrouter_key)
    try:
        return extract(
            transcript=transcript,
            model=model,
            lang=lang,
            openrouter_client=openrouter,
            members=members,
            labels=labels,
        )
    finally:
        _safe_close(openrouter)
        if backend is not None:
            _safe_close(backend)


def run_protocol(
    *,
    transcript: str,
    lang: str | None,
    model: str,
    openrouter_key: str,
    speakers=(),
    meeting_date: str = "",
):
    """Generate a 5-block MoM protocol. Returns ``ProtocolResult``.

    Raises ``ProtocolGenerationError`` on LLM / parse failure.
    """
    from tasks import protocol_generator
    from tasks.openrouter_client import OpenRouterClient

    openrouter = OpenRouterClient(openrouter_key)
    try:
        return protocol_generator.generate(
            transcript=transcript,
            speakers=list(speakers),
            meeting_date=meeting_date,
            lang=lang,
            model=model,
            openrouter_client=openrouter,
        )
    finally:
        _safe_close(openrouter)


def list_containers(*, backend_name: str, config: dict) -> list[dict]:
    """Return ``[{id, label}]`` for a backend's containers (teams/tables/boards).

    Lets the agent discover a ``--container-id`` for ``run_send``.
    """
    from tasks.backends import backend_from_name

    backend = backend_from_name(backend_name, config)
    try:
        return [
            {"id": c.id, "label": backend.container_label(c)}
            for c in backend.bootstrap()
        ]
    finally:
        _safe_close(backend)


def run_send(
    *,
    tasks,
    backend_name: str,
    container_id: str,
    config: dict,
    retry_failed: bool = False,
) -> list[SendResult]:
    """Send ``Task`` objects to a backend container. Returns per-task results.

    ``tasks`` is a list of ``tasks.schema.Task``. Only attempted tasks (those
    that transition to SENT/FAILED) appear in the result.
    """
    from tasks.backends import backend_from_name
    from tasks.sender import send_tasks_iter

    backend = backend_from_name(backend_name, config)
    results: list[SendResult] = []
    try:
        for task in send_tasks_iter(
            tasks,
            container_id=container_id,
            backend=backend,
            on_status_change=lambda _t, _s: None,
            cancel_check=lambda: False,
            retry_failed=retry_failed,
        ):
            results.append(
                SendResult(
                    title=task.title,
                    status=task.status.value,
                    url=task.linear_issue_url,
                    error=task.send_error,
                )
            )
    finally:
        _safe_close(backend)
    return results


def _safe_close(closeable) -> None:
    # Best-effort cleanup inside a finally-block: swallow ALL errors so a
    # close() failure can never mask the real pipeline exception being
    # propagated. (CLAUDE.md narrow-except rule yields here — masking the
    # original error would be strictly worse.)
    try:
        closeable.close()
    except Exception:
        pass
