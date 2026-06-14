# processing/vault_note.py
"""Write the meeting folder's transcript.md into the Obsidian vault.

The ONLY VoxNote writer that touches the vault. One meeting = one folder under
<meetings_dir>/<project>/<meeting>/ holding transcript.md (VoxNote). Hermes later
adds protocol.md + the tasks file into the same folder. Audio never enters the
vault — transcript.md's frontmatter records its source_path in Drive.
"""
from __future__ import annotations

import os

from directory.schema import Project
from processing.layout import target_dir
from transcript_format import format_diarized_markdown


def _yaml_str(value: str) -> str:
    """Quote a value so ':' and Windows paths survive YAML; backslashes -> '/'."""
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def render_transcript_note(
    *,
    segments: list[dict],
    title: str,
    project_name: str | None,
    date: str,
    time: str,
    participants: list[str],
    provider: str,
    language: str | None,
    voxnote_id: str,
    source_path: str | None,
    nudged: bool,
    speaker_map: dict[str, str] | None = None,
) -> str:
    """Render transcript.md = YAML frontmatter + diarized body. Pure, no I/O.
    ``title`` is accepted for symmetry/future use; the body is the diarized
    transcript and the meeting identity lives in the folder name."""
    sp_line = f"source_path: {_yaml_str(source_path)}" if source_path else 'source_path: ""'
    frontmatter = [
        "---",
        "type: meeting",
        f"date: {date}",
        f"time: {_yaml_str(time)}",
        f"project: {project_name or ''}",
        f"participants: [{', '.join(participants)}]",
        f"provider: {provider}",
        f"language: {language or ''}",
        f"voxnote_id: {voxnote_id}",
        sp_line,
        f"nudged: {'true' if nudged else 'false'}",
        "---",
        "",
    ]
    body = format_diarized_markdown(segments, speaker_map)
    return "\n".join(frontmatter) + body + "\n"


def write_transcript_note(
    meetings_dir: str, project: Project | None, meeting_name: str, content: str
) -> str:
    """Create <meetings_dir>/<project>/<meeting_name>/ (collision-safe folder) and
    write transcript.md inside (UTF-8, atomic). Returns the transcript.md path."""
    parent = target_dir(meetings_dir, project)
    os.makedirs(parent, exist_ok=True)
    folder = os.path.join(parent, meeting_name)
    n = 2
    while os.path.exists(folder):
        folder = os.path.join(parent, f"{meeting_name}-{n}")
        n += 1
    os.makedirs(folder)
    path = os.path.join(folder, "transcript.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    return path
