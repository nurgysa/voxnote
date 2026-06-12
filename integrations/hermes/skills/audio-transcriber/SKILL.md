---
name: audio-transcriber
description: Transcribe meeting audio (Kazakh/Russian/English incl. code-switching), extract action items, generate a 5-block Russian meeting protocol (MoM), and push tasks to Linear / Trello / Glide. Use when the user has an audio recording (mp3/wav/m4a) of a meeting, call, or interview and wants a transcript, tasks, a protocol, or those tasks sent to a task tracker.
version: 0.1.0
author: audio-transcriber
license: see repository
metadata:
  hermes:
    tags: [transcription, meetings, speech-to-text, tasks, protocol]
    category: productivity
required_environment_variables:
  - name: AUDIO_TRANSCRIBER_API_KEY
    prompt: "Cloud STT provider API key (default provider: AssemblyAI)"
    help: "Speech-to-text key. Default provider is AssemblyAI; set AUDIO_TRANSCRIBER_PROVIDER to Deepgram/Gladia/Speechmatics to use another (then this is that provider's key)."
    required_for: [transcribe_audio]
  - name: AUDIO_TRANSCRIBER_OPENROUTER_API_KEY
    prompt: "OpenRouter API key (task extraction + protocol)"
    help: "Key from openrouter.ai. Used by extract_tasks and generate_protocol."
    required_for: [extract_tasks, generate_protocol]
---

# Audio Transcriber

Turn a meeting recording into a transcript, action items, and a shareable
protocol — and optionally push the tasks into a task tracker.

## When to Use

Activate when the user:
- has an audio file (mp3 / wav / m4a) of a meeting, call, or interview and wants it transcribed;
- asks for the **action items / tasks** from a recording or an existing transcript;
- asks for a **meeting protocol / minutes (MoM)** — this tool produces a 5-block Russian protocol;
- wants extracted tasks **sent to Linear, Trello, or Glide**.

Speech may be Kazakh / Russian / English, including code-switching — pass language `mixed`.

## Prerequisites

The `audio-transcriber` project (Python) must be on this machine. Two ways to call it:

- **Preferred — MCP server.** If its MCP server is registered (Hermes config
  `mcp_servers.audio-transcriber`), the tools below appear in your tool list and
  resolve API keys server-side.
- **Fallback — CLI.** Run `python -m cli ...` from the project directory (`REPO`,
  the path where `audio-transcriber` is checked out).

Required env vars: `AUDIO_TRANSCRIBER_API_KEY`, `AUDIO_TRANSCRIBER_OPENROUTER_API_KEY`
(see frontmatter; the MCP registration can supply them via its `env` block).

## Tools / Commands

**MCP tools (preferred — typed, secrets resolved server-side):**
- `transcribe_audio(audio_path, language?, provider?, diarize?, hotwords?, denoise?)` → `{text, language, provider, diarized, segments}`
- `extract_tasks(transcript, language?, model?, backend?, container_id?)` → `{tasks, corrections, model}`
- `generate_protocol(transcript, language?, model?, speakers?, meeting_date?)` → `{markdown}`
- `list_containers(backend)` → `[{id, label}]`  (backend = `linear` | `glide` | `trello`)
- `send_tasks(tasks, backend, container_id, retry_failed?)` → per-task results

**CLI fallback (run inside `REPO`):**
```bash
python -m cli pipeline <audio> --provider AssemblyAI --language mixed --json
python -m cli transcribe <audio> --json
echo "<transcript>" | python -m cli extract-tasks --stdin --json
python -m cli list-containers --backend trello --json
```

## Procedure

1. Identify the audio file path the user means; confirm if ambiguous.
2. Transcribe → `transcribe_audio(audio_path, language="mixed")` (or a specific
   code `ru`/`kk`/`en`, or omit for auto-detect). Keep the returned `text`.
3. If the user wants tasks → `extract_tasks(text)`. If they want a protocol →
   `generate_protocol(text)`. (To do transcribe + tasks + protocol in one shot via
   CLI, use `pipeline`.)
4. To send tasks → first `list_containers(backend)` to obtain a `container_id`,
   then `send_tasks(tasks, backend, container_id)`.
5. Present the transcript / tasks / protocol. The protocol markdown is Russian by design.

## Pitfalls

- **Missing keys** → the tool/CLI fails (CLI exit code 3). Ensure the env vars above
  are set; the STT key must match the active provider.
- **Mixed-language meetings** → pass `language="mixed"` (KZ+RU+EN), not a single code.
- **`send_tasks` needs a real `container_id`** — always `list_containers` first; never guess it.
- **CLI output contract** — stdout carries the result (use `--json` to parse); status
  goes to stderr; non-zero exit codes mean 3 = config, 4 = transcribe, 5 = LLM, 6 = backend.
- **Never echo secrets** — keys come from env/config, not from tool arguments or logs.

## Verification

- Success: the tool returns the expected object (`text` / `tasks` / `markdown`), or
  the CLI exits 0 with parseable JSON.
- Failure: a non-zero CLI exit code (see Pitfalls) or an error object — surface the
  message to the user and re-check the relevant key/argument.

## Event mode (outbound webhook)

Beyond MCP-pull, the app can **push** events to Hermes. After each successful
transcription the app POSTs an `audio.transcribed` event to:

```text
http://localhost:8644/webhooks/audio-transcribed
```

The event carries the full transcript text, speaker segments, provider / language
metadata, and the local history-folder path. It is HMAC-SHA256 signed via the
`X-Webhook-Signature` header. Delivery is best-effort — transcription always
succeeds even if Hermes is unreachable.

Enable by setting `hermes_webhook_enabled: true` (and a shared secret) in
`~/.audio-transcriber/config.json` or via the
`AUDIO_TRANSCRIBER_HERMES_WEBHOOK_*` env vars.

For Hermes-side setup (gateway config, `hermes webhook subscribe`, health check,
and the full config/env reference) see **`AGENTS.md §4`** in the repo.
