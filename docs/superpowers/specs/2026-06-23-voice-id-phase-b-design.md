# Voice-ID Phase B — real speakers in transcript.md via Speechmatics speaker-ID — design

**Date:** 2026-06-23
**Status:** Approved (brainstorming) — ready for implementation plan
**Topic:** Make the processing queue's `transcript.md` `participants` (and the body
labels + `## Связи` links) the **actually-recognised people** from the directory
instead of the whole-project roster placeholder shipped in #163. Speaker
identification runs **server-side via the already-integrated Speechmatics
provider** (cloud HTTPS, no local inference); the directory fills itself through an
**enroll-on-first-sighting** loop surfaced as a badge in «Встречи».

## 1. Why

#163 wired each `transcript.md` into the Obsidian/GBrain graph by linking the
project + its directory **roster** as `participants` / `## Связи` — an honest
*bridge until Voice-ID* (roster ≈ participants). The felt gap the user wants
closed now: `participants` should be the people who **actually spoke**, not the
whole roster.

In the queue path today the diarized body renders anonymously (`**Спикер 1:** …`)
and `participants` = `resolve_participants(project_id)` (the roster). To name the
speakers we need a `SPEAKER_X → directory person` mapping. The Extract dialog
already has a *manual* such binding («Кто говорит»), but that is a separate
standalone surface; **the queue runs automatically, with no human in the loop** —
so the mapping must come from somewhere that fits an automatic pipeline.

The original Voice-ID Phase B plan
(`docs/superpowers/specs/2026-05-30-directories-and-voice-id-design.md`, Part B)
used **local ONNX ECAPA embeddings** and proposed an invariant-#2 carve-out
(D-F). That carve-out was never applied; CLAUDE.md still bans local inference
outright, and the user explicitly wants to avoid a local path. This spec
**supersedes Part B** with a cloud approach that needs **no invariant-#2 change**.

## 2. Decisions locked (brainstorming 2026-06-23)

| # | Decision | Rationale |
|---|----------|-----------|
| **D-1** | **Engine = Speechmatics speaker identification** (server-side, in the same transcription job). | VoxNote already integrates Speechmatics as one of 4 STT providers. Identification happens in the existing job — zero new dependency, zero invariant-#2 tension, no separate biometric-matching code, no extra API surface. |
| **D-2** | **Enrollment is free of a second call and of audio slicing.** Adding `get_speakers: true` to the job returns each speaker's `speaker_identifiers` **in the same transcript response**. | The enrollment artifact (the identifier blob) arrives with the transcript; naming a voice just persists the identifier already in hand. No re-transcription, no span extraction for enrollment. |
| **D-3** | **Enroll-on-first-sighting loop.** After each meeting, speakers still labelled `S1/S2` (not matched to any passed identifier) are the **new/unknown voices**; VoxNote surfaces them for naming. | "Unknown" = simply a label that is not one of the names we passed in — no separate matching step. First meeting with an empty directory ⇒ all voices new ⇒ the directory bootstraps itself. |
| **D-4** | **Surface = a non-intrusive badge in «Встречи»** («🆕 N новых голосов»), click → bind-and-enroll panel. NOT a popup/toast. | Respects the queue's no-popup, durable, work-when-convenient philosophy (background processing may finish while the user is away). |
| **D-5** | **The bind panel plays the voice** («▶ Прослушать»). | The user recognises a colleague by ear, not only by a text snippet. Playback uses the archived audio + the speaker's segment timings. |
| **D-6** | **Multi-identifier accumulation per person.** Binding a *known-but-mislabelled* voice (a person with identifiers who came back as a new `S1` because their tone/mic differed) to the existing profile **appends** a new identifier. | `Person.voiceprints` is already a list; Speechmatics `speaker_identifiers` is already an array per speaker. The system accumulates a person's voice tonalities; future jobs pass them all → recognition strengthens over time. Cap 5 (drop oldest), reusing existing `add_voiceprint`. |
| **D-7** | **Retroactive re-render of the just-named meeting.** Naming `S1=Айбек` rewrites *this* meeting's `transcript.md` (body labels + `participants` + `## Связи`) — no re-transcription, the mapping is known. | The mapping the user just supplied applies to this meeting too. Re-render from the persisted segments + new speaker-map (the queue note is machine-generated with no manual edits, so a clean re-render beats string substitution). |
| **D-8** | **`participants` = only the known people who actually spoke** (not the roster, not anonymous `Спикер N`) — whenever at least one speaker was identified. With **zero** identified speakers (voiceid off, non-Speechmatics, or nobody enrolled yet) it falls back to the #163 roster (D-9). | Directly answers the goal. Unknown speakers are not "participants" until named; after naming, the retroactive re-render adds them. The roster stays the bridge only until the first name lands. |
| **D-9** | **Voice-ID is an opt-in toggle** (`voiceid_enabled`, default `false`). | Phased rollout; clients without Speechmatics/Voice-ID see exactly today's behaviour. |
| **D-10** | **No invariant-#2 change.** Pure cloud HTTPS; no torch/pyannote/ONNX/local inference of any kind. | The whole point of choosing Speechmatics over the 2026-05-30 ONNX plan. |

