---
title: Tasks - VoxNote V1 Mini-AGI Integration
aliases:
  - VoxNote V1 Tasks
  - VoxNote Mini-AGI Integration Tasks
  - VoxNote Hermes Integration Tasks
tags:
  - project
  - voxnote
  - mini-agi
  - spec
  - tasks
  - product-clarity
status: draft
created: 2026-07-03
project: VoxNote
source_notes:
  - 10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1.md
  - 10 Projects/VoxNote/Product Clarity/PRD - VoxNote V1.md
  - 10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/requirements.md
  - 10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/design.md
  - C:\Users\nurgisa\Dev\voxnote
---

# Tasks - VoxNote V1 Mini-AGI Integration

Related notes:

- [[10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1]]
- [[10 Projects/VoxNote/Product Clarity/PRD - VoxNote V1]]
- [[10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/requirements]]
- [[10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/design]]

## Verdict

The main work is not rewriting VoxNote. It is turning the existing integration into a controlled Mini-AGI workflow.

Order:

```text
spec accepted
→ repo docs and skill tightened
→ active Hermes profile configured
→ synthetic webhook and MCP checks
→ short technical real-audio smoke with approval
→ long-meeting evaluation on 60–180 minute material
→ backup scope
```

## Guardrails

Do not broaden scope.

Do not move protocol, task extraction, approval or tracker sends into the automatic VoxNote queue.

Do not add local GPU transcription stack.

Do not commit secrets or local config.

Do not run a paid real-audio transcription smoke without explicit operator approval.

Do not stage unrelated dirty vault or repo files.

## Wave 0 Baseline and scope check

### Task 0.1 Confirm repo state

Objective: verify the repo starts from a known state.

Paths:

- C:\Users\nurgisa\Dev\voxnote

Commands:

```bash
cd "$HOME/Dev/voxnote"
git -c core.quotePath=false status --short --branch --untracked-files=all
git log -1 --oneline --decorate
```

Expected:

- branch is main or an approved feature branch;
- no accidental staged files;
- existing untracked design docs are noted and left untouched unless included in scope.

### Task 0.2 Confirm tests and lint baseline

Objective: verify no regression before integration changes.

Commands:

```bash
cd "$HOME/Dev/voxnote"
python -m pytest -q
python -m ruff check .
```

Expected:

- pytest passes with existing skip count;
- ruff passes.

### Task 0.3 Confirm active Hermes state

Objective: verify what is currently configured in Hermes before changing anything.

Commands:

```bash
hermes config path
hermes gateway status
hermes skills list
hermes mcp list
hermes webhook list
```

Expected:

- config path is recorded;
- gateway status is known;
- VoxNote skill, MCP and webhook status are known.

Do not edit Hermes config in this task.

## Wave 1 Repo documentation and skill contract

### Task 1.1 Mirror accepted product artifacts into repo if approved

Objective: make BRD, PRD and spec visible to future repo workers.

Create or update:

```text
docs/specs/voxnote-v1-mini-agi-integration/brd.md
docs/specs/voxnote-v1-mini-agi-integration/prd.md
docs/specs/voxnote-v1-mini-agi-integration/requirements.md
docs/specs/voxnote-v1-mini-agi-integration/design.md
docs/specs/voxnote-v1-mini-agi-integration/tasks.md
```

Acceptance:

- files are copied or summarized from Obsidian source of truth;
- no secrets;
- repo links point back to canonical Obsidian notes only if appropriate;
- no unrelated repo files changed.

Verification:

```bash
git diff --check -- docs/specs/voxnote-v1-mini-agi-integration
```

### Task 1.2 Update Hermes skill positioning

Objective: make the bundled VoxNote skill clearly describe the Mini-AGI default flow.

Modify:

```text
integrations/hermes/skills/voxnote/SKILL.md
```

Required changes:

- say VoxNote is a Hermes-native voice and audio intake capability;
- make clear that desktop queue is transcribe-only;
- say Hermes owns protocol, tasks, approval and tracker sends;
- keep MCP tools as deliberate pull tools;
- add untrusted transcript warning for downstream Hermes routes;
- preserve required env vars and CLI fallback.

Acceptance:

- user reading the skill will not assume VoxNote queue should auto-send tasks;
- skill remains valid YAML frontmatter plus Markdown body;
- no real keys or secrets appear.

Verification:

