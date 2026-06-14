# VoxNote transcription queue + history (Hermes-native) — design

**Date:** 2026-06-14
**Status:** approved (brainstorming)
**Supersedes (in part):** `2026-06-02-processing-queue-design.md` — that design's
auto-pipeline (transcribe → protocol → task-draft → awaiting_review) is replaced
by a **transcribe-only** queue. The merged PR-2a worker and the written PR-2b plan
(`plans/2026-06-14-processing-queue-pr2b-ui-wiring.md`) are reworked accordingly.

## Problem

The 2026-06-02 design made VoxNote a mini-orchestrator (transcribe → protocol →
task-draft → review). That predated VoxNote becoming **Hermes-native**. In the
user's live stack **Hermes already creates the protocol + tasks, gates them on a
human approval, and sends them to the right trackers** (Linear `MINIAGI`, Kanban
`nbs`, …). Generating protocol/tasks inside VoxNote duplicates that brain and
doubles LLM spend.

Realities that shape this:

1. **Shared Obsidian vault** (`C:\Users\nurgisa\Documents\Obsidian Vault`), used by
   both VoxNote and Hermes.
2. **Semi-autonomous, not 24/7** — the Telegram gateway and Kanban agent profiles
   are often stopped, so a webhook-dependent handoff would frequently fail.
3. **Source-file convention** — `.md`/text in the vault; non-text originals
   (audio, pdf, png…) go to a **`sources` folder on Google Drive (Desktop)**; the
   text files record where the original lives.
4. **Long recordings** — 60 min to **2–3 hours**.
5. **Capture on phone** — recordings are often made away from the desktop.

## Goal

Narrow VoxNote to its strength — **high-quality transcription + diarization** —
feeding a durable, Hermes-native pipeline:

> audio in (in-app record · «Выбрать файл» · **phone → Drive inbox**) → **VoxNote
> transcribes + diarizes** → creates the meeting folder
> `30 Meetings/<project>/<meeting>/` and writes **`transcript.md`** (diarized,
> referencing the audio) → **archives audio** to Drive `sources` → **nudges
> Hermes** → **Hermes writes `protocol.md` + the tasks file into the same folder,
> human-approves, and sends tasks to the trackers.**

End state per meeting (text in the vault, audio in Drive):

```
30 Meetings/<Project>/<meeting>/
  transcript.md   ← VoxNote (diarized; frontmatter → audio location)
  protocol.md     ← Hermes
  tasks.md        ← Hermes (after human approve + send to trackers)
Google Drive/.../sources/<meeting>.m4a   ← audio; every text file references it
```

Plus the supporting **queue** (jobs in flight) and **history** (browsable past
meetings, by project).

## Boundary — who writes what

| Artifact | Owner |
|---|---|
| `transcript.md` (diarized) + the meeting folder | **VoxNote** |
| audio → Drive `sources/` | **VoxNote** |
| `audio.transcribed` nudge | **VoxNote** |
| `protocol.md`, tasks file (in the same folder) | **Hermes** |
| human approval + send to trackers (Linear, …) | **Hermes** |

VoxNote never writes protocol/tasks and never sends to a tracker in this flow.
(The standalone **«Извлечь задачи»** dialog stays as a manual feature for clients
without Hermes — out of the queue.)

## Decisions (brainstorming Q&A, 2026-06-14)

1. **Positioning:** Hermes-first, graceful degradation. VoxNote must still
   *transcribe* without Hermes; it need not orchestrate without it.
2. **Handoff point:** transcribe-only — protocol/tasks are Hermes's.
3. **Toggle:** `hermes_webhook_enabled` gates only the nudge.
4. **Project:** carried locally (`30 Meetings/<project>/`) and in the nudge.
5. **Vault layout — folder per meeting:** `30 Meetings/<project>/<meeting>/`.
   VoxNote writes **one file there: `transcript.md` (diarized)**. Hermes adds
   `protocol.md` + the tasks file into the **same folder**.
6. **Durable handoff:** the **`transcript.md` in the shared vault is the real
   handoff** — durable regardless of Hermes. The `audio.transcribed` webhook is a
   **best-effort nudge**; failure is benign.
