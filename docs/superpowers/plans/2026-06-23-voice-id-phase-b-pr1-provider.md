# Voice-ID Phase B · PR-1 — Provider speaker-ID plumbing · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the provider layer to (a) request Speechmatics speaker
identification (`get_speakers` + known `speakers`), (b) keep identified real-name
speaker labels verbatim while still normalising anonymous `S1`→`SPEAKER_1`, and
(c) surface the returned per-speaker identifiers + the acoustic model up through
`transcriber` and `cli.core` — so later PRs (worker, store, UI) can drive the
enroll-on-first-sighting loop. **Ships dormant:** nothing calls the new options
with `known_speakers` yet, so behaviour is identical to today.

**Architecture:** Speaker-ID is a generic-but-provider-mapped capability, exactly
like `diarize`/`hotwords`. `TranscriptionOptions` gains `enroll_speakers` +
`known_speakers`; `TranscriptionResult` gains `speaker_identifiers` + `model`; the
`SpeechmaticsProvider` maps them into its job config and parses them back out;
`Transcriber` threads them and caches `last_speaker_identifiers`/`last_model`;
`cli.core.run_transcribe` exposes them on `TranscribeOutput`. Pure cloud HTTPS —
no new dependency, no local inference.

**Tech Stack:** Python 3.12, `requests` (HTTP, patched at
`providers._common.requests` in tests), `dataclasses`, `pytest`, `ruff`.

## Global Constraints

- **Invariant #2 unchanged** — no torch/pyannote/faster-whisper/ctranslate2/ONNX/
  local inference. This PR is pure cloud HTTPS.
- **Invariant #3** — no `requirements.txt` changes; no new dependency.
- `encoding="utf-8"` on every text read/write (none added here, but keep it).
- Narrow `except` only; add no broad `except` (the broad-except ratchet stays
  flat — `tests/test_broad_except_ratchet.py`).
- Russian user-facing strings; English code/comments/commits.
- UI is untouched in PR-1; no `ui.app` import appears in any new test.
- Commit messages lowercase-scoped, ending with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Tests run with `py -3 -m pytest` (Python 3.12), `py -3 -m ruff check .` — NOT
  bare `python` (that is 3.11 and lacks deps).

## Pinned Speechmatics contract (pre-flight — SDK-confirmed 2026-06-23)

Confirmed against the official Speechmatics Python SDK
(`speechmatics/speechmatics-python-sdk`, branch `main`,
`sdk/batch/speechmatics/batch/_models.py`) and
docs.speechmatics.com/speech-to-text/realtime/speaker-identification. The batch
and realtime APIs share the `speaker_diarization_config` schema.

- **Enroll (capture identifiers):** add to the job config's
  `transcription_config`:
  ```json
  "speaker_diarization_config": { "get_speakers": true }
  ```
- **Identify (label known speakers):** add to the same object:
  ```json
  "speaker_diarization_config": {
    "get_speakers": true,
    "speakers": [
      {"label": "Айбек Нурланов", "speaker_identifiers": ["<id1>", "<id2>"]},
      {"label": "Bob",            "speaker_identifiers": ["<bob_id1>"]}
    ]
  }
  ```
  SDK `class SpeakerIdentifier: label: str; speaker_identifiers: list[str]` — keys
  are exactly `label` and `speaker_identifiers`.
- **Result (json-v2):** a **top-level** `speakers` array carries the identifiers
  (SDK: result `speakers: Optional[list[SpeakerIdentifier]]`, parsed via
  `data.get("speakers")`):
  ```json
  { "results": [...], "metadata": {...},
    "speakers": [ {"label": "S1", "speaker_identifiers": ["<id>"]}, ... ] }
  ```
- **Per-word label:** identified speakers appear directly in
  `results[].alternatives[0].speaker` as the assigned `label` (e.g.
  `"Айбек Нурланов"`); unknown speakers as `"S1"`/`"S2"`; unattributable as
  `"UU"`.
