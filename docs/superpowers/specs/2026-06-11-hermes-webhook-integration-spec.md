# Audio Transcriber → Hermes Agent Outbound Webhook Integration V1 Implementation Plan

> **For Claude Desktop / Claude Code:** Use this document as the implementation spec. Work task-by-task. Do not rewrite the application. Do not push to GitHub unless Nurgisa explicitly asks.

**Goal:** Make `audio-transcriber` a Hermes Agent / Nurgisa Brain Stack event source by emitting a signed `audio.transcribed` webhook after successful transcription.

**Architecture:** Keep the existing Python desktop app, CLI, MCP server, cloud STT providers, and tests. Add a small outbound Hermes adapter under `integrations/hermes/`: one pure payload builder, one best-effort webhook client, config/env resolution, and minimal CLI/UI wiring after transcription succeeds.

**Tech Stack:** Python 3.11+, stdlib `json` / `hmac` / `hashlib` / `dataclasses` / `pathlib` / `datetime`, existing `requests` dependency, existing `pytest` + `uv` test workflow, Hermes generic webhook adapter.

---

## 0. Repository Context

Repository:

```text
C:\Users\nurgisa\audio-transcriber
/c/Users/nurgisa/audio-transcriber
https://github.com/nurgysa/audio-transcriber
```

Current known state from audit:

```text
branch: main
latest commit: e10a063 refactor(providers): lift identical helpers to _common (dedup PR-1) (#137)
working tree before spec creation: clean
tracked_files=274
python_files=193
test_files=105
docs_plans_specs=52
```

Existing Hermes-compatible surfaces:

1. **Headless CLI**
   - Entry: `python -m cli ...`
   - Important commands:
     - `transcribe <audio> --json`
     - `extract-tasks --stdin --json`
     - `protocol --stdin --json`
     - `pipeline <audio> ...`

2. **MCP stdio server**
   - Entry: `python -m cli.mcp_server`
   - Tools include:
     - `transcribe_audio`
     - `extract_tasks`
     - `generate_protocol`
     - `list_containers`
     - `send_tasks`

3. **Hermes skill artifact**
   - Path: `integrations/hermes/skills/audio-transcriber/SKILL.md`
   - MCP-first with CLI fallback.

4. **Security guard**
   - `cli/_paths.py` blocks reads inside private audio-transcriber config paths to avoid token/config exfiltration.

This V1 should add the missing direction:

```text
Audio Transcriber
  → transcript/result
  → POST http://localhost:8644/webhooks/audio-transcribed
  → Hermes Agent webhook route
  → Obsidian Inbox / GBrain / Telegram workflow
```

---

## 1. Non-Goals

Do **not** do any of these in V1:

- Do not migrate GUI to React, Tauri, Electron, web UI, or another framework.
- Do not move transcription logic into Hermes.
- Do not add a SaaS backend.
- Do not add a queue system.
- Do not add local Whisper, CUDA, pyannote, or other heavy ML dependencies.
- Do not rewrite the existing MCP server.
- Do not change provider auth flows.
- Do not commit or expose secrets.
- Do not edit the Hermes Agent core repository.
- Do not edit Obsidian vault files.
- Do not push to GitHub unless explicitly asked.
- Do not do large unrelated refactors.

---

## 2. Security Requirements

1. Never print, commit, or log API keys, webhook secrets, OAuth tokens, provider tokens, or connection strings.
2. Use placeholders like `[REDACTED]` in docs/examples.
3. `config.example.json` must contain empty/example values only.
4. The webhook secret is used only for HMAC signing.
5. Webhook delivery is **best-effort**: if Hermes is down, transcription must still succeed.
6. Do not send audio bytes to Hermes. Send metadata, transcript text, optional segments, and optional history path only.
7. Do not read or expose private config/token files.
8. Do not include secrets in exception messages or returned result objects.
9. Do not make webhook failures visible as user-facing transcription failures.

---

## 3. Hermes Webhook Compatibility Facts

Hermes generic webhook route format:

```text
POST /webhooks/{route_name}
```

For this integration:

```text
POST /webhooks/audio-transcribed
```

Hermes detects event type from payload field:

```json
{
  "event_type": "audio.transcribed"
}
```

Hermes accepts generic HMAC auth with:

```http
X-Webhook-Signature: <hex hmac_sha256(secret, raw_body)>
```

Also send idempotency key:

```http
X-Request-ID: <stable unique id>
```

Important: Hermes validates the signature against the exact raw request body bytes. Build the bytes once and use the same bytes for signing and POST body.

---

## 4. User-Facing Configuration

Use `audio-transcriber` specific names to avoid collision with Hermes gateway config.

### 4.1 Config JSON Keys

Add these keys to `config.example.json`:

```json
{
  "hermes_webhook_enabled": false,
  "hermes_webhook_url": "http://localhost:8644/webhooks/audio-transcribed",
  "hermes_webhook_secret": "",
  "hermes_webhook_timeout_seconds": 10,
  "hermes_webhook_routing_hint": "obsidian_inbox"
}
```

### 4.2 Environment Variable Overrides

Support these env vars:

```bash
AUDIO_TRANSCRIBER_HERMES_WEBHOOK_ENABLED=true
AUDIO_TRANSCRIBER_HERMES_WEBHOOK_URL=http://localhost:8644/webhooks/audio-transcribed
AUDIO_TRANSCRIBER_HERMES_WEBHOOK_SECRET=[REDACTED]
AUDIO_TRANSCRIBER_HERMES_WEBHOOK_TIMEOUT_SECONDS=10
AUDIO_TRANSCRIBER_HERMES_WEBHOOK_ROUTING_HINT=obsidian_inbox
```

Env vars should override config file values.

Boolean parsing should accept the following as true, case-insensitive:

```text
true, 1, yes, on
```

Everything else is false.

Default behavior must be disabled unless explicitly enabled.

---

## 5. Event Payload Schema

Implement a V1 payload builder for this JSON shape:

```json
{
  "event_type": "audio.transcribed",
  "version": "1.0",
  "source": "audio-transcriber",
  "routing_hint": "obsidian_inbox",
  "audio": {
    "filename": "meeting.m4a",
    "path": "C:/Users/nurgisa/...",
    "history_folder": "C:/Users/nurgisa/..."
  },
  "transcript": {
    "raw": "full transcript text",
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

### Required Top-Level Fields

Always include:

- `event_type`
- `version`
- `source`
- `routing_hint`
- `audio`
- `transcript`
- `analysis`
- `meta`

### Field Details

- `event_type`: exactly `"audio.transcribed"`
- `version`: exactly `"1.0"`
- `source`: exactly `"audio-transcriber"`
- `routing_hint`: default `"obsidian_inbox"`
- `audio.filename`: basename of audio path if available, else `null`
- `audio.path`: string path if available, else `null`
- `audio.history_folder`: string path if available, else `null`
- `transcript.raw`: transcript text, never audio bytes
- `transcript.segments`: list, default `[]`
- `analysis.summary`: optional, default `null`
- `analysis.tasks`: list, default `[]`
- `analysis.ideas`: list, default `[]`
- `analysis.decisions`: list, default `[]`
- `analysis.protocol`: optional, default `null`
- `meta.provider`: cloud provider name if available, else `null`
- `meta.language`: language if available, else `null`
- `meta.created_at`: UTC ISO-8601 timestamp ending with `Z`

---

## 6. Files to Inspect First

Before editing, inspect these files:

```text
README.md
AGENTS.md
pyproject.toml
config.example.json
cli/config.py
cli/app.py
cli/core.py
cli/mcp_server.py
ui/app/transcription_mixin.py
tests/test_cli_core.py
tests/test_cli_mcp.py
tests/test_hermes_skill.py
integrations/hermes/skills/audio-transcriber/SKILL.md
```

Understand existing style, config loading, logging, testing patterns, and CLI behavior before writing code.

---

## 7. Files to Create

Create:

```text
integrations/hermes/__init__.py
integrations/hermes/schema.py
integrations/hermes/client.py
tests/test_hermes_webhook_schema.py
tests/test_hermes_webhook_client.py
```

If the repository already has a stronger convention for module placement, follow it, but keep the integration conceptually under `integrations/hermes/`.

---

## 8. Files to Modify

Likely modify:

```text
config.example.json
cli/config.py
cli/app.py
ui/app/transcription_mixin.py
README.md
AGENTS.md
integrations/hermes/skills/audio-transcriber/SKILL.md
```

Only touch additional files if necessary.

---

## 9. Implementation Design

### 9.1 `integrations/hermes/schema.py`

Create a pure payload builder with no network calls.

Suggested public function:

```python
def build_audio_transcribed_event(
    *,
    transcript_text: str,
    audio_path: str | None = None,
    history_folder: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    segments: list | None = None,
    routing_hint: str = "obsidian_inbox",
    summary: str | None = None,
    tasks: list | None = None,
    ideas: list | None = None,
    decisions: list | None = None,
    protocol: str | None = None,
    created_at: str | None = None,
) -> dict:
    ...