7. **Vault hygiene:** only **text files** in the meeting folder (transcript from
   VoxNote; protocol/tasks from Hermes). **No audio, no machine-data** (the vault
   is GitHub-versioned). Every text file records the audio location.
8. **Source-file convention:** audio original → Drive `sources` (Google Drive
   Desktop filesystem sync, not the API). `transcript.md` frontmatter records
   `source_path`. `segments.json` (SRT/VTT sidecar) stays VoxNote-internal in
   app-data.
9. **Phone ingestion (Drive-inbox):** phone audio arrives via a Drive-synced
   **`inbox/`** folder VoxNote **polls**. Telegram is ruled out for real
   recordings (Bot API ~20 MB download cap ≪ 120–180 MB files). After
   transcription the audio **moves `inbox/ → sources/`**, draining the inbox.
   Manual «Выбрать файл» stays as a fallback.
10. **Long audio (2–3 h):** **whole-file upload to long-capable providers — no
    chunker** (cross-chunk diarization stitching would degrade speaker quality).
    Pre-flight duration/size guard per provider (Gladia is the weak link),
    **auto-disable denoise above ~45 min** (avoids the huge temp-WAV path), an STT
    cost estimate at enqueue, and generous poll timeouts.

## Architecture

```text
intake:
  in-app record ─────────────────┐
  «Выбрать файл» (desktop) ───────┤
  phone → Google Drive (mobile)   │   inbox watcher polls <Drive>/…/inbox/,
        → <Drive>/…/inbox/  ──────┘   waits for a SIZE-STABLE file, then enqueues
        ▼
 ProcessingQueue  ── serial daemon thread
        │
        ├─ pre-flight: duration + size → provider-cap guard · denoise off if >~45 min · cost estimate
        ├─ RUNNING ── cli.core.run_transcribe (provider + diarization)
        ├─ create folder 30 Meetings/<project>/<meeting>/ + write transcript.md   ← durable handoff
        ├─ archive audio → <Drive>/…/sources/<meeting>.<ext>
        │     MOVE for record/inbox (inbox thus drains); COPY for «Выбрать файл»
        ├─ sidecar: segments.json → ~/.voxnote/segments/<id>.json
        └─ nudge → audio.transcribed (note_path=transcript.md + source_path + project + text)
              failure → nudge_delivered=false  (BENIGN)
        ▼
   DONE → History ; Hermes reads transcript.md → writes protocol.md + tasks into
          the same folder → human approve → sends to trackers
```

VoxNote is a **capability + emitter**, not an orchestrator.

## Components

Each unit is small, single-purpose, unit-testable headlessly (no Tk).

### 1. State model — `processing/model.py` (rework)

Single `status` (reuse `StageStatus`: `PENDING · RUNNING · DONE · ERROR`; drop
`AWAITING_REVIEW`). `QueueItem`: `id, audio_path, title, created_at,
meeting_folder, project_id, options{provider, language, diarize, num_speakers,
min_speakers, max_speakers, denoise}, source (record|pick|inbox), status,
source_path, nudge_delivered, error_message`. (`meeting_folder` is reused from
PR-2a; `transcript.md` lives inside it.)

### 2. Inbox watcher — `processing/inbox_watcher.py` (new)

- `scan_inbox(inbox_dir, *, known) -> list[str]` — pure: audio files
  (`.m4a/.mp3/.wav/.ogg/.opus/.aac/.flac`) not already in-flight.
- **Debounce:** a file is *ready* only when its size is **stable across two
  consecutive scans** (≥ the poll interval) — a 180 MB file still syncing from
  Drive is never grabbed mid-write. `InboxWatcher` holds `{path:(size,seen_at)}`.
- `poll() -> list[str]` — ready files; the App's `after(~15 s)` loop enqueues each
  (no-project default; triage later). `inbox_dir` unset → idle.

### 3. Pre-flight / long-audio — `processing/preflight.py` (new)

- `probe(audio_path) -> {duration_s, size_bytes}` (via `audio_io.get_duration_s`).
- `provider_limit_ok(provider, duration_s, size_bytes) -> (ok, reason)` — per-
  provider caps (AssemblyAI/Speechmatics ~2 GB & hours; Deepgram long; **Gladia
  tighter — verify in impl**); warn/block before upload.
