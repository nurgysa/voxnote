"""Offline synthetic smoke helpers for the VoxNote → Hermes handoff.

The functions here are intentionally draft-only. They build a representative
``audio.transcribed`` payload, create the same signed HTTP request shape as the
webhook client, and render the shipped Hermes route prompt template without
contacting Hermes, STT providers, trackers, Obsidian, GBrain, or Drive.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from integrations.hermes.client import serialize_payload, sign_body
from integrations.hermes.schema import build_audio_transcribed_event

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTE_TEMPLATE = (
    _REPO_ROOT
    / "integrations"
    / "hermes"
    / "skills"
    / "voxnote"
    / "templates"
    / "audio-transcribed-route-prompt.md"
)
_PROMPT_FENCE_RE = re.compile(r"```text\s*\n(?P<body>.*?)\n```", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


def build_synthetic_audio_transcribed_event() -> dict:
    """Return a stable, non-sensitive Mini-AGI ``audio.transcribed`` payload."""
    transcript = (
        "Synthetic Mini-AGI smoke transcript. "
        "Обсудили интеграцию VoxNote с Mini-AGI. "
        "Решение: VoxNote создает transcript.md и шлет audio.transcribed. "
        "Задача: Hermes должен предложить protocol.md и tasks.md, "
        "но не отправлять задачи в трекеры без approval."
    )
    return build_audio_transcribed_event(
        transcript_text=transcript,
        audio_path="C:/Users/nurgisa/Recordings/synthetic-mini-agi-smoke.m4a",
        history_folder=(
            "C:/Users/nurgisa/Documents/Obsidian Vault/30 Meetings/"
            "Mini-AGI/2026-07-04_0900_synthetic-mini-agi-smoke"
        ),
        provider="synthetic",
        language="ru",
        segments=[
            {
                "speaker": "Speaker 1",
                "start": 0.0,
                "end": 8.0,
                "text": transcript,
            }
        ],
        routing_hint="obsidian_inbox",
        note_path=(
            "C:/Users/nurgisa/Documents/Obsidian Vault/30 Meetings/"
            "Mini-AGI/2026-07-04_0900_synthetic-mini-agi-smoke/transcript.md"
        ),
        source_path=(
            "G:/My Drive/Mini-AGI/sources/2026-07-04_0900_"
            "synthetic-mini-agi-smoke.m4a"
        ),
        project={"id": "mini-agi", "name": "Mini-AGI"},
        created_at="2026-07-04T09:00:00Z",
    )


def build_synthetic_webhook_request(secret: str = "synthetic-secret") -> dict:
    """Build a signed request dict without sending it anywhere.

    The body is produced by ``serialize_payload`` and the signature by
    ``sign_body`` so this dry run exercises the same client primitives as real
    delivery while keeping the smoke completely offline.
    """
    payload = build_synthetic_audio_transcribed_event()
    body = serialize_payload(payload)
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": sign_body(secret, body),
        "X-Request-ID": f"voxnote:{body_hash[:24]}",
    }
    return {"payload": payload, "body": body, "headers": headers}


def load_audio_transcribed_route_template(path: str | Path | None = None) -> str:
    """Load the fenced Hermes webhook prompt from the shipped skill template."""
    template_path = Path(path) if path is not None else _ROUTE_TEMPLATE
    text = template_path.read_text(encoding="utf-8")
    match = _PROMPT_FENCE_RE.search(text)
    if match is None:
        return text
    return match.group("body")


def _lookup(payload: dict, dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def render_audio_transcribed_route_prompt(
    payload: dict | None = None,
    *,
    template: str | None = None,
) -> str:
    """Render the route prompt with Hermes-style ``{dot.notation}`` fields.

    Missing fields raise ``KeyError`` so template/schema drift is visible during
    the smoke. ``None`` values render as an empty string, matching a conservative
    prompt-template fallback for optional fields.
    """
    data = payload if payload is not None else build_synthetic_audio_transcribed_event()
    template_text = template if template is not None else load_audio_transcribed_route_template()

    def replace(match: re.Match[str]) -> str:
        value = _lookup(data, match.group(1))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    return _PLACEHOLDER_RE.sub(replace, template_text)


def run_synthetic_smoke() -> dict:
    """Run the offline Wave 2 smoke and return a JSON-serializable summary."""
    request = build_synthetic_webhook_request()
    prompt = render_audio_transcribed_route_prompt(request["payload"])
    payload = request["payload"]
    body = request["body"]
    headers = request["headers"]
    return {
        "event_type": payload["event_type"],
        "version": payload["version"],
        "source": payload["source"],
        "request_id": headers["X-Request-ID"],
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "route_prompt_rendered": bool(prompt.strip()),
        "route_prompt_chars": len(prompt),
        "safety_policy_present": "untrusted meeting content" in prompt,
        "draft_only": True,
        "side_effects": "none",
        "preferred_source": payload["audio"]["note_path"],
        "fallback_source": "transcript.raw",
        "proposal": {
            "classification": "meeting_intake",
            "draft_outputs": ["protocol.md", "tasks.md"],
            "next_gate": "human_approval",
        },
        "approval_required_for": [
            "tracker_sends",
            "external_messages",
            "memory_or_gbrain_enrichment",
        ],
    }