- **Model binding:** identifiers are tied to the acoustic model that issued them
  (cross-model identifiers are silently ignored). SDK
  `TranscriptionConfig.model` is the field; `operating_point` is its deprecated
  predecessor. **Decision (refines spec D-2):** rather than hardcode a model
  constant, this plan **records the model from the response**
  (`metadata.transcription_config.model`, falling back to `operating_point`) and
  later PRs echo it back on identify — same goal (enroll+identify on one model),
  robust to Speechmatics renaming models. Mismatched identifiers passed are
  harmless (ignored server-side), so PR-3 may pass all and let the server filter.
- **Limits:** ≤50 identifiers per session; enrollment clips ideally 5–30 s.

> Optional live smoke (not a blocker for PR-1, which only shapes/parses config and
> is fully mock-tested): run one real Speechmatics job with
> `speaker_diarization_config.get_speakers=true` and confirm the top-level
> `speakers` array + the `metadata.transcription_config.model` key name. Defer to
> the PR-3 worker integration if no key is handy now.

---

## File structure

| File | Responsibility in PR-1 |
|------|------------------------|
| `providers/base.py` | `TranscriptionOptions` += `enroll_speakers`, `known_speakers`; `TranscriptionResult` += `speaker_identifiers`, `model`; `TranscriptionProvider.supports_speaker_id` flag. The provider-agnostic contract. |
| `providers/speechmatics.py` | Map the options into `speaker_diarization_config`; keep identified labels verbatim; parse the top-level `speakers` array + the model; set `supports_speaker_id = True`. |
| `transcriber/__init__.py` | Thread `enroll_speakers`/`known_speakers` into `TranscriptionOptions`; cache `last_speaker_identifiers` + `last_model`. |
| `cli/core.py` | `run_transcribe` accepts + forwards the two new args; `TranscribeOutput` += `speaker_identifiers`, `model`, read from the transcriber. |
| `tests/test_providers_base.py` | New-field defaults + `supports_speaker_id`. |
| `tests/test_providers_speechmatics.py` | config shaping, label pass-through, identifier + model parsing. |
| `tests/test_transcriber_dispatch.py` | thread-through + cached attributes. |
| `tests/test_cli_core.py` | `run_transcribe` forwarding + `TranscribeOutput` fields. |

---

## Task 1 — `providers/base.py`: the speaker-ID contract

**Files:**
- Modify: `providers/base.py`
- Test: `tests/test_providers_base.py`

**Interfaces:**
- Produces:
  - `TranscriptionOptions.enroll_speakers: bool = False`
  - `TranscriptionOptions.known_speakers: list[dict] = []` — each
    `{"label": str, "identifiers": list[str]}`
  - `TranscriptionResult.speaker_identifiers: dict[str, list[str]] | None = None`
    — label → identifiers, parsed from the provider response
  - `TranscriptionResult.model: str | None = None` — acoustic model used
  - `TranscriptionProvider.supports_speaker_id: bool = False`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers_base.py`:

```python
def test_options_speaker_id_defaults():
    from providers.base import TranscriptionOptions
    o = TranscriptionOptions()
    assert o.enroll_speakers is False
    assert o.known_speakers == []


def test_options_known_speakers_is_per_instance():
    # default_factory, not a shared mutable
    from providers.base import TranscriptionOptions
    a = TranscriptionOptions()
    a.known_speakers.append({"label": "X", "identifiers": ["i"]})
    b = TranscriptionOptions()
    assert b.known_speakers == []


def test_result_speaker_id_fields_default_none():
    from providers.base import TranscriptionResult
    r = TranscriptionResult(segments=[])
    assert r.speaker_identifiers is None
    assert r.model is None


