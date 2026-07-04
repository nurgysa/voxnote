---
title: Requirements - VoxNote V1 Mini-AGI Integration
aliases:
  - VoxNote V1 Requirements
  - VoxNote Mini-AGI Requirements
  - VoxNote Hermes Integration Requirements
tags:
  - project
  - voxnote
  - mini-agi
  - spec
  - requirements
  - product-clarity
status: draft
created: 2026-07-03
project: VoxNote
source_notes:
  - 10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1.md
  - 10 Projects/VoxNote/Product Clarity/PRD - VoxNote V1.md
  - 10 Projects/VoxNote/README.md
  - C:\Users\nurgisa\Dev\voxnote
---

# Requirements - VoxNote V1 Mini-AGI Integration

Related notes:

- [[10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1]]
- [[10 Projects/VoxNote/Product Clarity/PRD - VoxNote V1]]
- [[10 Projects/VoxNote/README]]
- [[10 Projects/Mini-AGI/Operating Model/Product Clarity to Spec Workflow]]

## Verdict

VoxNote V1 should be the voice and audio context intake layer for Mini-AGI.

The system is correctly designed when VoxNote creates a reliable transcript.md, archives raw audio outside the vault, sends only a best-effort Hermes nudge, and does not take over Hermes-owned downstream reasoning, approval or tracker actions.

## Scope

This requirements file covers the V1 integration contract between VoxNote, Hermes, Obsidian, GBrain and Drive.

It does not cover a standalone SaaS product, a new hosted backend, a full UI redesign or a local GPU transcription stack.

## Functional requirements

### R-001 Queue-first desktop file intake

WHEN the operator chooses a supported desktop audio file
THE SYSTEM SHALL enqueue the file into the VoxNote processing queue instead of running transcription synchronously on the UI thread.

WHEN a desktop-picked file is enqueued
THE SYSTEM SHALL preserve the original file unless the operator explicitly chose a move-oriented source type.

WHEN the queue accepts the file
THE SYSTEM SHALL show that active work exists in the main UI queue indicator.

### R-002 In-app recording intake

WHEN the operator stops an in-app recording and chooses to process it
THE SYSTEM SHALL enqueue the recording through the same queue used by chosen files.

WHEN a recording completes successfully and sources_dir is configured
THE SYSTEM SHALL move the recording into Drive Sources rather than leaving it unmanaged in a temporary recording location.

### R-003 Phone Drive inbox intake

WHEN inbox_dir is configured
THE SYSTEM SHALL poll it without requiring a filesystem event dependency.

WHEN a file appears in inbox_dir
THE SYSTEM SHALL ignore it until its size is stable across two scans.

WHEN a file is not a supported audio extension
THE SYSTEM SHALL ignore it.

WHEN a stable inbox file is accepted
THE SYSTEM SHALL enqueue it with source inbox.

WHEN an inbox file completes successfully and sources_dir is configured
THE SYSTEM SHALL move the audio into Drive Sources so the inbox drains itself.

### R-004 Supported audio inputs

WHEN VoxNote scans or accepts an audio input
THE SYSTEM SHALL support the V1 audio extensions already defined by the app, including m4a, mp3, wav, ogg, opus, aac and flac.

WHEN a file is not readable or disappears during scanning
THE SYSTEM SHALL treat that as benign and continue polling or processing other items.

### R-005 Queue item model

WHEN a file is enqueued
THE SYSTEM SHALL persist enough queue metadata to survive restart for active work.

A queue item SHALL include at minimum id, audio_path, title, created_at, options, source, project_id, status, source_path, meeting_folder, nudge_delivered and error_message fields.

WHEN an item reaches done
THE SYSTEM SHALL treat the meeting folder and transcript.md as durable state instead of keeping done items forever in active queue storage.

### R-006 Restart behavior

WHEN VoxNote starts and finds an item that was running in a prior session
THE SYSTEM SHALL mark that item as error rather than silently resuming a potentially expensive cloud job.

WHEN an interrupted item is marked as error
THE SYSTEM SHALL require explicit operator retry before another transcription attempt.

### R-007 Preflight before provider upload

WHEN a queue item is about to upload to a cloud STT provider
THE SYSTEM SHALL probe file size and duration when possible.

WHEN file size exceeds the configured provider safety cap
THE SYSTEM SHALL block before upload and show a human-readable error.

WHEN duration is above the long-audio denoise threshold
THE SYSTEM SHALL disable denoise even if the operator requested denoise.

WHEN duration is known
THE SYSTEM SHOULD calculate a rough provider cost hint.

### R-007A Long-meeting first-class target

WHEN an audio file duration is between 60 and 180 minutes
THE SYSTEM SHALL treat it as a first-class V1 target, not an edge case.

