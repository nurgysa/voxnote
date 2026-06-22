"""Path-confinement guard for untrusted file inputs (audit WS-5, P1).

The MCP server exposes ``transcribe_audio(audio_path)`` where the path is a
*model-supplied* tool argument, and the CLI accepts audio / transcript /
tasks file paths. Without a guard an agent could point those at the secret
store — ``~/.voxnote/{config.json,directory.json,queue.json}`` — and
exfiltrate credentials by having them transcribed and uploaded to a cloud
provider.

Policy: a **deny-list**, not an allowlist. Only paths resolving into the
secret store are rejected; every other location stays readable, because the
app legitimately transcribes recordings from arbitrary user directories
(Obsidian vault, Documents, …) and an allowlist would break that.

Stdlib-only leaf module — unit-testable on Linux CI without the pipeline.
"""
from __future__ import annotations

from pathlib import Path

_SECRET_DIR_NAME = ".voxnote"


def _secret_store_root() -> Path:
    # Resolved per-call (not cached at import) so tests can patch Path.home()
    # and so a changed HOME between calls is honoured.
    return (Path.home() / _SECRET_DIR_NAME).resolve()


def ensure_outside_secret_store(path: str) -> str:
    """Return ``path`` unchanged, or raise ``ValueError`` if it resolves into
    the secret store.

    The path is expanded (``~``) and resolved (symlinks + ``..``) *before* the
    containment check, so ``foo/../.voxnote/config.json`` and a
    symlink pointing into the store are both caught — string-prefix checks on
    the raw input would miss them. Containment is by resolved parent, so a
    sibling like ``~/.voxnote-public`` is allowed.
    """
    resolved = Path(path).expanduser().resolve()
    root = _secret_store_root()
    if resolved == root or root in resolved.parents:
        raise ValueError(
            f"Доступ к файлам в {_SECRET_DIR_NAME}/ запрещён "
            f"(защита секретов): {path}"
        )
    return path
