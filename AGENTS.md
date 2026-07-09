# AGENTS.md — using voxnote from a coding agent

This repo ships a **headless transcription pipeline** you can drive two ways:

1. **As a CLI** — run shell commands (`python -m cli ...`). Works in any agent
   that has a terminal/shell tool (Hermes, Codex, Claude Code, Antigravity).
2. **As an MCP server** — typed tools over stdio (`python -m cli.mcp_server`).
   Registration snippets for all four agents are below.

Pipeline (full chain, available to CLI/MCP callers): **transcribe → extract
tasks → generate protocol → send to a task backend** (Linear / Glide / Trello).
Cloud STT (AssemblyAI / Deepgram / Gladia / Speechmatics); KZ+RU+EN
code-switching. OpenRouter-backed task/protocol commands are manual/legacy
operator tools, not the Mini-AGI production downstream path.

> **Desktop queue (Mini-AGI / Hermes-native flow):** VoxNote's own processing
> queue runs **transcribe-only** — it writes a diarized `transcript.md` into the
> Obsidian vault, archives the audio under Google Drive
> `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/`, and fires a
> best-effort `audio.transcribed` nudge (§4). **Hermes** then owns the
> downstream queue: transcript interpretation, protocol/tasks drafts, approval,
> tracker sends, and GBrain enrichment. The `extract-tasks` / `protocol` /
> `send` / `process-meeting` commands below remain available for manual or
> agent-driven standalone use — they are not what the desktop auto-pipeline runs
> and not the preferred Mini-AGI production path.

For repo *development* conventions (invariants, test/lint contract, module map)
see **`CLAUDE.md`** — this file is only about *consuming the tool*.

---

## 1. CLI (shell)

Invoke `python -m cli <command>` from the repo root. Structured output with
`--json`; errors go to stderr with non-zero exit codes (0 ok, 2 usage, 3 config,
4 transcribe, 5 LLM, 6 backend, 130 cancelled).

| Command | Purpose |
|---|---|
| `transcribe <audio> [--provider --language ru\|kk\|en\|mixed\|auto --diarize --hotwords --denoise --json --save]` | Audio → transcript |
| `extract-tasks (--transcript F \| --stdin) [--backend --container-id --model --json]` | Transcript → tasks |
| `protocol (--transcript F \| --stdin) [--model --speakers --meeting-date --json]` | Transcript → 5-block MoM |
| `process-meeting --note-path F [--model --write --json]` | Saved VoxNote `transcript.md` → review-only `protocol.md` / `tasks.md` drafts |
| `list-containers --backend linear\|glide\|trello [--json]` | Discover a `--container-id` |
| `send --backend X --container-id Y (--tasks F \| --stdin) [--retry-failed --json]` | Tasks → backend |
| `pipeline <audio> [--backend --container-id --send ...]` | All of the above, one JSON object |

Examples:

```bash
# one-shot: audio → transcript + tasks + protocol (+ optional send)
python -m cli pipeline meeting.m4a --provider AssemblyAI --language mixed \
       --send --backend trello --container-id <listId>

# piping between steps
python -m cli transcribe meeting.m4a --json
echo "<transcript text>" | python -m cli extract-tasks --stdin --backend trello --json

# Mini-AGI / Hermes downstream: saved transcript → local review drafts
python -m cli process-meeting --note-path "path/to/transcript.md" --json
python -m cli process-meeting --note-path "path/to/transcript.md" --write --json
```

## 2. MCP server (typed tools)

```bash
pip install -r requirements-mcp.txt        # one-time: installs `mcp`
python -m cli.mcp_server                    # speaks JSON-RPC over stdio
```

Tools exposed: `transcribe_audio`, `extract_tasks`, `generate_protocol`,
`list_containers`, `send_tasks`. Tool arguments are the *what* (audio path,
language, backend); **secrets are never tool arguments** — the server reads them
from env / `config.json` (see §3).

### Registration per agent

Run from the **repo root** (`cwd`) so the `cli` package + pipeline modules import;
use the Python interpreter that has `requirements-mcp.txt` installed.

**Claude Code** — `.mcp.json` (repo root) or `claude mcp add`:

```json
{
  "mcpServers": {
    "voxnote": {
      "command": "python",
      "args": ["-m", "cli.mcp_server"],
      "cwd": "/path/to/voxnote",
      "env": { "VOXNOTE_ASSEMBLYAI_API_KEY": "…", "VOXNOTE_OPENROUTER_API_KEY": "…" }
    }
  }
}
```

