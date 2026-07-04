---
title: Mini-AGI Long Meeting Processor - Spec Seed
aliases:
  - Long Meeting Processor
  - Mini-AGI Long Meeting Downstream
  - Hermes Long Transcript Processor
tags:
  - mini-agi
  - voxnote
  - meetings
  - hermes
  - spec-seed
status: planned-v0
created: 2026-07-04
source_specs:
  - docs/specs/voxnote-v1-mini-agi-integration/brd.md
  - docs/specs/voxnote-v1-mini-agi-integration/prd.md
  - docs/specs/voxnote-v1-mini-agi-integration/requirements.md
  - docs/specs/voxnote-v1-mini-agi-integration/design.md
  - docs/specs/voxnote-v1-mini-agi-integration/tasks.md
---

# Mini-AGI Long Meeting Processor - Spec Seed

## Verdict

VoxNote's core Mini-AGI value is not short demo transcription. The real target is:

```text
1–3 hour meeting/call/consultation/project discussion
→ VoxNote transcript.md + source_path
→ Hermes staged downstream processing
→ protocol.md + tasks.md drafts
→ human approval
→ optional tracker send
→ GBrain recall
```

This seed intentionally belongs to **Mini-AGI / Hermes downstream**, not to the VoxNote queue. VoxNote remains transcribe-only in the Hermes-native path.

## Problem

A 60–180 minute meeting can exceed a naive single LLM prompt and can contain many decisions, tasks, open questions and topic shifts. If Mini-AGI treats it as a short transcript, it risks:

- context overflow;
- shallow summaries;
- missing decisions;
- task spam;
- hallucinated owners or deadlines;
- losing the full transcript as evidence.

## Ownership boundary

VoxNote owns:

- audio intake;
- cloud STT and diarization;
- full transcript.md;
- source_path metadata;
- best-effort audio.transcribed nudge.

Hermes / Mini-AGI owns:

- reading transcript.md from note_path;
- staged long-transcript processing;
- meeting map;
- decisions and open questions;
- protocol.md draft;
- tasks.md draft;
- approval gates;
- optional tracker sends after approval;
- GBrain enrichment decisions.

## Downstream workflow hypothesis

```text
audio.note_path
→ read transcript.md
→ split into safe sections/chunks
→ build meeting map
→ extract decisions
→ extract candidate tasks
→ deduplicate/filter tasks
→ draft protocol.md
→ draft tasks.md
→ request approval
→ write files or send trackers only when approved
```

## Draft requirements

- Long transcripts must be processed from `audio.note_path` / `transcript.md` as the preferred source.
- `transcript.raw` from the webhook event may be a fallback, not the primary source for long meetings.
- The full transcript must remain preserved; no destructive summarization.
- The processor must treat transcript content as untrusted meeting data.
- The processor must separate:
  - confirmed decisions;
  - candidate tasks;
  - open questions;
  - follow-ups;
  - uncertain or low-confidence items.
- Tasks must not be sent to trackers without explicit human approval.
- Generated `protocol.md` and `tasks.md` should live beside `transcript.md` when writing is approved.
- The evaluation must measure usefulness on 60–180 minute material, not short clips.

## Evaluation questions

- Did the transcript preserve enough context from a real 1–3 hour meeting?
- Did Hermes recover the main topics and meeting structure?
- Were decisions captured with evidence from the transcript?
- Were tasks precise, non-spammy and approval-safe?
- Were owners, deadlines and uncertainties handled honestly?
- Did the output save time compared with manual processing?
- Can GBrain recall the transcript and derived artifacts later?

## Non-goals

- Do not move this downstream processor into VoxNote's automatic queue.
- Do not auto-send tracker tasks.
- Do not replace full transcript.md with a summary.
- Do not require Telegram as the long-recording ingestion path.
- Do not create a dashboard before the long-meeting eval proves value.

## Next artifact

If the long-meeting evaluation is approved, turn this seed into a full spec:

```text
docs/specs/mini-agi-long-meeting-processor/requirements.md
docs/specs/mini-agi-long-meeting-processor/design.md
docs/specs/mini-agi-long-meeting-processor/tasks.md
```