```

Requirements:

- Return a JSON-serializable `dict`.
- If `created_at` is not provided, use current timezone-aware UTC time.
- Format timestamp as `YYYY-MM-DDTHH:MM:SSZ`.
- Use `Path(audio_path).name` for filename when `audio_path` exists.
- Do not crash if optional fields are missing.
- Normalize empty `segments`, `tasks`, `ideas`, and `decisions` to `[]`.
- Preserve Unicode transcript text.

### 9.2 `integrations/hermes/client.py`

Create a small best-effort webhook client.

Suggested dataclasses:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class HermesWebhookConfig:
    enabled: bool = False
    url: str = "http://localhost:8644/webhooks/audio-transcribed"
    secret: str = ""
    timeout_seconds: float = 10.0
    routing_hint: str = "obsidian_inbox"


@dataclass(frozen=True)
class HermesWebhookResult:
    enabled: bool
    sent: bool
    status_code: int | None = None
    error: str | None = None
```

Suggested functions:

```python
def sign_body(secret: str, body: bytes) -> str:
    """Return hex HMAC-SHA256 signature for the exact body bytes."""
```

```python
def serialize_payload(payload: dict) -> bytes:
    """Return deterministic UTF-8 JSON bytes used for both POST and HMAC."""
```

Use deterministic JSON:

```python
json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

```python
def post_event(payload: dict, config: HermesWebhookConfig) -> HermesWebhookResult:
    ...
```

Behavior:

- If `config.enabled` is false: do not send; return `enabled=False, sent=False`.
- If enabled but `url` is empty: do not send; return error.
- If enabled but `secret` is empty: do not send; return error.
- Use `requests.post`.
- Send headers:
  - `Content-Type: application/json`
  - `X-Webhook-Signature: <hex hmac>`
  - `X-Request-ID: <stable id>`
- Timeout must use `config.timeout_seconds`.
- Treat HTTP 2xx as success.
- Treat non-2xx as `sent=False` with status code and short error message.
- Catch `requests.RequestException`.
- Never raise network exceptions to caller.
- Never log or return the secret.

`X-Request-ID` can be deterministic enough for idempotency, for example:

```text
audio-transcriber:<sha256(body)[:24]>
```

Also provide convenience function:

```python
def emit_audio_transcribed_event(
    *,
    config: HermesWebhookConfig,
    transcript_text: str,
    audio_path: str | None = None,
    history_folder: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    segments: list | None = None,
    summary: str | None = None,
    tasks: list | None = None,
    ideas: list | None = None,
    decisions: list | None = None,
    protocol: str | None = None,
) -> HermesWebhookResult:
    ...
```

This function should:

1. Build payload with `build_audio_transcribed_event`.
2. POST it with `post_event`.
3. Return `HermesWebhookResult`.

### 9.3 Config Resolution

Inspect `cli/config.py` and follow existing project style.

Add a helper like:

```python
def get_hermes_webhook_config(config: dict | None = None) -> HermesWebhookConfig:
    ...
```

If importing `HermesWebhookConfig` into `cli/config.py` creates cycles, either:

- keep parsing in `integrations/hermes/client.py`, or
- create a small helper in a neutral location that does not create cycles.

Requirements:

- Existing config file values are supported.
- Env vars override config values.
- Missing config means disabled by default.
- Bad timeout values fall back to 10 seconds.
- No secret is printed or logged.

### 9.4 CLI Wiring

Find successful transcript/pipeline completion paths in `cli/app.py`.

After successful transcription, emit the webhook if enabled.

Important:

- Webhook emission must not change stdout JSON contract.
- If CLI command uses `--json`, keep JSON output clean.
- Log warnings to logger/stderr only if project convention allows it.
- Webhook failure must not change the command exit code.
- Do not slow down CLI too much; timeout defaults to 10 sec but is user-configurable.

Recommended behavior:

```text
transcription succeeds
  → normal CLI output happens
  → best-effort Hermes event emit
  → if Hermes fails, warn but exit remains success
