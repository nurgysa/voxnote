# Voice-ID Phase B · PR-3 — Worker wiring + voiceid sidecar + toggle · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the queue worker actually use Speechmatics speaker-ID when
`voiceid_enabled`: pass the directory's known speakers into the job, set
`transcript.md` `participants` to the people who actually spoke (real names, not
the roster), and persist the unknown ("new") voices to a `<id>.voiceid.json`
sidecar for PR-4 to surface and enroll.

**Architecture:** A pure label-partition helper (`processing/voiceid.py`) + sidecar
read/write helpers (`utils.py`) + a small directory reader
(`latest_voiceprint_model`) feed a focused change in `processing/worker.py`. The
worker reads `voiceid_enabled` from its injected config and a new
`resolve_known_speakers` callback (wired in `ui/app`). **Dormant by default:** the
toggle ships `false`, so with defaults the worker behaves exactly like #163
(participants = roster); behaviour changes only when a user enables Voice-ID and
transcribes with Speechmatics.

**Tech Stack:** Python 3.12, stdlib (`json`, `re`, `os`), CustomTkinter (one
Settings checkbox), `pytest`, `ruff`. No new dependency.

## Global Constraints

- **Invariant #2 unchanged** — pure stdlib + cloud HTTPS already in place; no
  torch/pyannote/ONNX/local inference. **Invariant #3** — no `requirements.txt`
  change, no new dependency.
- `encoding="utf-8"` on every text read/write (the sidecar + config helpers).
- Narrow `except` only; add no broad `except`. The worker's one existing
  broad-except boundary (`_process_item`) is unchanged.
- Russian user-facing strings; English code/comments/commits.
- **UI tests are source-slice** — never import `ui.app`/customtkinter (PortAudio
  crashes Linux CI). Assert on module text or via `spec_from_file_location`.
- Tests/lint via `py -3 -m pytest ...` and `py -3 -m ruff check .` — NOT bare
  `python` (3.11, lacks deps; `py -3` is 3.12).
- Commit messages lowercase-scoped, ending with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Design notes (read before implementing)

**Model handling (resolves the spec §9 chicken/egg).** A Speechmatics identifier
is tied to the model that issued it; to identify with it, the job must use the
same model. PR-1 chose to *record* the model from the response rather than pin a
constant — so the worker cannot know the model from a hardcoded string. This plan
derives the **active model from the directory itself**: `DirectoryStore.
latest_voiceprint_model()` returns the model of the most-recently-enrolled
voiceprint (all enrolled voiceprints share the provider's stable default model;
if Speechmatics ever changes its default, the newest voiceprint reflects the new
one). The `ui/app` wiring passes that model to PR-2's `identifiers_for_model`.
First run / empty directory → `None` → no known speakers → all voices are new →
PR-4 enrolls them. No model string is guessed; cross-model identifiers are ignored
server-side anyway. **This refines spec §5's "pin a constant exposed by the
provider" to "derive from the directory" — same goal (enroll+identify on one
model), no unconfirmed constant.** The injected callback is therefore
parameterless (`resolve_known_speakers() -> list[tuple[str, list[str]]]`), not the
spec's `Callable[[str], ...]` — the store owns the model, keeping the worker
headless.

**Label correlation.** PR-1's `_to_segments` rewrites Speechmatics `S1`→`SPEAKER_1`
in the segment `speaker` field, while the response's top-level `speakers` array
(→ `out.speaker_identifiers`) keys by the RAW label (`S1`, or the assigned real
name). The partition helper mirrors that one rule (`S\d`→`SPEAKER_\d`) to line a
pending voice's identifier up with its transcript label. Identified speakers keep
their real name in BOTH places, so they need no mapping.

**PR boundary.** `build_view`'s `pending_voices_count` and the «Встречи» badge are
PR-4 (they consume this sidecar). PR-3 only *produces* the sidecar.

## File structure