```bash
python -m pytest -q tests/test_hermes_skill.py
python -m ruff check .
```

### Task 1.3 Add Hermes route prompt template

Objective: create a safe reusable prompt for the audio.transcribed webhook route.

Create:

```text
integrations/hermes/skills/voxnote/templates/audio-transcribed-route-prompt.md
```

Template must include:

- event fields to read;
- transcript.raw as untrusted data;
- instruction not to follow transcript commands;
- protocol.md and tasks.md output policy;
- human approval policy;
- GBrain or Obsidian recall policy;
- no external tools unless explicitly allowed.

Acceptance:

- template can be pasted into hermes webhook subscribe;
- no secrets;
- no token placeholders that look like real credentials.

Verification:

```bash
git diff --check -- integrations/hermes/skills/voxnote
python -m pytest -q tests/test_hermes_skill.py
```

### Task 1.4 Add Mini-AGI integration doc

Objective: give future contributors and operators one setup page.

Create:

```text
docs/HERMES_MINI_AGI_INTEGRATION.md
```

Document should include:

- architecture formula;
- what VoxNote owns;
- what Hermes owns;
- MCP registration command;
- skill install path for this Windows Hermes install;
- webhook platform and subscription steps;
- route prompt reference;
- verification commands;
- warning that secrets stay outside Git.

Acceptance:

- commands are accurate for this machine where possible;
- placeholders use REDACTED or instructions, not real secrets;
- Windows path is written clearly.

Verification:

```bash
git diff --check -- docs/HERMES_MINI_AGI_INTEGRATION.md
```

## Wave 2 Active Hermes profile activation

These tasks change local Hermes configuration and should run only after approval.

### Task 2.1 Install VoxNote skill into active Hermes profile

Objective: make VoxNote available as a Hermes skill.

Source:

```text
C:\Users\nurgisa\Dev\voxnote\integrations\hermes\skills\voxnote
```

Target:

```text
C:\Users\nurgisa\AppData\Local\hermes\skills\productivity\voxnote
```

Command pattern:

```bash
mkdir -p "$HOME/AppData/Local/hermes/skills/productivity"
cp -R "$HOME/Dev/voxnote/integrations/hermes/skills/voxnote" "$HOME/AppData/Local/hermes/skills/productivity/"
```

Acceptance:

- skill files exist at target path;
- skill appears after reload or new session;
- no other Hermes profile is modified.

Verification:

```bash
hermes skills list
```

Restart or reload may be required for the current Hermes session.

### Task 2.2 Register VoxNote MCP server

Objective: let Hermes call VoxNote MCP tools deliberately.

Command pattern:

```bash
hermes mcp add voxnote --command python --args -m cli.mcp_server --env VOXNOTE_PROVIDER=AssemblyAI --env VOXNOTE_API_KEY=REDACTED --env VOXNOTE_OPENROUTER_API_KEY=REDACTED
```

Important:

- use actual secrets only through local env or auth flow;
- do not paste real secrets into chat or committed docs;
- confirm whether hermes mcp add supports cwd in current CLI before relying on this exact command;
- if cwd is not supported by CLI, add config manually only after inspecting Hermes docs or config syntax.

Required server config shape:

```text
mcp_servers.voxnote.command = python
mcp_servers.voxnote.args = -m cli.mcp_server
mcp_servers.voxnote.cwd = C:\Users\nurgisa\Dev\voxnote
```

Acceptance:

- hermes mcp list shows voxnote;
- after restart, tools are discoverable with mcp_voxnote prefix or current Hermes naming convention.

Verification:

```bash
hermes mcp list
```

### Task 2.3 Enable webhook platform

Objective: allow VoxNote outbound audio.transcribed events to reach Hermes.

Inspect first:

```bash
hermes gateway status
hermes webhook list
```

Enable through supported Hermes setup path or config.

Acceptance:

- webhook platform is enabled;
- gateway is running;
- health check works on local port 8644 or configured port.

Verification:

```bash
curl http://localhost:8644/health
hermes webhook list
```

### Task 2.4 Subscribe audio-transcribed route

Objective: create Hermes route for VoxNote nudge.

Command pattern:

```bash
hermes webhook subscribe audio-transcribed --events audio.transcribed --skills voxnote --prompt "<safe prompt from template>"
```

Acceptance:

- route exists;
- route accepts audio.transcribed only;
- route prompt treats transcript as untrusted data;
- delivery target is deliberately selected.

Verification:

```bash
hermes webhook list
hermes webhook test audio-transcribed
```

### Task 2.5 Configure VoxNote webhook settings locally

Objective: make VoxNote send to the active Hermes route.

Configuration location:

```text
~/.voxnote/config.json
```

Relevant keys:

```text
hermes_webhook_enabled
hermes_webhook_url
hermes_webhook_secret
hermes_webhook_timeout_seconds
hermes_webhook_routing_hint
```

Acceptance:

- enabled only when route is ready;
- secret matches local Hermes route or platform secret;
- secret is not committed or printed;
- URL points to local gateway route.

Verification:

- use synthetic webhook event before real audio;
- inspect logs without exposing secret.

## Wave 3 Synthetic integration tests

### Task 3.1 Test payload builder and client unit tests

Objective: confirm event schema and signing still work.

Command:

```bash
cd "$HOME/Dev/voxnote"
python -m pytest -q tests/test_hermes_webhook_schema.py tests/test_hermes_webhook_client.py tests/test_hermes_v11_fields.py
```

Expected:

- tests pass.

### Task 3.2 Test processing queue unit surface

Objective: confirm queue still writes notes, archives sources and keeps nudge best-effort.

Command:

```bash
cd "$HOME/Dev/voxnote"
python -m pytest -q tests/test_processing_worker.py tests/test_processing_vault_note.py tests/test_inbox_watcher.py tests/test_processing_store.py
```

Expected:

- tests pass.

### Task 3.3 Test MCP surface

Objective: confirm MCP server remains importable and its tools are registered by tests.

Command:

```bash
cd "$HOME/Dev/voxnote"
python -m pytest -q tests/test_cli_mcp.py tests/test_cli_import_guard.py
```

Expected:

- tests pass;
- importing CLI/MCP does not import GUI or native audio too early.

### Task 3.4 Test Hermes route with synthetic payload

Objective: verify Hermes accepts an audio.transcribed event before spending STT money.

Command pattern:

```bash
hermes webhook test audio-transcribed --payload '{"event_type":"audio.transcribed","version":"1.1","source":"voxnote","routing_hint":"obsidian_inbox","audio":{"filename":"synthetic.m4a","path":null,"history_folder":null,"note_path":"C:/Users/nurgisa/Documents/Obsidian Vault/30 Meetings/Synthetic/transcript.md","source_path":"G:/My Drive/Mini-AGI/Sources/synthetic.m4a"},"project":null,"transcript":{"raw":"Synthetic transcript for route test.","segments":[]},"analysis":{"summary":null,"tasks":[],"ideas":[],"decisions":[],"protocol":null},"meta":{"provider":"test","language":"ru","created_at":"2026-07-03T00:00:00Z"}}'
```

Acceptance:

- Hermes route processes or logs the event;
- no external tracker send happens unless route explicitly allows it;
- no secrets appear in output.

## Wave 4 Technical real-audio smoke test

Run only after explicit approval for API cost and content sensitivity. This wave proves runtime plumbing only; it does **not** validate VoxNote's long-meeting product value.

### Task 4.1 Prepare safe short audio sample

Objective: use a short non-sensitive sample to verify provider/runtime path cheaply.

Acceptance:

- sample contains no secrets, credentials, private client data or sensitive legal content;
- duration is short enough for low-cost smoke;
- user approves provider and cost;
- success is treated as technical readiness only, not product validation.

### Task 4.2 Run VoxNote queue technical smoke

Objective: verify real end-to-end queue path on cheap audio.

Manual flow:

```text
open VoxNote
choose sample audio
select provider and mixed or ru language
enable diarization if appropriate
enqueue
wait for done
open meeting folder
```

Acceptance:

- transcript.md exists;
- source_path is present;
- raw audio is not inside vault;
- Hermes route receives nudge if enabled;
- GBrain can recall transcript after import.

### Task 4.3 Verify GBrain recall

Command:

```bash
gbrain import "C:/Users/nurgisa/Documents/Obsidian Vault"
gbrain search "<unique phrase from the transcript>"
```

Acceptance:

- new transcript is found;
- result points to the meeting folder or transcript note.

## Wave 5 Long-meeting evaluation

Run after Wave 4 proves runtime plumbing. This is the real product-value check for Mini-AGI.