```

If it is safer to emit before output due to current code structure, ensure stdout JSON remains unchanged.

### 9.5 UI Wiring

Likely hook point:

```text
ui/app/transcription_mixin.py
TranscriptionMixin._on_complete(self, text: str)
```

Existing relevant flow:

- transcript text is written to textbox
- progress/status updated
- history entry is created via `create_history_entry`
- segments may be saved
- source recording may be deleted

Add best-effort emit after history folder is known.

Requirements:

- Do not block UI for long.
- Do not crash UI if Hermes is down.
- Do not show a modal error if webhook fails.
- Log failure as warning/debug only.
- Use existing `self._config`, `self._audio_path`, `_last_history_folder`, provider/language values where available.
- If possible, send:
  - transcript text
  - audio path
  - history folder
  - provider
  - language
  - routing hint from config/env

If synchronous `requests.post` in UI is risky, dispatch in a small daemon thread. Keep it simple; unit-test the client, not GUI threading.

### 9.6 Docs Update

Update docs with short, practical sections:

- `README.md`: `Hermes Agent integration`
- `AGENTS.md`: implementation/agent notes
- `integrations/hermes/skills/audio-transcriber/SKILL.md`: mention event mode
- `config.example.json`: add example keys

Document:

1. Existing MCP mode: Hermes calls Audio Transcriber via MCP.
2. New webhook event mode: Audio Transcriber notifies Hermes after transcription.
3. Config keys/env vars.
4. Security:
   - secret required
   - HMAC header
   - do not commit secrets
5. Hermes-side setup route.

Use placeholder secret only:

```text
[REDACTED]
```

---

## 10. Hermes-Side Setup Docs to Include

Document this as a user-run setup, not as code executed by the app.

### 10.1 Enable Hermes Webhook Platform

```bash
hermes gateway setup
```

Or manually configure Hermes:

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "[REDACTED]"
```

Restart Hermes gateway:

```bash
hermes gateway restart
```

Health check:

```bash
curl http://localhost:8644/health
```

### 10.2 Create Route

```bash
hermes webhook subscribe audio-transcribed \
  --events "audio.transcribed" \
  --skills "personal-ai-brain-stack" \
  --deliver telegram \
  --prompt "Audio Transcriber event received.

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

The app sends to:

```text
http://localhost:8644/webhooks/audio-transcribed
```

---

## 11. Test Plan

Use TDD.

### 11.1 Existing Compatibility Tests

Run before and after:

```bash
uv run --with pytest --with mcp==1.27.0 python -m pytest \
  tests/test_cli_core.py \
  tests/test_cli_import_guard.py \
  tests/test_hermes_skill.py \
  tests/test_cli_mcp.py -q
```

Expected before change observed previously:

```text
11 passed
```

### 11.2 New Tests

Add:

```text
tests/test_hermes_webhook_schema.py
tests/test_hermes_webhook_client.py
```

#### Schema Tests

Test:

1. Builds required top-level fields.
2. Uses event type `audio.transcribed`.
3. Uses version `1.0`.
4. Extracts filename from path.
5. Defaults optional arrays to `[]`.
6. Uses provided `created_at` if passed.
7. Handles missing audio path.
8. Handles Unicode transcript text.

Example assertions:

```python
payload = build_audio_transcribed_event(
    transcript_text="Привет мир",
    audio_path="C:/tmp/meeting.m4a",
    provider="AssemblyAI",
    language="ru",
    created_at="2026-06-11T12:00:00Z",
)

assert payload["event_type"] == "audio.transcribed"
assert payload["version"] == "1.0"
assert payload["source"] == "audio-transcriber"
assert payload["audio"]["filename"] == "meeting.m4a"
assert payload["transcript"]["raw"] == "Привет мир"
assert payload["transcript"]["segments"] == []
assert payload["meta"]["provider"] == "AssemblyAI"
assert payload["meta"]["language"] == "ru"
```

#### Client Tests

Use `unittest.mock` or pytest monkeypatch. Do not add new external test dependencies.

Test:

1. Disabled config makes no request.
2. Enabled config with missing URL makes no request and returns error.
3. Enabled config with missing secret makes no request and returns error.
4. `serialize_payload` is deterministic.
5. `sign_body` matches known HMAC SHA256.
6. Successful POST sends:
   - exact URL
   - exact body bytes
   - `Content-Type: application/json`
   - `X-Webhook-Signature`
   - `X-Request-ID`
7. Non-2xx returns `sent=False`.
8. `requests.RequestException` is caught and returned as error.
9. Secret never appears in result error.

Example HMAC test:

```python
body = b'{"event_type":"audio.transcribed"}'
secret = "test-secret"
expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
assert sign_body(secret, body) == expected
```

### 11.3 Full Targeted Test Command

After implementing:

```bash
uv run --with pytest --with mcp==1.27.0 python -m pytest \
  tests/test_hermes_webhook_schema.py \
  tests/test_hermes_webhook_client.py \
  tests/test_cli_core.py \
  tests/test_cli_import_guard.py \
  tests/test_hermes_skill.py \
  tests/test_cli_mcp.py -q
