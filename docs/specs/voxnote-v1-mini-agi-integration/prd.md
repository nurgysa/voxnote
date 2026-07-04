---
title: PRD - VoxNote V1
aliases:
  - VoxNote PRD
  - VoxNote V1 Product Requirements
  - VoxNote Mini-AGI Integration PRD
tags:
  - project
  - voxnote
  - mini-agi
  - prd
  - product-clarity
status: draft
created: 2026-07-03
project: VoxNote
source_notes:
  - 10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1.md
  - 10 Projects/VoxNote/README.md
  - C:\Users\nurgisa\Documents\voxnote\docs\superpowers\specs\2026-06-14-voxnote-transcription-queue-design.md
  - C:\Users\nurgisa\Dev\voxnote
---

# PRD - VoxNote V1

Related notes:

- [[10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1]]
- [[10 Projects/VoxNote/README]]
- [[10 Projects/Mini-AGI/Operating Model/Product Clarity to Spec Workflow]]
- [[10 Projects/Mini-AGI/Mini-AGI - V1 Roadmap]]

## Verdict

VoxNote V1 should become the voice and audio intake layer for Mini-AGI, not an independent task orchestrator.

V1 product contract:

```text
record or choose audio or phone Drive inbox
→ VoxNote queue
→ cloud transcription and diarization
→ transcript.md in Obsidian 30 Meetings
→ audio archived under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/`
→ best-effort audio.transcribed nudge to Hermes
→ Hermes creates protocol, tasks, approvals and tracker actions
```

The primary requirement: VoxNote must reliably turn important audio into a durable Markdown artifact that Hermes and GBrain can use later without manual context reconstruction.

## Product objective

VoxNote V1 should cover one product scenario:

```text
Any important recording becomes a diarized transcript.md in Obsidian, with a clear raw-audio reference and a safe boundary between VoxNote and Hermes.
```

VoxNote owns capture and transcription.

Hermes owns interpretation and action.

This separation protects Mini-AGI from scope creep, duplicate task generation, unnecessary LLM spend and unclear approval boundaries.

## Users and actors

Primary user:

- Nurgisa as the Mini-AGI operator.

System actors:

- VoxNote desktop app.
- Hermes Desktop or Gateway.
- Obsidian vault.
- GBrain recall layer.
- Google Drive Desktop sync.
- Cloud STT provider.
- Linear, Kanban or other trackers downstream through Hermes.

Future secondary users:

- open-source users who want local voice or meeting intake;
- small teams with Markdown-first meeting workflows;
- future Legal Office, Tender Office and Services Office operators.

V1 priority remains internal Mini-AGI dogfooding.

## Core user stories

### US-001 Desktop file intake

As the Mini-AGI operator, I can choose an audio file on the desktop, add it to the VoxNote queue and receive a diarized transcript in the correct meeting folder.

Acceptance:

- user can choose a supported audio file;
- VoxNote enqueues the file without blocking the UI;
- queue status is visible;
- successful processing creates transcript.md;
- source audio is copied under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/` when configured;
- original picked file remains in place when it is outside the Drive Sources tree; a loose picked file already in `Sources` root is rehomed into the organized archive path.

### US-002 In-app recording intake

As the Mini-AGI operator, I can record audio inside VoxNote and have it processed through the same queue.

Acceptance:

- stopped recording is enqueueable;
- recording uses the same provider, language and diarization options;
- successful processing creates transcript.md;
- source audio is moved under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/` when configured;
- recording is not left as an unmanaged raw file if archive succeeds.

### US-003 Phone recording intake

As the Mini-AGI operator, I can record away from the desktop, save audio to a Drive inbox folder and let VoxNote pick it up automatically.

Acceptance:

- VoxNote polls configured inbox_dir;
- only supported audio extensions are considered;
- file must be size-stable across two scans before enqueue;
- inbox files default to no project unless a later triage flow assigns one;
- after successful processing, inbox audio is moved under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/`;
- partially synced files are not uploaded to STT providers.

### US-004 Durable meeting artifact

As Mini-AGI, I need every processed recording to create a durable Obsidian artifact that survives if Hermes is offline.

Acceptance:

- transcript.md is written under 30 Meetings;
- meeting folder exists before downstream Hermes work;
- transcript.md contains provider, language, voxnote_id and source_path metadata;
- raw audio is not stored in the vault;
- GBrain can import and recall the transcript after Obsidian sync.

