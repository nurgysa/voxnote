"""Ratchet guard: broad ``except Exception`` handlers may not silently grow.

CLAUDE.md's exception convention prefers narrow except classes. A broad
``except Exception`` is legitimate only at thread/process boundaries (a
worker top-level, ``main()``, cleanup-then-reraise) and must carry a
one-line comment justifying why narrowing is wrong there.

This test freezes the per-file count of broad handlers (2026-06-11
hygiene pass). Adding a new one forces a conscious baseline edit in the
same diff — where the reviewer can ask for the justification. Removing
one fails too, so the baseline stays truthful: ratchet it down.

Pure source scan via ``ast`` — no app imports, so it runs on the Linux
CI leg (no Tk / PortAudio needed).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories that are not main-process app code. scripts/ is excluded by
# design: smoke/build scripts deliberately catch everything to print a
# FAIL line (their stdout IS the contract). Dot-dirs (.venv*, .cache,
# .git) and site-packages are skipped wholesale in _scan().
_EXCLUDED_DIRS = {
    "venv", "__pycache__", "site-packages",
    "build", "dist", "docs", "scripts", "tests", "vendor",
}

# file (posix path relative to repo root) -> number of broad handlers,
# each individually justified by an adjacent comment (or, for the CLI
# entry point, by the exit_code_for() mapping it feeds).
BASELINE = {
    "audio_cutter.py": 1,                          # decode-failure -> status label
    "cli/app.py": 1,                               # CLI boundary -> exit codes
    "cli/core.py": 1,                              # _safe_close in finally
    "gdrive/backup.py": 1,                         # UI callback isolation
    "processing/worker.py": 1,                     # single worker-thread boundary
    "providers/assemblyai.py": 1,                  # cancel-then-reraise
    "providers/speechmatics.py": 1,                # cancel-then-reraise
    "tasks/doc_context.py": 1,                     # per-file markitdown isolation
    "tasks/sender.py": 1,                          # belt-and-braces -> FAILED status
    "ui/app/main_entry.py": 2,                     # last-resort crash handler
    "ui/app/recorder_mixin.py": 1,                 # mic-open failures -> dialog
    "ui/app/transcription_mixin.py": 3,            # worker boundary + crash dump + hermes daemon
    "ui/dialogs/extract_tasks/__init__.py": 4,     # worker-thread boundaries
    "ui/dialogs/settings.py": 3,                   # OAuth/backup/bundle workers
    "ui/widgets.py": 1,                            # validate-callback boundary
}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except Exception`` / ``except:`` / tuples containing
    ``Exception``. ``BaseException`` would be worse, so count it too."""
    node = handler.type
    if node is None:  # bare except
        return True
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(
        isinstance(n, ast.Name) and n.id in ("Exception", "BaseException")
        for n in names
    )


def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        parts = rel.parts
        if _EXCLUDED_DIRS.intersection(parts) or any(
            p.startswith(".") for p in parts[:-1]  # dot-DIRS only, not files
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        n = sum(
            _is_broad(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
        )
        if n:
            counts[rel.as_posix()] = n
    return counts


def test_broad_except_count_matches_baseline():
    actual = _scan()
    grew = {
        f: (BASELINE.get(f, 0), n)
        for f, n in actual.items()
        if n > BASELINE.get(f, 0)
    }
    shrank = {
        f: (n, actual.get(f, 0))
        for f, n in BASELINE.items()
        if actual.get(f, 0) < n
    }
    assert not grew, (
        "New broad 'except Exception' handler(s): "
        + ", ".join(f"{f} ({old}->{new})" for f, (old, new) in sorted(grew.items()))
        + ". Narrow the class, or — if this is a genuine thread/process "
        "boundary — add a one-line justifying comment and bump BASELINE "
        "in this test."
    )
    assert not shrank, (
        "Broad handler count dropped below BASELINE: "
        + ", ".join(f"{f} ({old}->{new})" for f, (old, new) in sorted(shrank.items()))
        + ". Nice — ratchet the BASELINE down to match."
    )