- `should_denoise(duration_s, requested) -> bool` — `False` when
  `duration_s > DENOISE_MAX_S (~45 min)`; logged.
- `estimate_cost(provider, duration_s) -> float | None` — `$/h × duration`.

### 4. Transcript writer — `processing/vault_note.py` (new)

The only writer that touches the vault.
- `render_transcript_note(*, segments, title, project_name, date, time,
  participants, provider, language, voxnote_id, source_path, nudged) -> str` —
  YAML frontmatter + diarized body. Pure.
- `write_transcript_note(meetings_dir, project, meeting_name, content) -> str` —
  creates `<meetings_dir>/<project_dirname>/<meeting_name>/` (root project for
  None), writes **`transcript.md`** inside (UTF-8, atomic), collision-safe on the
  folder. Returns the transcript path. Reuses `processing.layout.target_dir`.
  Hermes later adds `protocol.md` + tasks into the same folder.

### 5. Source archiving — `processing/sources.py` (new)

- `archive_audio(audio_path, sources_dir, base_name, *, move) -> str` —
  `<sources_dir>/<base_name>.<ext>`, collision-safe. `move=True` for record/inbox,
  `move=False` (copy) for «Выбрать файл». Filesystem write (Drive Desktop syncs).
  `sources_dir` unset → skip (note records the original path).

### 6. Diarized rendering — `transcript_format.py` (extend)

`format_diarized_markdown(segments, speaker_map=None) -> str` — group consecutive
same-speaker segments → `**<speaker>:** <text>`; friendly fallback `Спикер 1/2…`;
no diarization → plain paragraphs.

### 7. Segments sidecar — `utils.py` (extend)

`save/load_segments_sidecar(voxnote_id, …)` → `~/.voxnote/segments/<id>.json`
(not vault, not sources).

### 8. Worker — `processing/worker.py` (rework: 3 stages → 1)

`_process_item(item)`: `RUNNING` → pre-flight (denoise decision; cost already
estimated at enqueue) → `cli.core.run_transcribe` (+ speaker-count hint threaded
through `cli.core`) → `write_transcript_note` (folder + `transcript.md`) →
`archive_audio` (move/copy by `source`) → `save_segments_sidecar` → nudge
(best-effort, benign) → `DONE`. Exception → `ERROR` + `humanize`, halt the item
(worker survives; **no auto-retry** — a re-run of a 3-h job costs real money).
Generous provider poll timeouts for multi-hour processing.

### 9. Persistence + history — `processing/store.py` (adapt)

`queue.json` (active items) unchanged. `build_view(meetings_dir, active)` scans
meeting **folders** under `30 Meetings/<project>/` (+ root) — a folder with
`transcript.md` is a meeting; reads its frontmatter; presence of `protocol.md` /
the tasks file shows as **Hermes-progress badges**. Overlays active items; skips
`recordings/`. (This is the merged PR-1 two-level folder scan — minimal change.)

### 10. Nudge event — `integrations/hermes/{schema,client}.py` + `AGENTS.md`

`audio.transcribed` v`1.1` (additive): `audio.note_path` (= the `transcript.md`
path; Hermes derives the folder), `audio.source_path`, `project:{id,name}|null`.

### 11. UI — main bar + «Встречи»

- **Enqueue:** record-stop auto-enqueues; «Выбрать файл» → «Добавить в очередь»;
  inbox files auto-enqueue via the App poll tick.
- **Project selector** (default `last_project_id`); inbox files default no-project.
- **Remove «Транскрибировать»** + the synchronous run-loop.
- **Indicator strip** `● Очередь: N в работе · K ошибок`; reactive
  (`on_change → after(0,…)`); long items show elapsed + queue position; cost hint
  at enqueue.
- **«Встречи» = queue + history**: project-grouped rows; status (в очереди / идёт
  `mm:ss` / готово / ошибка) + Hermes-progress badges (протокол/задачи есть?) +
  **Открыть в Obsidian**, **Повторить** (on error). The standalone «Извлечь
  задачи» stays available but is not part of the queue.

## Storage layout