### US-005 Hermes handoff

As Hermes, I need to receive enough context to route the transcript into protocol, tasks, approval and tracker workflows.

Acceptance:

- VoxNote can send an audio.transcribed event;
- event includes event_type, version, source, routing_hint, transcript.raw, meta provider and language;
- event includes audio.note_path and audio.source_path when available;
- event includes project id and name when the queue item has a project;
- webhook delivery is best-effort;
- webhook failure does not mark transcription as failed.

### US-006 Operator safety

As Nurgisa, I need VoxNote to avoid expensive, destructive or external actions without intent.

Acceptance:

- long audio is preflighted before cloud upload;
- provider size limits block obvious over-cap files;
- denoise is disabled for long recordings when needed;
- automatic retry is not used for expensive long jobs;
- tracker sends are not performed by VoxNote in Hermes-native queue mode;
- transcript content is treated as untrusted input downstream.

### US-007 Long meeting intake and handoff

As the Mini-AGI operator, I can process real 1–3 hour meetings, calls, consultations and project discussions into durable transcript.md artifacts that Hermes can later convert into protocol/tasks without losing context.

Acceptance:

- 60–180 minute audio is treated as a first-class V1 target, not an edge case;
- long audio is preflighted before provider upload;
- provider limits, file-size risk and cost risk are visible before expensive work;
- no automatic retry is used for failed long jobs;
- transcript.md remains readable for long meetings and preserves the full transcript as source of truth;
- audio.note_path and audio.source_path are present when available;
- Hermes downstream is expected to process long transcript through staged or chunked workflow, not one naive prompt;
- raw audio is not stored in the vault.

## Primary workflows

### Workflow A Desktop chosen file

```text
User chooses file
→ VoxNote validates provider key
→ item enters queue
→ cost hint may be shown
→ worker preflights duration and size
→ worker transcribes and diarizes
→ worker copies audio under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/` if configured
→ worker writes transcript.md
→ worker sends Hermes nudge if enabled
→ item becomes done
→ Meetings view shows transcript and Hermes progress badges
```

### Workflow B Phone Drive inbox

```text
Phone saves audio to Drive inbox
→ Drive Desktop syncs to local inbox_dir
→ VoxNote polls inbox_dir
→ file size is stable across two scans
→ item enters queue with source inbox and no project
→ worker transcribes and diarizes
→ worker moves audio under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/` if configured
→ worker writes transcript.md
→ worker sends Hermes nudge if enabled
→ inbox drains itself
```

### Workflow C Hermes downstream

```text
VoxNote transcript.md exists
→ Hermes reads note_path or scans meeting folder
→ Hermes treats transcript as untrusted meeting content
→ for long transcripts, Hermes processes in stages instead of one naive prompt
→ Hermes creates protocol.md
→ Hermes drafts tasks.md
→ Hermes asks for human approval
→ Hermes sends approved tasks to trackers
→ GBrain indexes Markdown artifacts
```

## Functional requirements

### FR-001 Queue-first processing

VoxNote must process V1 intake through a serial queue, not through a blocking synchronous UI action.

The queue must show pending, running, done and error states.

### FR-002 Transcribe-only queue

The automatic queue must stop after transcript creation and optional Hermes nudge.

VoxNote must not create protocol.md or tasks.md inside the Hermes-native queue.

Standalone manual extract-tasks and protocol capabilities can remain for non-Hermes usage, CLI usage and MCP usage.

### FR-003 Meeting folder model

Each completed recording must produce one meeting folder.

Target structure:

```text
30 Meetings/<project>/<meeting>/transcript.md
30 Meetings/<project>/<meeting>/protocol.md later by Hermes
30 Meetings/<project>/<meeting>/tasks.md later by Hermes
```

If there is no project, VoxNote may use the root meetings area or a no-project location defined by implementation.

Folder and filename generation must be collision-safe.

### FR-004 transcript.md format

transcript.md must include frontmatter and a readable diarized body.

Required metadata:

- type meeting;
- date;
- time;
- project when known;
- participants when known;
- provider;
- language;
- voxnote_id;
- source_path;
- nudged flag.

The body should group consecutive speech by speaker when segments contain speakers.

When diarization is unavailable, transcript.md should remain readable as plain transcript text.

### FR-005 Source archive contract