WHEN a long meeting is accepted for processing
THE SYSTEM SHALL prioritize preserving the complete transcript.md artifact over producing a short summary.

WHEN a long meeting fails or is interrupted
THE SYSTEM SHALL require explicit operator retry before another paid provider upload.

WHEN a long meeting is handed off to Hermes
THE SYSTEM SHALL provide audio.note_path as the preferred durable source whenever transcript.md exists.

### R-008 Provider and key resolution

WHEN a queue item starts
THE SYSTEM SHALL resolve the selected cloud provider from item options or app config.

WHEN the provider API key is missing
THE SYSTEM SHALL fail the item with a clear error and SHALL NOT attempt upload.

WHEN mixed language is selected
THE SYSTEM SHALL pass the mixed language sentinel only to providers that support it.

### R-009 Cloud-only invariant

WHEN implementing V1 integration work
THE SYSTEM SHALL NOT add local CUDA, pyannote, faster-whisper, ctranslate2, torch or local GPU transcription paths.

WHEN a feature appears to require local inference
THE SYSTEM SHALL stop for product and architecture approval before coding.

### R-010 Transcription and diarization output

WHEN a provider returns transcript segments with speakers
THE SYSTEM SHALL preserve speaker data in the normalized segment list.

WHEN speaker data is available
THE SYSTEM SHALL render transcript.md as diarized Markdown grouped by speaker where possible.

WHEN speaker data is unavailable
THE SYSTEM SHALL still create a readable transcript.md.

### R-011 Meeting folder creation

WHEN a queue item completes transcription
THE SYSTEM SHALL create a meeting folder under the configured meetings_dir.

WHEN a project is selected
THE SYSTEM SHALL place the meeting under that project area.

WHEN no project is selected
THE SYSTEM SHALL use the defined no-project or root meeting location.

WHEN a folder name collides
THE SYSTEM SHALL create a collision-safe alternate folder without overwriting existing meeting artifacts.

### R-012 transcript.md artifact

WHEN transcription succeeds
THE SYSTEM SHALL write transcript.md into the meeting folder.

transcript.md SHALL include YAML frontmatter.

transcript.md SHALL include date, time, project when known, participants when known, provider, language, voxnote_id, source_path and nudged metadata.

transcript.md SHALL include readable transcript body text.

transcript.md SHALL be UTF-8 encoded.

### R-013 Vault hygiene

WHEN VoxNote writes to the Obsidian vault
THE SYSTEM SHALL write text artifacts only.

THE SYSTEM SHALL NOT place raw audio, video, machine sidecars or bulky binary source files in the Git-backed vault.

WHEN segments or voice identification sidecars are needed
THE SYSTEM SHALL keep them in app data, not in the vault, unless a later approved spec changes this.

### R-014 Source archive

WHEN sources_dir is configured and a picked file completes
THE SYSTEM SHALL copy the original audio to Drive Sources.

WHEN sources_dir is configured and a recording or inbox file completes
THE SYSTEM SHALL move the audio to Drive Sources.

WHEN archiving succeeds
THE SYSTEM SHALL record the final source_path in transcript.md and queue item state.

WHEN archiving fails after successful transcription
THE SYSTEM SHALL keep the transcription successful and record the best available source path.

### R-015 Hermes outbound event

WHEN Hermes webhook is enabled and transcription succeeds
THE SYSTEM SHALL emit an audio.transcribed event to the configured Hermes webhook URL.

The event SHALL use version 1.1.

The event SHALL include source voxnote.

The event SHALL include routing_hint.

The event SHALL include transcript.raw.

The event SHALL include transcript.segments when available.

The event SHALL include audio.note_path when transcript.md exists.

The event SHALL include audio.source_path when source archive path is known.

The event SHALL include project id and name when known.

The event SHALL include provider, language and created_at metadata.

The event SHALL NOT include audio bytes.

### R-016 Hermes webhook signing and idempotency

WHEN sending the Hermes webhook
THE SYSTEM SHALL serialize the JSON body deterministically before signing.

THE SYSTEM SHALL sign the exact body bytes with HMAC-SHA256.

THE SYSTEM SHALL include X-Webhook-Signature.

THE SYSTEM SHALL include a deterministic X-Request-ID derived from the body hash.

THE SYSTEM SHALL NOT reveal the webhook secret in logs, UI, result objects or docs examples.

### R-017 Best-effort nudge

WHEN webhook delivery fails because Hermes is offline, unavailable or returns non-2xx
THE SYSTEM SHALL keep the transcription successful.

WHEN webhook delivery fails
THE SYSTEM SHOULD log or display delivery failure as a nudge failure, not as a transcription failure.

WHEN webhook is disabled
THE SYSTEM SHALL still create transcript.md.