```

Expected:

```text
all tests pass
```

Also run:

```bash
python -m cli --help
```

Expected:

```text
CLI help prints successfully
```

If the whole test suite is feasible, run it too. If not, state why.

---

## 12. Manual Local Smoke Test

Unit tests are acceptable for PR V1. If Hermes webhook is enabled locally, do a manual smoke test only with a user-provided local secret.

Do not invent, print, or expose real secrets.

Example curl shape for docs only:

```bash
BODY='{"event_type":"audio.transcribed","version":"1.0","source":"audio-transcriber","transcript":{"raw":"test","segments":[]},"audio":{"filename":"test.m4a","path":null,"history_folder":null},"analysis":{"summary":null,"tasks":[],"ideas":[],"decisions":[],"protocol":null},"meta":{"provider":"test","language":"ru","created_at":"2026-06-11T12:00:00Z"},"routing_hint":"obsidian_inbox"}'
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

Do not commit this with real secrets.

---

## 13. Acceptance Criteria

The feature is complete when all are true:

1. `audio-transcriber` can build a valid `audio.transcribed` Hermes event payload.
2. When disabled by default, no network request is made.
3. When enabled and configured, it POSTs to the configured Hermes webhook URL.
4. The POST body is deterministic JSON bytes.
5. The `X-Webhook-Signature` header is valid HMAC-SHA256 over the exact body bytes.
6. `X-Request-ID` is sent.
7. Network failure does not fail transcription.
8. CLI JSON output remains backward compatible.
9. UI transcription success remains successful even if Hermes is down.
10. Existing CLI/MCP/Hermes skill tests still pass.
11. New schema/client tests pass.
12. Docs explain setup without exposing secrets.
13. No local ML/heavy dependencies were added.
14. No unrelated rewrite/refactor was done.
15. Git diff contains only the intended integration/spec/docs/test changes.

---

## 14. Suggested Implementation Tasks

### Task 0: Read-only repo inspection

**Objective:** Confirm current code shape and avoid accidental rewrite.

**Files:** Read only.

**Steps:**

1. Check status:
   ```bash
   git status --short --branch
   ```
2. Check CLI:
   ```bash
   python -m cli --help
   ```
3. Read the files listed in section 6.
4. Summarize where config is loaded, where CLI transcription succeeds, and where UI completion happens.

**Expected:** No files modified.

---

### Task 1: Add schema builder tests

**Objective:** Define the payload contract before implementation.

**Files:**

- Create: `tests/test_hermes_webhook_schema.py`

**Steps:**

1. Write failing tests for required fields, filename extraction, Unicode transcript, defaults, `created_at` override, and missing audio path.
2. Run:
   ```bash
   uv run --with pytest --with mcp==1.27.0 python -m pytest tests/test_hermes_webhook_schema.py -q
   ```
3. Expected: fail because module/function does not exist.

---

### Task 2: Implement schema builder

**Objective:** Implement pure JSON-serializable event payload construction.

**Files:**

- Create: `integrations/hermes/__init__.py`
- Create: `integrations/hermes/schema.py`

**Steps:**

1. Implement `build_audio_transcribed_event`.
2. Run schema tests.
3. Expected: schema tests pass.

---

### Task 3: Add client tests

**Objective:** Define webhook signing and delivery behavior before implementation.

**Files:**

- Create: `tests/test_hermes_webhook_client.py`

**Steps:**

1. Write failing tests for disabled no-op, missing URL, missing secret, deterministic serialization, HMAC signing, successful mocked POST, non-2xx, and request exception handling.
2. Run:
   ```bash
   uv run --with pytest --with mcp==1.27.0 python -m pytest tests/test_hermes_webhook_client.py -q
   ```