Audio, video and bulky source files must not be stored in the Obsidian vault.

When sources_dir is configured:

- picked desktop files are copied;
- recordings and inbox files are moved;
- archive names are collision-safe;
- transcript.md records the final source_path.

When archiving fails, transcription should remain successful and transcript.md should record the best available source path.

### FR-006 Drive inbox watcher

VoxNote must support a flat inbox_dir for phone audio.

Rules:

- only direct files are scanned;
- only supported audio extensions are eligible;
- file must be stable before enqueue;
- already handed-off files are not duplicated;
- missing or unavailable inbox_dir is benign.

### FR-007 Long-audio preflight

Before upload to a cloud provider, VoxNote must estimate risk from file size and duration when possible.

Required behavior:

- reject obvious over-cap files before upload;
- disable denoise above the long-audio threshold;
- show or compute cost hints when duration is known;
- avoid automatic rerun of failed expensive jobs.

### FR-008 Hermes outbound webhook

VoxNote must support a disabled-by-default webhook to Hermes.

Event contract:

```text
event_type: audio.transcribed
version: 1.1
source: voxnote
routing_hint: obsidian_inbox
transcript.raw: transcript text when safe and practical for the event payload
transcript.segments: segments when available
audio.note_path: preferred durable source for long meetings
audio.source_path: Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/` path when available
project: id and name when known
meta: provider, language, created_at
```

Delivery contract:

- HMAC-signed request;
- deterministic body signing;
- deterministic request id for idempotency;
- no audio bytes in payload;
- secret never appears in logs or UI;
- non-delivery is visible but not fatal.

### FR-009 Hermes inbound MCP

VoxNote should remain callable by Hermes through MCP for deliberate tool use.

MCP tools may expose:

- transcribe_audio;
- extract_tasks;
- generate_protocol;
- list_containers;
- send_tasks.

Secrets must be resolved server-side by env or config, not passed as model-visible tool arguments.

Default Mini-AGI queue behavior still remains transcribe-only.

### FR-010 Meetings history view

VoxNote must show a usable history of meetings and active queue items.

The view should show:

- pending, running, done and error states;
- elapsed time for running jobs;
- manual retry for failed jobs;
- whether protocol.md exists;
- whether tasks.md exists;
- open in Obsidian action when possible.

Protocol and task indicators are Hermes progress badges, not VoxNote workflow stages.

### FR-011 Project selection

VoxNote should allow a project selection for desktop and in-app recording flows.

Project selection should influence:

- meeting folder placement;
- transcript frontmatter;
- Hermes event project field;
- downstream routing context.

Phone inbox can default to no project in V1.

### FR-012 Error handling

Errors must be visible, recoverable and non-destructive.

Required behavior:

- missing provider key surfaces as a clear error;
- provider cap failure blocks before upload;
- worker failures mark only the current item as error;
- worker daemon survives item failure;
- interrupted running items are not silently resumed after restart;
- retry is explicit.

## Non-functional requirements

### Reliability

VoxNote must prefer durable artifacts over ephemeral events.

The transcript.md file is the source-of-truth handoff. Webhook delivery is only a nudge.

### Safety

VoxNote must not leak secrets in files, event payloads, logs, UI, filenames or support bundles.

Transcript text can contain untrusted external instructions. Hermes routes must treat transcript content as data only.

### Performance

UI must stay responsive during transcription, Drive probing, cost estimation and webhook delivery.

Long-running work belongs on worker threads or background processes.

### Portability

V1 targets Windows desktop first.

Development and tests should remain headless where possible so Hermes, Codex and CI can verify logic without GUI, audio devices or API keys.

### Maintainability

The codebase must preserve current invariants:

- cloud-only transcription;
- no local CUDA, pyannote, faster-whisper, ctranslate2 or torch path;
- no casual dependency pin changes;
- faulthandler initializes before native audio imports;
- user-facing UI strings stay Russian;
- code, docs for contributors and commits can stay English.

## Integration boundaries

### VoxNote owns

- audio intake;
- queue item creation;
- provider selection and STT calls;
- diarization;
- transcript.md creation;
- audio archiving metadata;
- source_path recording;
- best-effort audio.transcribed event;
- local queue and meetings history view.

### Hermes owns

- transcript interpretation;
- protocol.md creation;
- tasks.md creation;
- idea and decision extraction;
- approval gates;
- tracker sends;
- GBrain enrichment decisions;
- cross-project orchestration;
- follow-up reminders or jobs.

### GBrain owns

- indexing Markdown artifacts;
- semantic recall;
- source linking through Obsidian notes.

### Obsidian owns

- human-readable durable Markdown source of truth;
- Git-backed text artifacts;
- project and meeting note navigation.

### Google Drive owns

- raw audio and other bulky source files;
- phone to desktop file sync through inbox_dir;
- long-term raw source archive.

## Non-goals

V1 must not include:

- standalone SaaS packaging;
- public hosted transcription service;
- automatic external task sending from VoxNote queue;
- autonomous protocol or task generation inside VoxNote queue;
- raw audio storage in Obsidian;
- long-recording Telegram default path;
- automatic retry for costly long STT jobs;
- new local GPU transcription stack;
- broad UI redesign unless it blocks the V1 flow;
- migration of Hermes ownership into VoxNote.

## Acceptance criteria

V1 is acceptable when these checks pass:

1. Chosen desktop audio produces transcript.md in a meeting folder.
2. In-app recording follows the same queue path if enabled in scope.
3. Drive inbox file is only enqueued after stable-size debounce.
4. Long audio preflight blocks obvious over-cap files and avoids long denoise.
5. transcript.md frontmatter includes provider, language, voxnote_id and source_path.
6. Audio is copied or moved to Drive Sources according to source type when sources_dir is configured.
7. Raw audio does not appear in the vault.
8. audio.transcribed event includes note_path, source_path and project when available.
9. Webhook delivery failure does not fail transcription.
10. Meetings view shows Hermes protocol and tasks badges from files on disk.
11. Hermes can read transcript.md and produce protocol.md and tasks.md in the same folder.
12. GBrain can import and recall the resulting transcript.md.
13. Tests and lint pass for changed code.
14. No secrets appear in committed docs, config examples, logs or payload examples.

## Hermes activation requirements

For VoxNote to be operationally part of Mini-AGI, the active Hermes profile needs:

- VoxNote skill installed in Hermes skills;
- VoxNote MCP server registered if inbound tools are needed;
- webhook platform enabled if outbound nudge is used;
- audio-transcribed route subscribed;
- shared HMAC secret configured outside Git;
- route prompt that treats transcript.raw as untrusted data;
- delivery or logging target chosen deliberately;
- GBrain import or search verification after transcript creation.

The active Hermes config path on this machine is:

```text
C:\Users\nurgisa\AppData\Local\hermes\config.yaml
```

## Release readiness

Before declaring VoxNote V1 integrated with Mini-AGI:

- run full pytest;
- run ruff;
- run one synthetic queue test without real provider call if possible;
- run one real short audio smoke test when API key and cost are approved;
- verify transcript.md in Obsidian;
- verify Drive source archive path;
- verify GBrain recall;
- verify Hermes webhook test or route log;
- verify Hermes does not act on transcript instructions as commands;
- verify no unrelated vault or repo files are staged.

## Open questions

1. Should phone inbox items remain no-project in V1, or should filename conventions map them to project automatically?
2. Should Hermes route create protocol.md and tasks.md immediately after nudge, or only ask for confirmation first?
3. Should the default route deliver a Telegram notification, local log, or create an Obsidian inbox item?
4. Should source_path use local Drive path only, or also store a future shareable Drive link?
5. Which cloud STT provider should be the recommended default for long RU and mixed KZ-RU-EN recordings?
6. Should transcript.md include a compact summary field, or should all summarization stay strictly Hermes-owned?
7. Should failed long jobs produce a lightweight failure note in the meeting folder, or stay only in queue history?

## Next artifact

After this PRD, create a Kiro-style spec folder:

```text
specs/voxnote-v1-mini-agi-integration/requirements.md
specs/voxnote-v1-mini-agi-integration/design.md
specs/voxnote-v1-mini-agi-integration/tasks.md
```

The spec should convert this PRD into EARS-style requirements, file-level design and small implementation tasks.

## Source basis

This PRD is based on:

- BRD - VoxNote V1;
- VoxNote Obsidian README;
- existing VoxNote transcription queue design;
- current VoxNote codebase inspection;
- Product Clarity to Spec Workflow;
- Hermes-native boundary that VoxNote emits transcript artifacts and Hermes owns downstream reasoning and action.