## 3. Architecture — end-to-end flow (one meeting)

```
1. Queue transcribes (provider=Speechmatics, voiceid_enabled):
   speaker_diarization_config = {
     get_speakers: true,                                   ← identifiers returned in the response
     speakers: [ {label: "Айбек Нурланов", speaker_identifiers: [id1, id2]}, … ]   ← all known, matching model
   }
2. json-v2 response:
   • known     → speaker = "Айбек Нурланов"   (passes through verbatim; formatters keep non-SPEAKER_ labels)
   • unknown   → speaker = "S1"/"S2", each carrying fresh speaker_identifiers (from get_speakers)
3. Write transcript.md:
   • participants = the set of known names that actually appeared (NOT the roster)
   • body: "**Айбек Нурланов:** …" for known, "**Спикер 1:** …" for new
   • persist unknown voices {label, identifier, model, sample_text, first_start} + note-meta → voiceid sidecar
4. «Встречи»: a finished meeting with unknown voices shows badge «🆕 N новых голосов»
5. Click → panel: one row per new voice — reply snippet + «▶ Прослушать» + dropdown [existing person / + create]
6. «Применить»:
   • add_voiceprint(person, {identifier, provider:"speechmatics", model, source_meeting})   ← enroll for the future
   • retroactive re-render of THIS meeting's transcript.md (S1→Айбек in body + participants + Связи)
   • drop the resolved entries from the sidecar; empty sidecar → badge clears
7. Next meeting with the same voices → step 1 passes their identifiers → they come back named automatically.
```

**Guardrails (backward compatibility):**

- `voiceid_enabled = false` (default) → #163 behaviour unchanged (`participants` = roster).
- Provider ≠ Speechmatics → Voice-ID silently inactive, `participants` = roster (no breakage for AssemblyAI/Deepgram/Gladia users).
- Empty directory / no model-matching identifiers → all voices new; the job is a plain diarization run plus a populated sidecar.

## 4. Data model + persistence

### 4.1 `directory/schema.py` — `Voiceprint` reshaped for the cloud model

Today's `vector: list[float]` (a local ECAPA embedding) is replaced — there is no
local embedder. No production data exists (Phase B never shipped), so no
migration is needed; `from_dict` simply ignores a legacy `vector` key.

```
Voiceprint:
  identifier    : str            # Speechmatics speaker identifier (opaque blob)
  provider      : str = "speechmatics"
  model         : str            # the Speechmatics model that issued it (cross-model ids are ignored!)
  enrolled_at   : str            # ISO (existing)
  source_meeting: str            # voxnote_id of the meeting it came from (existing)
```

