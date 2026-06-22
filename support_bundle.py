"""Diagnostic log bundle for the "Сохранить лог для отправки" button (D4).

Zips ``logs/`` + a REDACTED ``config.json`` into a single file the user sends
to support manually — there is NO telemetry backend (decision D4). Owns
``redact_config`` directly (deny-by-default secret stripping), so the bundled
config cannot leak API keys: a log archive that leaked credentials would be a
worse problem than the silent crash it is meant to diagnose.

Pure stdlib — unit-testable on Linux CI without the dialog.
"""
from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path
from typing import Any

REDACTION_PLACEHOLDER = "<REDACTED>"

REDACTED_KEYS = (
    "openrouter_api_key",
    "linear_api_key",
    "glide_api_key",
    "assemblyai_api_key",
    "trello_api_key",
    "trello_token",
    "hf_token",
)

_SECRET_NAME_HINTS = ("key", "token", "secret", "password")


def _looks_like_secret(key_name: str) -> bool:
    """True if ``key_name`` contains any _SECRET_NAME_HINTS substring."""
    lowered = key_name.lower()
    return any(hint in lowered for hint in _SECRET_NAME_HINTS)


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``config`` with all secret values replaced by
    REDACTION_PLACEHOLDER. Input is never mutated. Deny-by-default: any top-level
    string whose key is in REDACTED_KEYS or looks like a secret is replaced;
    cloud_api_keys values are replaced (provider names kept)."""
    out = copy.deepcopy(config)
    for key, value in out.items():
        if isinstance(value, str) and (key in REDACTED_KEYS or _looks_like_secret(key)):
            out[key] = REDACTION_PLACEHOLDER
    cloud_keys = out.get("cloud_api_keys")
    if isinstance(cloud_keys, dict):
        out["cloud_api_keys"] = {k: REDACTION_PLACEHOLDER for k in cloud_keys}
    return out


def build_log_bundle(config: dict, dest_zip, *, logs_dir=None) -> dict:
    """Write a zip of ``logs/`` + a redacted ``config.json`` to ``dest_zip``.

    Every file under ``logs_dir`` (app.log + rotated backups + crash dumps +
    faulthandler logs) is added under a ``logs/`` prefix; ``config.json`` is
    redacted via ``redact_config`` before it goes in. A missing
    ``logs_dir`` still yields a valid bundle with just the config (the user can
    send their settings even before any log accrues). ``config`` is never
    mutated (redact_config deep-copies).

    ``logs_dir`` defaults to the app's real ``logs/`` (from ``logging_setup``).
    Returns ``{"log_files": <count>, "dest": <str path>}`` for the status label.
    """
    if logs_dir is None:
        from logging_setup import get_log_dir
        logs_dir = get_log_dir()

    logs = Path(logs_dir)
    dest = Path(dest_zip)
    dest.parent.mkdir(parents=True, exist_ok=True)

    log_count = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if logs.exists():
            for path in sorted(logs.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname="logs/" + path.relative_to(logs).as_posix())
                    log_count += 1
        zf.writestr(
            "config.json",
            json.dumps(redact_config(config), indent=2, ensure_ascii=False),
        )
    return {"log_files": log_count, "dest": str(dest)}
