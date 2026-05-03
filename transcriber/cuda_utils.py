"""CUDA availability + cancellation primitives for the transcriber package.

This module is imported FIRST by :mod:`transcriber.__init__` so that
``ctranslate2`` loads before ``faster_whisper`` drags ``torch`` in. On
Windows, importing ``torch`` first locks/initializes CUDA DLLs in a way
that conflicts with ctranslate2 — see the comment in
:mod:`transcriber.__init__` and ``logs/transcribe_crash_*.log`` for the
``STATUS_DLL_INIT_FAILED`` trail.
"""
from __future__ import annotations

# ctranslate2 must be imported before torch on Windows (CUDA DLL ordering).
# Importing it here, at the top of the first transcriber/* module loaded,
# is what enforces the discipline package-wide.
import ctranslate2  # noqa: F401


class TranscriptionCancelled(Exception):
    """Raised inside :meth:`Transcriber.transcribe` when the cancel event fires.

    Caught in ``ui.app._run_transcription`` and routed to a "cancelled" UI
    state distinct from the "error" path — the user asked to stop, so
    we don't show a scary error dialog.
    """


def _check_cancelled(cancel_event) -> None:
    """Raise :class:`TranscriptionCancelled` if the event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled()


def _cuda_is_available() -> bool:
    """Cheap CUDA-availability probe via ctranslate2.

    We deliberately avoid ``torch.cuda.is_available()`` here — it would force
    a torch import in the main process before ctranslate2 has finished
    loading its DLLs, which on Windows triggers the CUDA DLL conflict that
    motivates the import-order discipline.

    Returns False on any error (no GPU, driver missing, broken CT2 install).
    """
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