def test_provider_supports_speaker_id_flag_default_false():
    from providers.base import TranscriptionProvider
    assert TranscriptionProvider.supports_speaker_id is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_providers_base.py -k "speaker_id or known_speakers" -v`
Expected: FAIL (`TypeError: ... unexpected keyword` / `AttributeError`).

- [ ] **Step 3: Implement the contract**

In `providers/base.py`, extend `TranscriptionOptions` (after the existing
`max_speakers` field):

```python
    enroll_speakers: bool = False      # Ask the provider to return per-speaker
                                       # identifiers (Speechmatics get_speakers).
    known_speakers: list[dict] = field(default_factory=list)
    # Each: {"label": str, "identifiers": list[str]} — pre-enrolled speakers to
    # label by name. Providers without speaker-ID ignore both fields.
```

Extend `TranscriptionResult` (after `raw`):

```python
    speaker_identifiers: dict[str, list[str]] | None = None
    # Provider speaker label -> its identifier blob(s), when the provider was
    # asked to return them (enroll_speakers). None when not requested/supported.
    model: str | None = None           # Acoustic model the provider used, when
                                       # known (identifiers are tied to it).
```

Add the class flag to `TranscriptionProvider` (next to `supports_mixed`):

```python
    #: True when the provider can identify pre-enrolled speakers by name
    #: (maps enroll_speakers / known_speakers to a native speaker-ID API).
    #: Default False; providers opt in. Mirrors supports_diarization.
    supports_speaker_id: bool = False
```

`field` is already imported (`from dataclasses import dataclass, field`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_providers_base.py -v`
Expected: PASS (all, including the pre-existing cases).

- [ ] **Step 5: Commit**

```bash
git add providers/base.py tests/test_providers_base.py
git commit -m "feat(providers): speaker-ID fields on options/result + supports_speaker_id"
```

---

## Task 2 — `providers/speechmatics.py`: `_build_config` speaker-ID + flag

**Files:**
- Modify: `providers/speechmatics.py`
- Test: `tests/test_providers_speechmatics.py`

**Interfaces:**
- Consumes: `TranscriptionOptions.enroll_speakers`, `.known_speakers` (Task 1).
- Produces: `_build_config` emits `transcription_config.speaker_diarization_config
  = {"get_speakers": true, "speakers": [...]}` when `enroll_speakers`;
  `SpeechmaticsProvider.supports_speaker_id = True`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers_speechmatics.py`:

```python
def test_build_config_enroll_adds_get_speakers_and_diarization():
    cfg = _build_config(TranscriptionOptions(enroll_speakers=True))
    tc = cfg["transcription_config"]
    # speaker-ID implies diarization even if diarize wasn't set explicitly
    assert tc["diarization"] == "speaker"
    sdc = tc["speaker_diarization_config"]
    assert sdc["get_speakers"] is True
    assert "speakers" not in sdc          # none known yet


def test_build_config_known_speakers_maps_to_speakers_array():
    cfg = _build_config(TranscriptionOptions(
        enroll_speakers=True,
        known_speakers=[
            {"label": "Айбек Нурланов", "identifiers": ["id1", "id2"]},
            {"label": "Bob", "identifiers": ["b1"]},
        ],
    ))
    sdc = cfg["transcription_config"]["speaker_diarization_config"]
    assert sdc["get_speakers"] is True
    assert sdc["speakers"] == [
        {"label": "Айбек Нурланов", "speaker_identifiers": ["id1", "id2"]},
        {"label": "Bob", "speaker_identifiers": ["b1"]},
    ]


def test_build_config_no_enroll_has_no_speaker_diarization_config():
    cfg = _build_config(TranscriptionOptions(diarize=True, language="ru"))
    assert "speaker_diarization_config" not in cfg["transcription_config"]


def test_speechmatics_supports_speaker_id_true():
    assert SpeechmaticsProvider.supports_speaker_id is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_providers_speechmatics.py -k "enroll or speaker_id or known_speakers" -v`
Expected: FAIL (`KeyError: 'speaker_diarization_config'` / `AttributeError`).

- [ ] **Step 3: Implement**

In `providers/speechmatics.py`, set the class flag (next to `supports_mixed`):

```python
    supports_speaker_id = True  # get_speakers + speaker_diarization_config.speakers
