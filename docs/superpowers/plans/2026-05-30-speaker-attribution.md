# Speaker Attribution (PR-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a «Кто говорит» panel to the Extract dialog that maps each diarized «Спикер N» to a directory person, substitutes the real ФИО into the transcript sent to the LLM, and persists the mapping into the `speakers:{}` slot PR-1 left in `speakers.json`.

**Architecture:** Three layers. (1) A pure helper `transcript_format.apply_speaker_names` rewrites bracketed labels → names. (2) `utils` gains `load_segments` and an optional `speaker_map` on `save_speakers`. (3) The Extract dialog builds one dropdown row per speaker label (from `segments.json` via the existing `_build_speaker_map`), auto-ticks the bound person as a participant, and — on extract — captures the maps on the main thread, rewrites the transcript, and passes it to both `extract()` and `generate()`. The LLM prompt-builder contracts are untouched; only the data changes.

**Tech Stack:** Python 3.10+, CustomTkinter (UI), pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-30-speaker-attribution-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `transcript_format.py` | pure segment/label formatters | **Add** `apply_speaker_names` |
| `utils.py` | persistence helpers | **Add** `load_segments`; **extend** `save_speakers` with `speaker_map` |
| `ui/dialogs/extract_tasks/__init__.py` | Extract dialog | **Add** «Кто говорит» rows + `_person_by_name` + `_on_speaker_bound` + `_selected_speaker_maps`; **extend** restore + `_run_extraction` |
| `tests/test_transcript_format.py` | pure formatter tests | **Extend** |
| `tests/test_utils_save_segments.py` | segments persistence tests | **Extend** (`load_segments`) |
| `tests/test_utils_save_speakers.py` | speakers persistence tests | **Extend** (`speaker_map`) |
| `tests/test_extract_dialog_context.py` | dialog source-text tests | **Extend** |

**Testing note (hard constraint):** UI tests are **source-text only** — never `import ui.app`/customtkinter in a test. Ubuntu CI lacks PortAudio, so importing the dialog crashes at import time (`sounddevice`). Local Windows pytest would pass while CI fails. All dialog assertions read the file text and check substrings (see existing `tests/test_extract_dialog_context.py`).

**Baseline:** `pytest` = 521 green before this PR; `ruff check .` clean. Run both before every commit.

---

## Task 1: `apply_speaker_names` pure helper

**Files:**
- Modify: `transcript_format.py` (append function at end of file)
- Test: `tests/test_transcript_format.py`

- [ ] **Step 1: Write the failing tests**

Add `apply_speaker_names` to the import block at the top of `tests/test_transcript_format.py`:

```python
from transcript_format import (
    _build_speaker_map,
    _fmt_time_human,
    _fmt_time_srt,
    _fmt_time_vtt,
    apply_speaker_names,
    format_diarized,
    format_srt,
    format_timed,
    format_vtt,
)
```

Append at the end of `tests/test_transcript_format.py`:

```python
# ── apply_speaker_names ────────────────────────────────────────────


def test_apply_speaker_names_replaces_bound_labels():
    text = "[00:05] [Спикер 1]: привет\n\n[00:12] [Спикер 2]: пока"
    out = apply_speaker_names(text, {"Спикер 1": "Айбек Нурланов"})
    assert "[Айбек Нурланов]: привет" in out
    assert "[Спикер 2]: пока" in out  # unbound label untouched


def test_apply_speaker_names_empty_map_is_identity():
    text = "[00:05] [Спикер 1]: привет"
    assert apply_speaker_names(text, {}) == text


def test_apply_speaker_names_no_collision_1_vs_11():
    text = "[Спикер 1]: a\n[Спикер 11]: b"
    out = apply_speaker_names(text, {"Спикер 1": "Сара"})
    assert "[Сара]: a" in out
    assert "[Спикер 11]: b" in out  # 11 must NOT be rewritten by the "1" rule
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_transcript_format.py -k apply_speaker_names -v`
Expected: FAIL — `ImportError: cannot import name 'apply_speaker_names'`

- [ ] **Step 3: Implement the helper**

Append to the end of `transcript_format.py`:

```python
def apply_speaker_names(text: str, name_by_label: dict[str, str]) -> str:
    """Replace bracketed friendly speaker labels with real names.

    ``name_by_label`` maps a friendly label ("Спикер 1") to a person's ФИО.
    Only bound labels are replaced; unbound labels stay "Спикер N". The
    bracketed token "[Спикер 1]" is replaced as a unit (both brackets
    included) so "Спикер 1" never matches inside "Спикер 11". Identity
    when the map is empty.
    """
    for label_text, name in name_by_label.items():
        text = text.replace(f"[{label_text}]", f"[{name}]")
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_transcript_format.py -k apply_speaker_names -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add transcript_format.py tests/test_transcript_format.py
git commit -m "feat(transcript): apply_speaker_names — substitute ФИО into labels"
```

---

## Task 2: `load_segments` persistence helper

**Files:**
- Modify: `utils.py` (add function after `load_speakers`, ~line 313)
- Test: `tests/test_utils_save_segments.py`

- [ ] **Step 1: Write the failing tests**

Change the import line at the top of `tests/test_utils_save_segments.py`:

```python
from utils import load_segments, save_segments
```

Append to `tests/test_utils_save_segments.py`:

```python
def test_load_segments_roundtrip(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "x", "speaker": "SPEAKER_00"}]
    save_segments(str(tmp_path), segs)
    assert load_segments(str(tmp_path)) == segs


def test_load_segments_missing_is_empty_list(tmp_path):
    assert load_segments(str(tmp_path)) == []


def test_load_segments_malformed_is_empty_list(tmp_path):
    (tmp_path / "segments.json").write_text("{not json", encoding="utf-8")
    assert load_segments(str(tmp_path)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_utils_save_segments.py -k load_segments -v`
Expected: FAIL — `ImportError: cannot import name 'load_segments'`

- [ ] **Step 3: Implement the helper**

In `utils.py`, add directly after the `load_speakers` function (it currently ends ~line 313, just before `list_history_entries`):

```python
def load_segments(folder: str) -> list[dict]:
    """Read <folder>/segments.json. Returns [] if absent or malformed.

    Mirror of load_speakers — the speaker-attribution panel must degrade
    silently when a meeting predates segments.json or the file is corrupt.
    Never raises.
    """
    target = os.path.join(folder, "segments.json")
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_utils_save_segments.py -v`
Expected: PASS (all, including the 3 pre-existing `save_segments` tests)

- [ ] **Step 5: Commit**

```bash
git add utils.py tests/test_utils_save_segments.py
git commit -m "feat(utils): load_segments — read <meeting>/segments.json"
```

---

## Task 3: extend `save_speakers` with `speaker_map`

**Files:**
- Modify: `utils.py` — `save_speakers` (currently `def save_speakers` ~line 280)
- Test: `tests/test_utils_save_speakers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_utils_save_speakers.py`:

```python
def test_save_speakers_writes_speaker_map(tmp_path):
    save_speakers(str(tmp_path), "p", ["a"], speaker_map={"SPEAKER_00": "a"})
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data["speakers"] == {"SPEAKER_00": "a"}


def test_save_speakers_default_speaker_map_is_empty(tmp_path):
    save_speakers(str(tmp_path), "p", ["a"])
    data = json.loads((tmp_path / "speakers.json").read_text(encoding="utf-8"))
    assert data["speakers"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_utils_save_speakers.py -k speaker_map -v`
Expected: FAIL — `TypeError: save_speakers() got an unexpected keyword argument 'speaker_map'`

- [ ] **Step 3: Extend the writer**

In `utils.py`, replace the `save_speakers` signature + docstring + payload. The current function is:

```python
def save_speakers(
    folder: str, project_id: str | None, participant_ids: list[str]
) -> None:
    """Atomically write the meeting's context selection to <folder>/speakers.json.

    Shape is forward-compatible with PR-2's per-speaker attribution: the empty
    "speakers" map is the slot that {SPEAKER_00: person_id, ...} fills later.
    PR-1 only ever writes project_id + participants.
    """
    payload = {
        "project_id": project_id,
        "participants": list(participant_ids),
        "speakers": {},
    }
```

Replace it with:

```python
def save_speakers(
    folder: str,
    project_id: str | None,
    participant_ids: list[str],
    speaker_map: dict[str, str] | None = None,
) -> None:
    """Atomically write the meeting's context selection to <folder>/speakers.json.

    ``speaker_map`` is the per-speaker attribution: raw provider label
    (e.g. "SPEAKER_00") → person_id. Defaults to None → writes an empty
    map, preserving the PR-1 caller shape exactly.
    """
    payload = {
        "project_id": project_id,
        "participants": list(participant_ids),
        "speakers": dict(speaker_map) if speaker_map else {},
    }
```

