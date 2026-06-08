"""argparse front-end for the audio-transcriber CLI.

Parses subcommands, resolves settings (flag > env > config.json via
``cli.config``), calls the pure orchestration in ``cli.core``, serialises the
result (text by default, ``--json`` for agents), and maps any exception to a
process exit code via ``exit_code_for``.

NO Tk, NO sounddevice — imports only ``cli.core`` / ``cli.config`` (+ ``utils``
lazily for ``--save``). Invoked via ``python -m cli`` (see ``cli.__main__``).
"""
from __future__ import annotations

import argparse
import json
import sys

from cli import config, core
from cli._paths import ensure_outside_secret_store

# ── Exit codes (contract with the calling agent) ──────────────────────
EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3       # missing/invalid config: no key, unknown provider
EXIT_TRANSCRIBE = 4   # provider / transcription failure
EXIT_LLM = 5          # OpenRouter / extraction / protocol failure
EXIT_BACKEND = 6      # task-send / backend failure
EXIT_CANCELLED = 130  # SIGINT / user cancellation

BACKENDS = ("linear", "glide", "trello")
_LANG_CHOICES = ("ru", "kk", "en", "mixed", "auto")


def exit_code_for(exc: Exception) -> int:
    """Map a pipeline exception to a process exit code.

    Target mapping (see the plan's exit-code table):

      | exit | meaning                       | typical exception types          |
      |------|-------------------------------|----------------------------------|
      | 3    | missing/invalid config        | ValueError; unknown-provider     |
      |      |                               | ProviderError                    |
      | 4    | transcription/provider failure| ProviderError (HTTP); RuntimeError|
      | 5    | LLM extract/protocol failure  | OpenRouterError; ExtractionError;|
      |      |                               | ProtocolGenerationError          |
      | 6    | task-send/backend failure     | LinearError/GlideError/TrelloError|
      | 130  | cancelled                     | TranscriptionCancelled           |
      | 1    | anything else                 | (fallback)                       |

    The exception classes live in: ``providers.ProviderError``,
    ``tasks.openrouter_client.OpenRouterError``,
    ``tasks.extractor.ExtractionError``,
    ``tasks.protocol_generator.ProtocolGenerationError``,
    ``tasks.linear_client.LinearError`` / ``glide_client.GlideError`` /
    ``trello_client.TrelloError``, ``transcriber.TranscriptionCancelled``.

    The ``ProviderError`` wrinkle (it covers BOTH an unknown-provider name and a
    provider HTTP failure, and subclasses ``RuntimeError``) is resolved by
    ordering: the ``_cmd_*`` handlers raise ``ValueError`` up-front for an empty
    key, so a ``ValueError`` reaching here is a config error (3); any
    ``ProviderError`` / ``RuntimeError`` that reaches here is a runtime
    provider/transcription failure (4). An unknown-provider name *with* a key
    present is rare and maps to 4 under this resolution — acceptable;
    distinguishing it would need brittle message sniffing.
    """
    # Lazy imports keep providers/tasks/transcriber off cli.app's import path —
    # the headless guarantee; they are pulled only when an error is mapped.
    from providers import ProviderError
    from tasks.extractor import ExtractionError
    from tasks.glide_client import GlideError
    from tasks.linear_client import LinearError
    from tasks.openrouter_client import OpenRouterError
    from tasks.protocol_generator import ProtocolGenerationError
    from tasks.trello_client import TrelloError
    from transcriber import TranscriptionCancelled

    if isinstance(exc, TranscriptionCancelled):
        return EXIT_CANCELLED
    if isinstance(exc, ValueError):
        return EXIT_CONFIG
    if isinstance(exc, (LinearError, GlideError, TrelloError)):
        return EXIT_BACKEND
    if isinstance(exc, (OpenRouterError, ExtractionError, ProtocolGenerationError)):
        return EXIT_LLM
    # ProviderError currently subclasses RuntimeError; list both so the mapping
    # survives ProviderError's parent changing.
    if isinstance(exc, (ProviderError, RuntimeError)):
        return EXIT_TRANSCRIBE
    return EXIT_GENERIC


