# VoxNote

[![CI](https://github.com/nurgysa/voxnote/actions/workflows/tests.yml/badge.svg)](https://github.com/nurgysa/voxnote/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/nurgysa/voxnote)](https://github.com/nurgysa/voxnote/releases/latest)
[![License: MIT](https://img.shields.io/github/license/nurgysa/voxnote)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](docs/CLIENT_SETUP.md)

VoxNote is a Windows desktop app for cloud speech-to-text, speaker diarization,
and durable meeting transcript management. It is cloud-only: transcription and
diarization run through managed HTTPS APIs, so no GPU or local ML stack is
required.

The app is built for Kazakh, Russian, and English code-switching meetings. Its
core Mini-AGI use case is realistic 1-3 hour meetings, calls, consultations, and
project discussions that produce durable `transcript.md` artifacts for later
Hermes / Mini-AGI processing.

> **Cloud-only since 2026-05-28.** The local faster-whisper / pyannote / CUDA /
> torch stack was removed from the codebase. If you need the old GPU version,
> inspect the git history before that rip-out; it is no longer supported.

## Download

Ready-to-run Windows app, no Python required:

**[Releases -> VoxNote-vX.Y.Z.zip](https://github.com/nurgysa/voxnote/releases/latest)**

Unzip it into a user-writable folder, run `VoxNote.exe`, and follow the first-run
setup guide: [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

The rest of this README is for developers and technical operators.

## Place in the Mini-AGI ecosystem

VoxNote is part of **Mini-AGI**, a personal AI operating system for knowledge,
tasks, projects, documents, and digital agents. In that system VoxNote owns the
voice/audio/transcription intake layer. It is designed as a Hermes-native app and
service, not as the downstream reasoning orchestrator.

The central layer is **Hermes Desktop**: the orchestrator for tools, skills,
memory, gateways, cron jobs, and agent processes. VoxNote connects to Hermes in
two directions:

- inbound MCP tools for deliberate Hermes -> VoxNote calls;
- outbound `audio.transcribed` webhook nudges after successful transcription.

| Component | Role in Mini-AGI |
|---|---|
| **Hermes Desktop** | Orchestrator for tools, skills, memory, gateways, cron, and agent processes |
| **VoxNote** | Voice, audio, and transcription intake |
| **Telegram** | Fast phone capture channel for ideas, tasks, voice notes, and requests |
| **Obsidian** | Human-readable Markdown knowledge base and source of truth |
| **GBrain** | Semantic memory, search, note links, and knowledge synthesis |
| **GitHub** | Versioning, review, change history, and safe checkpoints |
| **Google Drive** | Cloud storage, file sync, and document layer |
| **Linear** | Human-facing task board |
| **Hermes Kanban** | Internal queue for agentic execution |
| **Codex** | Primary AI execution agent for coding, file, and Obsidian workflows |

Mini-AGI is not a fixed closed toolkit. It grows through new services, apps,
agents, integrations, and business verticals as real workflows appear.

## Queue boundary

VoxNote owns the **transcription queue** only:

```text
audio file / recording / inbox
→ VoxNote queue
→ provider preflight
→ cloud STT + diarization
→ transcript.md + segments/speakers/source_path
→ audio.transcribed nudge
```

The queue ends at `transcript.md`. It does not own protocol generation, task
creation, tracker sends, GBrain enrichment, or long-meeting reasoning. Those are
Hermes / Mini-AGI responsibilities.

## Features

- **Cloud transcription:** AssemblyAI, Deepgram, Gladia, and Speechmatics.
- **Speaker diarization:** provider-level `Speaker A/B/...` labels, with manual
  naming or directory grounding where available.
- **Kazakh + Russian + English code-switching:** AssemblyAI Universal handles
  language switches inside one recording.
- **Manual/legacy LLM commands:** OpenRouter-backed `extract-tasks`, `protocol`,
  `pipeline`, and `process-meeting` remain available for standalone/operator
  use, but they are not the Mini-AGI production downstream path.
- **Long-meeting downstream drafts:** `process-meeting` turns a saved
  `transcript.md` into review-only `protocol.md` (5-block meeting-minutes format)
  and `tasks.md` drafts.
- **Document attachments:** attached PDF/DOCX/PPTX/XLSX files can be converted to
  Markdown with Microsoft markitdown and used as LLM context.
- **Microphone recording** and built-in **Audio Cutter**.
- **Meeting history** with search and project-based folders.
- **Headless CLI + MCP server** for agentic workflows.
- **Exports:** TXT, SRT, VTT, and Markdown.

## System requirements

| Component | Requirement |
|---|---|
| OS | Windows 10 64-bit or Windows 11 |
| Python, for development only | 3.12.x |
| ffmpeg, for development only | available in `PATH`; bundled inside the `.exe` release |
| Network | required; cloud APIs are used and offline transcription is not supported |
| GPU | not required |

## Install for development

### 1. Python 3.12

Download from [python.org](https://www.python.org/downloads/) and enable
**Add Python to PATH** during installation.

### 2. ffmpeg

Download a release build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/),
unpack it, and add `bin/` to `PATH`. Verify with:

```bash
ffmpeg -version
```

In the packaged `.exe`, ffmpeg is already bundled under `vendor/ffmpeg/`.

### 3. Dependencies

```bash
pip install -r requirements.txt
```

The versions in `requirements.txt` are intentionally pinned. These pins are
load-bearing on Windows, especially around CustomTkinter, soundfile, sounddevice,
and google-auth. Do not relax them without a clean Windows 10 and Windows 11
smoke test.

## API keys

All transcription and LLM work is cloud-based. Keys are entered in the app's
Settings screen and stored in `~/.voxnote/config.json`, which must not be
committed.

| Service | Purpose | Where to get it |
|---|---|---|
| **AssemblyAI** | transcription + diarization | <https://www.assemblyai.com> |
| **OpenRouter** | optional manual/legacy LLM commands, not required for Mini-AGI Hermes-native downstream | <https://openrouter.ai/keys> |
| Linear / Trello / Glide | optional task delivery | each service's settings/API page |

A secret-free template is available at [`config.example.json`](config.example.json).

## Cloud transcription providers

Pricing and model behavior can change. Treat the table below as orientation only
and verify the current terms on each provider's official pricing page.

| Provider | Approximate use | Kazakh support | Signup |
|---|---|---|---|
| AssemblyAI | default provider for code-switching meetings | yes, Universal | <https://www.assemblyai.com> |
| Deepgram | RU/EN transcription where supported | no Kazakh in current VoxNote assumptions | <https://console.deepgram.com> |
| Gladia | cloud Whisper-style transcription + diarization | yes | <https://app.gladia.io> |
| Speechmatics | premium transcription and diarization | yes | <https://portal.speechmatics.com> |

Audio is uploaded to the selected provider. Do not use VoxNote for recordings
that are not allowed to leave your machine or organization.

To add a provider, implement one class under [`providers/base.py`](providers/base.py)
and register it in [`providers/__init__.py`](providers/__init__.py).

## Run

```bash
python app.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
python -m ruff check .
```

Tests are mostly pure-function and mocked-HTTP checks. They do not require a GPU,
API keys, or network access.

## Build the `.exe`

```powershell
.\scripts\build_exe.ps1
python scripts\package_release.py --version X.Y.Z
```

`package_release.py` creates a Windows release zip with Python `zipfile`, verifies
forward-slash archive names, checks that secrets and local state are absent,
checks that markitdown is present, and verifies the bundled ffmpeg license file.
See [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md) for the end-user setup flow.

## Troubleshooting

| Symptom | What to try |
|---|---|
| `401 Unauthorized` during transcription | The provider key is missing, expired, or wrong. Re-check it in Settings. |
| `Insufficient credits` | Top up the selected transcription provider or OpenRouter account. |
| `ffmpeg: command not found`, development mode | ffmpeg is not in `PATH`; see the development setup section. |
| Empty or broken Kazakh transcript | Try Gladia or Speechmatics, or retry with cleaner audio. |
| `.exe` does not start or Windows Defender blocks it | Add the unpacked VoxNote folder to Windows Defender exclusions. |

Full end-user setup guide: [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

## Hermes Agent integration

VoxNote supports two integration directions with Hermes Agent:

| Mode | Direction | Description |
|---|---|---|
| **MCP, inbound** | Hermes -> VoxNote | Hermes calls tools such as `transcribe_audio` or `extract_tasks`. |
| **Webhook, outbound** | VoxNote -> Hermes | VoxNote emits `audio.transcribed` after successful transcription. |

### Outbound webhook, disabled by default

After transcription, VoxNote can POST an `audio.transcribed` JSON payload to a
Hermes endpoint. The payload can include transcript text, provider metadata,
language, speaker segments, and meeting artifact paths such as `audio.note_path`
and `audio.source_path`. The request is signed with HMAC-SHA256 in
`X-Webhook-Signature`; Hermes validates the signature.

Delivery is best-effort: if Hermes is unavailable, transcription still succeeds.

### Long meeting downstream

Mini-AGI production downstream is Hermes-native: Hermes reads `audio.note_path`,
performs staged reasoning with its own model/context, owns the downstream queue,
and writes or sends only after human approval.

For a saved VoxNote meeting transcript, `process-meeting` remains an optional CLI
fallback/reference for standalone operators — it generates review-only downstream
drafts:

```bash
python -m cli process-meeting --note-path "path/to/transcript.md" --json
python -m cli process-meeting --note-path "path/to/transcript.md" --write --json
```

`--write` creates `protocol.md` and `tasks.md` next to `transcript.md`. It does
not send tracker tasks.

Configuration via `~/.voxnote/config.json` or environment variables:

| config.json key | Environment variable | Default | Description |
|---|---|---|---|
| `hermes_webhook_enabled` | `VOXNOTE_HERMES_WEBHOOK_ENABLED` | `false` | Enable webhook delivery. |
| `hermes_webhook_url` | `VOXNOTE_HERMES_WEBHOOK_URL` | `http://localhost:8644/webhooks/audio-transcribed` | Hermes endpoint URL. |
| `hermes_webhook_secret` | `VOXNOTE_HERMES_WEBHOOK_SECRET` | `""` | Shared HMAC secret. |
| `hermes_webhook_timeout_seconds` | `VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS` | `10` | Request timeout in seconds. |
| `hermes_webhook_routing_hint` | `VOXNOTE_HERMES_WEBHOOK_ROUTING_HINT` | `obsidian_inbox` | Routing hint for Hermes. |

Empty environment variable values are ignored; non-empty environment variables
override `config.json`.

Do not commit real secrets. Use `config.example.json` as the public template.
For the Hermes-side route, see [`docs/HERMES_MINI_AGI_INTEGRATION.md`](docs/HERMES_MINI_AGI_INTEGRATION.md).

## Architecture

The module map, runtime model, and JSON contracts are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). AI-agent invariants and repo
conventions are documented in [`CLAUDE.md`](CLAUDE.md).

## License

MIT. See [LICENSE](LICENSE). Third-party components, including the bundled GPLv3
ffmpeg build invoked as a separate process, are listed in
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

## Acknowledgments

- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) - UI toolkit, MIT.
- [FFmpeg](https://ffmpeg.org/) - audio processing, GPLv3 build invoked as a separate process.
- [markitdown](https://github.com/microsoft/markitdown) - document-to-Markdown conversion, MIT.
- AssemblyAI, Deepgram, Gladia, and Speechmatics - cloud STT APIs.
- [OpenRouter](https://openrouter.ai/) - LLM routing for optional manual/legacy commands.