**OpenAI Codex CLI** — `~/.codex/config.toml`:

```toml
[mcp_servers.voxnote]
command = "python"
args = ["-m", "cli.mcp_server"]
cwd = "/path/to/voxnote"
env = { VOXNOTE_ASSEMBLYAI_API_KEY = "…", VOXNOTE_OPENROUTER_API_KEY = "…" }
```

**Hermes Agent** — `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  voxnote:
    command: python
    args: ["-m", "cli.mcp_server"]
    cwd: /path/to/voxnote
    env:
      VOXNOTE_ASSEMBLYAI_API_KEY: "…"
      VOXNOTE_OPENROUTER_API_KEY: "…"
```

**Google Antigravity** — `mcp_config.json`:

```json
{
  "mcpServers": {
    "voxnote": {
      "command": "python",
      "args": ["-m", "cli.mcp_server"],
      "cwd": "/path/to/voxnote",
      "env": { "VOXNOTE_ASSEMBLYAI_API_KEY": "…", "VOXNOTE_OPENROUTER_API_KEY": "…" }
    }
  }
}
```

### Hermes — native skill (it does not read AGENTS.md)

Hermes discovers capabilities via **skills**, not AGENTS.md. A ready skill lives at
`integrations/hermes/skills/voxnote/`. Install it — it auto-registers as
the `/voxnote` slash command and shows in the Hermes Desktop **Skills**
pane (same `~/.hermes` config across CLI / TUI / Desktop / Gateway):

```bash
# macOS / Linux
cp -r integrations/hermes/skills/voxnote ~/.hermes/skills/productivity/
# Windows (PowerShell)
Copy-Item -Recurse integrations\hermes\skills\voxnote "$env:USERPROFILE\.hermes\skills\productivity\"
```

The skill is MCP-first (uses the tools above) with a `python -m cli` fallback, and
declares its required env vars so Hermes prompts for them on first use.

## 3. Secrets & config

Resolution precedence for STT keys (CLI and MCP): **flag (CLI only) > provider-specific env > legacy env > `config.json`**.
The agent host often has no `config.json`, so pass secrets via env:

| Env var | Used for |
|---|---|
| `VOXNOTE_ASSEMBLYAI_API_KEY` / `_GLADIA_API_KEY` / `_DEEPGRAM_API_KEY` / `_SPEECHMATICS_API_KEY` | Preferred STT provider-specific keys |
| `VOXNOTE_API_KEY` | Legacy fallback STT key for the active provider |
| `VOXNOTE_PROVIDER` | Default STT provider (else `AssemblyAI`) |
| `VOXNOTE_OPENROUTER_API_KEY` | OpenRouter (tasks + protocol) |
| `VOXNOTE_LINEAR_API_KEY` / `_TRELLO_API_KEY` / `_TRELLO_TOKEN` / `_GLIDE_API_KEY` | Task backends |

The MCP server speaks JSON-RPC on stdout — never print to it. Transcription
progress is discarded; diagnostics go to `logs/faulthandler-mcp.log`.

## 4. Outbound webhook — `audio.transcribed` event

In addition to the MCP-pull mode above, the app can **push** a signed
`audio.transcribed` event to Hermes after every successful transcription.
This is the second integration direction:

```text
VoxNote  →  POST /webhooks/audio-transcribed  →  Hermes Agent
```

### 4.1 Event payload shape

```json
{
  "event_type": "audio.transcribed",
  "version": "1.1",
  "source": "voxnote",
  "routing_hint": "obsidian_inbox",
  "audio": {
    "filename": "meeting.m4a",
    "path": "C:/Users/.../meeting.m4a",
    "history_folder": "C:/Users/.../<meeting-folder>",
    "note_path": "C:/Users/.../30 Meetings/<project>/<meeting>/transcript.md",
    "source_path": "G:/My Drive/.../Sources/Audio/VoxNote/Meetings/2026-06-14/2026-06-14_1000_meeting.m4a"
  },
  "project": { "id": "p1", "name": "Kitng" },
  "transcript": {
    "raw": "<full transcript text>",
    "segments": []
  },
  "analysis": {
    "summary": null,
    "tasks": [],
    "ideas": [],
    "decisions": [],
    "protocol": null
  },
  "meta": {
    "provider": "AssemblyAI",
    "language": "ru",
    "created_at": "2026-06-11T12:00:00Z"
  }
}
```