# ── Output helpers ────────────────────────────────────────────────────

def _print_error(exc: Exception, code: int, as_json: bool) -> None:
    """Render an error to stderr. ``--json`` mode emits a parseable object."""
    message = str(exc) or exc.__class__.__name__
    if as_json:
        print(
            json.dumps({"error_code": code, "message": message}, ensure_ascii=False),
            file=sys.stderr,
        )
    else:
        print(f"Ошибка: {message}", file=sys.stderr)


def _status_printer(args):
    """Status callback → stderr (keeps stdout clean for the result). None if --quiet."""
    if getattr(args, "quiet", False):
        return None

    def _emit(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    return _emit


def _resolve_language(value: str | None) -> str | None:
    """CLI language token → transcribe() code. 'auto'/None → None (auto-detect)."""
    if not value or value == "auto":
        return None
    return value


def _read_transcript(args) -> str:
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    ensure_outside_secret_store(args.transcript)
    with open(args.transcript, encoding="utf-8") as f:
        return f.read()


def _read_tasks(args):
    from tasks.schema import Task

    if getattr(args, "stdin", False):
        raw = sys.stdin.read()
    else:
        ensure_outside_secret_store(args.tasks)
        with open(args.tasks, encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw)
    items = data["tasks"] if isinstance(data, dict) and "tasks" in data else data
    return [Task.from_dict(d) for d in items]


# ── Subcommand handlers (return an exit code; raise on failure) ───────

def _cmd_transcribe(args) -> int:
    cfg = config.base_config()
    provider = config.resolve(
        args.provider, "PROVIDER", cfg.get("cloud_provider"), default="AssemblyAI",
    )
    api_key = config.resolve(
        args.api_key, "API_KEY", (cfg.get("cloud_api_keys") or {}).get(provider),
    )
    if not api_key:
        raise ValueError(
            f"Нет API-ключа для провайдера {provider!r}. "
            "Передай --api-key или AUDIO_TRANSCRIBER_API_KEY."
        )
    out = core.run_transcribe(
        args.audio,
        provider=provider,
        api_key=api_key,
        language=_resolve_language(args.language),
        diarize=args.diarize,
        hotwords=args.hotwords,
        denoise=args.denoise,
        on_status=_status_printer(args),
    )
    if args.save:
        import utils

        folder = utils.create_history_entry(
            args.audio, out.text, out.language, f"cloud:{provider}",
        )
        utils.save_segments(folder, out.segments)
        print(f"Сохранено: {folder}", file=sys.stderr)

    if args.json:
        print(json.dumps(out.to_dict(), ensure_ascii=False))
    else:
        print(out.text)
    return EXIT_OK


def _cmd_extract_tasks(args) -> int:
    cfg = config.merged_config()
    openrouter_key = config.resolve(
        args.openrouter_key, "OPENROUTER_API_KEY", cfg.get("openrouter_api_key"),
    )
    if not openrouter_key:
        raise ValueError(
            "Нет ключа OpenRouter. Передай --openrouter-key или "
            "AUDIO_TRANSCRIBER_OPENROUTER_API_KEY."
        )
    result = core.run_extract_tasks(
        transcript=_read_transcript(args),
        lang=_resolve_language(args.language),
        model=args.model or core.DEFAULT_MODEL,
        openrouter_key=openrouter_key,
        backend_name=args.backend,
        container_id=args.container_id,
        config=cfg,
    )
    tasks = result.get("tasks", [])
    if args.json:
        payload = {
            "tasks": [t.to_dict() for t in tasks],
            "corrections": result.get("corrections", 0),
            "model": result.get("model", args.model or core.DEFAULT_MODEL),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for t in tasks:
            suffix = f" (@{t.assignee_name})" if t.assignee_name else ""
            print(f"- {t.title}{suffix}")
    return EXIT_OK


def _cmd_protocol(args) -> int:
    cfg = config.merged_config()
    openrouter_key = config.resolve(
        args.openrouter_key, "OPENROUTER_API_KEY", cfg.get("openrouter_api_key"),
    )
    if not openrouter_key:
        raise ValueError(
            "Нет ключа OpenRouter. Передай --openrouter-key или "
            "AUDIO_TRANSCRIBER_OPENROUTER_API_KEY."
        )
    speakers = [s.strip() for s in (args.speakers or "").split(",") if s.strip()]
    result = core.run_protocol(
        transcript=_read_transcript(args),
        lang=_resolve_language(args.language),
        model=args.model or core.DEFAULT_MODEL,
        openrouter_key=openrouter_key,
        speakers=speakers,
        meeting_date=args.meeting_date or "",
    )
    if args.json:
        print(json.dumps({"markdown": result.markdown}, ensure_ascii=False))
    else:
        print(result.markdown)
    return EXIT_OK


def _cmd_list_containers(args) -> int:
    containers = core.list_containers(
        backend_name=args.backend, config=config.merged_config(),
    )
    if args.json:
        print(json.dumps(containers, ensure_ascii=False))
    else:
        for c in containers:
            print(f"{c['id']}\t{c['label']}")
    return EXIT_OK


def _cmd_send(args) -> int:
    results = core.run_send(
        tasks=_read_tasks(args),
        backend_name=args.backend,
        container_id=args.container_id,
        config=config.merged_config(),
        retry_failed=args.retry_failed,
    )
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False))
    else:
        for r in results:
            line = f"[{r.status}] {r.title}"
            if r.url:
                line += f" → {r.url}"
            if r.error:
                line += f" ({r.error})"
            print(line)
    # Non-zero only if work was attempted and nothing succeeded — lets the
    # agent distinguish "all sent / partial" (0) from "total failure" (6).
    if results and all(r.status != "sent" for r in results):
        return EXIT_BACKEND
    return EXIT_OK