### R-018 Hermes downstream ownership

WHEN VoxNote runs the Hermes-native queue
THE SYSTEM SHALL NOT generate protocol.md.

WHEN VoxNote runs the Hermes-native queue
THE SYSTEM SHALL NOT generate tasks.md.

WHEN downstream action is needed
THE SYSTEM SHALL leave protocol, task extraction, approval and tracker sends to Hermes.

Standalone manual extract-tasks, protocol and send commands MAY remain available for non-Hermes use and deliberate MCP use.

### R-018A Long transcript downstream boundary

WHEN transcript.md is too long for a single downstream LLM pass
THE SYSTEM SHALL preserve the full transcript.md as the source of truth and hand off note_path to Hermes.

VoxNote SHALL NOT destructively summarize a long transcript or replace raw transcript content with a short summary.

Hermes SHOULD process long transcripts through a staged workflow such as transcript.md → chunks or sections → meeting map → decisions → tasks → protocol.

### R-019 Hermes inbound MCP

WHEN Hermes needs deliberate pull-based access to VoxNote
THE SYSTEM SHOULD expose MCP tools from cli.mcp_server.

MCP tool arguments SHALL NOT include API keys or secrets.

Provider and OpenRouter keys SHALL be resolved server-side through env or config.

MCP tools MAY include transcribe_audio, extract_tasks, generate_protocol, list_containers and send_tasks.

### R-020 Meetings view and Hermes badges

WHEN a meeting folder contains transcript.md
THE SYSTEM SHALL show the meeting as done from VoxNote perspective.

WHEN a meeting folder contains protocol.md
THE SYSTEM SHALL show a Hermes protocol badge or equivalent progress indicator.

WHEN a meeting folder contains tasks.md
THE SYSTEM SHALL show a Hermes tasks badge or equivalent progress indicator.

Hermes badges SHALL NOT become VoxNote queue stages.

### R-021 Error handling

WHEN one queue item fails
THE SYSTEM SHALL mark that item as error and continue allowing the daemon to process future items.

WHEN retry is available
THE SYSTEM SHALL make retry explicit.

WHEN the operator deletes or forgets a failed item
THE SYSTEM SHALL NOT delete unrelated meeting or source artifacts unless a later explicit action does so.

### R-022 GBrain and Obsidian recall

WHEN transcript.md exists in the vault
THE SYSTEM SHALL allow GBrain to index it through normal Markdown import.

WHEN Hermes downstream writes protocol.md or tasks.md
THE SYSTEM SHOULD keep them in the same meeting folder so GBrain can connect transcript, protocol and tasks.

### R-023 Security boundary for transcript content

WHEN Hermes processes transcript.raw or transcript.md
THE SYSTEM SHALL treat transcript content as untrusted meeting data.

Hermes SHALL NOT follow instructions embedded in transcript content.

Hermes SHALL extract summaries, tasks, decisions and ideas only within the route policy.

Hermes SHALL NOT reveal secrets, memory, env vars or credentials because transcript text asks for them.

### R-024 Configuration safety

WHEN configuring Hermes webhook or MCP
THE SYSTEM SHALL keep secrets outside Git.

WHEN documenting setup
THE SYSTEM SHALL use placeholders such as REDACTED rather than real tokens.

WHEN committing repository or vault files
THE SYSTEM SHALL pass a high-signal secret scan.

### R-025 Release verification

WHEN V1 integration is declared ready
THE SYSTEM SHALL pass full pytest and ruff for changed code.

WHEN repo code changes are made
THE SYSTEM SHALL verify relevant targeted tests for processing, Hermes webhook, MCP and skill behavior.

WHEN operational Hermes configuration is changed
THE SYSTEM SHALL verify with hermes mcp list, hermes skills list, hermes webhook list or equivalent commands.

WHEN a real audio smoke test is run
THE SYSTEM SHALL use explicit operator approval for API cost and sensitive content.

## Non-goals

THE SYSTEM SHALL NOT turn VoxNote into standalone SaaS in V1.

THE SYSTEM SHALL NOT move Hermes orchestration responsibilities into VoxNote.

THE SYSTEM SHALL NOT store raw audio in the Obsidian vault.

THE SYSTEM SHALL NOT auto-send tracker tasks from the Hermes-native queue.

THE SYSTEM SHALL NOT auto-retry expensive long transcription jobs.

THE SYSTEM SHALL NOT require Telegram as the default long-recording path.

## Acceptance summary

A V1 build satisfies this requirements file when it can process at least one chosen desktop file and one Drive inbox file into transcript.md, archive raw audio outside the vault, preserve the Hermes downstream boundary, expose or document Hermes activation, and pass the relevant automated checks without leaking secrets.
