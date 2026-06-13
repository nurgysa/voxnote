"""Ratchet guard: the legacy 'audio-transcriber' brand must not reappear.

Allowed survivors: the one-time migration shim (utils.py), the migration
test, and this guard's own pattern list. The dated docs/superpowers archive
and third-party vendor/ text are out of scope by design.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LEGACY = (
    "audio-transcriber",
    "audio_transcriber",
    "AUDIO_TRANSCRIBER",
    "Audio Transcriber",
    "AudioTranscriber",
)
SKIP_DIRS = {
    ".git", "vendor", "dist", "build", ".cache", "logs",
    "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
}
SKIP_REL_PREFIXES = (str(Path("docs") / "superpowers"),)
ALLOWLIST = {
    "utils.py",
    str(Path("tests") / "test_secret_dir_migration.py"),
    str(Path("tests") / "test_no_legacy_brand_guard.py"),
}
TEXT_EXT = {
    ".py", ".md", ".ps1", ".json", ".txt", ".toml",
    ".cfg", ".ini", ".yml", ".yaml", ".spec", ".bat",
}


def test_no_legacy_brand_outside_allowlist():
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(REPO)
            if str(rel).startswith(SKIP_REL_PREFIXES):
                continue
            if str(rel) in ALLOWLIST:
                continue
            if path.suffix.lower() not in TEXT_EXT:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for token in LEGACY:
                if token in text:
                    offenders.append(f"{rel}: {token}")
    assert not offenders, "legacy brand name found:\n" + "\n".join(offenders)
