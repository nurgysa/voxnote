"""Diagnostic log bundle for the "Сохранить лог для отправки" button (D4).

Zips ``logs/`` + a REDACTED ``config.json`` into a single file the user sends
to support manually — there is NO telemetry backend (decision D4). Reuses
``gdrive.backup.redact_config`` so the same deny-by-default secret stripping
protects the bundled config: a log archive that leaked API keys would be a
worse problem than the silent crash it is meant to diagnose.

Pure (stdlib + redact_config) — unit-testable on Linux CI without the dialog.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from gdrive.backup import redact_config


def build_log_bundle(config: dict, dest_zip, *, logs_dir=None) -> dict:
    """Write a zip of ``logs/`` + a redacted ``config.json`` to ``dest_zip``.

    Every file under ``logs_dir`` (app.log + rotated backups + crash dumps +
    faulthandler logs) is added under a ``logs/`` prefix; ``config.json`` is
    redacted via ``gdrive.backup.redact_config`` before it goes in. A missing
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