```text
<Obsidian Vault>/                         (GitHub-versioned)
  30 Meetings/<project>/<meeting>/
    transcript.md   ← VoxNote (diarized; frontmatter → audio)
    protocol.md     ← Hermes (later)
    tasks.md        ← Hermes (later)

<Google Drive Desktop>/
  …/inbox/          ← phone drops audio (to-process; drains itself)
  …/sources/<meeting>.m4a   ← audio archive (Hermes convention)

~/.voxnote/
  queue.json
  segments/<voxnote_id>.json   ← SRT/VTT sidecar (not vault, not sources)
```

Config paths (user-set, placeholder defaults): `meetings_dir` →
`<vault>/30 Meetings`; `sources_dir` → Drive `sources`; `inbox_dir` → Drive
`inbox`. Meeting folder + audio share the stem `<YYYY-MM-DD>_<HHMM>_<slug(title)>`.

**Low-stakes defaults chosen:** `inbox/` flat + no-project (triage later);
`sources/` flat.

## transcript.md format

```markdown
---
type: meeting
date: 2026-06-14
time: "10:00"
project: Kitng
participants: []
provider: AssemblyAI
language: ru
voxnote_id: 20260614-100000_planning-call
source_path: "G:/My Drive/sources/2026-06-14_1000_planning-call.m4a"
nudged: true
---

**Спикер 1:** …

**Спикер 2:** …
```

Hermes's `protocol.md` / `tasks.md` follow the same convention (record the audio
location) — that's Hermes's responsibility, noted here for the shared folder.

## Failure handling

- **Transcribe / write error** → `ERROR` + humanized message, item halts; worker
  survives (broad-except boundary, ratchet-tracked). Manual **Повторить** only —
  **no auto-retry** (re-running a 2–3 h job costs real STT money).
- **Nudge failure** → benign (`nudge_delivered=false`, WARNING); item still
  `DONE` (transcript + audio durable). Subtle "не пнули в Hermes" hint.
- **Sources/archiving failure or no `sources_dir`** → audio stays put; note
  records that path; logged, not fatal.
- **Inbox:** never grab a file until size is stable across ticks; non-audio /
  partial files skipped, not errored.
- **Provider cap exceeded** (pre-flight) → block with a clear Russian message
  before spending an upload.
- **Ordering:** transcribe → write transcript → archive audio. A record/inbox
  file is moved to `sources` only after a successful transcribe, so a failure
  never loses or strands audio.

## Long audio / performance

- **No chunking** — 60-min–3-h files upload whole to long-capable providers
  (AssemblyAI default ~185 h / 2 GB; Speechmatics ~2 GB; Deepgram streams).
  Reviving the chunker would wreck cross-chunk diarization and re-add deleted code.
- **Denoise off for long files** (> ~45 min): the denoise path forces
  `ensure_wav` → a multi-hundred-MB temp WAV + hours of ffmpeg. Skipped above the
  threshold (compressed original uploaded instead).
- **Pre-flight guard** rejects over-cap files before upload (**Gladia** the likely
  limiter — real cap verified in impl).
- **Cost visibility** — STT estimate at enqueue.
- **Timeouts/progress** — generous upload (AssemblyAI caps at 30 min) + poll
  timeouts for multi-hour processing; UI shows elapsed + queue position.

## Phone ingestion

- Phone → **Google Drive (mobile)** Share/Save → configured **`inbox/`** →
  **Google Drive for Desktop** syncs to the PC → the **inbox watcher** polls,
  waits for a size-stable file, auto-enqueues.
- **Not Telegram for real recordings** — Bot API download cap ~20 MB ≪ 120–180 MB;
  gateway often stopped. (Short voice memos could be a future large-file path.)
- After transcription the audio **moves `inbox/ → sources/`** (same archive step
  the convention needs) — the inbox drains for free.
- **Manual fallback** — any transfer → «Выбрать файл».

## Phasing (rough; writing-plans finalizes)

- **PR-A — storage core (headless):** `model.py`, `vault_note.py`
  (+`format_diarized_markdown`), `sources.py`, segments sidecar, `store.build_view`
  (folder scan + Hermes-progress badges). Unit-tested, no UI.