(Leave the tmp-write/`os.replace` tail of the function unchanged.)

- [ ] **Step 4: Run the full utils-speakers suite to verify pass + back-compat**

Run: `python -m pytest tests/test_utils_save_speakers.py -v`
Expected: PASS — the 2 new tests AND the 5 pre-existing ones (which call `save_speakers` with no `speaker_map` and assert `"speakers": {}`).

- [ ] **Step 5: Commit**

```bash
git add utils.py tests/test_utils_save_speakers.py
git commit -m "feat(utils): save_speakers accepts optional speaker_map"
```

---

## Task 4: dialog — «Кто говорит» rows + resolver + auto-sync + restore

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py`
- Test: `tests/test_extract_dialog_context.py`

This task is UI-only (no prompt change yet). It builds the rows, the name→person resolver, the participant auto-sync, and restore-on-reopen.

- [ ] **Step 1: Write the failing source-text tests**

Append to `tests/test_extract_dialog_context.py`:

```python
def test_dialog_builds_speaker_rows():
    src = SRC.read_text(encoding="utf-8")
    assert "Кто говорит" in src
    assert "_build_speaker_rows" in src
    assert "_speaker_row_vars" in src
    assert "load_segments" in src
    assert "_build_speaker_map" in src


def test_dialog_speaker_autosync_to_participants():
    src = SRC.read_text(encoding="utf-8")
    assert "_on_speaker_bound" in src
    assert "_person_by_name" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extract_dialog_context.py -k "speaker_rows or autosync" -v`
Expected: FAIL — assertions on missing substrings.

- [ ] **Step 3a: Initialise row state in `__init__`**

Find (currently ~line 104):

```python
        self._context_project_var = ctk.StringVar(value="— нет —")
        self._context_person_vars: dict[str, ctk.BooleanVar] = {}
```

Add two lines directly after it:

```python
        self._speaker_row_vars: dict[str, ctk.StringVar] = {}
        self._speaker_friendly: dict[str, str] = {}
```

- [ ] **Step 3b: Add the «Кто говорит» widgets to `ctx_frame`**

Find the end of the «Участники» block (currently ~line 335-339):

```python
        self._context_participants_frame.grid(
            row=1, column=1, padx=0, pady=(6, 0), sticky="ew",
        )
        self._rebuild_context_participants(set())
        self._restore_context_selection()
```

Replace it with (adds the speaker rows frame at `row=2` and reorders so the rows exist before restore runs):

```python
        self._context_participants_frame.grid(
            row=1, column=1, padx=0, pady=(6, 0), sticky="ew",
        )

        label(ctx_frame, "Кто говорит").grid(
            row=2, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
        )
        self._speaker_rows_frame = ctk.CTkFrame(ctx_frame, fg_color="transparent")
        self._speaker_rows_frame.grid(
            row=2, column=1, padx=0, pady=(6, 0), sticky="ew",
        )

        self._rebuild_context_participants(set())
        self._build_speaker_rows()
        self._restore_context_selection()