`Person.voiceprints: list[Voiceprint]` is unchanged in shape — it is the
tonality-accumulation store (D-6).

### 4.2 `directory/store.py`

- `add_voiceprint(person_id, vp)` — **unchanged**; already appends and caps at
  `VOICEPRINT_CAP = 5` (drop oldest). This *is* the accumulation mechanism.
- **New** `identifiers_for_model(model: str) -> list[tuple[str, list[str]]]` —
  for every person with ≥1 voiceprint of that `model`, returns
  `(full_name, [identifier, …])`. This is exactly the
  `speaker_diarization_config.speakers` payload the worker sends. People with
  only other-model identifiers are omitted (their ids would be ignored anyway).

### 4.3 New per-meeting "voiceid" sidecar (app-data, not the vault)

The identifier arrives in the transcription response and is unrecoverable later
without a new paid job, so it is persisted immediately. Mirrors the existing
`save_segments_sidecar` / `load_segments_sidecar` pair, keyed by `voxnote_id`:

```
~/.voxnote/segments/<voxnote_id>.voiceid.json
{
  "model": "<speechmatics model>",
  "pending": [ {"label":"S1", "identifier":"…", "sample_text":"…", "first_start": 12.3}, … ],
  "note_meta": { …the render kwargs needed to re-render transcript.md exactly… }
}
```

- `pending` drives the badge count, the panel rows, the «▶ Прослушать» window
  (`first_start` + the segments sidecar), and the enroll identifier.
- `note_meta` holds the non-segment `render_transcript_note` kwargs
  (`title, project_name, date, time, provider, language, voxnote_id, source_path,
  nudged`) so the retroactive re-render (D-7) reproduces the note exactly with
  the new speaker-map. Segments come from the existing `<id>.json` sidecar.
- New helpers `save_voiceid_sidecar` / `load_voiceid_sidecar` in `utils.py`
  (UTF-8, atomic, `None`/`{}` on absent/malformed — mirror the segments pair).

`directory.json` and both sidecars live under `~/.voxnote/` (local, outside the
vault and outside any backup); backup/restore is Hermes Desktop's job (gdrive was
removed in #164). Speechmatics identifiers are opaque blobs, not raw voice
vectors, but are still voice-derived → kept local by construction.

## 5. Components by file

