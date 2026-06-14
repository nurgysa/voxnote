# processing/inbox_watcher.py
"""Poll a Google Drive-synced `inbox/` folder for phone-uploaded audio.

Phone -> Google Drive (mobile) -> inbox/ -> (Drive Desktop syncs) -> this watcher.
Polling (not a filesystem-event lib) keeps it dependency-free and robust to Drive
sync quirks. A file is only handed off once its size is STABLE across two polls -
a large file (a 2-3 h recording is 100+ MB) is still syncing down and must not be
grabbed mid-write. No Tk: the App drives poll() on an after(...) tick and enqueues
the returned paths.
"""
from __future__ import annotations

import os

_AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac"}


def scan_inbox(inbox_dir: str, *, known: set[str]) -> list[str]:
    """Audio files directly in `inbox_dir` not already in `known`. Sorted, pure."""
    try:
        names = sorted(os.listdir(inbox_dir))
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        full = os.path.join(inbox_dir, name)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(name)[1].lower() not in _AUDIO_EXTS:
            continue
        if full in known:
            continue
        out.append(full)
    return out


class InboxWatcher:
    """Stateful debounce over scan_inbox. poll() returns files whose size held
    steady since the previous poll (i.e. finished syncing), each returned once."""

    def __init__(self, inbox_dir: str | None) -> None:
        self._inbox_dir = inbox_dir
        self._sizes: dict[str, int] = {}   # path -> size seen last poll
        self._done: set[str] = set()       # already handed off

    def poll(self) -> list[str]:
        if not self._inbox_dir or not os.path.isdir(self._inbox_dir):
            return []
        candidates = scan_inbox(self._inbox_dir, known=self._done)
        live = set(candidates)
        ready: list[str] = []
        for path in candidates:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if self._sizes.get(path) == size:   # unchanged since last poll -> stable
                ready.append(path)
                self._done.add(path)
                self._sizes.pop(path, None)
            else:
                self._sizes[path] = size
        # Drop bookkeeping for files that vanished (moved out / deleted).
        self._sizes = {p: s for p, s in self._sizes.items() if p in live}
        return ready
