---
title: BRD - VoxNote V1
aliases:
  - VoxNote BRD
  - VoxNote V1 Business Requirements
  - VoxNote Product Clarity BRD
tags:
  - project
  - voxnote
  - mini-agi
  - brd
  - product-clarity
status: draft
created: 2026-07-03
project: VoxNote
source_notes:
  - 10 Projects/VoxNote/README.md
  - C:\Users\nurgisa\Documents\voxnote\docs\superpowers\specs\2026-06-14-voxnote-transcription-queue-design.md
---

# BRD - VoxNote V1

Связанные заметки:

- [[10 Projects/VoxNote/README]]
- [[10 Projects/Mini-AGI/Operating Model/Product Clarity to Spec Workflow]]
- [[10 Projects/Mini-AGI/Mini-AGI - V1 Roadmap]]

## Вердикт

VoxNote подходит как первый benchmark для нового Product Clarity workflow.

Но его надо оценивать не как обычный SaaS, а как open-source Mini-AGI capability:

```text
voice/audio source
→ VoxNote transcription and diarization
→ Obsidian transcript.md
→ GBrain recall
→ Hermes protocol, tasks, approval and trackers
```

Главная бизнес-ценность VoxNote для Mini-AGI: убрать потерю голосовых и аудиоисточников из рабочего контура. Встречи, голосовые заметки и длинные записи должны становиться проверяемым Markdown evidence, а не исчезать в телефоне, Telegram, локальных Downloads или случайных аудиофайлах.

## Business problem

Mini-AGI не может быть надёжной операционной системой, если важный контекст остаётся в неструктурированном аудио.

Проблема сейчас:

- голосовые мысли легко теряются;
- длинные встречи сложно превратить в задачи и решения;
- raw audio плохо подходит для Obsidian и Git;
- Hermes и GBrain работают лучше с Markdown, чем с бинарными файлами;
- downstream protocol and tasks должны быть в Hermes, а не в отдельном transcriber app;
- long recordings требуют предсказуемого queue, cost guard и human intent.

## Buyer and stakeholder

Primary internal stakeholder: Nurgisa как operator, product owner and Mini-AGI architect.

Primary system stakeholder: Mini-AGI.

Secondary future stakeholders:

- open-source users who need local voice or meeting intake;
- AI-native operators who use Obsidian or Markdown-first workflows;
- small teams that need transcription handoff into their own task system;
- future Services or Legal Office workflows that need meeting evidence.

VoxNote V1 should serve internal Mini-AGI dogfooding first. External packaging should not drive V1 scope unless the internal loop works reliably.

## Current workaround

Current or likely workarounds:

- manually upload audio to a transcription service;
- paste transcripts into Obsidian by hand;
- use Telegram voice notes for short capture only;
- keep long recordings in Drive or local folders without durable Markdown handoff;
- ask Hermes to process notes after the fact with missing context;
- rely on ad hoc naming, storage and task extraction.

These workarounds are fragile because they do not produce a standard meeting folder with transcript, source reference, Hermes handoff and GBrain recall.

## Business objective

VoxNote V1 should make audio intake boring, reliable and durable.

Objective:

```text
Any important voice/audio source becomes a diarized transcript.md in the vault, with raw audio archived outside the vault and a best-effort nudge to Hermes.
```

The business outcome is not transcription for its own sake. The outcome is better Mini-AGI memory, decision capture, meeting follow-through and task execution.

## Success metrics

V1 is successful when these conditions are true:

- a desktop audio file can be queued and transcribed into the correct Obsidian meeting folder;
- a phone recording can arrive through Drive inbox and be processed without manual file surgery;
- long recordings are preflighted before expensive transcription starts;
- raw audio is archived to Drive Sources, not stored in the vault;
- transcript.md includes project, provider, language and source path metadata;
- GBrain can recall the transcript after import;
- Hermes can use transcript.md as the downstream handoff for protocol.md and tasks.md;
- nudge failure does not lose the transcript;
- VoxNote does not generate protocol or tasks inside the Hermes-native queue;
- a failed or expensive rerun requires explicit human intent.

Practical V1 target:

```text
5 real recordings processed end-to-end with zero lost transcript artifacts and no raw audio committed to the vault.
```

## Scope boundaries

In scope for V1:

- desktop choose-file intake;
- in-app recording if already available or low-risk;
- Drive inbox polling for phone audio;
- stable-file detection before enqueue;
- transcription and diarization;
- transcript.md writing into 30 Meetings;
- audio archiving to Drive Sources;
- best-effort audio.transcribed nudge to Hermes;
- queue and history view;
- clear error states and manual retry;
- cost and provider preflight for long recordings.

Out of scope for V1:

- VoxNote generating protocol.md in the Hermes-native queue;
- VoxNote generating tasks.md in the Hermes-native queue;
- sending tasks to Linear, Kanban or other trackers;
- replacing Hermes approval gates;
- storing audio or machine sidecars in the Obsidian vault;
- long-recording Telegram ingestion as the main path;
- automatic expensive retry of long transcription jobs;
- turning VoxNote into SaaS before internal dogfooding proves repeated demand.

## Constraints

Technical constraints:

- vault path is local Obsidian Markdown;
- non-Markdown sources belong in Google Drive Sources;
- GBrain indexes Markdown, not raw audio;
- Drive sync can expose partially uploaded files, so stable-size detection is required;
- long recordings may be 60 minutes to 3 hours;
- provider duration, file size and pricing limits matter;
- diarization quality can degrade if long audio is chunked poorly.

Operating constraints:

- Hermes owns protocol, tasks, approval and tracker delivery;
- VoxNote is a capability and emitter, not orchestrator;
- nudge is best-effort, not the source of truth;
- secrets must not appear in transcript, filenames, logs or vault notes;
- external packaging should not override internal Mini-AGI reliability.

## Risks

Main risks:

- scope creep turns VoxNote back into a mini-orchestrator;
- duplicate protocol and task generation increases LLM spend and creates inconsistent outputs;
- long-audio processing becomes expensive or unreliable;
- Drive inbox grabs a file before sync completes;
- raw audio accidentally enters the Git-backed vault;
- diarization quality is not good enough for meeting recall;
- Hermes nudge failure is mistaken for pipeline failure;
- open-source packaging distracts from internal dogfood quality;
- transcripts may contain sensitive information and need careful handling.

## Decision

Decision: continue VoxNote V1 as a Mini-AGI dogfood product and open-source capability.

Do not position it as a standalone SaaS yet.

Use VoxNote as the first benchmark case for Product Clarity to Spec Workflow:

```text
BRD
→ PRD
→ requirements.md
→ design.md
→ tasks.md
→ implementation and verification
```

The immediate next artifact should be PRD - VoxNote V1, based on this BRD and the existing transcription queue design.

## Acceptance gate for moving to PRD

Move to PRD when this BRD is accepted as the business framing.

PRD should focus on:

- user flows;
- intake modes;
- queue behavior;
- meeting folder model;
- transcript.md format;
- error handling;
- safety gates;
- integration boundary with Hermes;
- V1 release criteria.

## Source basis

This BRD is based on:

- VoxNote README in the Obsidian project folder;
- existing VoxNote transcription queue design dated 2026-06-14;
- Mini-AGI Product Clarity to Spec Workflow;
- current rule that VoxNote is open source and Hermes-native.