def _cmd_pipeline(args) -> int:
    cfg = config.merged_config()
    provider = config.resolve(
        args.provider, "PROVIDER", cfg.get("cloud_provider"), default="AssemblyAI",
    )
    api_key = config.resolve(
        args.api_key, "API_KEY", (cfg.get("cloud_api_keys") or {}).get(provider),
    )
    openrouter_key = config.resolve(
        args.openrouter_key, "OPENROUTER_API_KEY", cfg.get("openrouter_api_key"),
    )
    if not api_key:
        raise ValueError(f"Нет API-ключа для провайдера {provider!r}.")
    if not openrouter_key:
        raise ValueError("Нет ключа OpenRouter.")

    language = _resolve_language(args.language)
    model = args.model or core.DEFAULT_MODEL

    transcript = core.run_transcribe(
        args.audio, provider=provider, api_key=api_key, language=language,
        diarize=args.diarize, hotwords=args.hotwords, denoise=args.denoise,
        on_status=_status_printer(args),
    )
    extract_result = core.run_extract_tasks(
        transcript=transcript.text, lang=language, model=model,
        openrouter_key=openrouter_key, backend_name=args.backend,
        container_id=args.container_id, config=cfg,
    )
    tasks = extract_result.get("tasks", [])
    protocol = core.run_protocol(
        transcript=transcript.text, lang=language, model=model,
        openrouter_key=openrouter_key,
    )
    out = {
        "transcript": transcript.to_dict(),
        "tasks": [t.to_dict() for t in tasks],
        "protocol": protocol.markdown,
    }
    if args.send and args.backend and args.container_id:
        send_results = core.run_send(
            tasks=tasks, backend_name=args.backend, container_id=args.container_id,
            config=cfg, retry_failed=False,
        )
        out["sent"] = [r.to_dict() for r in send_results]
    print(json.dumps(out, ensure_ascii=False))
    return EXIT_OK


# ── Parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio-transcriber",
        description="Headless transcription pipeline (transcribe → tasks → "
        "protocol → send) for shell / agent use.",
    )
    sub = parser.add_subparsers(dest="command")

    # transcribe
    p = sub.add_parser("transcribe", help="Transcribe an audio file.")
    p.add_argument("audio", help="Path to an audio file (mp3/wav/m4a).")
    p.add_argument("--provider", help="Cloud provider display name (e.g. AssemblyAI).")
    p.add_argument("--api-key", help="Provider API key (else env/config).")
    p.add_argument("--language", choices=_LANG_CHOICES, default="auto")
    p.add_argument("--diarize", action="store_true", help="Request speaker diarization.")
    p.add_argument("--hotwords", help="Comma-separated terms to bias recognition.")
    p.add_argument("--denoise", action="store_true", help="RNNoise before upload.")
    p.add_argument("--save", action="store_true", help="Also write a history entry.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.add_argument("--quiet", action="store_true", help="Suppress status to stderr.")
    p.set_defaults(func=_cmd_transcribe)

    # extract-tasks
    p = sub.add_parser("extract-tasks", help="Extract tasks from a transcript.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--transcript", help="Path to a transcript text file.")
    src.add_argument("--stdin", action="store_true", help="Read transcript from stdin.")
    p.add_argument("--backend", choices=BACKENDS, help="Backend for member/label context.")
    p.add_argument("--container-id", help="Team/table/board id for context.")
    p.add_argument("--model", help=f"OpenRouter model (default {core.DEFAULT_MODEL}).")
    p.add_argument("--openrouter-key", help="OpenRouter API key (else env/config).")
    p.add_argument("--language", choices=_LANG_CHOICES, default="auto")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_extract_tasks)

    # protocol
    p = sub.add_parser("protocol", help="Generate a 5-block MoM protocol.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--transcript", help="Path to a transcript text file.")
    src.add_argument("--stdin", action="store_true", help="Read transcript from stdin.")
    p.add_argument("--model", help=f"OpenRouter model (default {core.DEFAULT_MODEL}).")
    p.add_argument("--openrouter-key", help="OpenRouter API key (else env/config).")
    p.add_argument("--language", choices=_LANG_CHOICES, default="auto")
    p.add_argument("--meeting-date", help="ISO date, e.g. 2026-05-30.")
    p.add_argument("--speakers", help="Comma-separated participant names.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_protocol)

    # list-containers
    p = sub.add_parser("list-containers", help="List a backend's teams/tables/boards.")
    p.add_argument("--backend", choices=BACKENDS, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list_containers)

    # send
    p = sub.add_parser("send", help="Send tasks to a backend container.")
    p.add_argument("--backend", choices=BACKENDS, required=True)
    p.add_argument("--container-id", required=True, help="Team/table/board id.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tasks", help="Path to a tasks JSON file.")
    src.add_argument("--stdin", action="store_true", help="Read tasks JSON from stdin.")
    p.add_argument("--retry-failed", action="store_true", help="Resend FAILED tasks only.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_send)

    # pipeline (always JSON)
    p = sub.add_parser("pipeline", help="transcribe → extract → protocol (+optional send).")
    p.add_argument("audio", help="Path to an audio file.")
    p.add_argument("--provider")
    p.add_argument("--api-key")
    p.add_argument("--openrouter-key")
    p.add_argument("--language", choices=_LANG_CHOICES, default="auto")
    p.add_argument("--model")
    p.add_argument("--diarize", action="store_true")
    p.add_argument("--hotwords")
    p.add_argument("--denoise", action="store_true")
    p.add_argument("--backend", choices=BACKENDS)
    p.add_argument("--container-id")
    p.add_argument("--send", action="store_true", help="Also send tasks (needs backend+container).")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=_cmd_pipeline, json=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return func(args)
    except KeyboardInterrupt:
        print("Прервано пользователем.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as exc:
        code = exit_code_for(exc)
        _print_error(exc, code, getattr(args, "json", False))
        return code
