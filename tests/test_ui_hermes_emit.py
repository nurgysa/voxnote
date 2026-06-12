"""Source-slice tests for Hermes webhook emit wiring in TranscriptionMixin.

No ui.app import — sounddevice/PortAudio would break Linux CI. Uses window-
sliced source-text checks (pattern established in test_settings_async_stats.py).

Checks:
  1. _on_complete spawns a daemon thread targeting _emit_hermes_event.
  2. Tk vars are read in _on_complete (Tk thread), NOT inside _emit_hermes_event.
  3. _emit_hermes_event calls get_hermes_webhook_config, checks .enabled, and
     has the justified broad except at the worker-thread boundary.
"""
from __future__ import annotations

from pathlib import Path

_SRC = Path("ui/app/transcription_mixin.py").read_text(encoding="utf-8")


# ── helper: slice a method body by name ──────────────────────────────

def _method_body(name: str) -> str:
    """Return the text slice from 'def <name>' up to the next 'def ' at the
    same or lower indentation level (4 spaces = method indent)."""
    start = _SRC.index(f"def {name}")
    # Find the next method definition (same class level: 4-space indent)
    try:
        end = _SRC.index("\n    def ", start + 1)
    except ValueError:
        # Last method in the file
        end = len(_SRC)
    return _SRC[start:end]


# ── 1. _on_complete spawns a daemon thread ────────────────────────────

def test_on_complete_spawns_daemon_thread():
    body = _method_body("_on_complete")
    assert "threading.Thread(" in body, "_on_complete must spawn a Thread"
    assert "daemon=True" in body, "thread must be daemon=True"


def test_on_complete_thread_targets_emit_hermes_event():
    body = _method_body("_on_complete")
    assert "_emit_hermes_event" in body, "thread target must be _emit_hermes_event"


def test_on_complete_calls_thread_start():
    body = _method_body("_on_complete")
    assert ".start()" in body, "daemon thread must be .start()-ed"


# ── 2. Tk vars are read in _on_complete, not in _emit_hermes_event ───

def test_tk_vars_read_in_on_complete_not_in_worker():
    """_cloud_provider_var and _lang_var are read in _on_complete (Tk thread).

    The worker method _emit_hermes_event must NOT reference them directly —
    their values are passed as plain args to keep the code Tk-thread-safe.
    """
    on_complete_body = _method_body("_on_complete")
    worker_body = _method_body("_emit_hermes_event")

    # _on_complete reads the vars before spawning the thread
    assert "_cloud_provider_var" in on_complete_body, (
        "_cloud_provider_var must be read in _on_complete (Tk thread)"
    )
    assert "_lang_var" in on_complete_body, (
        "_lang_var must be read in _on_complete (Tk thread)"
    )

    # The worker method must NOT access Tk vars directly
    assert "_cloud_provider_var" not in worker_body, (
        "_cloud_provider_var must not appear inside _emit_hermes_event — "
        "values must be passed as pre-read args from the Tk thread"
    )
    assert "_lang_var" not in worker_body, (
        "_lang_var must not appear inside _emit_hermes_event — "
        "values must be passed as pre-read args from the Tk thread"
    )


# ── 3. _emit_hermes_event contains the required building blocks ───────

def test_emit_hermes_event_calls_get_hermes_webhook_config():
    body = _method_body("_emit_hermes_event")
    assert "get_hermes_webhook_config" in body


def test_emit_hermes_event_checks_enabled():
    body = _method_body("_emit_hermes_event")
    assert "hermes_cfg.enabled" in body or ".enabled" in body


def test_emit_hermes_event_calls_emit():
    body = _method_body("_emit_hermes_event")
    assert "emit_audio_transcribed_event" in body


def test_emit_hermes_event_has_broad_except_at_boundary():
    """The justified broad except must exist inside the worker method."""
    body = _method_body("_emit_hermes_event")
    assert "except Exception" in body, (
        "_emit_hermes_event must have a broad except at the worker-thread "
        "boundary (spec §9.5) — justified by the adjacent comment"
    )


def test_emit_hermes_event_has_justifying_comment():
    """The broad except must be accompanied by a justifying comment."""
    body = _method_body("_emit_hermes_event")
    # The comment uses the word "boundary" (established house pattern)
    assert "boundary" in body, (
        "The broad except must have a one-line comment containing 'boundary' "
        "to justify it per CLAUDE.md exception convention"
    )
