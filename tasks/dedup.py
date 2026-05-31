"""Task-dedup engine — decide if a new task duplicates a past SENT one.

Pure logic with no I/O in the body: the history loader and the LLM client
are injected (mirrors ``tasks/extractor.py``) so the whole module is
unit-testable without the filesystem or the network. PR-2 defines this;
PR-3 wires it into the Extract dialog. Nothing here is called by the
running app yet.

Pipeline (PR-3 caller shape):
    reg = build_sent_registry(list_history_entries(), load_tasks,
                              exclude_folder=current_folder)
    cands = find_candidates(new_task, reg, backend=b, container_id=c)
    if cands and cands[0][1] >= FUZZY_HIGH:
        match = cands[0][0]                      # confident, no LLM
    elif cands:                                  # borderline band
        match = disambiguate_via_llm(
            new_task, [c for c, _ in cands], openrouter_client, model)
    else:
        match = None                             # nothing close enough

Public API:
    SentTask                 — value type for a previously-sent task
    normalize_title(str)     — shared title normalization (exposed for tests)
    FUZZY_HIGH / FUZZY_LOW   — score thresholds (config-overridable in PR-3)
    build_sent_registry(...) — scan meeting history -> list[SentTask]
    find_candidates(...)     — fuzzy match within backend+container scope
    disambiguate_via_llm(...)— LLM resolves the borderline band
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

# Reuse the extractor's codefence stripper so dedup parses LLM JSON exactly
# like extraction does (intentional cross-module reuse of a shared helper).
from tasks.extractor import _strip_codefence
from tasks.openrouter_client import OpenRouterError
from tasks.persistence import PersistenceError
from tasks.schema import Task, TaskStatus

logger = logging.getLogger(__name__)

# Fuzzy-match score band (difflib.SequenceMatcher.ratio() on normalized
# titles). >=HIGH: confident duplicate, no LLM. LOW..HIGH: borderline ->
# ask the LLM. <LOW: not a match. PR-3 overrides these from config keys
# dedup_fuzzy_high / dedup_fuzzy_low.
FUZZY_HIGH = 0.82
FUZZY_LOW = 0.55

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


@dataclass(frozen=True)
class SentTask:
    """A task already created in a tracker on a past meeting.

    ``ref`` is the comment-addressable backend id (Linear node UUID /
    Trello full card id) copied from ``Task.backend_ref``; ``identifier``
    + ``url`` are the human badge/link for the UI. ``backend`` +
    ``container_id`` scope the match — a comment must land on the same
    team/board the new task would be created in.
    """
    title: str
    backend: str
    container_id: str
    ref: str
    identifier: str
    url: str
    meeting_name: str
    meeting_date: str


def normalize_title(title: str) -> str:
    r"""Lowercase, strip punctuation, collapse whitespace for fuzzy compare.

    ``\w`` is Unicode-aware (``re.UNICODE``) so Cyrillic / Kazakh letters
    survive — only punctuation and separators are removed. Empty/None-ish
    input returns "".
    """
    if not title:
        return ""
    lowered = title.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()


def build_sent_registry(
    entries: list[dict],
    load_tasks: Callable[[str], dict],
    *,
    exclude_folder: str | None = None,
) -> list[SentTask]:
    """Build the registry of previously-sent tasks from meeting history.

    ``entries`` come from ``utils.list_history_entries()`` (folder_path /
    folder_name / date_created). ``load_tasks`` is
    ``tasks.persistence.load_tasks`` injected so tests pass a fixture
    loader. A meeting contributes one ``SentTask`` per task with
    ``status == SENT`` and a non-empty ``backend_ref`` — older sent tasks
    predate ``backend_ref`` and have no comment-addressable id, so they
    cannot be commented on and are skipped. ``exclude_folder`` (the current
    meeting's ``folder_path``) never dedups against itself. Meetings with
    no/broken ``tasks.json`` (PersistenceError) are silently skipped — most
    meetings have no extracted tasks at all.
    """
    registry: list[SentTask] = []
    for entry in entries:
        folder = entry.get("folder_path")
        if not folder or folder == exclude_folder:
            continue
        try:
            loaded = load_tasks(folder)
        except PersistenceError:
            continue
        backend = loaded.get("backend") or "linear"
        container_id = loaded.get("team_id") or ""
        meeting_name = entry.get("folder_name") or ""
        meeting_date = entry.get("date_created") or ""
        for task in loaded.get("tasks", []):
            if task.status != TaskStatus.SENT or not task.backend_ref:
                continue
            registry.append(SentTask(
                title=task.title,
                backend=backend,
                container_id=container_id,
                ref=task.backend_ref,
                identifier=task.linear_issue_id or "",
                url=task.linear_issue_url or "",
                meeting_name=meeting_name,
                meeting_date=meeting_date,
            ))
    return registry


def find_candidates(
    new_task: Task,
    registry: list[SentTask],
    *,
    backend: str,
    container_id: str,
) -> list[tuple[SentTask, float]]:
    """Score ``new_task`` against same-scope registry entries, best first.

    Scope filter: only registry tasks with the SAME ``backend`` AND
    ``container_id`` are eligible — a dedup comment must land on the same
    team/board the new task would otherwise be created in. Score =
    ``difflib.SequenceMatcher.ratio()`` on normalized titles, in [0, 1].
    Returns ``(SentTask, score)`` pairs with ``score >= FUZZY_LOW``, sorted
    by score descending (Python's stable sort keeps registry order on
    ties). The caller distinguishes confident (``>= FUZZY_HIGH``) from
    borderline (``FUZZY_LOW..FUZZY_HIGH``) and only LLM-checks the latter.
    """
    new_norm = normalize_title(new_task.title)
    if not new_norm:
        return []
    scored: list[tuple[SentTask, float]] = []
    for sent in registry:
        if sent.backend != backend or sent.container_id != container_id:
            continue
        score = SequenceMatcher(None, new_norm, normalize_title(sent.title)).ratio()
        if score >= FUZZY_LOW:
            scored.append((sent, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def disambiguate_via_llm(
    new_task: Task,
    candidates: list[SentTask],
    openrouter_client,
    model: str,
) -> SentTask | None:
    """Ask the LLM which candidate (if any) is the same task as ``new_task``.

    Called only for the borderline fuzzy band, where string similarity is
    ambiguous. Reuses the extractor's OpenRouter call shape: json_mode
    first, retry once without it on a 400, ``_strip_codefence`` + json
    parse. Returns the matched ``SentTask`` (by ``ref``), or ``None`` when
    the LLM says "no match", names an unknown id, or returns unparseable
    output. A malformed reply fails SAFE to ``None`` (-> create a new task)
    rather than risk commenting on the wrong card. Network/HTTP errors
    other than the 400-json_mode case propagate as ``OpenRouterError``.
    """
    if not candidates:
        return None
    by_ref = {c.ref: c for c in candidates}
    cand_lines = "\n".join(f'- id={c.ref} | "{c.title}"' for c in candidates)
    system = (
        "Ты дедупликатор задач. Дано НОВОЕ название задачи и список РАНЕЕ "
        "созданных задач с их id. Верни строго JSON "
        '{"match_id": "<id одной совпадающей задачи>"} или '
        '{"match_id": null}, если ни одна не совпадает по смыслу. Совпадение '
        "= та же по сути работа, даже если формулировки разные. Без markdown, "
        "без пояснений."
    )
    user = (
        f'НОВАЯ задача: "{new_task.title}"\n\n'
        f"РАНЕЕ созданные:\n{cand_lines}\n\n"
        "Верни только JSON-объект."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        response = openrouter_client.complete(
            model=model, messages=messages, json_mode=True,
        )
    except OpenRouterError as e:
        if "вернул 400:" in str(e):
            logger.info("dedup model %s rejected json_mode, retrying without", model)
            response = openrouter_client.complete(
                model=model, messages=messages, json_mode=False,
            )
        else:
            raise

    raw = response["content"]
    try:
        data = json.loads(_strip_codefence(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "dedup LLM returned non-JSON, treating as no-match: %r", (raw or "")[:200],
        )
        return None
    match_id = data.get("match_id") if isinstance(data, dict) else None
    if not match_id:
        return None
    return by_ref.get(match_id)
