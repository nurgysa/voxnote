"""MCP stdio server exposing the transcription pipeline as typed tools.

Wraps ``cli.core`` (the same seam the CLI uses) so terminal coding agents —
Hermes Agent, OpenAI Codex CLI, Claude Code, Google Antigravity — can call
transcription / task-extraction / protocol generation / task-send as first-class
MCP tools instead of parsing CLI stdout. All four support MCP stdio servers; see
``AGENTS.md`` for per-agent registration snippets.

Design notes:

* **Secrets are server-side.** Provider / OpenRouter / backend keys are resolved
  from env + config.json via ``cli.config`` — they are NEVER tool parameters, so
  they never pass through the model's context.
* **stdout is sacred.** An stdio MCP server speaks JSON-RPC on stdout; nothing
  else may write there. Transcription progress is discarded (``on_status=None``)
  and faulthandler is pointed at a file (see ``main``), never stdout.
* **Headless.** Imports only ``mcp`` + ``cli.core`` / ``cli.config`` — never the
  GUI. Heavy pipeline deps load lazily inside ``cli.core`` at tool-call time.

Run: ``python -m cli.mcp_server``
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cli import config, core

mcp = FastMCP("voxnote")


# ── Server-side secret resolution (never tool params) ─────────────────

def _provider_and_key(provider: str | None, cfg: dict) -> tuple[str, str]:
    resolved = config.resolve(
        provider, "PROVIDER", cfg.get("cloud_provider"), default="AssemblyAI",
    )
    key = config.resolve(None, "API_KEY", (cfg.get("cloud_api_keys") or {}).get(resolved))
    if not key:
        raise ValueError(
            f"Нет API-ключа для провайдера {resolved!r} "
            "(config.json cloud_api_keys или VOXNOTE_API_KEY)."
        )
    return resolved, key


def _openrouter_key(cfg: dict) -> str:
    key = config.resolve(None, "OPENROUTER_API_KEY", cfg.get("openrouter_api_key"))
    if not key:
        raise ValueError(
            "Нет ключа OpenRouter "
            "(config.json openrouter_api_key или VOXNOTE_OPENROUTER_API_KEY)."
        )
    return key


def _lang(language: str | None) -> str | None:
    return None if (not language or language == "auto") else language


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def transcribe_audio(
    audio_path: str,
    provider: str | None = None,
    language: str | None = None,
    diarize: bool = False,
    hotwords: str | None = None,
    denoise: bool = False,
) -> dict:
    """Transcribe an audio file (mp3/wav/m4a) via a cloud STT provider.

    language: "ru" | "kk" | "en" | "mixed" (KZ+RU+EN) | null (auto-detect).
    Returns {text, language, provider, diarized, segments}. The provider API key
    comes from server config/env, not this call.
    """
    cfg = config.base_config()
    resolved_provider, key = _provider_and_key(provider, cfg)
    out = core.run_transcribe(
        audio_path,
        provider=resolved_provider,
        api_key=key,
        language=_lang(language),
        diarize=diarize,
        hotwords=hotwords,
        denoise=denoise,
        on_status=None,
    )
    return out.to_dict()


@mcp.tool()
def extract_tasks(
    transcript: str,
    language: str | None = None,
    model: str | None = None,
    backend: str | None = None,
    container_id: str | None = None,
) -> dict:
    """Extract action items from a meeting transcript via OpenRouter.

    Returns {tasks: [...], corrections, model}. Pass backend ("linear"|"glide"|
    "trello") + container_id to ground assignee/label IDs against that container.
    """
    cfg = config.merged_config()
    result = core.run_extract_tasks(
        transcript=transcript,
        lang=_lang(language),
        model=model or core.DEFAULT_MODEL,
        openrouter_key=_openrouter_key(cfg),
        backend_name=backend,
        container_id=container_id,
        config=cfg,
    )
    return {
        "tasks": [t.to_dict() for t in result.get("tasks", [])],
        "corrections": result.get("corrections", 0),
        "model": result.get("model", model or core.DEFAULT_MODEL),
    }


@mcp.tool()
def generate_protocol(
    transcript: str,
    language: str | None = None,
    model: str | None = None,
    speakers: list[str] | None = None,
    meeting_date: str = "",
) -> dict:
    """Generate a 5-block MoM protocol (markdown) from a transcript via OpenRouter.

    Returns {markdown}.
    """
    cfg = config.merged_config()
    result = core.run_protocol(
        transcript=transcript,
        lang=_lang(language),
        model=model or core.DEFAULT_MODEL,
        openrouter_key=_openrouter_key(cfg),
        speakers=tuple(speakers or ()),
        meeting_date=meeting_date,
    )
    return {"markdown": result.markdown}


@mcp.tool()
def list_containers(backend: str) -> list[dict]:
    """List a task backend's containers (Linear teams / Glide tables / Trello lists).

    backend: "linear" | "glide" | "trello". Returns [{id, label}] — use an id as
    ``container_id`` for ``extract_tasks`` / ``send_tasks``.
    """
    return core.list_containers(backend_name=backend, config=config.merged_config())


@mcp.tool()
def send_tasks(
    tasks: list[dict],
    backend: str,
    container_id: str,
    retry_failed: bool = False,
) -> list[dict]:
    """Send extracted tasks to a backend container. Returns per-task results.

    tasks: task dicts as returned by ``extract_tasks``. backend + container_id
    target the destination (see ``list_containers``).
    """
    from tasks.schema import Task

    task_objs = [Task.from_dict(t) for t in tasks]
    results = core.run_send(
        tasks=task_objs,
        backend_name=backend,
        container_id=container_id,
        config=config.merged_config(),
        retry_failed=retry_failed,
    )
    return [r.to_dict() for r in results]


def main() -> None:
    # Faulthandler before the first tool call triggers cli.core's lazy
    # transcriber import (→ native audio C-extensions) — CLAUDE.md invariant #1.
    # Pointed at a file, never stdout (which carries the JSON-RPC protocol).
    import faulthandler
    import os

    logs_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs",
    )
    os.makedirs(logs_dir, exist_ok=True)
    fault_log = open(  # noqa: SIM115  (lives for process lifetime)
        os.path.join(logs_dir, "faulthandler-mcp.log"), "w", encoding="utf-8",
    )
    faulthandler.enable(file=fault_log, all_threads=True)

    from utils import migrate_legacy_secret_dir

    migrate_legacy_secret_dir()
    mcp.run()


if __name__ == "__main__":
    main()
