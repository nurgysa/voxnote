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


_WIKILINK_ILLEGAL = str.maketrans({c: " " for c in "[]|#^"})


def _wikilink_safe(name: str) -> str:
    """Strip characters that would break an Obsidian [[wikilink]] and collapse
    whitespace. Returns '' when nothing usable remains."""
    return " ".join(name.translate(_WIKILINK_ILLEGAL).split())


def _render_relations(project_name: str | None, participants: list[str]) -> str:
    """Inline '## Связи' section linking the project + roster people as
    [[wikilinks]] (the Obsidian graph + GBrain are fed by inline links, not
    frontmatter). Returns '' when there is neither a project nor any participant,
    so the caller omits the whole section."""
    lines: list[str] = []
    proj = _wikilink_safe(project_name or "")
    if proj:
        lines.append(f"- **Проект:** [[{proj}]]")
    people = [s for s in (_wikilink_safe(p) for p in participants) if s]
    if people:
        joined = ", ".join(f"[[{p}]]" for p in people)
        lines.append(f"- **Участники:** {joined}")
    if not lines:
        return ""
    return "\n## Связи\n\n" + "\n".join(lines) + "\n\n"


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
    model: str | None = None,
    diarized: bool | None = None,
    duration_s: float | None = None,
    cost_estimate_usd: float | None = None,
    source_sha256: str | None = None,
    speaker_map: dict[str, str] | None = None,
) -> str:
    """Render transcript.md = YAML frontmatter + diarized body. Pure, no I/O.
    ``title`` is accepted for symmetry/future use; the body is the diarized
    transcript and the meeting identity lives in the folder name."""
    sp_line = f"source_path: {_yaml_str(source_path)}" if source_path else 'source_path: ""'
    duration_line = (
        f"duration_sec: {duration_s:.1f}" if duration_s is not None
        else "duration_sec: null"
    )
    cost_line = (
        f"cost_estimate_usd: {cost_estimate_usd:.6f}"
        if cost_estimate_usd is not None else "cost_estimate_usd: null"
    )
    frontmatter = [
        "---",
        "type: meeting",
        "tags: [meeting]",
        f"date: {date}",
        f"time: {_yaml_str(time)}",
        f"project: {project_name or ''}",
        f"participants: [{', '.join(_yaml_str(p) for p in participants)}]",
        f"provider: {provider}",
        f"model: {model or ''}",
        f"language: {language or ''}",
        f"diarized: {'true' if diarized else 'false'}",
        duration_line,
        cost_line,
        f"source_sha256: {source_sha256 or ''}",
        f"voxnote_id: {voxnote_id}",
        sp_line,
        f"nudged: {'true' if nudged else 'false'}",
        "---",
        "",
    ]
    body = format_diarized_markdown(segments, speaker_map)
    return (
        "\n".join(frontmatter)
        + _render_relations(project_name, participants)
        + body
        + "\n"
    )


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


def overwrite_transcript_note(meeting_folder: str, content: str) -> str:
    """Atomically overwrite ``<meeting_folder>/transcript.md`` with ``content``
    (UTF-8). The Voice-ID retroactive re-render reuses an existing meeting folder,
    so — unlike write_transcript_note — this never creates a new collision-safe
    folder. Returns the transcript.md path."""
    path = os.path.join(meeting_folder, "transcript.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    return path