```

- [ ] **Step 3c: Add the new methods**

Insert these four methods directly after `_selected_context_people` (currently ends ~line 494, before `_restore_context_selection`):

```python
    def _build_speaker_rows(self) -> None:
        """Render one «Спикер N → person» dropdown per diarized speaker label.

        Reads <meeting>/segments.json and maps raw labels to the same
        friendly «Спикер N» the transcript shows (via _build_speaker_map).
        No segments / no diarization / empty directory → a muted hint and
        no rows (pure manual mapping is impossible; the dialog still works).
        """
        from transcript_format import _build_speaker_map
        from utils import load_segments

        for w in self._speaker_rows_frame.winfo_children():
            w.destroy()
        self._speaker_row_vars = {}
        self._speaker_friendly = {}

        label_map = _build_speaker_map(load_segments(self._history_folder))
        people = self._dir_store.people()
        if not label_map or not people:
            hint = (
                "(нет данных о спикерах)"
                if not label_map
                else "(справочник пуст — добавьте людей в «Справочники»)"
            )
            label(self._speaker_rows_frame, hint).grid(
                row=0, column=0, padx=4, pady=2, sticky="w",
            )
            return

        names = ["— не выбрано —"] + [p.full_name for p in people]
        for i, (raw, friendly) in enumerate(label_map.items()):
            self._speaker_friendly[raw] = friendly
            var = ctk.StringVar(value="— не выбрано —")
            self._speaker_row_vars[raw] = var
            label(self._speaker_rows_frame, friendly).grid(
                row=i, column=0, padx=(4, 8), pady=2, sticky="w",
            )
            ctk.CTkComboBox(
                self._speaker_rows_frame, variable=var, values=names,
                width=220, height=28, state="readonly",
                font=ctk.CTkFont(family=FONT, size=12),
                border_color=BORDER, button_color=BORDER,
                fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
                command=lambda _v, r=raw: self._on_speaker_bound(r),
            ).grid(row=i, column=1, padx=0, pady=2, sticky="w")

    def _person_by_name(self, full_name: str):
        """First directory person whose full_name matches, else None.

        «— не выбрано —» / unknown → None. Duplicate names resolve to the
        first match (same caveat as _selected_context_project).
        """
        if not full_name or full_name == "— не выбрано —":
            return None
        for p in self._dir_store.people():
            if p.full_name == full_name:
                return p
        return None

    def _on_speaker_bound(self, raw_label: str) -> None:
        """Auto-tick the chosen person as a participant (D-2 auto-sync)."""
        person = self._person_by_name(self._speaker_row_vars[raw_label].get())
        if person is not None and person.id in self._context_person_vars:
            self._context_person_vars[person.id].set(True)

    def _selected_speaker_maps(self) -> tuple[dict, dict]:
        """Resolve speaker rows → (speaker_map, name_by_label).

        speaker_map:  raw label  → person_id   (persisted to speakers.json)
        name_by_label: «Спикер N» → ФИО         (rewrites the LLM transcript)

        MUST be called on the main thread — Tk vars are not thread-safe; the
        result is passed into the _run_extraction worker.
        """
        speaker_map: dict[str, str] = {}
        name_by_label: dict[str, str] = {}
        for raw, var in self._speaker_row_vars.items():
            person = self._person_by_name(var.get())
            if person is not None:
                speaker_map[raw] = person.id
                name_by_label[self._speaker_friendly[raw]] = person.full_name
        return speaker_map, name_by_label
```

- [ ] **Step 3d: Restore bindings on re-open**

Find `_restore_context_selection` (currently ~line 496-507):

```python
    def _restore_context_selection(self) -> None:
        """Re-apply a previously saved project + participants from speakers.json."""
        from utils import load_speakers
        data = load_speakers(self._history_folder)
        if not data:
            return
        project = self._dir_store.get_project(data.get("project_id") or "")
        if project is not None:
            self._context_project_var.set(project.name)
        checked = set(data.get("participants") or [])
        if checked:
            self._rebuild_context_participants(checked)
```

Append the binding-restore block to the end of that method (still inside it, after the `if checked:` block):

```python
        # PR-2: restore per-speaker bindings (raw label → person_id). Setting
        # the StringVar does not fire the combobox command, so no auto-sync
        # re-runs here — participants were already restored above.
        for raw, person_id in (data.get("speakers") or {}).items():
            person = self._dir_store.get_person(person_id)
            if person is not None and raw in self._speaker_row_vars:
                self._speaker_row_vars[raw].set(person.full_name)
```

- [ ] **Step 4: Run the dialog source-text tests + full suite**

Run: `python -m pytest tests/test_extract_dialog_context.py -v`
Expected: PASS — the 2 new tests plus all pre-existing ones.

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_context.py
git commit -m "feat(ui): «Кто говорит» speaker-binding rows + auto-sync"
```

---

## Task 5: dialog — rewrite transcript + persist speaker_map on extract

**Files:**
- Modify: `ui/dialogs/extract_tasks/__init__.py` — the extract trigger + `_run_extraction`
- Test: `tests/test_extract_dialog_context.py`

- [ ] **Step 1: Write the failing source-text tests**

Append to `tests/test_extract_dialog_context.py`:

```python
def test_run_extraction_rewrites_transcript_with_names():
    src = SRC.read_text(encoding="utf-8")
    assert "apply_speaker_names(" in src
    # rewritten transcript flows into BOTH extract() and generate()
    assert src.count("transcript=transcript_for_llm") >= 2


def test_run_extraction_persists_speaker_map():
    src = SRC.read_text(encoding="utf-8")
    assert "speaker_map=speaker_map" in src
    assert "_selected_speaker_maps()" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extract_dialog_context.py -k "rewrites_transcript or persists_speaker_map" -v`
