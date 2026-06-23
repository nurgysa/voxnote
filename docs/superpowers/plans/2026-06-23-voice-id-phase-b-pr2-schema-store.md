# Voice-ID Phase B · PR-2 — Schema + store · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape `directory.schema.Voiceprint` from a local ECAPA `vector` to the
cloud model (an opaque Speechmatics `identifier` tied to a `model`), and add the
`DirectoryStore.identifiers_for_model(model)` reader the PR-3 worker will use to
build `speaker_diarization_config.speakers`.

**Architecture:** Pure data + an in-memory query method — no I/O changes, no new
deps. The reshape touches only `directory/schema.py` (and the tests that construct
`Voiceprint`); the reader is a small loop over the already-loaded people dict.
**Ships dormant:** nothing calls `identifiers_for_model` yet (PR-3 wires it); no
production code constructs `Voiceprint` with the old `vector` key (only tests did),
so reshaping breaks nothing in production.

**Tech Stack:** Python 3.12, `dataclasses`, `pytest`, `ruff`. No third-party deps.

## Global Constraints

- **Invariant #2 unchanged** — pure dataclass/dict + a list comprehension; no
  torch/pyannote/ONNX/local inference. **Invariant #3** — no `requirements.txt`
  change, no new dependency.
- `encoding="utf-8"` on every text read/write (none added here; the store's atomic
  write already complies — untouched).
- Narrow `except` only; add no broad `except` (ratchet stays flat).
- Russian user-facing strings; English code/comments/commits.
- No `ui.app` import in any test.
- Tests/lint via `py -3 -m pytest ...` and `py -3 -m ruff check .` — NOT bare
  `python` (3.11, lacks deps; `py -3` is 3.12).
- Commit messages lowercase-scoped, ending with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Context the implementer needs (verified)

- `Voiceprint` is constructed with `vector=` **only in tests** —
  `tests/test_directory_schema.py` (2 sites) and `tests/test_directory_store.py`
  (2 sites). No production code builds a `Voiceprint(vector=...)` or reads
  `.vector`; the only production construction is `Voiceprint.from_dict(...)` in
  `directory/schema.py`. So the reshape's blast radius is those 4 test sites.
- `DirectoryStore` keeps people in `self._people: dict[str, Person]`;
  `Person.voiceprints: list[Voiceprint]`. `add_voiceprint` already appends + caps
  at `VOICEPRINT_CAP = 5` (drop oldest) — **unchanged** in this PR.

## File structure