```

In `_build_config`, after the `if options.diarize:` block and before the
`if options.hotwords:` block, add:

```python
    if options.enroll_speakers:
        # Speaker identification requires diarization; force it on so an
        # enroll/identify run still produces speaker-labelled segments.
        transcription_config["diarization"] = "speaker"
        sdc: dict = {"get_speakers": True}
        if options.known_speakers:
            sdc["speakers"] = [
                {"label": s["label"], "speaker_identifiers": s["identifiers"]}
                for s in options.known_speakers
            ]
        transcription_config["speaker_diarization_config"] = sdc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_providers_speechmatics.py -v`
Expected: PASS (new + all pre-existing).

- [ ] **Step 5: Commit**

```bash
git add providers/speechmatics.py tests/test_providers_speechmatics.py
git commit -m "feat(providers): map Speechmatics speaker-ID config (get_speakers + speakers)"
```

---

## Task 3 — `providers/speechmatics.py`: keep named labels verbatim + parse identifiers/model

**Files:**
- Modify: `providers/speechmatics.py`
- Test: `tests/test_providers_speechmatics.py`

**Interfaces:**
- Consumes: the Speechmatics json-v2 response (top-level `speakers`,
  `metadata.transcription_config`).
- Produces:
  - `_normalise_speaker(label, known_labels=frozenset())` — a label in
    `known_labels` is kept verbatim; `^S\d+$` → `SPEAKER_<n>`; anything else →
    `SPEAKER_<label>` (unchanged behaviour).
  - `_to_segments(payload, want_diarization, known_labels=None)` — threads
    `known_labels` into `_normalise_speaker`.
  - `_parse_speaker_identifiers(payload) -> dict[str, list[str]] | None`
  - `_extract_model(payload) -> str | None`
  - `transcribe()` populates `TranscriptionResult.speaker_identifiers` and
    `.model`, and passes the known labels + enroll flag into `_to_segments`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers_speechmatics.py`:

```python
def test_normalise_speaker_keeps_known_label_verbatim():
    known = frozenset({"Айбек Нурланов"})
    assert _normalise_speaker("Айбек Нурланов", known) == "Айбек Нурланов"
    # still anonymises the S-labels and UU when not in the known set
    assert _normalise_speaker("S1", known) == "SPEAKER_1"
    assert _normalise_speaker("UU", known) == "SPEAKER_UU"


def test_normalise_speaker_default_arg_unchanged():
    # back-compat: no known_labels behaves exactly as before
    assert _normalise_speaker("S3") == "SPEAKER_3"
    assert _normalise_speaker("UU") == "SPEAKER_UU"


def test_to_segments_named_speaker_passes_through():
    payload = {"results": [
        _word("Привет", 0.0, 0.4, "Айбек Нурланов"),
        _word("мир",    0.5, 0.8, "Айбек Нурланов"),
        _word("Как",    1.2, 1.4, "S1"),
    ]}
    segs = _to_segments(
        payload, want_diarization=True,
        known_labels=frozenset({"Айбек Нурланов"}),
    )
    assert segs[0]["speaker"] == "Айбек Нурланов"   # verbatim, NOT SPEAKER_*
    assert segs[1]["speaker"] == "SPEAKER_1"         # unknown still normalised


def test_parse_speaker_identifiers_reads_top_level_array():
    from providers.speechmatics import _parse_speaker_identifiers
    payload = {"speakers": [
        {"label": "S1", "speaker_identifiers": ["id-a"]},
        {"label": "Айбек Нурланов", "speaker_identifiers": ["id-b", "id-c"]},
    ]}
    assert _parse_speaker_identifiers(payload) == {
        "S1": ["id-a"],
        "Айбек Нурланов": ["id-b", "id-c"],
    }


def test_parse_speaker_identifiers_absent_is_none():
    from providers.speechmatics import _parse_speaker_identifiers
    assert _parse_speaker_identifiers({"results": []}) is None


def test_extract_model_from_metadata():
    from providers.speechmatics import _extract_model
    assert _extract_model(
        {"metadata": {"transcription_config": {"model": "m-x"}}}
    ) == "m-x"
    # operating_point fallback + absent
    assert _extract_model(
        {"metadata": {"transcription_config": {"operating_point": "enhanced"}}}
    ) == "enhanced"
    assert _extract_model({"metadata": {}}) is None


def test_transcribe_surfaces_identifiers_and_model(fake_audio):
    submit_resp = MagicMock(status_code=200, ok=True,
                            json=MagicMock(return_value={"id": "j1"}))
    poll_resp = MagicMock(status_code=200, ok=True,
                          json=MagicMock(return_value={"job": {"status": "done"}}))
    transcript_resp = MagicMock(status_code=200, ok=True, json=MagicMock(
        return_value={
            "results": [_word("Привет", 0.0, 0.4, "Айбек Нурланов")],
            "metadata": {"transcription_config": {"model": "m-x"}},
            "speakers": [
                {"label": "Айбек Нурланов", "speaker_identifiers": ["id-b"]},
            ],
        }))
    p = SpeechmaticsProvider("good-key")
    with patch("providers._common.requests.post", return_value=submit_resp), \
         patch("providers._common.requests.get",
               side_effect=[poll_resp, transcript_resp]):
        result = p.transcribe(fake_audio, TranscriptionOptions(
            diarize=True,
            enroll_speakers=True,
            known_speakers=[{"label": "Айбек Нурланов", "identifiers": ["id-b"]}],
        ))
    assert result.segments[0]["speaker"] == "Айбек Нурланов"
    assert result.speaker_identifiers == {"Айбек Нурланов": ["id-b"]}
    assert result.model == "m-x"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_providers_speechmatics.py -k "named or identifiers or extract_model or surfaces or known_label" -v`
