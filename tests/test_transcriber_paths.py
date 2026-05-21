"""Path-resolution invariants for the ``transcriber`` package.

Guards F4-style regressions: when ``transcriber.py`` was promoted to a
package, ``__file__`` shifted one level deeper and the diarize-worker
subprocess path silently went stale — the GUI looked fine until a user
clicked Транскрибировать with diarization on, then crashed with
``[Errno 2] No such file or directory``. CI tests didn't catch it
because they don't spawn the subprocess.

This file pins the path-derivation invariant so the next reorg fails at
test time, not at run time.
"""

from __future__ import annotations

from pathlib import Path

import transcriber


def test_diarize_worker_path_points_to_existing_file() -> None:
    worker_path = Path(transcriber._DIARIZE_WORKER_PATH)
    assert worker_path.is_file(), (
        f"diarize_worker.py not found at {worker_path}. "
        "If transcriber/ was reorganised, update _DIARIZE_WORKER_PATH "
        "in transcriber/__init__.py."
    )
