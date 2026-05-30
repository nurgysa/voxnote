"""People/projects directory (Phase A): schema, store, prompt-context renderer."""
from directory.context import render_meeting_context
from directory.schema import Person, Project, Voiceprint
from directory.store import DirectoryError, DirectoryStore

__all__ = [
    "Person",
    "Project",
    "Voiceprint",
    "DirectoryStore",
    "DirectoryError",
    "render_meeting_context",
]