| File | Responsibility in PR-2 |
|------|------------------------|
| `directory/schema.py` | `Voiceprint` reshaped to `{identifier, model, provider, enrolled_at, source_meeting}`; tolerant `from_dict` (legacy `vector` ignored). |
| `directory/store.py` | New `identifiers_for_model(model)` reader; stale "Google Drive backup" line in the module docstring corrected (gdrive was removed in #164). |
| `tests/test_directory_schema.py` | Voiceprint round-trip + a legacy-`vector`-tolerated test, on the new shape. |
| `tests/test_directory_store.py` | The two `Voiceprint(vector=...)` tests rewritten to the new shape; new `identifiers_for_model` tests. |

---

## Task 1 — Reshape `Voiceprint` (schema) + repair its construction sites

**Files:**
- Modify: `directory/schema.py` (the `Voiceprint` dataclass, ~lines 21-42)
- Test: `tests/test_directory_schema.py` (rewrite 2 tests, add 1)
- Test: `tests/test_directory_store.py` (rewrite 2 tests that construct `Voiceprint`)

**Interfaces:**
- Produces: `Voiceprint(identifier: str, model: str, provider: str =
  "speechmatics", enrolled_at: str = <now>, source_meeting: str = "")` with
  `to_dict()` / `from_dict()`. `from_dict` ignores any legacy `"vector"` key and
  defaults missing fields (`identifier`/`model` → `""`).

- [ ] **Step 1: Rewrite the schema tests to the new shape (they will fail)**

In `tests/test_directory_schema.py`, replace `test_voiceprint_roundtrip` and
`test_person_roundtrip_with_voiceprints`, and add a legacy-tolerance test:

```python
def test_voiceprint_roundtrip():
    vp = Voiceprint(
        identifier="sp-id-1", model="m-x", source_meeting="2026-05-30_x",
    )
    vp2 = Voiceprint.from_dict(vp.to_dict())
    assert vp2.identifier == "sp-id-1"
    assert vp2.model == "m-x"
    assert vp2.provider == "speechmatics"
    assert vp2.source_meeting == "2026-05-30_x"


def test_person_roundtrip_with_voiceprints():
    p = Person(full_name="A", voiceprints=[Voiceprint(identifier="id1", model="m")])
    p2 = Person.from_dict(p.to_dict())
    assert len(p2.voiceprints) == 1
    assert p2.voiceprints[0].identifier == "id1"


def test_voiceprint_from_dict_ignores_legacy_vector():
    # Pre-Phase-B records held {"vector": [...]} and no identifier; they must
    # load without error (identifier/model fall back to "").
    vp = Voiceprint.from_dict({"vector": [0.1, 0.2], "source_meeting": "old"})
    assert vp.identifier == ""
    assert vp.model == ""
    assert vp.source_meeting == "old"
    assert not hasattr(vp, "vector")
```

- [ ] **Step 2: Run the schema tests to verify they fail**

Run: `py -3 -m pytest tests/test_directory_schema.py -q`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument
'identifier'` — the dataclass still has `vector`).

- [ ] **Step 3: Reshape the `Voiceprint` dataclass**

In `directory/schema.py`, replace the whole `Voiceprint` class (currently lines
21-42) with:

```python
@dataclass
class Voiceprint:
    """One enrolled voiceprint = an opaque Speechmatics speaker identifier, tied
    to the model that issued it (cross-model identifiers are ignored server-side).
    A person accumulates several across meetings — different voice tonalities —
    and the worker passes them all on identify. (Voice-ID Phase B fills these.)"""

    identifier: str
    model: str
    provider: str = "speechmatics"
    enrolled_at: str = field(default_factory=_now_iso)
    source_meeting: str = ""

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "model": self.model,
            "provider": self.provider,
            "enrolled_at": self.enrolled_at,
            "source_meeting": self.source_meeting,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Voiceprint:
        return cls(
            identifier=d.get("identifier", ""),
            model=d.get("model", ""),
            provider=d.get("provider", "speechmatics"),
            enrolled_at=d.get("enrolled_at") or _now_iso(),
            source_meeting=d.get("source_meeting", ""),
        )
```

(`field`, `dataclass`, and `_now_iso` are already imported/defined in the file —
no import changes.)

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `py -3 -m pytest tests/test_directory_schema.py -q`
Expected: PASS (all, including the pre-existing `Person`/`Project` tests).

- [ ] **Step 5: Repair the two store tests that construct `Voiceprint(vector=...)`**

In `tests/test_directory_store.py`, replace `test_add_voiceprint_caps_at_five_dropping_oldest`
and `test_add_voiceprint_unknown_person_raises`:

```python
def test_add_voiceprint_caps_at_five_dropping_oldest(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    for i in range(6):
        s.add_voiceprint(p.id, Voiceprint(identifier=f"id{i}", model="m"))
    vps = s.get_person(p.id).voiceprints
    assert len(vps) == 5
    assert vps[0].identifier == "id1"   # oldest (id0) evicted
    assert vps[-1].identifier == "id5"


def test_add_voiceprint_unknown_person_raises(tmp_path):
    s = _fresh(tmp_path)
    with pytest.raises(DirectoryError):
        s.add_voiceprint("nope", Voiceprint(identifier="id1", model="m"))
```

- [ ] **Step 6: Run the full suite to verify green**

Run: `py -3 -m pytest -q`
Expected: PASS — no other test constructs or reads a `Voiceprint`'s `vector`
(verified). Baseline ≈ 1066 + 1 new schema test.

- [ ] **Step 7: Commit**

```bash
git add directory/schema.py tests/test_directory_schema.py tests/test_directory_store.py
git commit -m "feat(directory): reshape Voiceprint to a Speechmatics identifier (drop ECAPA vector)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — `identifiers_for_model` store reader (+ stale-docstring cleanup)

**Files:**
- Modify: `directory/store.py` (add the method after `add_voiceprint`; fix the
  module docstring's stale Google-Drive line)
- Test: `tests/test_directory_store.py` (2 new tests)

**Interfaces:**
- Consumes: `Voiceprint.identifier` / `.model` (Task 1).
- Produces: `DirectoryStore.identifiers_for_model(model: str) ->
  list[tuple[str, list[str]]]` — `(full_name, [identifier, ...])` per person who
  has ≥1 voiceprint of `model`, sorted by `full_name`; people with no matching
  voiceprint omitted; identifier order preserved within a person.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_store.py`:

```python
def test_identifiers_for_model_groups_by_person_filtering_model(tmp_path):
    s = _fresh(tmp_path)
    a = Person(full_name="Алмас")
    b = Person(full_name="Данияр")
    c = Person(full_name="Чужой")
    s.upsert_person(a)
    s.upsert_person(b)
    s.upsert_person(c)
    s.add_voiceprint(a.id, Voiceprint(identifier="a1", model="m-x"))
    s.add_voiceprint(a.id, Voiceprint(identifier="a2", model="m-x"))
    s.add_voiceprint(b.id, Voiceprint(identifier="b1", model="m-x"))
    s.add_voiceprint(c.id, Voiceprint(identifier="c1", model="OTHER"))  # wrong model
    assert s.identifiers_for_model("m-x") == [
        ("Алмас", ["a1", "a2"]),
        ("Данияр", ["b1"]),
    ]  # sorted by full_name; Чужой omitted (no m-x voiceprint)


def test_identifiers_for_model_empty_when_none_match(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    s.add_voiceprint(p.id, Voiceprint(identifier="i", model="m-x"))
    assert s.identifiers_for_model("OTHER") == []
    assert s.identifiers_for_model("m-x") == [("A", ["i"])]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_directory_store.py -k identifiers_for_model -v`
Expected: FAIL (`AttributeError: 'DirectoryStore' object has no attribute
'identifiers_for_model'`).

- [ ] **Step 3: Implement the reader (+ fix the stale docstring line)**

In `directory/store.py`, add the method immediately after `add_voiceprint`
(before the `# ── persistence ──` section):

```python
    def identifiers_for_model(self, model: str) -> list[tuple[str, list[str]]]:
        """(full_name, [identifier, ...]) for every person holding >=1 voiceprint
        of `model`, sorted by full_name. This is the payload the queue worker
        passes as speaker_diarization_config.speakers. People without a
        matching-model voiceprint are omitted (their ids would be ignored
        server-side anyway); identifier order within a person is preserved."""
        result: list[tuple[str, list[str]]] = []
        for person in sorted(self._people.values(), key=lambda p: p.full_name):
            ids = [
                vp.identifier
                for vp in person.voiceprints
                if vp.model == model and vp.identifier
            ]
            if ids:
                result.append((person.full_name, ids))
        return result
```

Also fix the stale Google-Drive reference in the module docstring (gdrive was
removed in #164). Change the line:

```
{"people": [...], "projects": [...]}. Atomic write (tmp + os.replace),
mirroring tasks/persistence.py. Lives outside history/ and config.json so
voiceprint biometrics never ride the Google Drive backup.
```

to:

```
{"people": [...], "projects": [...]}. Atomic write (tmp + os.replace),
mirroring tasks/persistence.py. Lives under ~/.voxnote (outside the vault) so
voiceprint biometrics stay local — backup/restore is Hermes Desktop's job.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_directory_store.py -q`
Expected: PASS (new + all pre-existing, including the Task-1-repaired ones).

- [ ] **Step 5: Full-suite + lint gate, then commit**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
git add directory/store.py tests/test_directory_store.py
git commit -m "feat(directory): identifiers_for_model reader for speaker-ID enrollment" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: full suite green (baseline ≈ 1066 + 3 new PR-2 tests); `ruff` clean.

---

## Self-review (writing-plans)

**1. Spec coverage (§4.1 + §4.2):**
- §4.1 Voiceprint reshape (`identifier`/`provider`/`model`, tolerant from_dict,
  legacy `vector` ignored) → Task 1. ✓
- §4.2 `identifiers_for_model` (group by full_name, filter by model, omit
  non-matching) + `add_voiceprint` unchanged → Task 2. ✓
- Out of PR-2 scope (later PRs), intentionally absent: the voiceid sidecar
  helpers + worker wiring (PR-3), the «Встречи» UI (PR-4). ✓

**2. Placeholder scan:** No TBD/TODO. Every code step shows the full code; the
docstring edit shows exact before/after text.

**3. Type consistency:** `Voiceprint(identifier: str, model: str, provider: str =
"speechmatics", ...)` is used identically in Task 1 (definition + schema/store
test constructions) and Task 2 (test constructions). `identifiers_for_model`
returns `list[tuple[str, list[str]]]` — matched in both test asserts (a list of
`(name, [ids])` tuples) and the spec §4.2 signature. `from_dict` defaults
`identifier`/`model` to `""`, consistent with the legacy-tolerance test.

**Note on the docstring cleanup (Task 2):** removing the stale "Google Drive
backup" line is in-file hygiene for a subsystem deleted in #164, done while the
file is open for the feature change — flagged here so the reviewer sees it is
intentional, not scope creep.