3. Expected: fail because client does not exist.

---

### Task 4: Implement client

**Objective:** Implement best-effort signed POST client.

**Files:**

- Create: `integrations/hermes/client.py`

**Steps:**

1. Implement `HermesWebhookConfig`.
2. Implement `HermesWebhookResult`.
3. Implement `serialize_payload`.
4. Implement `sign_body`.
5. Implement `post_event`.
6. Implement `emit_audio_transcribed_event`.
7. Run client tests.
8. Expected: client tests pass.

---

### Task 5: Add config resolution

**Objective:** Allow users to enable/configure Hermes webhook through config or env.

**Files:**

- Modify: `cli/config.py` or another config module matching project conventions.
- Modify/Add tests if config tests exist.

**Steps:**

1. Add parsing for config keys.
2. Add env var overrides.
3. Add safe boolean parsing.
4. Add timeout fallback.
5. Ensure missing config means disabled.
6. Run relevant tests.

---

### Task 6: Wire CLI success path

**Objective:** Emit Hermes event after successful CLI transcription without changing CLI output contract.

**Files:**

- Modify: `cli/app.py`
- Possibly modify: `tests/test_cli_core.py` or add focused tests.

**Steps:**

1. Identify successful transcription/pipeline completion path.
2. Call the emitter if enabled.
3. Keep JSON stdout unchanged.
4. Ensure webhook failure does not change exit code.
5. Add mocks/tests if practical.
6. Run targeted CLI tests.

---

### Task 7: Wire UI success path

**Objective:** Emit Hermes event after successful UI transcription without interrupting UX.

**Files:**

- Modify: `ui/app/transcription_mixin.py`

**Steps:**

1. Hook into `_on_complete(self, text: str)` after history folder is available.
2. Build config from existing UI config/env.
3. Emit transcript, audio path, history folder, provider, language, and routing hint.
4. Catch all unexpected exceptions around the emitter.
5. Do not show modal errors for webhook failure.
6. Prefer a daemon thread if synchronous POST is risky.

---

### Task 8: Docs

**Objective:** Make the integration discoverable and safe to configure.

**Files:**

- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `config.example.json`
- Modify: `integrations/hermes/skills/audio-transcriber/SKILL.md`

**Steps:**

1. Document MCP mode.
2. Document webhook event mode.
3. Document config/env keys.
4. Document HMAC signing.
5. Document Hermes-side route setup.
6. Use `[REDACTED]` for secrets.

---

### Task 9: Final verification

**Objective:** Prove the implementation is complete and safe.

Run:

```bash
git diff --check
python -m cli --help
uv run --with pytest --with mcp==1.27.0 python -m pytest \
  tests/test_hermes_webhook_schema.py \
  tests/test_hermes_webhook_client.py \
  tests/test_cli_core.py \
  tests/test_cli_import_guard.py \
  tests/test_hermes_skill.py \
  tests/test_cli_mcp.py -q
git status --short --branch
```

Report:

- files changed
- tests run
- pass/fail
- any manual steps needed for Hermes gateway setup

Do not push.

---

## 15. Suggested Branch and Commit Names

Branch:

```text
feat/hermes-webhook-events
```

Possible commits:

```text
feat: add Hermes webhook event payload builder
feat: add Hermes webhook client
feat: emit Hermes event after transcription
docs: document Hermes webhook integration
```

If only one commit:

```text
feat: add Hermes webhook event integration
```

---

## 16. Final Report Format

When finished, report:

```markdown
## Summary
- Added ...
- Updated ...
- Preserved ...

## Tests
- `...` → passed
- `...` → passed

## Files changed
- `...`

## Manual setup required
- Enable Hermes webhook platform.
- Subscribe route `audio-transcribed`.
- Set `AUDIO_TRANSCRIBER_HERMES_WEBHOOK_*` env vars or config keys.

## Notes
- No secrets committed.
- Webhook delivery is best-effort.
- Existing MCP mode unchanged.
```

---

## 17. Recommended Claude Desktop Workflow

1. Open this file in Claude Desktop.
2. Ask Claude to perform **Task 0 only** and summarize implementation touch points.
3. Review the summary.
4. Ask Claude to perform **Tasks 1–4 only**.
5. Run tests and inspect diff.
6. Only then proceed to CLI/UI wiring.

This keeps the first PR small and avoids accidental rewrite.