Key fields for Hermes routing: `event_type`, `routing_hint`,
`transcript.raw`, `meta.provider`, `meta.language`, `audio.history_folder`,
`audio.note_path` (the vault `transcript.md`), `audio.source_path` (the archived
audio under Drive `Sources/Audio/VoxNote/Meetings/YYYY-MM-DD/`), and `project` (`{id, name}`, or `null` outside a
queue run).

### 4.2 Config / env reference

| config.json key | Env var | Default | Notes |
|---|---|---|---|
| `hermes_webhook_enabled` | `VOXNOTE_HERMES_WEBHOOK_ENABLED` | `false` | Enable sending |
| `hermes_webhook_url` | `VOXNOTE_HERMES_WEBHOOK_URL` | `http://localhost:8644/webhooks/audio-transcribed` | Hermes endpoint |
| `hermes_webhook_secret` | `VOXNOTE_HERMES_WEBHOOK_SECRET` | `""` | HMAC shared secret |
| `hermes_webhook_timeout_seconds` | `VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS` | `10` | Request timeout |
| `hermes_webhook_routing_hint` | `VOXNOTE_HERMES_WEBHOOK_ROUTING_HINT` | `obsidian_inbox` | Routing target hint |

**Empty-env-string semantics:** an env var set to an empty string (`=""`) is
treated as *unset* and the config.json value is used instead. This matches
the behaviour of the other `VOXNOTE_*` vars throughout the project.

Boolean env vars accept (case-insensitive): `true`, `1`, `yes`, `on` → enabled.
Everything else is false. The secret is consumed only for HMAC signing and
is never logged or included in error messages.

### 4.3 Delivery contract

- **Best-effort:** if Hermes is unreachable or returns non-2xx, the
  transcription still succeeds — webhook failure is logged as a WARNING only.
- **HMAC signing:** `X-Webhook-Signature: <hex HMAC-SHA256 over exact body bytes>`
  The body bytes are built once and the same bytes are used for both signing
  and the POST body (deterministic JSON: sorted keys, compact separators,
  UTF-8).
- **Idempotency:** `X-Request-ID: voxnote:<sha256(body)[:24]>` —
  deterministic per unique payload, safe for Hermes to deduplicate retries.
- **No audio bytes** are sent — only metadata, transcript text, and paths.

### 4.4 Hermes-side setup

**Step 1 — Enable the Hermes webhook platform:**

```bash
hermes gateway setup
```

Or add manually to `~/.hermes/config.yaml`:

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "[REDACTED]"
```

Then restart:

```bash
hermes gateway restart
```

Health check:

```bash
curl http://localhost:8644/health
```

**Step 2 — Subscribe the route:**

```bash
hermes webhook subscribe audio-transcribed \
  --events "audio.transcribed" \
  --skills "personal-ai-brain-stack" \
  --deliver telegram \
  --prompt "VoxNote event received.

Source: {source}
File: {audio.filename}
Provider: {meta.provider}
Language: {meta.language}

Transcript:
{transcript.raw}

Route into Nurgisa Brain Stack:
1. classify as task/idea/note/meeting,
2. save useful content to Obsidian Inbox or the relevant project note,
3. prepare concise next actions,
4. queue or trigger GBrain sync if useful."
```

The app sends to: `http://localhost:8644/webhooks/audio-transcribed`

Use the **same secret** in `hermes_webhook_secret` (config or env) and in the
Hermes webhook platform config — never commit it.

### 4.5 Docs-only curl smoke example

Manually verify Hermes accepts the event shape (substitute `[REDACTED]`
with your actual secret, never commit it):

```bash
BODY='{"analysis":{"decisions":[],"ideas":[],"protocol":null,"summary":null,"tasks":[]},"audio":{"filename":"test.m4a","history_folder":null,"note_path":null,"path":null,"source_path":null},"event_type":"audio.transcribed","meta":{"created_at":"2026-06-11T12:00:00Z","language":"ru","provider":"test"},"project":null,"routing_hint":"obsidian_inbox","source":"voxnote","transcript":{"raw":"test","segments":[]},"version":"1.1"}'
SIG=$(BODY="$BODY" SECRET="[REDACTED]" python - <<'PY'
import hmac, hashlib, os
body = os.environ["BODY"].encode("utf-8")
secret = os.environ["SECRET"].encode("utf-8")
print(hmac.new(secret, body, hashlib.sha256).hexdigest())
PY
)
curl -X POST http://localhost:8644/webhooks/audio-transcribed \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: $SIG" \
  -H "X-Request-ID: manual-test-1" \
  --data "$BODY"
```

Note: the JSON body must use **sorted keys** (the app uses `sort_keys=True`).
The `BODY` above is pre-sorted for the smoke test.