- **PR-B — worker + intake + nudge (headless):** rework `worker.py` (1-stage);
  `inbox_watcher.py`; `preflight.py`; speaker-count through `cli.core`; event
  schema v1.1. Unit-tested.
- **PR-C — UI wiring:** enqueue + project selector + indicator (elapsed/position) +
  cost hint; inbox poll tick; remove «Транскрибировать»; «Встречи» = queue +
  history (with Hermes-progress badges). Source-text + manual smoke.

## Tests

- **inbox_watcher:** extension filter + skip known; debounce (growing file not
  ready until size stable across ticks); `sources/` skipped.
- **preflight:** `probe`; `provider_limit_ok` blocks over-cap; `should_denoise`
  False above threshold; `estimate_cost` math.
- **render/format_diarized_markdown:** frontmatter incl. `source_path`; speaker
  grouping; friendly fallback; no-diarization; UTF-8.
- **write_transcript_note / archive_audio:** folder + `transcript.md` placement;
  root for no project; collision-safe; copy vs move; skip when dir unset.
- **worker:** `auto` item → pre-flight → transcribe → folder+transcript →
  archive (move/copy by `source`) → sidecar → nudge → DONE (deps patched);
  transcribe error → ERROR + halt; nudge fail → DONE + `nudge_delivered=false`;
  denoise auto-disabled for >45-min; speaker-count forwarded.
- **store.build_view:** folder scan finds meetings (transcript.md); protocol/tasks
  presence → badges; overlay wins.
- **schema:** `note_path` + `source_path` + `project`; v1.1; back-compat.
- **UI wiring:** source-text (record/file/inbox → enqueue; selector ↔
  `last_project_id`; «Транскрибировать» removed; indicator).

## Out of scope (deliberate)

- Protocol / task generation + tracker send (Hermes owns them). Manual «Извлечь
  задачи» stays, outside the queue.
- **Chunking / chunker revival** (whole-file to long-capable providers).
- **Telegram ingestion of long files** (Bot API 20 MB cap).
- Auto-retry; parallel transcription (serial v1).
- A "pending nudges" re-sweep for notes written while Hermes was down.
- `gdrive/` **API** for archiving/intake — v1 relies on Drive **Desktop** sync.
- One-time migration of old meetings (old folders stay readable; new ones use this
  layout — already folder-per-meeting, so minimal drift).
- Voice-ID Phase B (respects invariant #2).

## Affected files

| File | Change |
|---|---|
| `processing/model.py` | rework — single `status`, `source`, `source_path`, `nudge_delivered` (keep `meeting_folder`) |
| `processing/inbox_watcher.py` | new — poll Drive `inbox/`, debounce, enqueue |
| `processing/preflight.py` | new — probe, provider-cap guard, denoise decision, cost estimate |
| `processing/vault_note.py` | new — render + write the meeting folder's `transcript.md` |
| `processing/sources.py` | new — archive audio into Drive `sources` (move/copy) |
| `transcript_format.py` | add `format_diarized_markdown` |
| `utils.py` | add `save/load_segments_sidecar` |
| `processing/worker.py` | rework — 1-stage: pre-flight → transcribe → transcript → archive → nudge |
| `processing/store.py` | `build_view` folder scan + Hermes-progress badges |
| `processing/indicator.py` | new — counts/format |
| `integrations/hermes/schema.py` + `client.py` | `note_path` + `source_path` + `project`, v1.1 |
| `cli/core.py` | thread speaker-count hint through `run_transcribe` |
| `ui/app/{builder,recorder_mixin,settings_mixin,transcription_mixin,dialogs_mixin,__init__}.py` | enqueue, project selector, indicator, inbox poll tick, cost hint, remove «Транскрибировать» |
| `ui/dialogs/meetings.py` | «Встречи» = queue + history (Hermes-progress badges) |
| `ui/dialogs/settings.py` | path settings: `meetings_dir`/vault, `sources_dir`, `inbox_dir` |
| `AGENTS.md`, `CLAUDE.md`, `config.example.json` | docs + `last_project_id` + `sources_dir` + `inbox_dir` |
| `tests/` | per the Tests section |
```
