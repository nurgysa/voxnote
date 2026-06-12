"""Worker→UI marshalling in Settings must survive dialog destruction.

PR #142 introduced the narrow-TclError guard for the folder-stats
worker; this locks the generalized helper (``_post_to_ui``) and forbids
raw ``self.after(0, ...)`` calls anywhere else in settings.py. A raw
after() from a daemon worker raises TclError if the user closed
Settings mid-flight — or, worse, lands in the worker's broad
``except Exception``, gets mis-routed into the failure handler, whose
own after() then dies uncaught.

Source-text checks — settings.py imports CTk, which Linux CI cannot
import (no PortAudio via the ui package chain).
"""
from pathlib import Path

SETTINGS = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")


def _helper_block() -> str:
    """Body of _post_to_ui (up to the next method def)."""
    start = SETTINGS.index("def _post_to_ui")
    end = SETTINGS.index("\n    def ", start + 1)
    return SETTINGS[start:end]


def test_post_to_ui_guards_tclerror():
    block = _helper_block()
    assert "self.after(0" in block
    assert "except tk.TclError" in block, (
        "_post_to_ui must drop the callback when the dialog is destroyed"
    )


def test_no_raw_after_zero_outside_helper():
    # Every worker must marshal via _post_to_ui — a raw self.after(0, ...)
    # reintroduces the destroyed-dialog crash class. (self.after(200, ...)
    # for the icon workaround runs on the Tk thread and is out of scope.)
    total = SETTINGS.count("self.after(0")
    in_helper = _helper_block().count("self.after(0")
    assert total == in_helper, (
        f"{total - in_helper} raw self.after(0 call(s) outside _post_to_ui "
        "— route worker results through the guarded helper"
    )


def test_workers_use_the_helper():
    # stats(1) + gdrive sign-in(2) + backup status/success/failure(3) +
    # log bundle(2) = 8 marshalling sites today; growth is fine.
    assert SETTINGS.count("self._post_to_ui(") >= 8