| File | Responsibility in PR-3 |
|------|------------------------|
| `utils.py` | `save_voiceid_sidecar` / `load_voiceid_sidecar` — `~/.voxnote/segments/<id>.voiceid.json`, mirroring the segments-sidecar pair. |
| `processing/voiceid.py` (new) | `partition_speakers(segments, speaker_identifiers, known_names)` — pure split into identified `participants` + `pending` unknown voices. |
| `directory/store.py` | `latest_voiceprint_model()` — the active model the worker filters known speakers by. |
| `processing/worker.py` | Inject `resolve_known_speakers`; when `voiceid_enabled` + Speechmatics, pass known speakers + `enroll_speakers`; set participants from identified names (roster fallback); write the voiceid sidecar. |
| `config.example.json` | `"voiceid_enabled": false`. |
| `ui/app/__init__.py` | Inject `resolve_known_speakers` into `ProcessingQueue`. |
| `ui/dialogs/settings_builder.py` + `ui/dialogs/settings.py` | `build_voiceid_section` checkbox + its call in the Транскрипция tab. |

---

## Task 1 — `utils.py`: voiceid sidecar read/write helpers

**Files:**
- Modify: `utils.py` (add two functions next to `save_segments_sidecar` / `load_segments_sidecar`)
- Test: `tests/test_voiceid_sidecar.py` (new)

**Interfaces:**
- Produces:
  - `save_voiceid_sidecar(voxnote_id: str, payload: dict, *, base_dir: str | None = None) -> str`
    — writes `<dir>/<voxnote_id>.voiceid.json` (UTF-8, atomic); returns the path.
  - `load_voiceid_sidecar(voxnote_id: str, *, base_dir: str | None = None) -> dict | None`
    — reads it; `None` when absent or malformed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voiceid_sidecar.py`:

```python
from utils import load_voiceid_sidecar, save_voiceid_sidecar


def test_save_then_load_roundtrip(tmp_path):
    payload = {"model": "m-x", "pending": [{"label": "SPEAKER_1"}], "note_meta": {}}
    path = save_voiceid_sidecar("vid-1", payload, base_dir=str(tmp_path))
    assert path.endswith("vid-1.voiceid.json")
    assert load_voiceid_sidecar("vid-1", base_dir=str(tmp_path)) == payload


def test_load_absent_returns_none(tmp_path):
    assert load_voiceid_sidecar("nope", base_dir=str(tmp_path)) is None


def test_load_malformed_returns_none(tmp_path):
    import os
    os.makedirs(tmp_path, exist_ok=True)
    (tmp_path / "bad.voiceid.json").write_text("{ not json", encoding="utf-8")
    assert load_voiceid_sidecar("bad", base_dir=str(tmp_path)) is None