Expected: FAIL (`ImportError` for the new helpers; named label normalised to `SPEAKER_*`).

- [ ] **Step 3: Implement**

In `providers/speechmatics.py`:

Replace `_normalise_speaker` with the known-aware version:

```python
def _normalise_speaker(label: str, known_labels: frozenset = frozenset()) -> str:
    """Speechmatics uses ``S1``/``S2``; rewrite to ``SPEAKER_1`` so the «Спикер N»
    path treats them like pyannote output. A label we asked to identify (in
    ``known_labels``) is a real name — keep it verbatim. ``UU`` and any other
    non-S\\d label fall through to the anonymous bucket unchanged."""
    if label in known_labels:
        return label
    if label.startswith("S") and label[1:].isdigit():
        return f"SPEAKER_{label[1:]}"
    return f"SPEAKER_{label}"
```

Thread `known_labels` through `_to_segments` (signature + the one call site in
`_flush`):

```python
def _to_segments(
    payload: dict, want_diarization: bool, known_labels: frozenset | None = None,
) -> list[dict]:
    ...
    known = known_labels or frozenset()
    ...
    # inside _flush(), where the speaker is attached:
        if want_diarization and cur_speaker:
            seg["speaker"] = _normalise_speaker(cur_speaker, known)
```

(Only the `_normalise_speaker(cur_speaker)` call changes — add the `known`
argument. The `known = known_labels or frozenset()` line goes at the top of
`_to_segments`, before `_flush` is defined, so the closure captures it.)

Add the two parsers near `_extract_language`:

```python
def _parse_speaker_identifiers(payload: dict) -> dict[str, list[str]] | None:
    """Top-level ``speakers`` array (present only when get_speakers was set) →
    {label: [identifier, ...]}. None when absent."""
    speakers = payload.get("speakers")
    if not speakers:
        return None
    out: dict[str, list[str]] = {}
    for sp in speakers:
        label = sp.get("label")
        ids = sp.get("speaker_identifiers") or []
        if label:
            out[label] = list(ids)
    return out or None


def _extract_model(payload: dict) -> str | None:
    """Acoustic model echoed in metadata (identifiers are tied to it).
    Falls back to the deprecated operating_point."""
    cfg = (payload.get("metadata") or {}).get("transcription_config") or {}
    model = cfg.get("model") or cfg.get("operating_point")
    return str(model) if model else None
```