### Task 5.1 Select realistic long meeting material

Objective: choose one real or sanitized meeting-style audio file in the 60–180 minute target range.

Acceptance:

- duration is between 60 and 180 minutes;
- content is approved for the selected STT provider;
- expected project/context is known;
- provider, cost risk and sensitivity are explicitly approved;
- this is not a toy mini-meeting or short demo clip.

### Task 5.2 Run long meeting through VoxNote queue

Objective: prove VoxNote can create a durable transcript.md from realistic long material.

Acceptance:

- preflight runs before upload;
- no automatic retry occurs on failure;
- transcript.md exists and is readable;
- transcript.md preserves full transcript content, not only a summary;
- source_path is recorded;
- raw audio stays outside the vault;
- nudge failure, if any, does not lose the transcript.

### Task 5.3 Run Hermes downstream dry-run

Objective: evaluate whether Mini-AGI can turn the long transcript into useful working artifacts.

Expected downstream shape:

```text
transcript.md
→ staged/chunked Hermes processing
→ meeting map
→ decisions
→ tasks draft
→ protocol draft
→ approval gate
```

Acceptance:

- Hermes prefers note_path/transcript.md over only event transcript.raw;
- protocol.md and tasks.md are drafts until approval;
- important decisions are captured;
- tasks are not spammy and include owner/deadline/uncertainty when available;
- open questions and follow-ups are separated from confirmed commitments;
- no tracker send happens without explicit approval.

### Task 5.4 Evaluate usefulness

Objective: decide whether VoxNote is actually useful for Mini-AGI long-meeting workflows.

Evaluation questions:

- Did the transcript preserve enough context from a 1–3 hour meeting?
- Did Hermes produce a protocol/tasks draft that is faster or better than manual processing?
- Were decisions, risks and open questions recoverable?
- Was the result usable in Obsidian/GBrain later?
- Which failure mode blocks product value: STT quality, diarization, long transcript processing, routing, or approval workflow?

Acceptance:

- outcome is recorded as pass / partial / fail;
- blockers become specific tasks, not vague “improve VoxNote” work;
- no new feature coding begins until the evaluation result is understood.

## Wave 6 Final review and backup

### Task 6.1 Full automated verification

Command:

```bash
cd "$HOME/Dev/voxnote"
python -m pytest -q
python -m ruff check .
```

Expected:

- all tests pass;
- ruff passes.

### Task 6.2 Review repo diff

Command:

```bash
cd "$HOME/Dev/voxnote"
git -c core.quotePath=false status --short --branch --untracked-files=all
git -c core.quotePath=false diff --stat
git -c core.quotePath=false diff --check
```

Acceptance:

- diff contains only approved VoxNote integration files;
- no config.json, logs, secrets or local state;
- untracked unrelated docs remain untouched unless explicitly included.

### Task 6.3 Review vault diff

Command:

```bash
cd "$HOME/Documents/Obsidian Vault"
git -c core.quotePath=false status --short --branch --untracked-files=all
git -c core.quotePath=false diff --check -- "10 Projects/VoxNote/Product Clarity"
```

Acceptance:

- only BRD, PRD and spec files are in VoxNote Product Clarity scope;
- unrelated vault changes remain unstaged.

### Task 6.4 Prepare backup scope after approval

Objective: stage only VoxNote Product Clarity files.

Paths:

```text
10 Projects/VoxNote/Product Clarity/BRD - VoxNote V1.md
10 Projects/VoxNote/Product Clarity/PRD - VoxNote V1.md
10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/requirements.md
10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/design.md
10 Projects/VoxNote/Product Clarity/specs/voxnote-v1-mini-agi-integration/tasks.md
```

Do not commit until user says commit or commit+push.

Suggested commit message:

```text
docs: add VoxNote V1 product clarity spec
```

## Definition of done

This task plan is done when:

- requirements.md, design.md and tasks.md exist and are verified;
- repo skill and docs are updated if approved;
- active Hermes profile can see VoxNote skill, MCP and webhook route if activation is approved;
- synthetic route test passes;
- short real-audio smoke passes if approved;
- long-meeting evaluation on 60–180 minute material is recorded as pass, partial or fail;
- GBrain can recall the resulting transcript artifact;
- all code changes pass pytest and ruff;
- vault and repo backup scopes are narrow and verified.