def test_save_unicode_is_utf8(tmp_path):
    save_voiceid_sidecar("vid-2", {"pending": [{"sample_text": "Привет"}]},
                         base_dir=str(tmp_path))
    raw = (tmp_path / "vid-2.voiceid.json").read_text(encoding="utf-8")
    assert "Привет" in raw  # ensure_ascii=False
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3 -m pytest tests/test_voiceid_sidecar.py -q`
Expected: FAIL (`ImportError: cannot import name 'save_voiceid_sidecar'`).

- [ ] **Step 3: Implement (mirror the segments-sidecar pair)**

In `utils.py`, immediately after `load_segments_sidecar` (the segments pair ends
around line 638), add:

```python
def save_voiceid_sidecar(
    voxnote_id: str, payload: dict, *, base_dir: str | None = None
) -> str:
    """Persist the Voice-ID sidecar (pending unknown voices + model + the render
    kwargs PR-4 re-renders from) outside the vault, keyed by voxnote_id. Atomic
    write. Returns the file path."""
    target_dir = base_dir or _segments_sidecar_dir()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{voxnote_id}.voiceid.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_voiceid_sidecar(
    voxnote_id: str, *, base_dir: str | None = None
) -> dict | None:
    """Read the Voice-ID sidecar by voxnote_id. None when absent or malformed."""
    target_dir = base_dir or _segments_sidecar_dir()
    path = os.path.join(target_dir, f"{voxnote_id}.voiceid.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
```

(`os`, `json`, and `_segments_sidecar_dir` are already imported/defined in
`utils.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `py -3 -m pytest tests/test_voiceid_sidecar.py -q`
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add utils.py tests/test_voiceid_sidecar.py
git commit -m "feat(queue): voiceid sidecar read/write helpers" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — `processing/voiceid.py`: pure label-partition helper

**Files:**
- Create: `processing/voiceid.py`
- Test: `tests/test_voiceid_partition.py` (new)

**Interfaces:**
- Produces: `partition_speakers(segments: list[dict], speaker_identifiers:
  dict[str, list[str]], known_names: set[str]) -> tuple[list[str], list[dict]]`
  — returns `(participants, pending)`. `participants` = sorted unique segment
  speaker labels that are identified real names (not anonymous `SPEAKER_N`,
  not blank). `pending` = one dict per unknown returned voice
  `{"label": <normalised, e.g. "SPEAKER_1">, "identifier": <first id>,
  "sample_text": <first segment text for that label>, "first_start": <float>}`,
  sorted by `first_start`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voiceid_partition.py`:

```python
from processing.voiceid import partition_speakers


def _seg(speaker, text, start):
    return {"speaker": speaker, "text": text, "start": start, "end": start + 1}


def test_identified_go_to_participants_unknown_go_to_pending():
    segments = [
        _seg("Айбек Нурланов", "привет", 0.0),
        _seg("SPEAKER_1", "кто это", 2.0),
        _seg("SPEAKER_1", "ещё", 3.0),
    ]
    speaker_identifiers = {
        "Айбек Нурланов": ["known-id"],   # raw label == name (identified)
        "S1": ["new-id"],                  # raw anonymous label
    }
    participants, pending = partition_speakers(
        segments, speaker_identifiers, known_names={"Айбек Нурланов"},
    )
    assert participants == ["Айбек Нурланов"]
    assert pending == [{
        "label": "SPEAKER_1", "identifier": "new-id",
        "sample_text": "кто это", "first_start": 2.0,
    }]


def test_participants_sorted_and_unique():
    segments = [_seg("Данияр", "a", 0.0), _seg("Алмас", "b", 1.0),
                _seg("Данияр", "c", 2.0)]
    participants, pending = partition_speakers(segments, {}, known_names=set())
    assert participants == ["Алмас", "Данияр"]
    assert pending == []


def test_pending_skipped_when_no_identifier():
    segments = [_seg("SPEAKER_1", "x", 0.0)]
    participants, pending = partition_speakers(
        segments, {"S1": []}, known_names=set(),
    )
    assert participants == []
    assert pending == []  # no identifier → cannot enroll → not surfaced


def test_pending_sorted_by_first_start():
    segments = [_seg("SPEAKER_2", "later", 5.0), _seg("SPEAKER_1", "early", 1.0)]
    _, pending = partition_speakers(
        segments, {"S2": ["id2"], "S1": ["id1"]}, known_names=set(),
    )
    assert [p["label"] for p in pending] == ["SPEAKER_1", "SPEAKER_2"]


def test_uu_label_treated_as_unknown():
    # Speechmatics may emit "UU" (unattributable); _to_segments normalised it to
    # "SPEAKER_UU". It carries an identifier → surfaces as a pending voice.
    segments = [_seg("SPEAKER_UU", "mumble", 0.0)]
    _, pending = partition_speakers(
        segments, {"UU": ["uu-id"]}, known_names=set(),
    )
    assert pending == [{
        "label": "SPEAKER_UU", "identifier": "uu-id",
        "sample_text": "mumble", "first_start": 0.0,
    }]
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3 -m pytest tests/test_voiceid_partition.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'processing.voiceid'`).

- [ ] **Step 3: Implement**

Create `processing/voiceid.py`:

```python
"""Pure helpers for the Voice-ID queue path — split a Speechmatics speaker-ID
result into identified participants and unknown ("new") voices awaiting naming.

Tk-free and side-effect-free so it unit-tests without any UI or network.
"""
from __future__ import annotations

import re

_ANON_RE = re.compile(r"^SPEAKER_\d+$")


def _normalise_raw_label(raw: str) -> str:
    """Mirror providers.speechmatics._normalise_speaker for anonymous labels:
    ``S1`` -> ``SPEAKER_1``; anything else -> ``SPEAKER_<raw>`` (e.g. ``UU`` ->
    ``SPEAKER_UU``). Identified real names never reach this (they are filtered by
    known_names first), so we only ever map anonymous Speechmatics labels here."""
    if raw.startswith("S") and raw[1:].isdigit():
        return f"SPEAKER_{raw[1:]}"
    return f"SPEAKER_{raw}"


def _first_sample(segments: list[dict], label: str) -> tuple[str, float]:
    """(text, start) of the first segment spoken by ``label``; ("", 0.0) if none."""
    for seg in segments:
        if seg.get("speaker") == label:
            return (seg.get("text") or "").strip(), float(seg.get("start", 0.0))
    return "", 0.0


def partition_speakers(
    segments: list[dict],
    speaker_identifiers: dict[str, list[str]],
    known_names: set[str],
) -> tuple[list[str], list[dict]]:
    """Split a diarized speaker-ID result.

    participants: sorted unique identified real names that actually spoke
        (segment labels that are neither anonymous ``SPEAKER_N`` nor blank).
    pending: one dict per unknown returned voice (a response label not in
        ``known_names``) carrying its identifier + a sample for recognition.
    """
    spoke: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp and not _ANON_RE.match(sp) and sp not in seen:
            seen.add(sp)
            spoke.append(sp)
    participants = sorted(spoke)

    pending: list[dict] = []
    for raw_label, ids in (speaker_identifiers or {}).items():
        if raw_label in known_names:
            continue          # an identified person, not a new voice
        if not ids:
            continue          # no identifier → cannot enroll → don't surface
        label = _normalise_raw_label(raw_label)
        sample_text, first_start = _first_sample(segments, label)
        pending.append({
            "label": label,
            "identifier": ids[0],
            "sample_text": sample_text,
            "first_start": first_start,
        })
    pending.sort(key=lambda p: p["first_start"])
    return participants, pending
```

- [ ] **Step 4: Run to verify it passes**

Run: `py -3 -m pytest tests/test_voiceid_partition.py -q`
Expected: PASS (5/5).

- [ ] **Step 5: Commit**

```bash
git add processing/voiceid.py tests/test_voiceid_partition.py
git commit -m "feat(queue): pure partition helper (participants vs pending voices)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — `directory/store.py`: `latest_voiceprint_model`

**Files:**
- Modify: `directory/store.py` (add after `identifiers_for_model`)
- Test: `tests/test_directory_store.py` (2 new tests)

**Interfaces:**
- Consumes: `Voiceprint.model` / `.enrolled_at` (PR-2).
- Produces: `DirectoryStore.latest_voiceprint_model() -> str | None` — the
  `model` of the voiceprint with the most recent `enrolled_at` across all people
  (ties broken arbitrarily); `None` when no person has a voiceprint with a
  non-empty model.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_store.py`:

```python
def test_latest_voiceprint_model_returns_newest(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    s.add_voiceprint(p.id, Voiceprint(
        identifier="i1", model="old", enrolled_at="2026-01-01T00:00:00"))
    s.add_voiceprint(p.id, Voiceprint(
        identifier="i2", model="new", enrolled_at="2026-06-01T00:00:00"))
    assert s.latest_voiceprint_model() == "new"


def test_latest_voiceprint_model_none_when_empty(tmp_path):
    s = _fresh(tmp_path)
    s.upsert_person(Person(full_name="A"))  # no voiceprints
    assert s.latest_voiceprint_model() is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/test_directory_store.py -k latest_voiceprint_model -v`
Expected: FAIL (`AttributeError: ... has no attribute 'latest_voiceprint_model'`).

- [ ] **Step 3: Implement**

In `directory/store.py`, add immediately after `identifiers_for_model`:

```python
    def latest_voiceprint_model(self) -> str | None:
        """The model of the most-recently-enrolled voiceprint across all people,
        or None when nobody has a model-bearing voiceprint. The worker filters
        known speakers by this (enroll + identify share the provider's stable
        default model; if it ever changes, the newest voiceprint reflects it)."""
        best_at = ""
        best_model: str | None = None
        for person in self._people.values():
            for vp in person.voiceprints:
                if vp.model and vp.enrolled_at >= best_at:
                    best_at = vp.enrolled_at
                    best_model = vp.model
        return best_model
```

(`enrolled_at` is an ISO timestamp string, so lexicographic `>=` is chronological.)

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/test_directory_store.py -q`
Expected: PASS (new + all pre-existing).

- [ ] **Step 5: Commit**

```bash
git add directory/store.py tests/test_directory_store.py
git commit -m "feat(directory): latest_voiceprint_model active-model reader" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — `processing/worker.py`: wire Voice-ID into the queue (+ config + ui/app)

**Files:**
- Modify: `processing/worker.py` (`ProcessingQueue.__init__` injection + `_process_item`)
- Modify: `config.example.json` (add `"voiceid_enabled": false`)
- Modify: `ui/app/__init__.py` (inject `resolve_known_speakers`)
- Test: `tests/test_processing_worker.py` (new voiceid tests)
- Test: `tests/test_ui_queue_wiring.py` (source-slice: assert the injection line) — if
  this file does not exist, create it as a source-text check.

**Interfaces:**
- Consumes: `utils.save_voiceid_sidecar` (Task 1), `processing.voiceid.partition_speakers`
  (Task 2), `DirectoryStore.identifiers_for_model` (PR-2) + `latest_voiceprint_model`
  (Task 3), `cli.core.run_transcribe(..., enroll_speakers, known_speakers)` +
  `TranscribeOutput.speaker_identifiers` / `.model` (PR-1).
- Produces: `ProcessingQueue(..., resolve_known_speakers: Callable[[], list[tuple[str, list[str]]]] | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_processing_worker.py` (reuse its `_queue`, `_audio`,
`_sandbox_home`, `preflight.probe` patch; add a richer fake output):

```python
class _VOut:
    """run_transcribe output carrying speaker-ID fields (PR-1)."""
    text = "hi"
    language = "ru"
    def __init__(self, segments, speaker_identifiers, model="m-x"):
        self.segments = segments
        self.speaker_identifiers = speaker_identifiers
        self.model = model


def test_voiceid_on_sets_participants_and_writes_sidecar(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr("processing.preflight.probe",
                        lambda p: {"duration_s": 60.0, "size_bytes": 1000})
    capture = {}

    def _fake(*a, **k):
        capture.update(k)
        return _VOut(
            segments=[{"speaker": "Айбек Нурланов", "text": "привет", "start": 0.0},
                      {"speaker": "SPEAKER_1", "text": "кто", "start": 2.0}],
            speaker_identifiers={"Айбек Нурланов": ["known"], "S1": ["new-id"]},
        )
    monkeypatch.setattr("cli.core.run_transcribe", _fake)

    q = _queue(
        tmp_path,
        config_loader=lambda: {"cloud_provider": "Speechmatics",
                               "cloud_api_keys": {"Speechmatics": "k"},
                               "voiceid_enabled": True, "meetings_dir": str(tmp_path / "m")},
        resolve_known_speakers=lambda: [("Айбек Нурланов", ["known"])],
    )
    item_id = q.enqueue(_audio(tmp_path), {"provider": "Speechmatics", "diarize": True})
    q._process_item(q.snapshot()[0])

    # known speakers + enroll passed to the job
    assert capture["enroll_speakers"] is True
    assert capture["known_speakers"] == [
        {"label": "Айбек Нурланов", "identifiers": ["known"]}]
    # sidecar holds the pending new voice + model
    from utils import load_voiceid_sidecar
    sc = load_voiceid_sidecar(item_id, base_dir=str(tmp_path / ".voxnote" / "segments"))
    assert sc["model"] == "m-x"
    assert sc["pending"] == [{
        "label": "SPEAKER_1", "identifier": "new-id",
        "sample_text": "кто", "first_start": 2.0}]
    assert sc["note_meta"]["voxnote_id"] == item_id


def test_voiceid_off_uses_roster_and_no_sidecar(tmp_path, monkeypatch):
    _sandbox_home(tmp_path, monkeypatch)
    monkeypatch.setattr("processing.preflight.probe",
                        lambda p: {"duration_s": 60.0, "size_bytes": 1000})
    capture = {}

    def _fake(*a, **k):
        capture.update(k)
        return _VOut(segments=[{"speaker": "SPEAKER_1", "text": "x", "start": 0.0}],
                     speaker_identifiers={"S1": ["i"]})
    monkeypatch.setattr("cli.core.run_transcribe", _fake)

    q = _queue(
        tmp_path,
        config_loader=lambda: {"cloud_provider": "Speechmatics",
                               "cloud_api_keys": {"Speechmatics": "k"},
                               "voiceid_enabled": False, "meetings_dir": str(tmp_path / "m")},
        resolve_participants=lambda pid: ["Ростер Человек"],
        resolve_known_speakers=lambda: [("X", ["i"])],
    )
    item_id = q.enqueue(_audio(tmp_path), {"provider": "Speechmatics", "diarize": True})
    q._process_item(q.snapshot()[0])

    assert capture.get("enroll_speakers") is False
    from utils import load_voiceid_sidecar
    assert load_voiceid_sidecar(item_id, base_dir=str(tmp_path / ".voxnote" / "segments")) is None
```

> Implementer note: confirm the exact `_sandbox_home` segments dir
> (`~/.voxnote/segments`) and the `meetings_dir` key the worker reads
> (`self._meetings_dir`, passed via `_queue`). Adjust the `base_dir` in the asserts
> to match `utils._segments_sidecar_dir()` under the sandboxed `USERPROFILE`. If
> the worker raises before writing (e.g. a missing config key), read the ERROR
> status's message and supply the key — do not weaken the assert.

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/test_processing_worker.py -k voiceid -v`
Expected: FAIL (`TypeError: ... unexpected keyword 'resolve_known_speakers'`).

- [ ] **Step 3: Implement the worker change**

In `processing/worker.py` `ProcessingQueue.__init__`, add the keyword-only param
(after `resolve_participants`) and store it:

```python
        resolve_participants: Callable[[str | None], list[str]] | None = None,
        resolve_known_speakers: Callable[[], list[tuple[str, list[str]]]] | None = None,
```
```python
        self._resolve_participants = resolve_participants or (lambda _pid: [])
        self._resolve_known_speakers = resolve_known_speakers or (lambda: [])
```

In `_process_item`, replace the `out = core.run_transcribe(...)` block and the
`participants=` argument. First, compute the voiceid gate and known speakers
BEFORE the transcribe call (right after `denoise = preflight.should_denoise(...)`):

```python
            voiceid_on = bool(cfg.get("voiceid_enabled")) and provider == "Speechmatics"
            known_speakers = [
                {"label": name, "identifiers": ids}
                for name, ids in (self._resolve_known_speakers() if voiceid_on else [])
            ]

            out = core.run_transcribe(
                audio_path,
                provider=provider,
                api_key=api_key,
                language=language,
                diarize=bool(opts.get("diarize")),
                hotwords=opts.get("hotwords") or None,
                denoise=denoise,
                num_speakers=opts.get("num_speakers"),
                min_speakers=opts.get("min_speakers"),
                max_speakers=opts.get("max_speakers"),
                enroll_speakers=voiceid_on,
                known_speakers=known_speakers or None,
            )

            voiceid_pending: list[dict] = []
            identified: list[str] = []
            if voiceid_on:
                from processing.voiceid import partition_speakers
                known_names = {s["label"] for s in known_speakers}
                identified, voiceid_pending = partition_speakers(
                    out.segments, out.speaker_identifiers or {}, known_names,
                )
```

Change the note's `participants=` argument (currently
`participants=self._resolve_participants(item.project_id)`) to:

```python
                participants=(identified or self._resolve_participants(item.project_id)),
```

After `utils.save_segments_sidecar(item.id, out.segments)`, add the voiceid sidecar
write:

```python
            if voiceid_on and voiceid_pending:
                utils.save_voiceid_sidecar(item.id, {
                    "model": out.model,
                    "pending": voiceid_pending,
                    "note_meta": {
                        "title": item.title,
                        "project_name": getattr(project, "name", None),
                        "date": date,
                        "time": time_str,
                        "provider": provider,
                        "language": out.language,
                        "voxnote_id": item.id,
                        "source_path": source_path or audio_path,
                        "nudged": hermes_cfg.enabled,
                    },
                })
```

In `config.example.json`, add `"voiceid_enabled": false,` after the
`"denoise_audio": true,` line.

In `ui/app/__init__.py`, add the injection to the `ProcessingQueue(...)` call
(after the `resolve_participants=...` argument, ~line 235):

```python
            resolve_known_speakers=lambda: self._dir_store.identifiers_for_model(
                self._dir_store.latest_voiceprint_model() or ""
            ),
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/test_processing_worker.py -q`
Expected: PASS (new voiceid tests + all pre-existing worker tests — voiceid
defaults off / `resolve_known_speakers` defaults to `lambda: []`, so existing
tests are unaffected).

- [ ] **Step 5: Add the ui/app source-slice guard**

Append to `tests/test_ui_queue_wiring.py` (create it if absent — read
`ui/app/__init__.py` as text, do NOT import it):

```python
from pathlib import Path


def test_app_injects_resolve_known_speakers():
    src = Path("ui/app/__init__.py").read_text(encoding="utf-8")
    assert "resolve_known_speakers=lambda" in src
    assert "latest_voiceprint_model" in src
    assert "identifiers_for_model" in src
```

Run: `py -3 -m pytest tests/test_ui_queue_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Full-suite + lint gate, then commit**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
git add processing/worker.py config.example.json ui/app/__init__.py tests/test_processing_worker.py tests/test_ui_queue_wiring.py
git commit -m "feat(queue): use Speechmatics speaker-ID in the worker (participants + voiceid sidecar)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: full suite green; `ruff` clean.

---

## Task 5 — Settings: `voiceid_enabled` toggle

**Files:**
- Modify: `ui/dialogs/settings_builder.py` (add `build_voiceid_section`)
- Modify: `ui/dialogs/settings.py` (call it in the Транскрипция tab)
- Test: `tests/test_settings_voiceid.py` (new, source-slice)

**Interfaces:**
- Consumes: the `voiceid_enabled` config key (Task 4).
- Produces: a checkbox bound to `dialog._voiceid_enabled_var`, persisting
  `config["voiceid_enabled"]` via `save_config`.

- [ ] **Step 1: Write the failing test (source-slice — no `ui.app` import)**

Create `tests/test_settings_voiceid.py`:

```python
from pathlib import Path


def test_settings_builder_has_voiceid_section():
    src = Path("ui/dialogs/settings_builder.py").read_text(encoding="utf-8")
    assert "def build_voiceid_section(" in src
    assert "_voiceid_enabled_var" in src
    assert 'config["voiceid_enabled"]' in src
    assert "Speechmatics" in src  # the note telling users it needs Speechmatics


def test_settings_calls_build_voiceid_section():
    src = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
    assert "build_voiceid_section(self, scroll_transcription)" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -3 -m pytest tests/test_settings_voiceid.py -q`
Expected: FAIL (`assert "def build_voiceid_section(" in src` → False).

- [ ] **Step 3: Implement (mirror `build_dedup_section`)**

In `ui/dialogs/settings_builder.py`, add a new builder (mirrors
`build_dedup_section`; the Транскрипция tab's rows 0-7 are taken, so use row 8):

```python
def build_voiceid_section(dialog, parent) -> None:
    """Voice-ID on/off — pre-fill transcript.md participants with recognised
    speakers (Speechmatics speaker identification). Default off; the queue worker
    reads config.get("voiceid_enabled", False)."""
    section = section_card(dialog, parent, "Распознавание говорящих", row=8)

    dialog._voiceid_enabled_var = ctk.BooleanVar(
        value=bool(dialog._parent._config.get("voiceid_enabled", False)),
    )

    def _on_toggled() -> None:
        dialog._parent._config["voiceid_enabled"] = bool(
            dialog._voiceid_enabled_var.get(),
        )
        save_config(dialog._parent._config)

    ctk.CTkCheckBox(
        section,
        text="Узнавать говорящих по голосу (работает с провайдером Speechmatics)",
        variable=dialog._voiceid_enabled_var,
        command=_on_toggled,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    ).grid(row=0, column=0, columnspan=2, padx=4, pady=6, sticky="w")
```

In `ui/dialogs/settings.py`, add the call right after
`build_dictionaries_section` (the last Транскрипция-tab section, ~line 158):

```python
        settings_builder.build_voiceid_section(self, scroll_transcription)
```

- [ ] **Step 4: Run to verify it passes**

Run: `py -3 -m pytest tests/test_settings_voiceid.py -q`
Expected: PASS.

- [ ] **Step 5: Full-suite + lint gate, then commit**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
git add ui/dialogs/settings_builder.py ui/dialogs/settings.py tests/test_settings_voiceid.py
git commit -m "feat(settings): voiceid_enabled toggle in the Транскрипция tab" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: full suite green (baseline ≈ 1069 + the PR-3 tests); `ruff` clean.

---

## Self-review (writing-plans)

**1. Spec coverage (§4.3 + §5, PR-3 slice):**
- §4.3 voiceid sidecar (`<id>.voiceid.json` = model + pending + note_meta) +
  `save_/load_voiceid_sidecar` → Tasks 1 + 4. ✓
- §5 worker: known-speakers request when voiceid+Speechmatics, partition
  named→participants / `SPEAKER_\d`→pending, roster fallback, sidecar write →
  Task 4 (partition logic in Task 2). ✓
- §5 `resolve_known_speakers` injection (refined parameterless) + `ui/app` wiring
  → Task 4. ✓
- §5 `voiceid_enabled` toggle + Settings → Tasks 4 (config) + 5 (UI). ✓
- Out of PR-3 scope (PR-4), intentionally absent: `build_view`
  `pending_voices_count`, the «Встречи» badge, the bind-and-enroll panel,
  playback, retroactive re-render. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step shows full code; the two
implementer notes point at concrete files to confirm, not vague gaps.

**3. Type consistency:** `resolve_known_speakers() -> list[tuple[str, list[str]]]`
(Task 4 injection, `identifiers_for_model`'s PR-2 return type — matches). The
worker maps each `(name, ids)` tuple to `{"label": name, "identifiers": ids}`
(the provider's `known_speakers` dict shape from PR-1). `partition_speakers(...)
-> (list[str], list[dict])` is used identically in Task 2's tests and Task 4's
worker call. Sidecar payload keys (`model`, `pending`, `note_meta`) match between
Task 4's write and Task 1's round-trip test. Pending-entry keys (`label`,
`identifier`, `sample_text`, `first_start`) match between Task 2 and Task 4.

**Refinements flagged for the reviewer:** (a) the injected `resolve_known_speakers`
is parameterless (store owns the model) rather than spec §5's `Callable[[str]]`;
(b) the active model is derived from the directory (`latest_voiceprint_model`)
rather than a pinned provider constant — both serve the spec's enroll+identify-on-
one-model goal without an unconfirmed model string. Documented in "Design notes".