Update `transcribe()` (the `_to_segments` call + the `TranscriptionResult`
construction):

```python
        known_labels = frozenset(
            s["label"] for s in options.known_speakers if s.get("label")
        )
        segments = _to_segments(
            payload,
            want_diarization=options.diarize or options.enroll_speakers,
            known_labels=known_labels,
        )
        return TranscriptionResult(
            segments=segments,
            language=_extract_language(payload),
            raw=payload,
            speaker_identifiers=_parse_speaker_identifiers(payload),
            model=_extract_model(payload),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_providers_speechmatics.py -v`
Expected: PASS (new + all pre-existing, including `test_to_segments_*` which call
`_to_segments` without `known_labels`).

- [ ] **Step 5: Commit**

```bash
git add providers/speechmatics.py tests/test_providers_speechmatics.py
git commit -m "feat(providers): preserve identified Speechmatics labels + parse identifiers/model"
```

---

## Task 4 — `transcriber/__init__.py`: thread options + cache identifiers/model

**Files:**
- Modify: `transcriber/__init__.py`
- Test: `tests/test_transcriber_dispatch.py`

**Interfaces:**
- Consumes: `TranscriptionResult.speaker_identifiers`, `.model` (Task 1/3).
- Produces:
  - `Transcriber.transcribe(..., enroll_speakers=False, known_speakers=None)`
  - `Transcriber.last_speaker_identifiers: dict[str, list[str]] | None`
  - `Transcriber.last_model: str | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcriber_dispatch.py` (mirror the file's existing
provider-mock style; if it patches `providers.get_provider`, reuse that):

```python
def test_transcribe_threads_speaker_id_and_caches(monkeypatch, tmp_path):
    from providers.base import TranscriptionResult
    import transcriber as tmod

    captured = {}

    class FakeProvider:
        supports_mixed = True
        supports_speaker_id = True
        def transcribe(self, path, opts, on_status=None, on_progress=None,
                       cancel_event=None):
            captured["enroll"] = opts.enroll_speakers
            captured["known"] = opts.known_speakers
            return TranscriptionResult(
                segments=[{"start": 0.0, "end": 1.0, "text": "hi",
                           "speaker": "Айбек Нурланов"}],
                language="ru",
                speaker_identifiers={"Айбек Нурланов": ["id-b"]},
                model="m-x",
            )

    monkeypatch.setattr(tmod, "get_provider", lambda *a, **k: FakeProvider(),
                        raising=False)
    # If transcriber imports get_provider locally, patch providers.get_provider:
    import providers
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: FakeProvider())

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 16)

    t = tmod.Transcriber()
    t.transcribe(
        str(audio), diarize=True, cloud_provider="Speechmatics",
        cloud_api_key="k", enroll_speakers=True,
        known_speakers=[{"label": "Айбек Нурланов", "identifiers": ["id-b"]}],
    )
    assert captured["enroll"] is True
    assert captured["known"] == [{"label": "Айбек Нурланов", "identifiers": ["id-b"]}]
    assert t.last_speaker_identifiers == {"Айбек Нурланов": ["id-b"]}
    assert t.last_model == "m-x"


def test_last_speaker_identifiers_default_none():
    import transcriber as tmod
    t = tmod.Transcriber()
    assert t.last_speaker_identifiers is None
    assert t.last_model is None
```

> Implementer note: check how `tests/test_transcriber_dispatch.py` already mocks
> the provider (it patches the `get_provider` symbol that
> `transcriber._transcribe_via_cloud` imports — `from providers import ...
> get_provider`). Use that exact patch target; the two `monkeypatch.setattr`
> lines above are belt-and-suspenders — keep whichever one matches the existing
> tests and drop the other.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_transcriber_dispatch.py -k "speaker_id or last_speaker or caches" -v`
Expected: FAIL (`TypeError: unexpected keyword 'enroll_speakers'` /
`AttributeError: last_speaker_identifiers`).

- [ ] **Step 3: Implement**

In `transcriber/__init__.py`:

`__init__` — initialise the caches next to `last_segments`:

```python
        self.last_segments: list[dict] | None = None
        self.last_speaker_identifiers: dict[str, list[str]] | None = None
        self.last_model: str | None = None