| File | Change |
|------|--------|
| `providers/base.py` | `TranscriptionOptions` += `enroll_speakers: bool = False`, `known_speakers: list[dict] = []` (each `{label, identifiers}`). `TranscriptionResult` += `speaker_identifiers: dict[str, list[str]] | None = None`. New class flag `supports_speaker_id: bool = False` (mirrors `supports_diarization`). Generic-but-provider-mapped, exactly like `diarize`/`hotwords`. |
| `providers/speechmatics.py` | `supports_speaker_id = True`. `_build_config`: when `enroll_speakers`, add `speaker_diarization_config = {get_speakers: true, speakers: [{label, speaker_identifiers}]}` (omit `speakers` when none known) and **pin the model** (a module constant, e.g. the enhanced model) so enroll/identify stay on the same model. `_normalise_speaker`: rewrite **only** labels matching `^S\d+$` → `SPEAKER_\d`; pass any other label (a real name) through verbatim. Parse the response `speakers[]` array → populate `TranscriptionResult.speaker_identifiers` (label → identifiers). |
| `transcriber/__init__.py` | `transcribe(...)` threads `enroll_speakers`/`known_speakers` into `TranscriptionOptions`; after the run, stash `last_speaker_identifiers` and `last_model` (the pinned model) next to `last_segments`. |
| `cli/core.py` | `run_transcribe(...)` gains `enroll_speakers`/`known_speakers`; `TranscribeOutput` += `speaker_identifiers: dict | None` and `model: str | None`, read from the transcriber after the run. |
| `directory/schema.py` | `Voiceprint` reshape (§4.1). |
| `directory/store.py` | `+ identifiers_for_model(model)` (§4.2); `add_voiceprint` unchanged. |
| `processing/worker.py` | New keyword-only injection `resolve_known_speakers: Callable[[str], list[tuple[str, list[str]]]] | None` (model → known speakers); module stays headless (no direct store import). The pinned model is a constant exposed by the Speechmatics provider, which the worker reads **before** the job to fetch matching identifiers (resolves the chicken/egg — the model is static, not discovered from the response). Before the job: when `voiceid_enabled` (from config) **and** the resolved provider is Speechmatics, pass `enroll_speakers=True, known_speakers=resolve_known_speakers(model)`. After: partition segment labels — **named (non-`SPEAKER_`) → `participants`**; `SPEAKER_\d` → `pending` (with identifier from `out.speaker_identifiers`, `sample_text` = first segment text, `first_start`). Write the voiceid sidecar. **Fallback:** voiceid off / non-Speechmatics / no names produced → `participants = resolve_participants(project_id)` (today's #163 path, unchanged). |
| `processing/store.py` | `build_view` exposes `pending_voices_count` per finished meeting (via the note's `voxnote_id` → voiceid sidecar `pending` length). |
| `ui/dialogs/meetings*` + `ui/dialogs/meetings_view.py` | Render the «🆕 N новых голосов» badge on rows with `pending_voices_count > 0`; click opens the bind-and-enroll panel. |
| `ui/dialogs/` (new bind-and-enroll panel) | One row per pending voice: `sample_text` snippet + «▶ Прослушать» + dropdown (every directory person + «+ создать нового…»). «Применить» → `add_voiceprint` for each named, retroactive re-render (D-7), drop resolved entries from the sidecar. Creating a person reuses the «Справочники» person-create path. |
| `ui/dialogs/settings*` + `config.example.json` | `voiceid_enabled` toggle (default `false`) + a one-line note «Voice-ID работает с провайдером Speechmatics». |
| `ui/app/__init__.py` | Inject `resolve_known_speakers=lambda model: self._dir_store.identifiers_for_model(model)` into `ProcessingQueue` (alongside the existing `resolve_participants`). |

**Tk-free helpers** (unit-testable without importing `ui.app` — PortAudio crashes
Linux CI): label partition (named vs `SPEAKER_\d`), re-render-with-speaker-map,
and the playback-window computation (audio path + segments + label → start/dur).
UI tests stay source-slice.

## 6. Phasing (one spec, four PRs)

Each PR is independently green (`pytest` + `ruff` + the broad-except ratchet) and
shippable. PR-1..3 ship **dormant** (the loop is inert until PR-4 adds the UI);
with an empty directory the queue behaves exactly like today.

- **PR-1 — Provider speaker-ID plumbing.** `providers/base.py` +
  `providers/speechmatics.py` + `transcriber/__init__.py` + `cli/core.py`.
  Mocked-HTTP tests. Nothing calls it with `known_speakers` yet.
- **PR-2 — Schema + store.** `Voiceprint` reshape + `identifiers_for_model`.
  Pure unit tests.
- **PR-3 — Worker wiring + sidecar + toggle.** `processing/worker.py` +
  `utils.py` voiceid-sidecar helpers + `processing/store.py` `pending_voices_count`
  + `voiceid_enabled` config/Settings + `ui/app` injection. The queue now produces
  named `participants` and records pending voices; no resolution UI yet.
- **PR-4 — «Встречи» badge + bind-and-enroll panel + playback + retroactive
  re-render.** Closes the loop. If oversized, split playback into **PR-4b**.

## 7. Testing strategy

UI suites must **not** import `ui.app` (source-slice / `spec_from_file_location`).

- **Provider** (mocked HTTP): config carries `get_speakers` + `speakers[]` when
  `known_speakers` passed and the pinned model; named labels survive
  `_to_segments` verbatim while `S1` → `SPEAKER_1`; `speaker_identifiers` parsed
  from the response `speakers[]`.
- **Schema:** `Voiceprint` to_dict/from_dict round-trip with new fields; a legacy
  `{"vector": …}` dict loads without error (field ignored).
- **Store:** `identifiers_for_model` groups by `full_name`, filters by model,
  omits people without a matching-model id; `add_voiceprint` accumulation + cap-5
  eviction (existing tests extended).
- **Worker** (source-slice + behavioural): known names → `participants`; unknown →
  sidecar `pending` with the right identifier/sample/first_start; **fallback to
  roster** when voiceid off / provider ≠ Speechmatics / no names; only
  model-matching identifiers are passed.
- **UI:** `meetings_view` exposes `pending_voices_count`; the panel module
  references the enroll + re-render helpers; the Tk-free helpers (partition,
  re-render-with-map, playback-window) are unit-tested directly.
- **Regression guard:** `voiceid_enabled=false` → `transcript.md` is byte-identical
  to the #163 output (pin the contract).

Baseline ≈ 1047 tests; this adds ~40–55 across the four PRs.

## 8. Global constraints (repo invariants)

- **Invariant #2 unchanged** — pure cloud HTTPS; **no** torch/pyannote/
  faster-whisper/ctranslate2/ONNX/local inference. This is the reason Speechmatics
  was chosen over the 2026-05-30 ONNX plan.
- **Invariant #3** — no `requirements.txt` pin changes; **no new dependency**
  (Speechmatics is already integrated; no client library is added).
- `encoding="utf-8"` on every text read/write (sidecars, note re-render, config).
- Narrow `except` only; the worker's existing broad-except boundary is unchanged.
  New code adds no broad `except` (ratchet stays flat).
- Russian user-facing strings; English code/comments/commits.
- One concern per PR; the user merges each PR.
- Commit messages lowercase-scoped (`feat(voiceid):` / `feat(providers):` /
  `feat(queue):` / `feat(directory):` / `test:` / `docs:`), ending with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 9. Risks & pre-flight (resolve in the plan, before PR-1)

- **Load-bearing API contract.** Confirm against a live Speechmatics batch job:
  the exact `get_speakers` response shape (the `speakers[]` array and the
  `speaker_identifiers` key name), the `speaker_diarization_config.speakers`
  request key, and that identified speakers appear by label directly in the
  json-v2 `speaker` field. Verified at the documentation level
  (docs.speechmatics.com/speech-to-text/features/speaker-identification +
  /batch/batch-diarization, 2026-06-23); the plan must verify it live before
  building on it.
- **Model pinning.** Identifiers are tied to the issuing model (cross-model ids
  are ignored). Pin one Speechmatics model for voiceid jobs, store `model` on each
  voiceprint, and pass only matching identifiers. A Speechmatics model
  change/deprecation invalidates stored identifiers — acceptable: re-enrollment
  happens automatically via the same loop (the person reappears as a new voice).
- **Cost.** Confirm whether `get_speakers` / identification adds cost over plain
  diarization (likely none) — note it in the plan, not a blocker.
- **Diarization quality ceiling.** Over/under-clustering shifts work to the human
  panel — acceptable; the panel is the safety net (same stance as the 2026-05-30
  spec).
- **50-identifier/session cap.** With cap-5 per person that is ~10 people. For
  larger directories, bound the passed set (people in the meeting's project
  first) and `log()` the truncation — no silent cap.

## 10. Out of scope

- Voice-ID for non-Speechmatics providers (roster fallback stands).
- Enrollment from a fresh dedicated recording or an imported clip (the
  enroll-on-first-sighting loop is the only enrollment path in this phase).
- Re-attributing meetings transcribed before this feature (no stored
  identifiers).
- Any change to the Extract dialog's existing manual «Кто говорит» binding, to
  the Hermes webhook, or to `processing/sources.py`.
- Team-shared / multi-device directory sync.