Expected: FAIL — substrings absent.

- [ ] **Step 3a: Capture the maps on the main thread before starting the worker**

Find the extract-trigger block (currently ~line 695-701):

```python
        project = self._selected_context_project()
        people = self._selected_context_people()
        threading.Thread(
            target=self._run_extraction,
            args=(container, model, backend_name, project, people),
            daemon=True,
        ).start()
```

Replace it with:

```python
        project = self._selected_context_project()
        people = self._selected_context_people()
        speaker_map, name_by_label = self._selected_speaker_maps()
        threading.Thread(
            target=self._run_extraction,
            args=(container, model, backend_name, project, people,
                  speaker_map, name_by_label),
            daemon=True,
        ).start()
```

- [ ] **Step 3b: Widen the `_run_extraction` signature + rewrite the transcript**

Find the signature + opening of `_run_extraction` (currently ~line 712-724):

```python
    def _run_extraction(
        self, container, model: str, backend_name: str, project, people: list,
    ) -> None:
        from directory.context import render_meeting_context
        from tasks.backends import backend_from_name
        from tasks.extractor import ExtractionError, extract
        from tasks.glide_client import GlideError
        from tasks.linear_client import LinearError
        from tasks.openrouter_client import OpenRouterClient, OpenRouterError
        from tasks.persistence import save_tasks_raw
        from tasks.trello_client import TrelloError
        from utils import save_speakers
        meeting_context = render_meeting_context(people, project) or None
```

Replace it with (adds two params, the `apply_speaker_names` import, and the rewrite):

```python
    def _run_extraction(
        self, container, model: str, backend_name: str, project, people: list,
        speaker_map: dict, name_by_label: dict,
    ) -> None:
        from directory.context import render_meeting_context
        from tasks.backends import backend_from_name
        from tasks.extractor import ExtractionError, extract
        from tasks.glide_client import GlideError
        from tasks.linear_client import LinearError
        from tasks.openrouter_client import OpenRouterClient, OpenRouterError
        from tasks.persistence import save_tasks_raw
        from tasks.trello_client import TrelloError
        from transcript_format import apply_speaker_names
        from utils import save_speakers
        meeting_context = render_meeting_context(people, project) or None
        # PR-2: substitute bound ФИО into the transcript labels before the LLM
        # sees it. Empty name_by_label → identity (no diarization / no binding).
        transcript_for_llm = apply_speaker_names(self._transcript, name_by_label)
```

- [ ] **Step 3c: Feed the rewritten transcript to `extract()`**

Find (currently ~line 748-756):

```python
            result = extract(
                transcript=self._transcript,
                model=model,
                lang=self._transcript_lang,
                openrouter_client=openrouter,
                members=members,
                labels=labels,
                context=meeting_context,
            )
```

Change the first argument to `transcript=transcript_for_llm`:

```python
            result = extract(
                transcript=transcript_for_llm,
                model=model,
                lang=self._transcript_lang,
                openrouter_client=openrouter,
                members=members,
                labels=labels,
                context=meeting_context,
            )
```

- [ ] **Step 3d: Persist the speaker_map**

Find the `save_speakers` call (currently ~line 779-785):

```python
            if project is not None or people:
                try:
                    save_speakers(
                        self._history_folder,
                        project.id if project else None,
                        [p.id for p in people],
                    )
```

Replace the `if` guard and the call so a binding alone also triggers a write, and the map is persisted:

```python
            if project is not None or people or speaker_map:
                try:
                    save_speakers(
                        self._history_folder,
                        project.id if project else None,
                        [p.id for p in people],
                        speaker_map=speaker_map,
                    )
```

- [ ] **Step 3e: Feed the rewritten transcript to `generate()`**

Find the protocol `generate` call (currently ~line 820-828):

```python
                    proto_result = protocol_generator.generate(
                        transcript=self._transcript,
                        speakers=[p.full_name for p in people],
                        meeting_date="",  # not tracked at dialog level in v1.0
                        lang=self._transcript_lang,
                        model=model,
                        openrouter_client=openrouter,
                        context=meeting_context,
                    )
```