```

`transcribe(...)` — add the two params (after `max_speakers`):

```python
        enroll_speakers: bool = False,
        known_speakers: list[dict] | None = None,
```

and forward them in the `_transcribe_via_cloud(...)` call:

```python
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            enroll_speakers=enroll_speakers,
            known_speakers=known_speakers,
            cloud_provider=cloud_provider,
```

`_transcribe_via_cloud(...)` — add the params to its keyword-only signature:

```python
        max_speakers: int | None,
        enroll_speakers: bool = False,
        known_speakers: list[dict] | None = None,
        cloud_provider: str,
```

build them into the options:

```python
        opts = TranscriptionOptions(
            language=language,
            diarize=diarize,
            hotwords=hotword_list,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            enroll_speakers=enroll_speakers,
            known_speakers=known_speakers or [],
        )
```

and cache the new result fields next to `last_segments`:

```python
        # Cache for SRT/VTT export by the save dialog.
        self.last_segments = result.segments
        self.last_speaker_identifiers = result.speaker_identifiers
        self.last_model = result.model
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_transcriber_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add transcriber/__init__.py tests/test_transcriber_dispatch.py
git commit -m "feat(transcriber): thread speaker-ID options + cache last identifiers/model"
```

---

## Task 5 — `cli/core.py`: forward options + expose on `TranscribeOutput`

**Files:**
- Modify: `cli/core.py`
- Test: `tests/test_cli_core.py`

**Interfaces:**
- Consumes: `Transcriber.transcribe(..., enroll_speakers, known_speakers)`,
  `Transcriber.last_speaker_identifiers`, `.last_model` (Task 4).
- Produces:
  - `run_transcribe(..., enroll_speakers=False, known_speakers=None)`
  - `TranscribeOutput.speaker_identifiers: dict | None`,
    `TranscribeOutput.model: str | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_core.py` (mirror its existing transcriber-mock style):

```python
def test_run_transcribe_forwards_speaker_id_and_exposes_fields(monkeypatch):
    import cli.core as core

    captured = {}

    class FakeTranscriber:
        last_segments = [{"start": 0.0, "end": 1.0, "text": "hi",
                          "speaker": "Айбек Нурланов"}]
        last_speaker_identifiers = {"Айбек Нурланов": ["id-b"]}
        last_model = "m-x"
        def transcribe(self, audio_path, **kw):
            captured.update(kw)
            return "Айбек Нурланов: hi"

    # run_transcribe does `from transcriber import Transcriber` locally.
    import transcriber
    monkeypatch.setattr(transcriber, "Transcriber", lambda: FakeTranscriber())
    # Path-confinement guard: run_transcribe calls ensure_outside_secret_store.
    monkeypatch.setattr(core, "ensure_outside_secret_store", lambda p: None)

    out = core.run_transcribe(
        "meeting.wav", provider="Speechmatics", api_key="k", diarize=True,
        enroll_speakers=True,
        known_speakers=[{"label": "Айбек Нурланов", "identifiers": ["id-b"]}],
    )
    assert captured["enroll_speakers"] is True
    assert captured["known_speakers"] == [
        {"label": "Айбек Нурланов", "identifiers": ["id-b"]}]
    assert out.speaker_identifiers == {"Айбек Нурланов": ["id-b"]}
    assert out.model == "m-x"


def test_transcribe_output_speaker_fields_default_none():
    from cli.core import TranscribeOutput
    o = TranscribeOutput(text="t", language=None, provider="P", diarized=False)
    assert o.speaker_identifiers is None
    assert o.model is None
```

> Implementer note: confirm the exact patch target for `Transcriber` and
> `ensure_outside_secret_store` against the existing `tests/test_cli_core.py` —
> `run_transcribe` imports `Transcriber` lazily (`from transcriber import
> Transcriber` inside the function) and calls `ensure_outside_secret_store` at
> module scope. Match whatever the file's other `run_transcribe` tests already
> patch.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3 -m pytest tests/test_cli_core.py -k "speaker_id or speaker_fields or forwards" -v`
Expected: FAIL (`TypeError: unexpected keyword 'enroll_speakers'` /
`AttributeError: speaker_identifiers`).

- [ ] **Step 3: Implement**

In `cli/core.py`:

Extend `TranscribeOutput` (after `segments`):

```python
    speaker_identifiers: dict | None = None
    model: str | None = None
```

and add them to `to_dict`:

```python
    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "provider": self.provider,
            "diarized": self.diarized,
            "segments": self.segments,
            "speaker_identifiers": self.speaker_identifiers,
            "model": self.model,
        }
```

Extend `run_transcribe` — add the two params (after `max_speakers`):

```python
    enroll_speakers: bool = False,
    known_speakers: list[dict] | None = None,
    on_status=None,
```

forward them to `transcriber.transcribe(...)`:

```python
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        enroll_speakers=enroll_speakers,
        known_speakers=known_speakers,
        on_status=on_status,
    )
```

and populate the output from the transcriber's caches:

```python
    segments = transcriber.last_segments or []
    diarized = any(s.get("speaker") for s in segments)
    return TranscribeOutput(
        text=text,
        language=language,
        provider=provider,
        diarized=diarized,
        segments=segments,
        speaker_identifiers=transcriber.last_speaker_identifiers,
        model=transcriber.last_model,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -3 -m pytest tests/test_cli_core.py -v`
Expected: PASS.

- [ ] **Step 5: Full-suite + lint gate, then commit**

```bash
py -3 -m pytest -q
py -3 -m ruff check .
git add cli/core.py tests/test_cli_core.py
git commit -m "feat(queue): expose Speechmatics speaker identifiers + model via cli.core"
```

Expected: full suite green (baseline ≈ 1047 + the ~14 new tests in this PR);
`ruff` clean.

---

## Self-review (writing-plans)

**1. Spec coverage (PR-1 slice of §5/§6):**
- `providers/base.py` options/result/flag → Task 1. ✓
- `providers/speechmatics.py` get_speakers + speakers[], named-label verbatim,
  identifier + model parsing, `supports_speaker_id` → Tasks 2–3. ✓
- `transcriber/__init__.py` thread + `last_speaker_identifiers`/`last_model` →
  Task 4. ✓
- `cli/core.py` `run_transcribe` + `TranscribeOutput` fields → Task 5. ✓
- Out of PR-1 scope (later PRs), intentionally absent: schema/store
  (PR-2), worker/sidecar/Settings (PR-3), «Встречи» UI (PR-4). ✓

**2. Placeholder scan:** No TBD/TODO. Every code step shows the code; the model
key name has a documented fallback (`model` → `operating_point`) and the optional
live smoke is explicitly non-blocking. ✓

**3. Type consistency:** `known_speakers` items are `{"label": str,
"identifiers": list[str]}` everywhere (Task 1 default, Task 2 mapping reads
`s["label"]`/`s["identifiers"]`, Task 3 `known_labels` reads `s["label"]`, Tasks
4–5 pass the same dicts). The Speechmatics wire key is `speaker_identifiers`
(Task 2 mapping, Task 3 parse) — distinct from our internal `identifiers`, mapped
at the provider boundary only. `speaker_identifiers: dict[str, list[str]] | None`
and `model: str | None` are identical across base/result, transcriber caches, and
`TranscribeOutput`. ✓

**Note on the contract refinement:** the plan records the model from the response
rather than hardcoding a constant (spec D-2 said "pin a constant"). Same goal
(enroll+identify share one model); flagged in the pinned-contract block so the
reviewer sees the intentional, spec-serving deviation.