Change the first argument to `transcript=transcript_for_llm` (leave `speakers=` as-is — bound people are already participants via auto-sync):

```python
                    proto_result = protocol_generator.generate(
                        transcript=transcript_for_llm,
                        speakers=[p.full_name for p in people],
                        meeting_date="",  # not tracked at dialog level in v1.0
                        lang=self._transcript_lang,
                        model=model,
                        openrouter_client=openrouter,
                        context=meeting_context,
                    )
```

- [ ] **Step 4: Verify the single call site + run tests**

Confirm `_run_extraction` has exactly one caller (the block edited in 3a):

Run: `python -m pytest tests/test_extract_dialog_context.py -v`
Expected: PASS — the 2 new tests + all pre-existing (including `test_protocol_speakers_uses_real_names` and `test_run_extraction_passes_context_to_both_calls`, which are unaffected).

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/extract_tasks/__init__.py tests/test_extract_dialog_context.py
git commit -m "feat(ui): rewrite transcript with bound names + persist speaker_map"
```

---

## Task 6: full-suite gate + manual GUI smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest`
Expected: PASS — baseline 521 + ~10 new ≈ 531 green. Investigate any failure before proceeding.

- [ ] **Step 2: Lint**

Run: `python -m ruff check .`
Expected: clean.

- [ ] **Step 3: Manual GUI smoke (user-run on Windows — CTk is not headless-testable)**

From the real keyed install (NOT a worktree — gitignored `config.json`/`history/` don't follow worktrees):

1. Transcribe (or open from История) a meeting that has ≥2 diarized speakers → confirm `<meeting>/segments.json` exists.
2. Open «Извлечь задачи». Under «Контекст встречи» the «Кто говорит» rows list «Спикер 1 / Спикер 2 …».
3. Bind «Спикер 1» to a directory person → confirm that person's «Участники» checkbox auto-ticks.
4. Click «Извлечь» with protocol generation on. Confirm: the generated `protocol.md` / tasks refer to the real name, not «Спикер 1».
5. Inspect `<meeting>/speakers.json` → `"speakers": {"SPEAKER_0": "<person_id>", ...}`.
6. Re-open the dialog → the binding dropdowns and participant ticks restore.
7. Edge: open the dialog on a meeting with no `segments.json` → «(нет данных о спикерах)» hint, extraction still works (transcript sent unchanged).

- [ ] **Step 4: Finalize**

Use `superpowers:finishing-a-development-branch` to push `feat/speaker-attribution` and open the PR (Summary + Test plan checklist per repo convention).

---

## Self-Review

**Spec coverage:**
- D-1 (substitute ФИО into transcript) → Task 1 (`apply_speaker_names`) + Task 5 (3b/3c/3e). ✓
- D-2 (keep both + auto-sync) → Task 4 (`_on_speaker_bound`, rows at `row=2` alongside participants). ✓
- D-3 (defer inline create) → dropdown is `["— не выбрано —"] + people` only; no create option. ✓
- D-4 (edit-preserving, not re-render) → Task 5 rewrites `self._transcript` (the textbox text) via string replace, never re-renders from segments. ✓
- D-5 (raw label key) → rows keyed on raw `raw` from `_build_speaker_map`; `speaker_map[raw] = person.id`. ✓
- Persistence (`load_segments`, `save_speakers` map) → Tasks 2, 3. ✓
- Restore → Task 4 step 3d. ✓
- Edge cases (no segments / no diarization / empty dir / deleted person / write fail) → Task 4 (hint), Task 5 (write guard + existing OSError catch), Task 6 step 3. ✓
- Tests source-text only → all dialog tests read `SRC.read_text`. ✓

**Type/name consistency:** `_speaker_row_vars` (raw→StringVar), `_speaker_friendly` (raw→"Спикер N"), `_build_speaker_rows`, `_person_by_name`, `_on_speaker_bound`, `_selected_speaker_maps`, `transcript_for_llm`, `speaker_map`, `name_by_label` — used identically across Tasks 4 and 5. `_run_extraction` new params `(speaker_map, name_by_label)` match the call site in 3a. ✓

**Placeholder scan:** every code step shows complete code; no TBD/TODO. ✓

**Out of scope (unchanged):** `tasks/protocol_generator.py`, `tasks/extractor.py`, `directory/`, `requirements.txt`; untracked `cli/` must not be staged.
