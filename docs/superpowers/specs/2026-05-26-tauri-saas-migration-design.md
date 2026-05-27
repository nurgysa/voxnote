# Tauri SaaS Migration — Design

**Date:** 2026-05-26
**Status:** Draft (post-brainstorm, pre-plan)
**Author:** nurgysa (with Claude)
**Brainstorm session:** `.superpowers/brainstorm/9343-1779808576/`

## 1. Context

The current `audio-transcriber` is a Windows desktop app (Python + CustomTkinter + faster-whisper + pyannote) optimized for an ASUS ROG Strix G15 with a GTX 1650 Ti. Most of `CLAUDE.md`'s "Hard invariants" (faulthandler bootstrap, ctranslate2/torch import order, cuDNN disable, VRAM management, etc.) exist solely because both Whisper-large and pyannote cannot fit in 4 GB VRAM simultaneously on that card.

We are migrating to a production-ready **managed transcription SaaS** for tech-insider / developer users. The migration:

- Drops all local-CUDA ML (faster-whisper, pyannote, ctranslate2 import order, VRAM tricks).
- Replaces the CustomTkinter UI with a Tauri 2 + React + TypeScript desktop client.
- Adds a server backend (FastAPI + Postgres + Supabase + Stripe).
- Adds bi-directional MCP integration (app exposes tools to external agents AND consumes external MCP servers).
- Keeps full feature parity with the current audio-transcriber (recording, voice library, audio editor, silence removal, task extraction, GDrive backup, code-switching KZ/RU/EN, etc.).

**Local-first data principle.** All user-generated content (audio files, transcripts, extracted tasks, voice-library embeddings) persists only on the user's device. The backend is stateless with respect to user data: it sees content in memory while routing it (STT, LLM, Linear/Glide) but never writes it to disk or database. The backend persists only billing/quota/auth state — the minimum required to operate a managed paid service. This is the central privacy claim for the developer audience.

**Vault model (Obsidian-style).** All user content lives inside a single user-chosen folder ("the vault"), organized as `<vault>/<Project>/<Meeting>/...`. App state (SQLite index, voice-library embeddings, preferences) lives in a hidden `.audio-transcriber/` subfolder inside the vault. The vault is fully self-contained and portable — copying or syncing the folder copies or syncs everything. Users who want cross-device usage can place the vault inside their own cloud-sync folder (Dropbox, GDrive, iCloud, git) and it works out of the box, with the standard Obsidian caveat that simultaneous edits from two devices are not supported (last-write-wins, with a startup reconciler).

**Target consumers beyond direct UI.** The audience overlaps significantly with **Claude Cowork** users (researchers, analysts, ops, legal, finance — knowledge workers running agentic workflows on local files). Two consequences shape the design from §1: (a) the vault is structured as plain files (markdown, jsonl, toml) precisely so that **Cowork can read it natively without integration work** — a user can already point Cowork at `<vault>/Project Alpha/` and ask "summarize last month's open questions" without us shipping anything Cowork-specific; (b) the stdio MCP surface (§6.1, 16 tools) is designed to be **first-class consumable by Cowork as an MCP host**, not just by Claude Desktop chat. Both audiences (direct app users + Cowork users orchestrating across our data) get value from the same architecture.

The Python audio-transcriber stays as a legacy reference during the rewrite, but is not used after launch.

## 2. Decisions log

Locked-in choices from the brainstorm session:

| Question | Choice |
|---|---|
| Scope | Migration of `audio-transcriber` to new stack |
| ML location | Cloud-only (no local CUDA pipeline) |
| MCP role | Bi-directional (server + client) |
| Goal | Production release for other users |
| Business model | Managed service (backend + billing) |
| Audience | Tech-insiders / developers |
| MVP scope | Full feature parity from `audio-transcriber` + managed layer + MCP |
| Phasing | Big-bang v1.0 (~6 months solo) |
| Backend stack | Python (FastAPI) + Postgres |
| BaaS | Supabase (Auth + DB + Realtime — **Storage unused**, no user blobs in our infra) |
| User data location | **All local** — audio, transcripts, tasks, voice embeddings persist only on user device (see §3.4) |
| Voice-library embeddings | Local SQLite via `sqlite-vec` (vault-scoped). ECAPA-TDNN inference on backend; vectors returned to client and never persisted server-side |
| MCP transport | Both stdio (Tauri sidecar) AND HTTPS (FastAPI). **HTTPS scope: billing/quota/usage only** (no user data — see §6.1) |
| Vault layout | Obsidian-style. One user-chosen folder per install. **3-bucket meeting folder** (see §3.5): root `{audio.<ext>, README.md, transcript.md, tasks.json, meeting.toml, notes.md?}` + `.cache/{audio.trimmed.wav, segments.jsonl}` + `analysis/{summary, protocol, agenda, decisions, open_questions, insights, topics}.md/.json` + `<vault>/.audio-transcriber/` for index/library/settings |
| Source of truth | **Hybrid.** Files own content (audio, transcript.md, tasks.json) so external tools can edit them. SQLite owns segments + metadata index so search/filter is fast. Reconciler on startup + manual rescan in Settings |
| Cross-device | Out of the box via user's own cloud sync (Dropbox/GDrive/iCloud) — vault is just a folder. App-server-mediated sync remains out of scope (§14) |
| Backup target | **Google Drive only** for v1.0 (lifted from Python Phase 7.1; OAuth via backend, upload client→GDrive direct so backend never sees backup contents). Notion as backup explicitly rejected (vendor lock-in, fragile export, GDrive zip is strictly better for disaster-recovery). Notion as publish-target deferred to Phase 2 (§14). |
| Pre-transcription metadata | Import dialog captures 4 fields with smart defaults — `meeting_date` (default = file mtime), `meeting_type` (10 seeded defaults: Standup, 1-on-1, Design Review, Sprint Retro, Sprint Planning, Demo, Interview, Customer Call, Workshop, Other — editable in Settings), `project` (default = last used), `participants` from voice library multiselect (default = last used set, inline enrollment for new speakers). Nothing mandatory — user can press Go immediately. Pre-specified participants narrow the voice-identify candidate set in §7.2 step 16 (accuracy jump). |
| Project description | Per-project free-text description (20-200 words). Lives in `<vault>/<Project>/README.md`, denormalized into `projects.description` for fast access. **Strongly-suggested, not mandatory** — project can be created without it; banner on all project screens until 20+ words are present. Passed to STT providers as `initial_prompt` / `context_prompt` where supported (Groq, OpenAI Whisper, Gladia, Speechmatics) for ~3-8% WER reduction on domain-specific terms, and prepended to **every post-processing pass's system prompt** (§7.9) so all LLM-generated artifacts are framed in the project's context. |
| LLM post-processing pipeline | After transcribe + voice-identify, runs **7 passes automatically** (§7.9): `summary`, `tasks` (with assignee + due_date enrichment), `decisions`, `topics`, `open_questions`, `insights` in parallel (Phase A); then `protocol` composed from Phase-A outputs using per-meeting-type template (Phase B). Each pass = one OpenRouter call, stateless-proxied through backend, outputs persist as files in meeting folder. Per-meeting opt-out at import time (advanced expander). Per-pass regenerate button in meeting view. Per-meeting-type protocol templates editable in `<vault>/.audio-transcriber/protocol_templates/`. |
| Diarization | **Cloud-only** (locked decision). Providers that support diarization (AssemblyAI, Speechmatics, Gladia, Deepgram Nova-3) return segments with speaker labels (`Speaker_A/B/C`) inside their STT response — no extra pipeline stage. Providers that don't (Groq Whisper, OpenAI Whisper) return segments without speaker labels — voice-identify post-pass (§7.2 step 16) is the only path to attach names from voice library. **No client-side diarization fallback** in v1.0 (no pyannote / no ECAPA clustering on the client — see §14 + §13.14 trade-off). Users get cleaner architecture, smaller installer, and a clear cost/quality choice at provider-selection time. |
| RAG chat over vault | **Chat-only UX** (no separate search UI — semantic retrieval happens inside RAG). **Vault-wide scope** with "In:" selector to narrow to project/meeting (default = all vault). **Auto-indexing** after transcribe + post-process: chunks embedded via backend (OpenAI text-embedding-3-small, 1536-dim, $0.02/1M tokens), stored in local `<vault>/.audio-transcriber/embeddings.db` (sqlite-vec). Backend embeds query in-memory + retrieves top-k via local vault SQLite + sends question+chunks to OpenRouter LLM → SSE answer with inline citations linking to source meetings + timestamps. Backend never persists chunks, queries, or answers — full §3.4 privacy invariant preserved. Full design: §7.14. |
| Task backends | **v1.0 ships 7 native backends + 1 generic**: Linear (lifted), Glide (lifted), **Notion** (new), **Jira / Atlassian** (new), **Яндекс Трекер** (new — critical for Yandex-shop KZ/RU audience), **Битрикс24** (new — dominant CIS), **GitHub Projects v2** (new — covers open-source / startup / dev-team workflows), plus **generic Webhook** (signed JSON payload → covers ClickUp / Asana / Monday / Trello / Todoist / MeisterTask / MS Project / MS Planner and 200+ other tools via Zapier/Make/n8n/Pipedream user-side automation). All native backends use Protocol-based dispatch via lifted `tasks/backends/` ABC — adding a new one is ~1-2 weeks of work; community plugin architecture deferred to Phase 2 (§14). Assignee mapping: v1.0 sends speaker name as string (no per-backend user lookup); Phase 2 adds proper user mapping per backend. |
| Meeting folder layout | **3-bucket layout** — source-of-truth (root: `audio.<ext>`, `transcript.md`, `tasks.json`, `meeting.toml`, `README.md`) + `.cache/` (regenerable: `audio.trimmed.wav`, `segments.jsonl`) + `analysis/` (LLM-derived: 7 markdown/json artifacts). Buckets encode operation semantics — `.cache/` rebuild = free, `analysis/` rebuild = API $. See §3.5. |
| Meeting README.md | **Auto-generated entry-point** at meeting-folder root. Composed from frontmatter metadata + first-paragraph summary excerpt + quick links to artifacts. Lazy regeneration via `meetings.readme_dirty` flag. User edits NOT respected (regenerated); optional `notes.md` for free-form user notes. See §3.5 + §7.16. |
| Frontmatter convention | **All `.md` files in vault carry YAML frontmatter** with universal fields (`schema_version`, `generated_at`, `generated_by`) + per-type fields. `derived_from:` declares explicit dependency graph for reconciler invalidation. speaker_id in frontmatter (immutable), name in body (mutable). See §3.5.1. |
| Wiki-link convention | **Generated content uses full paths** (e.g. `[[Project Alpha/README\|Project Alpha]]`); user-authored content relies on Obsidian proximity resolution. Tauri command `resolve_wiki_links()` normalizes via SQLite lookup. See §3.5.2. |
| LLM post-process pass count | **8 passes** (was 7 in earlier draft): Phase A (summary, tasks, decisions, topics, open_questions, insights, **agenda**) in parallel + Phase B (protocol) after Phase A. Agenda extracted from first 5-10 minutes of transcript by LLM with «*Повестка не зафиксирована*» fallback. See §7.9. |
| protocol.md template structure | **5-block MoM (Minutes of Meeting)** skeleton: (1) Метаданные / (2) Повестка дня / (3) Ключевые тезисы + решения + разногласия / (4) Action items table / (5) Ссылки + следующая встреча. 10 seeded `<vault>/.audio-transcriber/protocol_templates/<Type>.md` files — type-specific variants on the skeleton. See §7.9. |
| Next-meeting extraction | **Embedded in `summary` pass** (no new pass). LLM extracts «увидимся в четверг»-style mentions from transcript; stored as frontmatter field `next_meeting: {date, topic, confidence}` in `analysis/summary.md`. Rendered in protocol.md Block 5 when `confidence >= 0.5`. See §7.9. |
| Protocol distribution | **v1.0: Email + Telegram** via backend-proxy with **draft→preview→send** model. Email = SES default (sender `notifications@audiotranscriber.io`) + opt-in Gmail/Outlook OAuth (from user's address). Telegram = shared `@AudioTranscriberBot` (backend-held token). Slack/Teams → Phase 2; WhatsApp dropped (§14). See §7.15. |
| Speaker contact storage | **Frontmatter** in `<vault>/People/<name>.md` (synced with vault). Fields: `email`, `telegram_chat_id`, `auto_distribute_protocols` (default true; per-speaker opt-out), `protocol_distribution_channels` (priority order). At-first-contact-entry warning: «контакты идут с vault при cloud-sync». See §3.5 + §7.15. |
| Distribution audit log | **Dual: SQLite `distributions` table** (queryable) + `<vault>/.audio-transcriber/distributions.log` JSONL (portable, Cowork-readable). Same pattern as segments.jsonl/meeting_segments and tasks.json/meeting_tasks — «files own content, SQLite owns index» per §3.3. See §4.3 + §7.15. |
| Repo structure | Monorepo (Approach A) |
| Frontend stack pinning | **React 19 + TS 5.x + Vite + TanStack Query / Router + shadcn/ui + Tailwind v4 + Zustand + Vitest + Playwright** (see §2.1 for justification). Chosen 2026-05-28 — pins versions and adds the previously-implicit Vite / TanStack / Tailwind / state / test layers. |

## 2.1 Frontend tech stack pinning (added 2026-05-28)

The §2 decisions table set the high-level frame (Tauri 2 + React + TS + shadcn/ui + pnpm) but left several layers implicit. This subsection pins each layer with an exact choice + rationale so plan-writing time isn't burned re-litigating. Versions reflect what's current and well-supported on Windows desktop in 2026; minor-version drift during the ~6-month build is expected and fine, but cross-major bumps need explicit revisit.

| Layer | Choice | Version pin | Why |
|---|---|---|---|
| Frontend framework | React | 19.x | Strongest training-data coverage in Claude/Codex/Cowork → highest agent-generated code quality. React 19 ships Server Components + the new compiler — relevant if/when we add server-side rendering hooks for shared content. Svelte 5 considered + rejected: smaller bundle but ~10× less LLM training data → noticeably worse agent output, and that's the bottleneck for a solo-dev 6-month build. |
| Language | TypeScript | 5.x | Mandatory at this scale. The `packages/shared-types` OpenAPI-generated `.d.ts` (§3.3) is the cross-stack contract — turning a backend breakage into a TS compile error is the whole point. |
| Build tool | Vite | 6.x (or current major at build start) | De-facto standard for React + TS in 2026; Tauri 2's `create-tauri-app --template react-ts` defaults to it. Esbuild dev + Rollup prod = fast iteration, small bundles. Reject: Webpack (legacy DX), Turbopack (still beta for non-Next.js usage as of plan-writing). |
| Routing | TanStack Router | 1.x | Type-safe routes (compile-time path/param checking matches our TS-everywhere discipline), file-based routing supported but optional. React Router was strongly considered (more mindshare) but TanStack Router's typed-search-params model fits the meeting-detail / project-detail / chat URL surface we'll have. |
| Data fetching | TanStack Query | 5.x | Cache + revalidation + optimistic updates + SSE/WebSocket integration — all the patterns we need for `/api/v1/billing/*`, `/api/v1/transcribe/*` SSE streams, and Supabase Realtime subscriptions. Default for any non-trivial React app in 2026. Reject: SWR (less feature-rich), Redux Toolkit Query (more coupled to Redux). |
| UI primitives | shadcn/ui | latest (copy-paste model, not versioned) | Component-source-in-repo model. Agents read + modify components like normal source files — no opaque library abstractions. Tailwind-native. |
| CSS | Tailwind | v4.x | shadcn/ui v4-compatible release. Tailwind v4 dropped the JS config file in favor of CSS-native `@theme` directive — simpler. PostCSS-free pipeline reduces moving parts. |
| State management | **Zustand** | 5.x | Most app state is global-by-default (current vault, signed-in user, active meeting, UI prefs) → Zustand's single-store-with-slices model fits. Reject **Jotai**: better for fine-grained atomic dependencies (Figma-like graphs, complex form state) but we don't have that pattern — would be over-engineered. Component-local state stays in React `useState` per usual. |
| Unit tests | Vitest | 3.x | Vite-native test runner — same config + transforms as the dev build. Drop-in Jest-compatible API. |
| E2E / desktop tests | Playwright | 1.x | Tauri 2 has a `@tauri-apps/cli test` runner that drives Playwright under the hood — same author-once-run-everywhere flow as web Playwright tests. |
| Package manager | pnpm | 9.x | Already specified in §3.2. Strict-node_modules layout catches phantom dependencies — important when bundling. |
| Workspace tool | pnpm workspaces | — | TS side. Python side stays uv workspaces (`apps/api`, `packages/mcp-tools`). |
| MCP SDK (client + server) | `@modelcontextprotocol/sdk` | latest TS | Already specified in §6. Most mature SDK in the MCP ecosystem; first-party Anthropic. |
| Backend bridge | Tauri 2 + Rust | 2.x | Already specified throughout. Native FS, IPC, system tray, auto-updater, OS keychain via the `keyring` crate, deep-link via `tauri-plugin-deep-link`. |
| CI / cross-build | GitHub Actions + `tauri-action` | latest | Cross-compiles Windows MSI/NSIS + macOS DMG (if ever added) + Linux AppImage from a single workflow. Code-signing certificate flow built in. |
| Code signing | Windows: EV cert (provisioned during release prep) | — | Avoids SmartScreen warnings for end users. EV cert procurement is a manual founder-task before v1.0 ship; v0.x betas can ship unsigned with a SmartScreen warning + install instructions. |

**Things this section does NOT pin** — left to plan-writing or first-implementation discretion:
- Animation library (Framer Motion likely, but maybe none if shadcn primitives are enough).
- Form library (React Hook Form likely if forms grow complex; bare controlled inputs are fine for the few we have).
- i18n library (out of scope v1.0 — Russian + English markdown content; UI strings stay hardcoded Russian as in the legacy app).
- Telemetry / error tracking SDK (Sentry / PostHog / etc. — decide when wiring observability, not now).
- Markdown renderer for transcript/protocol viewers (likely `react-markdown` + `remark-gfm`; verify when building §7.14 chat UI).

**Phase 2 candidates** (deferred): server-side rendering hooks (React 19 RSC) if we ever surface meeting content on a web view; Cowork plugin packaging if community demand emerges.

## 3. Architecture

### 3.1 Five-tier overview

```
┌─────────────────────────────────────────────────────────────┐
│ User Desktop (Tauri 2) — owns ALL user data                  │
│   React UI ──── Tauri Rust core ──── Python MCP sidecar     │
│   (shadcn/ui)   (mic, FS, keychain,  (PyOxidizer-embedded)  │
│                  SQLite + sqlite-vec,                        │
│                  deep-link)                                  │
│   Local SQLite holds: transcripts, segments, tasks,         │
│                       voice embeddings, audio path index    │
└──────────────┬──────────────────────────────────────────────┘
               │ HTTPS (REST + SSE)   Supabase Realtime WS
               │ (in-transit proxy)   (billing updates only)
               ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI Backend (Railway) — stateless re user data           │
│   /api/v1/* REST   /mcp (HTTPS — billing/quota only)         │
│   /webhooks/stripe                                           │
│   In-memory proxies (zero persistence):                      │
│     • streaming STT (audio → provider → SSE result)         │
│     • LLM task-extract (transcript → OpenRouter → SSE)      │
│     • voice-enroll (audio → ECAPA-TDNN → embedding vector)  │
│     • voice-identify (audio + candidate embeddings → match) │
│     • Linear/Glide send (task data → external API)          │
│   Lifted Python: providers/, tasks/, cloud_chunker,         │
│                  audio_io, voice_library, enrollment_worker │
└──────────────┬──────────────────────────────────────────────┘
               │
       ┌───────┼─────────┬──────────┬────────────┐
       ▼       ▼         ▼          ▼            ▼
   Supabase  STT     Stripe   OpenRouter   Linear/Glide
   (Auth +   cloud   (billing) (LLM for    (task
    Postgres APIs              task        backends)
    — billing                  extraction)
    + quota
    + auth                                
    only)
```

External MCP clients:
- **Claude Desktop** → connects to Tauri stdio sidecar (via spawned Python subprocess).
- **Cursor, web agents** → connect to backend `/mcp` HTTPS endpoint.

Bi-directional client role:
- **Tauri React app** consumes external MCP servers (file system, web search, user's own Linear MCP) via `@modelcontextprotocol/sdk` in the webview process. Settings page lets users add URLs + tokens. External tools become available inside transcript views (e.g. "search the web for this term").

### 3.2 Approach A (monorepo) — repository layout

```
audio-transcriber-saas/
├── apps/
│   ├── desktop/                # Tauri 2 + React + TS
│   │   ├── src/                # React app
│   │   ├── src-tauri/          # Rust commands + Python sidecar bundle
│   │   └── package.json
│   └── api/                    # FastAPI backend
│       ├── audio_transcriber_api/
│       │   ├── routes/         # REST endpoints
│       │   ├── mcp_endpoint.py # HTTPS MCP mount
│       │   ├── webhooks/       # Stripe webhook handlers
│       │   ├── streaming.py    # audio chunk piping
│       │   └── auth.py         # Supabase JWT validation, RLS role-set
│       ├── tests/
│       └── pyproject.toml
├── packages/
│   ├── shared-types/           # TS types generated from FastAPI OpenAPI
│   └── mcp-tools/              # Pydantic + handlers (SOURCE OF TRUTH)
│       ├── transcribe.py
│       ├── voice_library.py
│       ├── postprocess.py      # 8 LLM passes: summary/protocol/decisions/topics/tasks/open_questions/insights/agenda
│       └── pyproject.toml      # imported by apps/api AND sidecar
├── infra/
│   ├── supabase/               # SQL migrations, RLS policies, edge functions
│   └── stripe/                 # product/price config, fixture seeds
├── docs/
├── .github/workflows/          # CI: lint, test, build .msi, deploy api
└── pnpm-workspace.yaml + pyproject.toml (uv workspace)
```

Tooling:
- pnpm workspaces for TS code (`apps/desktop`, `packages/shared-types`).
- uv workspaces for Python code (`apps/api`, `packages/mcp-tools`).
- A top-level `Makefile` (or `just`-file) coordinates cross-language builds.

### 3.3 Single source of truth contracts

Two cross-stack contracts must never drift:

1. **REST API**: FastAPI auto-generates `openapi.json`. CI runs `openapi-typescript openapi.json -o packages/shared-types/api.d.ts`. Frontend imports `{ paths } from '@audio-transcriber/shared-types'`. A breaking backend change becomes a frontend TS compile error → CI blocks merge.

2. **MCP tools**: defined once in `packages/mcp-tools/*.py` using Pydantic models + `@mcp.tool()` decorators. Each tool carries a `scope` tag (`"billing"` or `"user_data"`). `apps/api` mounts `mcp.streamable_http_app()` at `/mcp` with a `scope_filter=["billing"]` so HTTPS clients see only billing/quota/usage tools — backend has no user data to serve. The Python sidecar (`apps/desktop/src-tauri/python-sidecar/mcp_stdio_entry.py`) imports the same package, registers a local-data fetch handler, and calls `mcp.run(transport="stdio")` with no scope filter — full tool surface, reading from the user's local SQLite. Same Pydantic schemas + handler bodies across transports; the only divergence is which subset is registered and where the data lives.

### 3.4 User data handling (local-first, zero server persistence)

**Nothing the user creates persists in our infrastructure.** All audio, transcripts, extracted tasks, and voice-library embeddings live only on the user's device. The backend functions as a stateless proxy for cloud-bound operations and only persists the minimum operational state needed to run a managed service: auth, subscription tier, quota usage, MCP API tokens, user settings.

Server data inventory:

| Persisted server-side | Not persisted server-side |
|---|---|
| User identity (Supabase `auth.users`) | Audio files (any format) |
| Subscription / Stripe state | Audio chunks during streaming |
| Aggregate usage counters (minutes, cost) | Transcript text |
| MCP API token hashes | Transcript segments |
| User preferences (locale, default provider) | Extracted tasks |
| | Voice-library embeddings |
| | Linear/Glide task payloads |
| | Original filename / mime / source path |

In-transit visibility (necessary, time-bounded):

The backend sees user content in RAM during each proxy call: audio chunks while routing to the STT provider, transcript text while routing to OpenRouter for task extraction, task data while routing to Linear/Glide, an audio sample while computing an ECAPA-TDNN embedding. Each buffer is garbage-collected when the request completes. No disk writes, no DB inserts, no log entries containing payloads (logs use opaque request IDs only).

Local store on the user device:

- All user data lives inside a single **vault** (a normal folder on disk — see §3.5 for layout). Default location `~/Documents/Audio Transcriber/`, user can move it via Settings.
- **Audio + transcripts + tasks + per-meeting metadata** live as files on disk inside the vault, one folder per meeting. Files are the source of truth for content — readable, editable by external tools (Obsidian, VS Code, GitHub).
- **SQLite index** at `<vault>/.audio-transcriber/index.db` holds segments + denormalized metadata for fast search/filter. Rebuildable from the files at any time (`Settings → Rescan vault`).
- **Voice-library embeddings** at `<vault>/.audio-transcriber/voice_library.db` (`sqlite-vec`-backed). Vault-scoped so cross-device cloud-sync includes voice library automatically.
- **Settings** at `<vault>/.audio-transcriber/settings.json` (vault-local preferences) and `%APPDATA%/audio-transcriber/app.json` (install-global, e.g. current vault path, telemetry opt-in).

Implications:

- Supabase **Storage** is not used at all (avatars come from OAuth providers if needed; backups are handled by user-side vault copy, not server-side blobs).
- App-server-mediated cross-device sync is **out of scope for v1.0** (see §14). Users who want cross-device get it for free by placing the vault inside their own cloud-sync folder (Dropbox/GDrive/iCloud/git). Standard caveat: simultaneous edits from two devices may produce a `.conflict.<hash>` file the user resolves manually (mirrors Obsidian behavior).
- Backup / disaster recovery is the user's responsibility — and now trivial. "Back up everything" = "copy the vault folder somewhere safe". Settings shows a "where is your vault?" hint and a one-click "Reveal in Explorer" button.

### 3.5 Vault layout

```
<vault>/                                  # user-chosen path, e.g. ~/Documents/Audio Transcriber/
├── People/                               # people directory (§3.5.1) — one markdown per speaker
│   ├── Иванов Иван Иванович.md           # YAML frontmatter (structured) + markdown body (free-form notes)
│   ├── Петр Петров.md
│   └── ...
├── Project Alpha/                        # one folder per project
│   ├── README.md                         # project description (20-200 words, optional but strongly suggested)
│   │                                     #   used as STT initial_prompt + LLM task-extract context
│   ├── INSTRUCTIONS.md                   # OPTIONAL Cowork-specific agentic instructions (§7.10)
│   │                                     #   read by Claude Cowork when this folder is used as a Cowork Project
│   ├── 2026-05-27 Standup/               # one folder per meeting (display name)
│   │   │
│   │   │   ─── BUCKET 1: SOURCE-OF-TRUTH (root level — user-editable, deletion = data loss) ───
│   │   ├── README.md                     # auto-generated entry-point (§7.16); frontmatter + summary
│   │   │                                 #   excerpt + quick links; regenerated lazily via
│   │   │                                 #   meetings.readme_dirty flag; user edits NOT respected
│   │   ├── audio.m4a                     # original audio (whatever format user provided) — playback source
│   │   ├── transcript.md                 # human-readable transcript with speaker labels
│   │   ├── tasks.json                    # action items + assignee + due_date — sits at root despite
│   │   │                                 #   being LLM-generated because user works with it manually
│   │   │                                 #   (send to Linear/Glide, edit, mark done)
│   │   ├── meeting.toml                  # metadata: created_at, provider, language, duration_seconds,
│   │   │                                 #   code_switching, schema_version, speaker_id → name map,
│   │   │                                 #   silence_intervals (in original time), VAD params used,
│   │   │                                 #   postprocess_status per pass (done/error/pending)
│   │   ├── notes.md                      # OPTIONAL user-authored free-form notes; lazy-created at
│   │   │                                 #   first user write; never touched by the app
│   │   │
│   │   │   ─── BUCKET 2: REGENERABLE CACHE (.cache/ — rebuild = free CPU local, hidden by default) ───
│   │   ├── .cache/
│   │   │   ├── audio.trimmed.wav         # preprocessed audio sent to STT (16 kHz mono PCM_S16);
│   │   │   │                             #   absent if all preprocessing disabled; regenerable from
│   │   │   │                             #   audio.<ext> + meeting.toml params
│   │   │   └── segments.jsonl            # one segment per line; precise timing (ORIGINAL time) +
│   │   │                                 #   speaker_id + language; written incrementally during STT;
│   │   │                                 #   regenerable by re-running STT (API $ cost)
│   │   │
│   │   │   ─── BUCKET 3: LLM-DERIVED ANALYSIS (analysis/ — rebuild = API $) ───
│   │   └── analysis/
│   │       ├── summary.md                # post-process pass: 2-3 paragraph overview; carries
│   │       │                             #   next_meeting:{date,topic,confidence} frontmatter field
│   │       │                             #   when LLM finds a mention with confidence ≥ 0.5 (§7.9)
│   │       ├── protocol.md               # post-process pass (Phase B): structured 5-block MoM
│   │       │                             #   minutes from per-meeting-type template (§7.9)
│   │       ├── agenda.md                 # post-process pass (Phase A): agenda items extracted
│   │       │                             #   from first 5-10 minutes; «*Повестка не
│   │       │                             #   зафиксирована*» fallback when LLM finds none
│   │       ├── decisions.md              # post-process pass: key decisions made
│   │       ├── open_questions.md         # post-process pass: unresolved questions + disagreements
│   │       ├── insights.md               # post-process pass: notable observations / "aha" moments
│   │       └── topics.json               # post-process pass: chapter list with original-time timestamps
│   ├── 2026-05-28 Design Review/
│   │   └── ...
│   └── 2026-05-30 Retro/
│       └── ...
├── Project Beta/
│   └── ...
└── .audio-transcriber/                   # app-state, hidden in Finder/Explorer
    ├── index.db                          # SQLite index over all meetings (rebuildable)
    ├── voice_library.db                  # sqlite-vec speakers (vault-scoped)
    ├── settings.json                     # vault-local prefs (default provider for this vault, etc.)
    ├── protocol_templates/               # per-meeting-type protocol templates (markdown w/ placeholders)
    │   ├── Standup.md                    # seeded at vault-init from built-in defaults
    │   ├── 1-on-1.md
    │   ├── Customer Call.md
    │   └── ...                           # one per seeded meeting_type; user-editable
    ├── reconcile.log                     # last reconcile output (debug)
    └── schema_version                    # for vault-layout migrations
```

File-format conventions:

- **`audio.<ext>`** (bucket 1, root): original audio, untouched. Extension preserved (we don't transcode for storage). This is the **playback source** in the app — segment-click plays from here, so the user always hears what they recorded.
- **`README.md`** (bucket 1, root — meeting-level): auto-generated entry-point read first when meeting folder is opened in Obsidian / Finder / VS Code / Cowork. Composed from `meeting.toml` metadata + first 2-3 sentences of `analysis/summary.md` + quick-link list with counts (e.g. «Задачи — 3 active», «Решения — 2 принято»). Carries `file_type: meeting_readme` frontmatter (§3.5.1) so Obsidian graph view treats it as an index node. **User edits NOT respected** — reconciler backups any manual edits to `README.md.user-edited-<ts>.bak` and regenerates from metadata, then surfaces a toast pointing user to `notes.md` for free-form notes. Lazy regeneration via `meetings.readme_dirty` flag (§4.3) — pre-regenerated on `meeting_view_opened` event, not synchronously after every state change. Full flow in §7.16.
- **`audio.trimmed.wav`** (bucket 2, `.cache/`): fully-preprocessed audio sent to STT (see §7.2 step 4 — full pipeline). Produced from `audio.<ext>` via ffmpeg decode → highpass 80Hz → optional RNNoise denoise → loudnorm (EBU R128 -16 LUFS) → Silero VAD silence trim. WAV format (16 kHz mono, PCM_S16) — uncompressed because (a) it's transient (regeneratable from `audio.<ext>` + recorded params in `meeting.toml`), (b) STT providers prefer uncompressed for accuracy, (c) WAV is universal so cloud_chunker doesn't have to re-encode. Present iff `meeting.toml.silence_removal_applied = true` (default-on) — note the file also contains denoise + loudnorm effects so it's NOT just silence-trimmed original. Not played back to the user (`audio.<ext>` is the playback source — what they actually recorded). Located in `.cache/` because **regenerable for free** (CPU local) — `rm -rf .cache/` is a safe disk-reclaim operation; next transcribe-related action regenerates on demand using the params recorded in `meeting.toml`.
- **`transcript.md`** (bucket 1, root): human-readable. Speakers as `## Speaker: Иван`-style sections, segments as lines with optional `[hh:mm:ss]` timestamps in **original time** (configurable in Settings). Editable in any markdown editor — edits are picked up at next reconcile.
- **`segments.jsonl`** (bucket 2, `.cache/`): one JSON object per line. Fields: `{idx, start, end, text, speaker_id?, provider_speaker_tag?, language?, confidence?, words?: [{text, start, end, confidence?}]}`. Timestamps are **always in original-audio time** (post-trim mapping done before persistence — see §7.2 step 12). `provider_speaker_tag` is the cloud STT's raw label (`"Speaker_A"`, `"spk_0"`, etc. — varies per provider) when diarization-capable provider was used; `speaker_id` is the vault voice library FK assigned by post-pass voice-identify (§7.2 step 16). `words` is the optional word-level timestamps array from providers that support it (Groq, OpenAI Whisper, Deepgram, AssemblyAI, Speechmatics, Gladia — see §7.12 capability matrix); enables word-level highlighting in transcript playback + powers `speaker_aligner` when provider gives word-level but no diarization tags. This file is the precise source for SQLite index. Located in `.cache/` because regenerable from STT re-run (API $ cost — strictly speaking more expensive to recompute than `audio.trimmed.wav`, but classified bucket-2 because the canonical content lives in SQLite `meeting_segments` mirror — file is rebuildable from DB). If a user edits `transcript.md` without editing `segments.jsonl`, the reconciler diffs them — text edits in `.md` flow back into `segments.jsonl` on a best-effort basis (line-by-line alignment); structural changes (segment splits, merges) require re-running transcription.
- **`notes.md`** (bucket 1, root — optional, lazy-created): free-form user notes («Иван выглядел напряжённым в этом месте», «нужно вернуться к этому в next 1-on-1»). Not generated by the app; **lazy-created on first user write** through the UI (`Add notes` button in meeting view → opens editor → saves the file). Once present, `README.md` regenerator links to it in Quick links («Заметки — 3 entries»). No frontmatter required; `file_type: notes` if user wants to carry one for Obsidian-graph purposes. **App never edits or deletes `notes.md`** — pure user-owned bucket-1 file.
- **`<vault>/People/<Speaker Name>.md`** (one per enrolled speaker, structured profile): YAML frontmatter (machine-readable, synced to `voice_library_speakers` SQLite mirror) + markdown body (free-form notes, lightly edited by humans). Example:

  ```markdown
  ---
  schema_version: 1
  file_type: person
  speaker_id: 01J9R...K2
  display_name: Иван
  full_name: Иванов Иван Иванович
  organization: ACME Corp
  role: CTO
  projects:
    - Project Alpha
    - Project Beta
  embedding_version: v2-denoised

  # Contact channels (used by §7.15 protocol distribution)
  email: ivan@acme.com
  telegram_chat_id: 123456789      # populated when recipient first /starts our shared bot
  auto_distribute_protocols: true  # per-speaker opt-out flag (default true if absent);
                                   #   false → speaker excluded by default from preview-dialog
                                   #   recipient list with «BLOCKED — opt-out» label
  protocol_distribution_channels:  # priority order; first available channel used by default
    - email
    - telegram

  created_at: 2026-05-27
  archived_at: null
  generated_by: user               # all People/ files are user-authored
  ---

  # Иван Иванов

  ## Должностные обязанности
  - Технический strategy + roadmap ownership
  - Архитектурные решения (auth, infra, data layer)
  - Code review для critical PRs
  - Hiring engineering

  ## Заметки
  Любит pragmatic decisions. Prefers async written discussion. Часовой пояс UTC+5.
  ```

  **Privacy note for contact fields:** these live as plain-text in vault. Vault sync (Dropbox/iCloud/GDrive folder, git) carries contacts to all devices — convenient for the user, but means **vault sharing = contacts sharing**. At first contact-field entry through Settings → Voice Library, app surfaces a one-time warning dialog: «Контакты участников сохраняются в plain-text в `People/<name>.md` и идут вместе с vault при cloud-sync. Не помещай vault в публично-расшаренный folder если не хочешь делиться контактами.» User acknowledges to proceed. Alternative «keep contacts only in OS keychain» path was considered (§7.15) but rejected for v1.0 because (a) Cowork-agent access pattern requires plain-text frontmatter to work, (b) team-vault use-case (where contacts are intentional shared knowledge) is the dominant target, (c) cross-device UX requires re-entering contacts per device with keychain.

  Voice embedding (binary, 192-float ECAPA-TDNN) lives in `<vault>/.audio-transcriber/voice_library.db` keyed by `speaker_id` — not embedded in the markdown (binary doesn't fit cleanly + would balloon file size). The markdown file is the source of truth for everything else; reconciler keeps SQLite mirror up-to-date. User can edit in Obsidian / VS Code / any markdown editor.
  
  `projects` array in frontmatter declares this speaker's project associations (many-to-many, mirrored into `speaker_projects` SQLite junction — §4.3). Powers declarative smart defaults (§7.8).

  Default speaker file is auto-created at enrollment with minimum fields (display_name only); user expands fields over time via Settings → Voice Library UI or by editing the markdown directly.
- **`<vault>/<Project>/INSTRUCTIONS.md`** (one per project, optional): Cowork-specific agentic guidance, read by Claude Cowork when this folder is added as a Cowork Project (§7.10). Distinct from README.md: README describes **WHAT** the project is (used by our pipeline for STT + LLM context); INSTRUCTIONS describes **HOW** Cowork should behave (used by Cowork's agent). Example: "When asked about action items, prefer tasks.json over re-parsing transcript.md. When generating weekly digests, group by speaker." Seeded by our app with a generic template on first project creation; fully user-editable; can be deleted entirely if user doesn't use Cowork. Not consumed by our backend or post-processing pipeline — purely Cowork-facing.
- **`<vault>/<Project>/README.md`** (one per project, not per meeting): free-text description of the project — what it's about, key people, product names, domain jargon, internal terminology. 20-200 words target (enforced client-side at edit). Strongly-suggested, never required. Used at runtime as:
  - **STT `initial_prompt` / `context_prompt`** where the chosen provider supports it (Groq Whisper, OpenAI Whisper, Gladia, Speechmatics). Helps recognize proper nouns, abbreviations, project-specific vocabulary. Truncated to provider's token cap (Whisper-family = 224 tokens ≈ 170 English words / 140 Russian words).
  - **All LLM post-processing passes' system prompt prefix** (§7.9) — prepended so every generated artifact (summary, protocol, tasks, decisions, topics, open_questions, insights) is framed in the project's context.
  - Provider-specific term-boosting (AssemblyAI `word_boost`, Deepgram `keywords`) extraction from this text is a Phase-2 enhancement.

  Reconciler computes `description_word_count` on every change. Banner shown on project screens while count < 20 (offers "Add description" CTA); silently fine at 20-200; warning shown at > 200 ("only the first 200 words are used in STT/LLM context") with edit affordance.
- **`tasks.json`** (bucket 1, root): array of `{id, title, description, assignee_speaker_id?, assignee_name?, due_date?, confidence, source_segment_idx?, sent_to?, external_id?, created_at}`. Produced by the post-processing `tasks` pass (§7.9). `assignee_*` resolved from voice library when possible (LLM matches "Иван возьмёт" → speaker_id); else `assignee_name` holds the raw string. `due_date` parsed from natural language ("к пятнице", "by Friday"). `confidence` is LLM self-reported. `source_segment_idx` cross-references `segments.jsonl` for click-to-play. **Lives at root despite being LLM-generated** because user works with it manually (sends to Linear/Glide/Notion/Jira/etc., marks done, edits inline) — actionable artifact, not analysis.
- **`analysis/summary.md`** (bucket 3): post-process pass output — 2-3 paragraph free-form overview. Written by `summary` pass. **Frontmatter additionally carries `next_meeting: {date, topic, confidence}`** when LLM extracts «увидимся в четверг»-style mentions with confidence ≥ 0.5 from transcript (§7.9) — rendered in protocol.md Block 5 (next steps). Human-edited freely; reconciler treats user edits as authoritative and skips regeneration unless user clicks "Regenerate".
- **`analysis/protocol.md`** (bucket 3): post-process pass output — full structured meeting minutes in **5-block MoM format** (Метаданные / Повестка / Тезисы+решения / Action items / Ссылки и следующая встреча — see §7.9 for full template), generated from per-meeting-type template (`<vault>/.audio-transcriber/protocol_templates/<TypeName>.md`) with placeholders filled by `protocol` pass. Composes summary + agenda + decisions + tasks + open_questions + insights into one shareable document. Frontmatter `derived_from` declares dependencies on Phase A outputs — reconciler-invalidated when any dependency changes. Same edit-respecting semantics as `summary.md`. **This file is the canonical artifact distributed via Email/Telegram** (§7.15) — user's preview-and-send dialog edits operate on a copy, not this file directly.
- **`analysis/agenda.md`** (bucket 3): post-process pass output — bulleted list of agenda items LLM extracted from first 5-10 minutes of `transcript.md`. Skipped (file contains «*Повестка не зафиксирована в первых минутах встречи. Опционально дополни вручную.*») when LLM confidence below threshold. Typically 1-5 items. Used as Block 2 («Повестка дня») in `protocol.md`. Distinct as a separate Phase-A pass (not embedded in summary) because (a) agenda is structurally different content type than narrative summary, (b) some meeting types (Demo, Standup) reuse the same agenda template each time — single-pass agenda extraction lets user verify/edit independently of summary regeneration.
- **`analysis/decisions.md`** (bucket 3): post-process pass output — markdown list of "the team decided X because Y" items. Distinct from `tasks.json` (decisions are conclusions reached; tasks are actions to take).
- **`analysis/open_questions.md`** (bucket 3): post-process pass output — markdown list of unresolved questions raised during the meeting + points of disagreement that didn't reach a decision. Powerful for follow-up planning ("we still need to figure out Z").
- **`analysis/insights.md`** (bucket 3): post-process pass output — qualitative observations the LLM surfaced from the conversation (e.g. "Иван repeatedly emphasized customer churn — may indicate a priority shift", "team energy dropped after the budget discussion"). Optional / experimental — users may disable this pass if they find it noisy.
- **`analysis/topics.json`** (bucket 3): post-process pass output — `[{idx, title, start_seconds, end_seconds, segment_idx_range}]`. Original-time timestamps for chapter navigation. Rendered as a sidebar timeline in the meeting view; click jumps audio + scrolls transcript. Skipped for meetings shorter than 10 minutes (chapter overhead not worth it).
- **`meeting.toml`**: small, hand-readable. TOML chosen over JSON for comments + multi-line strings (notes field). Contains schema_version for forward-compat. Top-level fields include the pre-transcription metadata block (captured at import time — see §7.2 step 1):

  ```toml
  meeting_date = 2026-05-27           # TOML native date; default at import = audio file mtime
  meeting_type = "Standup"            # by-name reference; user can rename freely without breaking
  participant_speaker_ids = [
    "01J9R...K2",                     # uuid v7 from voice_library_speakers
    "01J9S...M4",
  ]
  ```

  And the `silence_removal` block:

  ```toml
  [silence_removal]
  applied = true
  removed_seconds = 412.3       # for cost-saved display ("you saved 6.9 min")
  vad_model = "silero-v5.1.2"   # which VAD model version cut these intervals
  vad_threshold = 0.5           # parameters used (so re-running reproduces)
  min_speech_ms = 250
  min_silence_ms = 500
  speech_pad_ms = 200
  intervals = [                  # in ORIGINAL-audio seconds; sorted ascending
    [12.4, 14.8],
    [38.1, 41.0],
    # ...
  ]
  ```

  When `silence_removal.applied = false` or the block is absent, no trimmed file exists and segments map straight from STT output.

Naming rules (enforced by Tauri commands on create/rename):

- Project and meeting folder names sanitized for cross-platform FS compatibility (drop `/\:*?"<>|`, trim trailing whitespace/dots, max 200 chars).
- Display names stored verbatim in `meeting.toml` and `index.db`; folder names are the sanitized version. Conflict resolution: append ` (2)`, ` (3)`, etc.
- "Untitled Project" / "Untitled Meeting <date>" defaults if user doesn't name on create.

Why TOML for `meeting.toml`: JSON for machines (`segments.jsonl`, `tasks.json`), TOML for human-edited (a user may open `meeting.toml` in a text editor to fix a typo in a speaker name or add a note). Same rationale as `pyproject.toml` and Cargo manifests.

The vault layout is itself versioned (`schema_version` file at vault root + per-meeting `meeting.toml.schema_version`). Layout migrations on app upgrade are explicit, opt-in (user clicks "Upgrade vault"), and backed up to `.audio-transcriber/migration-backups/` before applying.

### 3.5.1 Frontmatter conventions

**All `.md` files in the vault carry YAML frontmatter** (except `INSTRUCTIONS.md` which is Cowork-only and pure markdown). This enables three things:

1. **Obsidian graph view** picks up structured relationships between files automatically.
2. **Machine-readability** for external tools (Cowork, user scripts, future migrations) without re-parsing the body.
3. **Reconciler invalidation** through declarative `derived_from:` field — explicit dependency DAG vs ad-hoc invalidation in code.

**Universal fields (every `.md` file):**

```yaml
schema_version: 1                  # integer; for migrations across app versions
generated_at: 2026-05-27T14:23:11Z # ISO timestamp; null for user-authored content
generated_by: "audio-transcriber@1.0.0" | "user" | "llm-pass:<pass_type>" | "reconciler"
```

**Per-file-type fields (additive to universal):**

**Meeting `README.md`:**

```yaml
file_type: meeting_readme
meeting_id: 01J9R...K2
project: "Project Alpha"
project_id: 01J9R...P0
meeting_date: 2026-05-27
meeting_type: "Standup"
meeting_type_id: 01J9R...T1
duration_seconds: 1842
participants:                      # speaker_id refs (immutable anchor)
  - "01J9R...M4"
  - "01J9R...P7"
status: "done"
```

**`transcript.md`:**

```yaml
file_type: transcript
meeting_id: 01J9R...K2
project: "Project Alpha"
meeting_date: 2026-05-27
participants:
  - "01J9R...M4"
  - "01J9R...P7"
provider: "groq"                   # STT provider that produced the source segments
language: "mixed"                  # "ru" | "en" | "kk" | "mixed"
provider_diarization: true         # true if segments had provider_speaker_tag
voice_identify_applied: true
# user_edited_at: 2026-05-27T15:00:00Z   # appears if reconciler detects body edits
```

**`analysis/<pass>.md` (all LLM-derived):**

```yaml
file_type: analysis_pass
pass_type: summary | protocol | decisions | open_questions | insights | agenda
meeting_id: 01J9R...K2
project: "Project Alpha"
meeting_date: 2026-05-27
derived_from:                       # explicit dependency graph for reconciler invalidation
  - transcript.md                   # paths relative to meeting folder
  - meeting.toml
  # protocol.md additionally lists Phase A outputs it composes:
  # - analysis/summary.md
  # - analysis/agenda.md
  # - analysis/decisions.md
  # - analysis/open_questions.md
  # - tasks.json
model_used: "openrouter/meta-llama/llama-3.3-70b-instruct"
cost_usd: 0.0023
input_token_count: 8421
output_token_count: 312
# analysis/summary.md additionally carries (when LLM extracts it from transcript):
# next_meeting:
#   date: 2026-06-03
#   topic: "Sprint Planning"
#   confidence: 0.78
```

**Project `README.md`:**

```yaml
file_type: project_readme
project_id: 01J9R...P0
project_name: "Project Alpha"
description_word_count: 87
created_at: 2026-04-15T09:00:00Z
participants:                       # speakers with `projects:` containing this project
  - "01J9R...M4"
  - "01J9R...P7"
generated_by: user                  # body is user-authored, no generated_at
```

**`<vault>/People/<name>.md`:** see the dedicated example above (§3.5 file-format conventions list — includes `file_type: person`, contact fields, opt-out flag).

**Conventions:**

- **Reconciler-managed fields** (`generated_at`, `derived_from`, `cost_usd`, etc.) reconcile from SQLite on every change. **User-managed fields** (project description text, body content, custom tags) are preserved verbatim — reconciler never strips unknown frontmatter keys (forward-compat). Custom tags like `tags: [important, customer-facing]` survive reconcile unchanged.
- **`derived_from:` is load-bearing** — reconciler reads it to build an inverted dependency index. When `transcript.md` mtime increases, reconciler queries «which files declare `derived_from: [transcript.md]`?» and marks all of them stale. This replaces ad-hoc "regenerate after STT" logic that would otherwise live in code, making the invalidation graph declarative + introspectable.
- **speaker_id-in-frontmatter / name-in-body** split (see §3.5 People/<name>.md note): the immutable anchor (`speaker_id`) lives in frontmatter; the human-readable label (`display_name`) lives in body. Reconciler synchronizes the body label to the current `voice_library_speakers.display_name` when the speaker is renamed — same pattern applies to project names in any `.md` body content. Wiki-links use names (§3.5.2) which then resolve correctly via the SQLite lookup.
- **`schema_version` migration:** when frontmatter `schema_version` is older than current app's expected version, reconciler runs forward-migration functions per field. Migrations are Rust functions in `apps/desktop/src-tauri/src/vault/frontmatter_migrations/v<N>.rs`. Same versioning pattern as `<vault>/schema_version` for the layout itself.
- **Robustness:** if frontmatter is malformed (bad YAML, missing required field) — reconciler logs to `.audio-transcriber/reconcile.log`, marks meeting `needs_review` in UI, restores frontmatter from SQLite (which holds ground-truth for these derived fields). User-managed content in the body is never lost — only the frontmatter block is repaired.

### 3.5.2 Wiki-link conventions

The vault contains two files named `README.md` per project — the project's own (`<vault>/<Project>/README.md`) and one per meeting (`<vault>/<Project>/<Meeting>/README.md`). Obsidian's `[[README]]` syntax is **ambiguous** in this layout — without a path qualifier it falls back to alphabetical filename collision resolution which is unstable. This sub-section specifies the resolution strategy for both Obsidian-user-authored wiki-links and our generated content.

**Three resolution cases:**

| Where the wiki-link is written | Obsidian behavior |
|---|---|
| Inside a meeting folder (e.g. `transcript.md` body) | `[[README]]` resolves to **meeting README** (proximity wins) |
| Inside a project folder (e.g. `<Project>/INSTRUCTIONS.md` body) | `[[README]]` resolves to **project README** (proximity wins) |
| Outside both (e.g. `People/Иван.md` body, vault-root note) | Ambiguous — Obsidian picks by alphabetical collision (unstable across vault changes). **Documented as «avoid; use full path»**. |

**Canonical reference types** (used by our generated content + recommended for user-authored cross-folder links):

- People: `[[Иван Иванов]]` — resolves to `<vault>/People/Иван Иванов.md` (one canonical path, no ambiguity).
- Projects: `[[Project Alpha/README|Project Alpha]]` — full path + display override.
- Meetings: `[[Project Alpha/2026-05-27 Standup/README|2026-05-27 Standup]]` — full path + display override.

**Our generated content always uses full paths** so it survives any vault reorganization. The Obsidian display-override syntax (`[[full/path|short label]]`) keeps the rendered link readable while the underlying reference is canonical.

**Tauri command `resolve_wiki_links(markdown_body, current_file_path)`:**

- Parses `[[ref]]` and `[[ref|display]]` syntax.
- Resolves to canonical SQLite-mirrored path via lookup (by `display_name` for People, by `folder_name` for projects/meetings — handles disambiguation when two speakers have same display name through `(2)` suffix).
- Returns annotated AST that React renderer + Obsidian-export use to produce correct links.

**Cascading rename via reconciler:**

When user renames a speaker (`Иван` → `Иван Иванов`) or project (`Project Alpha` → `Aurora`) through the UI, reconciler scans all `.md` body content in the vault for matching wiki-links + plain-name mentions, updates them, and updates frontmatter `participants:` / `project:` fields. The immutable `speaker_id` / `project_id` in frontmatter is the anchor — body-text labels follow. Performance: lazy + cached — reconciler tracks "last name change" timestamp and only re-walks files modified after the change OR touched by recent reconcile.

**What's NOT a wiki-link:**

- File paths in code-fenced blocks (` ```...``` `) — wiki-link parser respects markdown code fencing.
- Markdown auto-link URLs (`<https://...>`).
- Custom `audio-transcriber://meeting/<id>?t=<seconds>` deep-links (used in tasks-to-:backend payloads, §5.1) — these are URI-scheme links, not wiki-links; handled by the OS deep-link handler when clicked.

## 4. Data model

Two data stores. Server-side: Postgres (Supabase-managed) for billing + auth + quota. Client-side: SQLite + `sqlite-vec` for all user-generated content.

### 4.1 Server tables (6)

| Table | Purpose |
|---|---|
| `users` | Mirror of `auth.users` (Supabase). id, email, display_name, created_at |
| `subscriptions` | Stripe state. user_id (FK), stripe_subscription_id, tier enum(free,pro,business), status, current_period_end, monthly_quota_minutes |
| `usage_log` | Append-only metering. user_id, billable_unit enum(transcription_minute, llm_postprocess_call, voice_enroll, voice_identify, llm_chat_turn, email_distribution, telegram_distribution), units (numeric), provider, cost_usd, request_id, created_at (indexed). **No reference to user content** — `request_id` is an opaque correlation id, not a transcript id. |
| `mcp_api_tokens` | User-issued HTTPS MCP tokens. id, user_id, name, token_hash bcrypt, scopes jsonb (subset of `["billing","usage","settings"]` — no user-data scopes available), last_used_at, revoked_at |
| `user_settings` | 1:1 with users. ui_locale (ru/en/kk), default_provider, prefs jsonb (telemetry opt-in, default language, etc.) |
| `oauth_tokens` | Encrypted refresh tokens for **email-OAuth providers only** (Gmail, Outlook) — required so backend can refresh access tokens between user sessions when sending emails «from» user's address (§7.15). user_id (FK), provider enum(gmail, outlook), access_token_encrypted (envelope-encrypted with per-user key derived from Supabase auth + KMS), refresh_token_encrypted (same scheme), expires_at, scope, email_address (denormalized for display in Settings; user's «from» address), created_at, last_used_at. **Per-user encryption key** prevents single-secret compromise — leak of one envelope ≠ leak of all tokens. **Why NOT keychain** (the pattern used by all other §7.5 OAuth tokens like Linear/Glide/Drive): email refresh tokens are needed at **scheduled times when desktop app may be offline** (e.g. auto-send if Phase 2 enables it; or recipient re-send retry from backend) — keychain is desktop-bound. v1.0 manual-send model doesn't strictly need server-side storage, but storing here from day-1 avoids painful migration later. All other OAuth providers (Linear/Glide/Notion/Jira/Yandex/Bitrix24/GitHub/Google Drive) remain keychain-stored per §7.5 — desktop-driven flows. |

Dropped versus brainstorm draft:
- `transcriptions`, `transcription_segments` — content lives in client SQLite (§4.3).
- `extracted_tasks` — same.
- `voice_library_speakers` — embeddings live in client `sqlite-vec` (§4.3); ECAPA-TDNN inference stays on backend, but vectors are returned to client without server persistence.
- `audio_files`, `voice_library_samples` — never existed in this design (audio never persisted).

pgvector extension is **no longer required** server-side.

### 4.2 Row-Level Security (RLS) strategy

Far simpler than the brainstorm draft because only billing/quota/auth tables exist server-side. Every table with `user_id` carries policy `USING (user_id = auth.uid())` for SELECT/UPDATE/DELETE and `WITH CHECK (user_id = auth.uid())` for INSERT. `usage_log` inserts and Stripe webhook updates use the Supabase service-role bypass.

FastAPI receives a Supabase JWT in the `Authorization` header. For each request:
1. Validate JWT signature against Supabase JWKS (cached).
2. Open Postgres connection from pool.
3. `SET LOCAL request.jwt.claims = '<jwt_payload_json>';` — Supabase functions inside RLS policies read this to derive `auth.uid()`.
4. Execute query; RLS filters automatically.
5. Release connection.

Requires PgBouncer in **transaction pooling** mode (Supabase default has session pooling — use the transaction-mode connection string `*.supabase.co:6543`).

RLS surface is tiny — easier to audit, easier to test exhaustively. There are no transcript-shaped joins, no cross-user content leak vectors. The worst possible RLS failure exposes billing/quota for another user (still bad, but bounded).

### 4.3 Client-side state (vault SQLite + sqlite-vec)

SQLite database lives at `<vault>/.audio-transcriber/index.db`, opened with `sqlite-vec` loaded. WAL mode so the Python stdio sidecar can read while the Tauri Rust process writes (see §6.3). Install-global state (current vault path, telemetry opt-in) lives in `%APPDATA%/audio-transcriber/app.json` — outside the vault.

| Table | Purpose |
|---|---|
| `projects` | id (uuid v7), name (display), folder_name (sanitized), created_at, archived_at nullable, sort_order, **description text default '' (mirror of `<vault>/<Project>/README.md` body; reconciler keeps in sync)**, **description_word_count int default 0 (derived; drives the < 20-word banner UI)**. One row per project folder under the vault. |
| `meetings` | id (uuid v7), project_id (FK, indexed), name (display), folder_name (sanitized), created_at, completed_at nullable, **meeting_date date (default = audio file mtime; user-editable at import)**, **meeting_type_id (FK meeting_types, nullable, indexed)**, status enum(queued,queued_offline,vad_running,processing,identifying,postprocessing,done,error,audio_missing,audio_silent), provider, language, code_switching bool, duration_seconds (original audio length), trimmed_duration_seconds nullable (length after VAD trim — billable to STT provider), silence_removal_applied bool default true, silence_removed_seconds nullable, audio_ext, error_message nullable, backend_request_id (transient correlation), **readme_dirty bool default true (lazy-regenerate trigger for `<meeting>/README.md` — see §7.16; set true on any state change affecting README content, cleared on regeneration)**. Replaces what the brainstorm draft called `transcripts`. `queued_offline` means audio preprocessing completed but transcribe is in `offline_queue` (§7.13). |
| `meeting_types` | id (uuid v7), name unique (display), color (hex string, optional — for dashboard chips/charts), icon (optional Lucide name), sort_order int, archived_at nullable. Vault-scoped; seeded with 10 defaults at vault-init: Standup, 1-on-1, Design Review, Sprint Retro, Sprint Planning, Demo, Interview, Customer Call, Workshop, Other. Seed runs ONLY on the first schema migration — subsequent app upgrades never re-insert defaults (user-deleted types stay deleted). |
| `meeting_participants` | meeting_id (FK, indexed), speaker_id (FK voice_library_speakers), is_pre_specified bool. `is_pre_specified=true` means the user listed this speaker in the import dialog (used as voice-identify candidate set — §7.2 step 16). `is_pre_specified=false` means voice-identify discovered the speaker post-transcription (matched against library beyond the narrowed set). Primary key (meeting_id, speaker_id). |
| `meeting_postprocess_runs` | meeting_id (FK, indexed), pass_type enum(summary,protocol,decisions,topics,tasks,open_questions,insights,**agenda**), status enum(queued,running,done,error,skipped,user_edited), started_at, completed_at nullable, error_message nullable, cost_usd nullable, model_used (text — e.g. "openrouter/meta-llama/llama-3.3-70b-instruct"), input_token_count nullable, output_token_count nullable. Primary key (meeting_id, pass_type). One row per pass per meeting; tracks the post-processing pipeline (§7.9). `status="user_edited"` means user modified the artifact after generation — regenerate is gated behind a confirm dialog to avoid clobbering edits. `agenda` pass added in v1.0 alongside the original 7 to support the 5-block MoM `protocol.md` template (§7.9). |
| `meeting_segments` | meeting_id (FK, indexed), idx, start_seconds, end_seconds, text, speaker_id nullable, language (per-seg for code-switching), confidence. Mirrors `<meeting>/.cache/segments.jsonl` on disk; rebuilt on reconcile. **Source-of-truth direction**: SQLite is the authoritative mirror; `segments.jsonl` rebuildable from SQLite. Both are derived from STT output during transcription. |
| `meeting_tasks` | id, meeting_id (FK), title, description, sent_to enum(none,linear,glide), external_id nullable, status, created_at. Mirrors `tasks.json` on disk. |
| `voice_library_speakers` | id (uuid v7), **display_name (was: name — short for UI)**, **full_name nullable (formal ФИО — "Иванов Иван Иванович")**, **organization nullable (employer / company)**, **role nullable (job title — "CTO", "Product Manager")**, **responsibilities text nullable (longer description — markdown allowed)**, created_at, archived_at nullable, embedding (192-float ECAPA-TDNN vector via `sqlite-vec` vec0 virtual table), **embedding_version enum(v1-raw, v2-denoised)** — `v1-raw` for legacy entries imported from the Python audio-transcriber's voice library (trained on raw audio); `v2-denoised` for entries enrolled by the Tauri app (trained on RNNoise-denoised audio). Identify pass (§7.2 step 16) routes per-version: raw slice for v1, denoised slice for v2. Settings → Voice Library has "Migrate to denoised" button that re-enrolls each speaker. Mirrors `<vault>/People/<display_name>.md` — reconciler keeps SQLite + file in sync. Vault-scoped — cross-device sync of voice library happens for free if the vault is in a synced folder. |
| `speaker_projects` | speaker_id (FK voice_library_speakers, indexed), project_id (FK projects, indexed), role_in_project nullable (free-form — "lead engineer", "stakeholder", etc.), added_at. Primary key (speaker_id, project_id). Many-to-many junction. Declarative associations powering smart defaults (§7.8) AND used as filter source for "show speakers in this project" UI views. Mirrors `projects:` array in `<vault>/People/<speaker>.md` frontmatter — reconciler keeps in sync. |
| `meeting_segment_speakers` | meeting_id, segment_idx, speaker_id (FK voice_library_speakers, nullable). Many-to-one mapping from segments to voice-library speakers. Kept separate so reconcile from files doesn't churn the speaker assignment. |
| `mcp_external_servers` | url, keychain_ref (token in OS keychain, only ref here), enabled, name. User's external MCP connections (§6.4). Vault-local. |
| `fts_segments` | SQLite FTS5 virtual table indexing `meeting_segments.text` for fast full-text search across the entire vault. |
| `reconcile_state` | last_full_reconcile_at, vault_layout_version, individual meeting reconcile timestamps. Lets startup-reconcile do incremental work instead of re-scanning the whole vault. |
| `backup_history` | id, started_at, completed_at nullable, status enum(running,success,failed,aborted), gdrive_file_id nullable, bytes_uploaded nullable, file_count nullable, manifest_sha256 nullable, error_message nullable. One row per backup attempt (§7.7). |
| `distributions` | id (uuid v7), meeting_id (FK, indexed), sent_at (indexed), channel enum(email, telegram), recipient_speaker_id (FK voice_library_speakers, nullable — null if recipient was ad-hoc address not in voice library), recipient_address (denormalized email or telegram_chat_id — preserves history even if speaker's contact field changes later), recipient_display_name (denormalized at-send-time), status enum(sent, failed, bounced, blocked_opt_out, pending), backend_request_id (correlation with usage_log), subject_line (for email; null for telegram), error_message nullable, retry_count int default 0. One row per recipient per send. Mirrors `<vault>/.audio-transcriber/distributions.log` JSONL append-only mirror (§3.5). Distribution audit log (§7.15) — answers «когда я последний раз отправил протокол Ивану?» / «какие митинги были разосланы external customer X за last quarter?» / «failed bounces last month — нужно обновить контакты». Queryable by meeting_id, recipient, date range. |
| `distribution_drafts` | id (uuid v7), meeting_id (FK, indexed, unique — only one in-flight draft per meeting at a time), subject_template (rendered subject; user-editable), body_markdown (rendered protocol body; user-editable in preview dialog), recipient_overrides jsonb (per-speaker channel choice + send/skip decision from preview-dialog interaction), created_at, last_edited_at, status enum(draft, sending, completed, abandoned). Lives across app restarts so user can come back to half-edited draft. On `status=completed` (i.e. send-button clicked + all per-recipient `distributions` rows written), row is preserved for 30 days then garbage-collected by reconciler (allows user to recall «what did I send?» before audit-log retention). Body lives here (not in `distributions`) because it can be ~10KB markdown and is identical for all recipients of one send. |
| `offline_queue` | id (uuid v7), action_type enum(transcribe, postprocess_pass, embed_meeting, voice_enroll, voice_identify, send_to_linear, send_to_glide, gdrive_backup), action_payload jsonb (REST body that would have been sent), status enum(pending,running,done,failed,cancelled), meeting_id nullable (FK for context grouping), priority int default 100 (lower = sooner), retry_count int default 0, last_error nullable, parent_action_id nullable (links resumed chunks), queued_at, started_at nullable, completed_at nullable. Queue drained by Tauri Rust background runner on app start + on online-status-changed events (§7.13). |
| `transcript_chunks` | id (uuid v7), meeting_id (FK, indexed), idx int (chunk order within meeting), segment_idx_start int (FK into meeting_segments.idx), segment_idx_end int, text (chunk content), token_count int, embedding (sqlite-vec vec0 virtual table, 1536-dim float), embedding_model_version (text — e.g. `"openai-text-embedding-3-small@2024"`), created_at, source_topic_id nullable (FK into post-process topics if chunking used topic boundaries; null if sliding-window fallback). One row per chunk; ~25 chunks per 60-min meeting. RAG retrieval (§7.14) queries this table. |
| `chat_sessions` | id (uuid v7), title (auto-generated from first question, user-editable), scope_type enum(vault, project, meeting), scope_id nullable (project_id or meeting_id for narrower scope), created_at, last_activity_at (indexed for "recent chats" sort), archived_at nullable. One row per chat conversation; cheap. |
| `chat_messages` | id (uuid v7), session_id (FK, indexed), idx int (order within session), role enum(user, assistant), content (text — markdown for assistant), retrieved_chunk_ids jsonb (array of `transcript_chunks.id` used as context for this assistant turn — for citation rendering + audit), tokens_used int (LLM tokens for cost tracking), created_at. |
| `schema_version` | single-row migration tracker for `index.db` itself (separate from vault-layout version) |

Migrations are run by the Tauri Rust process at startup using `rusqlite_migration`. The sidecar opens the same DB read-only — schema is shared but ownership of writes is the Rust side.

Source-of-truth split (hybrid model). Each row maps a file path to its bucket (§3.5), its SQLite mirror (if any), and whether deletion is recoverable:

| File path (relative to meeting folder) | Bucket | SQLite mirror | Regenerable? | Notes |
|---|---|---|---|---|
| `audio.<ext>` | 1 (root) | `meetings.audio_ext` reference | ❌ user data loss | original recording; never modified |
| `README.md` | 1 (root) | — (derived) | ✅ cheap | regenerated by `<vault>/.audio-transcriber/`-managed code from SQLite + summary excerpt; lazy via `meetings.readme_dirty` flag (§7.16) |
| `transcript.md` | 1 (root) | — (regenerated from `meeting_segments` + speakers on export, or merged back from .md edits on reconcile) | ✅ cheap | rendered from `.cache/segments.jsonl` + speaker map; edits flow back to `meeting_segments` via line-alignment heuristic |
| `tasks.json` | 1 (root) | `meeting_tasks` (mirror) | ⚠️ API $ to regenerate | LLM-produced but lives at root because user-actionable — sends to backends, manual edits respected |
| `meeting.toml` | 1 (root) | `meetings` row (mirror) | ✅ cheap (from SQLite) | per-meeting metadata + speaker_id→name map + silence_intervals + VAD params; TOML chosen for hand-editability |
| `notes.md` | 1 (root, optional) | — | ❌ user data loss | user-authored free-form notes; lazy-created; never touched by app |
| `.cache/audio.trimmed.wav` | 2 (cache) | — (not mirrored) | ✅ cheap (CPU local) | preprocessed audio; regenerable from `audio.<ext>` + `meeting.toml` VAD params via §7.2 step 4 pipeline |
| `.cache/segments.jsonl` | 2 (cache) | `meeting_segments` (mirror — kept in sync by reconciler) | ✅ from SQLite | precise segment timing + speaker_id + language per segment |
| `analysis/summary.md` | 3 (analysis) | `meeting_postprocess_runs[summary]` (run metadata only — body in file) | ⚠️ API $ | regenerable via `/postprocess/summary` re-run |
| `analysis/protocol.md` | 3 (analysis) | `meeting_postprocess_runs[protocol]` | ⚠️ API $ | composed from Phase A outputs via per-meeting-type template |
| `analysis/agenda.md` | 3 (analysis) | `meeting_postprocess_runs[agenda]` | ⚠️ API $ | LLM-extracted from first 5-10 min |
| `analysis/decisions.md` | 3 (analysis) | `meeting_postprocess_runs[decisions]` | ⚠️ API $ | |
| `analysis/open_questions.md` | 3 (analysis) | `meeting_postprocess_runs[open_questions]` | ⚠️ API $ | |
| `analysis/insights.md` | 3 (analysis) | `meeting_postprocess_runs[insights]` | ⚠️ API $ | |
| `analysis/topics.json` | 3 (analysis) | `meeting_postprocess_runs[topics]` | ⚠️ API $ | chapter list with original-time timestamps |
| — | — | `voice_library_speakers` embeddings (binary, not file-friendly; export-only) | ⚠️ user re-enroll | binary ECAPA-TDNN vectors via `sqlite-vec`; persisted in `<vault>/.audio-transcriber/voice_library.db` |
| — | — | `fts_segments` (derived index for search) | ✅ from `meeting_segments` | rebuildable via Settings → Rescan vault |
| — | — | `transcript_chunks` (RAG embeddings) | ⚠️ API $ | regenerable via `embed_meeting` Tauri command (§7.14.1) |
| — | — | `distributions` + `distribution_drafts` | n/a | append-only audit log; no file mirror per row (JSONL log mirrors aggregate event list) |
| `<vault>/.audio-transcriber/distributions.log` | n/a (vault-level audit) | `distributions` (queryable mirror) | ✅ from SQLite | JSONL append-only; Cowork/external-tool readable |

**Deletion semantics summary** (for the user-facing «Free up disk space» / «Reset analysis» actions in Settings):

| Action | Effect | Cost to undo |
|---|---|---|
| `rm -rf <meeting>/.cache/` | reclaims preprocessed audio + segments JSONL | Free (regenerable from `audio.<ext>` + SQLite on next access) |
| `rm -rf <meeting>/analysis/` | reclaims all LLM outputs for one meeting | API $ to re-run 7 enabled passes |
| `rm <meeting>/README.md` | reclaims auto-generated index | Free (regenerated on next view) |
| `rm <meeting>/audio.<ext>` | **data loss** — meeting becomes `audio_missing` | Re-record or restore from backup |
| `rm <meeting>/transcript.md` | edits lost; segment data survives in SQLite | Free (regenerated from SQLite on next view) |
| `rm <meeting>/notes.md` | **user data loss** if user had notes | Restore from backup only |
| Entire `<meeting>/` folder removed | full meeting deletion | Restore from backup |

Reconcile policy:

- **Startup reconcile**: walk vault, compare folder mtimes against `reconcile_state`, refresh changed meetings only. Fast unless user did a large external edit.
- **Manual rescan**: Settings button. Forces full walk + diff. Reports any conflicts (e.g. `.cache/segments.jsonl` modified but `meeting.toml.duration_seconds` doesn't match).
- **FS watch (opt-in)**: live updates when user edits `transcript.md` in Obsidian. Off by default — extra OS-level perms + occasional false positives.

Backup: no app-mediated backup. "Back up" = copy or sync the vault. "Export voice library to JSON" exists as a portable supplement (helps when user wants to share voice library across vaults).

## 5. REST API contract

### 5.1 Key endpoints (~17 total — shrunk from the brainstorm draft because the backend has no user content to CRUD)

All endpoints are either **stateless proxies** (in-memory only, SSE response stream) or **operational** (billing/auth/quota/MCP tokens). The client owns persistence.

**Stateless proxies (no user data stored server-side)**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/transcribe/start` | Pre-flight: quota check on `duration_seconds`. Body: `{duration_seconds, mime_type, provider, language, code_switching, project_description?}`. Optional `project_description` is forwarded by the backend to the chosen STT provider as `initial_prompt`/`context_prompt` (truncated to ~224 tokens for Whisper-family). Backend does not store the description — pure in-transit. Returns `{request_id, stream_url, expires_at}`. No DB row written. `request_id` is opaque (used only for usage_log correlation). |
| `POST` | `/api/v1/transcribe/:request_id/stream` | Chunked upload + SSE response. Body: audio chunks (`Transfer-Encoding: chunked`). Response: SSE events `{type: "segment"\|"done"\|"error", ...}`. Client persists segments locally as they arrive. Backend writes usage_log on `done`. |
| `POST` | `/api/v1/voice/enroll` | Body: `{audio_chunk, audio_was_denoised}` — client pre-applies RNNoise via Tauri Rust before sending (v1.0 default), so resulting embedding is trained on cleaned characteristics. `audio_was_denoised=true` for all v1.0 enrollments. Response: `{embedding: [192 floats], embedding_version: "v2-denoised"}`. Client stores embedding + version in local `voice_library_speakers`. Server discards audio after embedding. No DB row written. |
| `POST` | `/api/v1/voice/identify` | Body: `{audio_chunk, audio_was_denoised, candidates: [{speaker_id, embedding, embedding_version}, ...]}`. Caller pre-processes audio to match candidate's training characteristics (denoised slice for v2 candidates, raw for v1). Server computes embedding from audio with matching normalization, scores against candidates in memory, returns `{best_match: speaker_id, score}`. Identification work happens client-side over many segments — server gets called per-segment as needed (or once per transcript with all candidates batched). Mixed-version candidate sets must be split into two calls (one per version) per §7.2 step 16. |
| `POST` | `/api/v1/postprocess/:pass_type` | Generic LLM post-processing endpoint. `:pass_type ∈ {summary, protocol, decisions, topics, tasks, open_questions, insights, agenda}` (8 passes — `agenda` added in v1.0 for protocol-template Block 2; see §7.9). Body: `{transcript_text, language, project_description?, meeting_type?, participants_map?, prior_pass_outputs?, prompt_template?}`. The `prior_pass_outputs` field lets the composing `protocol` pass receive outputs of Phase-A passes as input. The `prompt_template` lets the client override the backend's default per-pass template (used for `protocol` to inject the per-meeting-type template). The `summary` pass additionally returns a `next_meeting: {date, topic, confidence}` JSON object alongside the markdown body when the LLM finds a next-meeting mention in transcript — stored as frontmatter field in `analysis/summary.md` (§7.9), rendered in protocol Block 5 when confidence ≥ 0.5. Response: SSE stream of `{type: "chunk"\|"done"\|"error", payload}` where `payload` schema is pass-specific (JSON array for `tasks`/`topics`, markdown string for `summary`/`protocol`/`decisions`/`open_questions`/`insights`/`agenda`). Backend never persists any input or output — pure proxy to OpenRouter. `tasks/extract` from earlier drafts is replaced by `/postprocess/tasks`. |
| `POST` | `/api/v1/embed` | Batch text → embedding vectors via OpenAI text-embedding-3-small (1536-dim, multilingual incl. RU/KK/EN). Body: `{texts: string[], model_version?}` (model_version pinned by client for consistency — backend rejects requests for unsupported versions to avoid silent drift). Response: `{embeddings: number[][], model_version_used, tokens_billed}`. Backend never persists texts or embeddings — pure proxy. Used by §7.14 indexing pipeline + per-query embedding. |
| `POST` | `/api/v1/chat` | RAG completion endpoint. Body: `{question, retrieved_chunks: [{text, meeting_id, meeting_name, meeting_date, speaker_name?, project_name, timestamp_start, timestamp_end, idx}], conversation_history?: [{role, content}], language_hint?}`. Backend sends question + chunks + history to OpenRouter LLM with a citation-enforcing system prompt. Response: SSE stream `{type: "token"\|"citation"\|"done"\|"error", payload}` — `citation` events emit `{chunk_idx_in_input, inline_marker}` so client can render `[1]`-style inline references with hover-cards linking to source meetings. Backend never persists question, chunks, history, or answer — pure proxy. |
| `POST` | `/api/v1/tasks/send-to-:backend` | Generic task-send endpoint. `:backend ∈ {linear, glide, notion, jira, yandex_tracker, bitrix24, github}`. Body: `{tasks: [...], backend_config: {<per-backend params>}, access_token, refresh_token?}`. Per-backend `backend_config`: Linear `{team_id, project_id}`, Notion `{database_id}`, Jira `{cloud_id, project_key, issue_type}`, Yandex Tracker `{queue}`, Bitrix24 `{portal_url, group_id?}`, Glide `{table_id}`, GitHub `{project_id (GraphQL node id), destination_type: "draft_issue"\|"issue", repo_owner?, repo_name?}` (`repo_owner` + `repo_name` required if `destination_type="issue"`). Each task includes back-link to source meeting (Tauri assembles markdown `[Source: Project Alpha / 2026-05-27 Standup / 12:34](audio-transcriber://meeting/<id>?t=754)`) appended to description so the destination tool surfaces it. Backend never persists tokens or task payloads. Returns `{created: [{task_id, external_id, external_url}]}`. |
| `POST` | `/api/v1/tasks/send-to-webhook` | Generic webhook export. Body: `{tasks: [...], webhook_url, shared_secret?}`. Backend POSTs to `webhook_url` with `{tasks, meta: {meeting_id, project_id, sent_at}}` JSON payload + `X-Audio-Transcriber-Signature: sha256=<hex>` header (HMAC-SHA256 of body with `shared_secret`; omitted if secret is empty). Used for Zapier/Make/n8n/Pipedream and any tool we don't natively integrate. Webhook URL stored in vault `settings.json` per-project or vault-default (no token, no OAuth — just a URL the user provides). |
| `POST` | `/api/v1/distribute/email` | Send protocol via email to one or more recipients (§7.15). Body: `{subject, body_markdown, body_html (server-rendered fallback), recipients: [{email, display_name, speaker_id?}], sender_mode: "ses"\|"oauth_gmail"\|"oauth_outlook", reply_to?: string, meeting_id (correlation only — not persisted in usage_log row body), draft_id (correlation with client `distribution_drafts` row)}`. `sender_mode="ses"` uses our SES infrastructure with `From: notifications@audiotranscriber.io` (Reply-To populated from user's auth email when set). `sender_mode="oauth_gmail"` or `"oauth_outlook"` looks up encrypted refresh token in `oauth_tokens` table (§4.1), refreshes if needed, sends via Gmail/Microsoft Graph API as user's connected account. Response: per-recipient result array `[{recipient_email, status: "sent"\|"failed"\|"bounced", external_message_id?, error_message?}]`. Backend never persists `subject`, `body_markdown`, `recipients`, or per-recipient outcomes beyond `usage_log` (which records `billable_unit=email_distribution`, units=recipient count, opaque request_id — no addresses or content). Per-user rate limit (see §13.19) enforced before send. |
| `POST` | `/api/v1/distribute/telegram` | Send protocol via Telegram to one or more recipients (§7.15). Body: `{message_markdown, recipients: [{chat_id (integer or "@username" string), display_name, speaker_id?}], meeting_id, draft_id}`. Backend uses **shared bot** (`@AudioTranscriberBot`; bot token held in Railway secret, never exposed to client). For each recipient: call `sendMessage` Telegram Bot API with `parse_mode=MarkdownV2`, content auto-escaped per Telegram's MarkdownV2 rules. Recipients who haven't `/start`-ed our bot return Telegram error 403 (Forbidden) → status `failed` with humanized `error_message: "Recipient hasn't started @AudioTranscriberBot yet — share link audiotranscriber.io/tg-start"`. Response: per-recipient result array (same shape as email). Backend never persists message body or chat IDs beyond `usage_log` (`billable_unit=telegram_distribution`). Per-user rate limit enforced. |

**Operational (billing/auth/quota/MCP/settings — persistent)**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/healthz` | Liveness probe (used by client offline-detection — §7.13). Returns `{ok: true, server_time}` in <5s when backend is reachable + database up. Unauthenticated. Cheap (no DB query on hot path; cached liveness check). |
| `GET` | `/api/v1/usage/current` | Quota balance for current billing period. |
| `GET` | `/api/v1/subscriptions/current` | Current tier + Stripe state. |
| `POST` | `/api/v1/billing/checkout` | Create Stripe Checkout session. |
| `POST` | `/api/v1/billing/portal` | Create Stripe Customer Portal session. |
| `POST` | `/webhooks/stripe` | Stripe event handler (signature verified). |
| `GET/POST/DELETE` | `/api/v1/mcp/tokens` | List/issue/revoke HTTPS MCP API tokens. Token value shown once on creation. |
| `GET` | `/api/v1/settings` | Read server-side prefs (locale, default provider). |
| `PATCH` | `/api/v1/settings` | Update server-side prefs. UI-only prefs (theme, layout) stay local. |
| `POST` | `/mcp` | HTTPS MCP server (Streamable HTTP transport). Auth: bearer MCP token. Scope: billing/quota/usage tools only (§6.1). |

**OAuth link flow (Linear, Glide, Notion, Jira, Yandex Tracker, Bitrix24, GitHub, Google Drive — see §7.5 + §7.7; backend brokers the dance but never persists tokens)**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/oauth/:provider/start` | Returns provider authorize URL with CSRF `state`. `:provider` ∈ `{linear, glide, notion, jira, yandex_tracker, bitrix24, github, google, gmail, outlook}`. Per-provider scope chosen for minimum privilege: Notion `databases.read + pages.write`; Jira `read:jira-work + write:jira-work`; Yandex Tracker `tracker.write` (Yandex OAuth quirk: requires explicit consent per scope); Bitrix24 `task` (portal-scoped — user supplies portal URL during start); GitHub `project` (sufficient for DraftIssue mode — default); upgrade-on-demand to `project, repo` when user picks Issue mode for the first time. Google `drive.file`. **Gmail `gmail.send` (send-only, narrower than the default `gmail.modify` — we never read user mail)**; **Outlook `Mail.Send` (Microsoft Graph; same send-only narrowing — we never request `Mail.Read`)**. |
| `POST` | `/api/v1/oauth/:provider/exchange` | Body: `{code, state, [provider-specific extras like Bitrix24 portal_url]}`. Returns `{access_token, refresh_token, [provider_metadata]}` to client; nothing stored. `provider_metadata` carries e.g. Jira's `cloudid` list (user picks which Atlassian cloud to push to), Notion's workspace info, Bitrix24's portal canonical URL — needed for subsequent `/tasks/send-to-:backend` calls. **EXCEPTION for `gmail` and `outlook`**: backend additionally **encrypts and persists** the refresh_token in `oauth_tokens` table (§4.1) keyed by user_id + provider, returns access_token + `email_address` to client. Persistence required because Phase 2 may enable scheduled auto-send when desktop app is offline, and to support backend-initiated retries on bounce; client never sees the persisted refresh token, only the live access_token for in-app use. `provider_metadata` for gmail/outlook = `{email_address, expires_in}`. For all other providers, behavior unchanged (no persistence; client stores tokens in keychain). |
| `POST` | `/api/v1/oauth/:provider/refresh` | Body: `{refresh_token}`. Returns refreshed token pair; nothing stored. Note: Bitrix24 and Yandex Tracker have shorter token TTLs than Linear/Notion — client refreshes lazily on 401. For `gmail` and `outlook`, refresh happens **server-side** automatically before each `/api/v1/distribute/email` call when expires_at < now + 60s — client does not call this endpoint for those providers (server-managed token lifecycle). |

**Auth model per backend (varies — not all backends are OAuth):**

| Backend | Auth method | Notes |
|---|---|---|
| Linear | OAuth 2.0 | Existing pattern |
| Glide | OAuth 2.0 | Existing pattern |
| Notion | OAuth 2.0 | Notion OAuth requires workspace selection at link time |
| Jira (Atlassian Cloud) | OAuth 2.0 | Multi-cloud users see picker; cloudid stored per session |
| Jira (Self-hosted / Server) | Personal Access Token (PAT) | No OAuth; user pastes PAT + base URL in Settings → Integrations → Jira |
| Yandex Tracker | OAuth 2.0 (Yandex OAuth) | Russian-region quirks; backend handles |
| Bitrix24 | OAuth 2.0 (per-portal) | User enters portal URL (`<company>.bitrix24.ru` / `.bitrix24.com` / `.bitrix24.kz`); OAuth scoped to that portal |
| GitHub Projects | OAuth 2.0 (GitHub OAuth App) | Scope `project` default (DraftIssue mode); elevates to `project, repo` on first switch to Issue mode (in-app re-auth prompt). PAT fallback also accepted — user pastes classic / fine-grained PAT in Settings → Integrations → GitHub for self-managed token rotation. |
| Generic Webhook | None (URL + optional shared secret) | No OAuth dance; user pastes URL into Settings |

Note on backend roles: for Linear/Glide/Notion/Jira-Cloud/Yandex/Bitrix the actual API calls go **through the backend** (no CORS for arbitrary origins). For Google Drive uploads from the Tauri Rust process, the client uses the access token to call `googleapis.com` **directly** — backend never sees backup zip contents. This is a stronger privacy posture than the proxy pattern. The backend's role for non-Google providers is purely OAuth-brokering + per-request proxying (token in request body, payload in-memory only).

**Dropped versus the brainstorm draft:**

- `GET /api/v1/transcriptions/:id`, `GET /api/v1/transcriptions`, `DELETE /api/v1/transcriptions/:id` — backend has no transcripts to serve. All transcript CRUD is now a local-SQLite operation invoked via Tauri commands (`local_db_query` and friends).
- `POST /api/v1/tasks/:id/send` (with a server-side id) → replaced by stateless `send-to-linear` / `send-to-glide` taking task bodies inline (the client already owns the task).

### 5.2 Error contract

Errors return `{error_code, message, details?}`. Frontend looks up `error_code` in `i18n/{locale}.json`. Examples: `QUOTA_EXCEEDED`, `PROVIDER_TIMEOUT`, `INVALID_AUDIO_FORMAT`, `STRIPE_PAYMENT_REQUIRED`, `LINEAR_OAUTH_EXPIRED`. No localization on server.

## 6. MCP architecture

### 6.1 Tool surface (split by transport)

Stdio transport (Tauri sidecar — local vault access, full surface ~16 tools). Each tool declares `offline_capable: bool` (§7.13) — Cowork / external MCP hosts use this to hide / skip tools when offline.

| Tool | Scope tag | Offline | Data source |
|---|---|---|---|
| `list_projects` | `user_data` | ✅ | reads vault SQLite |
| `create_project` | `user_data` | ✅ | creates project folder + SQLite row |
| `archive_project` | `user_data` | ✅ | marks archived (no folder deletion — that's a user choice) |
| `list_meetings` | `user_data` | ✅ | reads vault SQLite (optional project filter) |
| `get_meeting` | `user_data` | ✅ | reads vault SQLite + reads `transcript.md` / `tasks.json` on demand |
| `transcribe_audio` | `user_data` | ❌ (queues) | proxies backend `/transcribe/*`, writes meeting folder + SQLite (creates meeting if `meeting_id` omitted) |
| `delete_meeting` | `user_data` | ✅ | removes SQLite rows; folder removal requires explicit `delete_files=true` confirmation |
| `run_postprocess_pass` | `user_data` | ❌ (queues) | proxies backend `/postprocess/:pass_type`; persists output to the appropriate file in the meeting folder + updates `meeting_postprocess_runs` row. `:pass_type` ∈ {summary, protocol, decisions, topics, tasks, open_questions, insights} (§7.9). |
| `get_postprocess_status` | `user_data` | ✅ | returns per-pass status for a meeting; used by UI to show progress badges. |
| `send_tasks_to_backend` | `user_data` | ❌ (queues) | Unified MCP tool for all native task backends. Input: `{backend: "linear"\|"glide"\|"notion"\|"jira"\|"yandex_tracker"\|"bitrix24"\|"github", tasks: [...], meeting_id?}`. Reads stored OAuth token for the backend from keychain, calls `/api/v1/tasks/send-to-:backend`, updates `tasks.json` with external_ids. Backend-specific config (database_id, project_key, queue, github_project_id+destination_type, etc.) resolved from per-backend default in vault settings. Per-task `external_url` returned so MCP host (Cowork) can show "✅ Sent → opened in Jira at <url>". |
| `send_tasks_to_webhook` | `user_data` | ❌ (queues) | Sends task batch to a vault-configured webhook URL (signed payload). Used for ClickUp/Asana/Monday/Trello/Todoist/MeisterTask/MS-anything/etc. via Zapier/Make/n8n routing. Input: `{tasks: [...], webhook_name?}` (multiple named webhooks supported — `default`, `jira-secondary`, `personal-todoist`, etc.). |
| `enroll_speaker` | `user_data` | ❌ (queues) | proxies backend `/voice/enroll`, persists vector to vault `voice_library.db` |
| `identify_speakers` | `user_data` | ❌ (queues) | reads vault speakers, proxies backend `/voice/identify` |
| `list_speakers` | `user_data` | ✅ | reads vault SQLite. Returns rich fields: `{id, display_name, full_name?, organization?, role?, projects[], embedding_version}`. Optional `project_id` filter narrows to one project's declared members. |
| `get_speaker` | `user_data` | ✅ | reads a single speaker's full profile from `<vault>/People/<name>.md` + SQLite (includes `responsibilities` markdown + recent meeting count). |
| `update_speaker` | `user_data` | ✅ | writes structured fields back to `<vault>/People/<name>.md` frontmatter (YAML) + body (responsibilities/notes). Reconciler picks up + updates SQLite mirror. |
| `search_meetings` | `user_data` | ✅ | FTS5 full-text query across the vault |
| `reconcile_vault` | `user_data` | ✅ | triggers manual rescan (useful when user edited files externally) |
| `rag_chat` | `user_data` | ❌ (queues) | RAG over vault transcripts (§7.14). Input: `{question, scope?: vault\|project\|meeting, scope_id?, history?}`. Performs local vec retrieval + proxies backend `/chat` → returns answer with citations. Cowork hosts can use this to ask meeting-aware questions without ingesting vault content into their own context window. |
| `get_usage` | `billing` | ⚠️ stale | reads backend `/usage/current`; returns last cached with `stale: true` flag offline |
| `get_settings` | `billing` | ⚠️ partial | vault settings ✅, backend mirror falls back to last cached when offline |

HTTPS transport (FastAPI `/mcp` — billing only, ~2 tools):

| Tool | Scope tag | Data source |
|---|---|---|
| `get_usage` | `billing` | backend Postgres |
| `get_settings` | `billing` | backend Postgres (server-side prefs only — locale, default provider) |

HTTPS clients (Cursor, web agents, automation scripts run on user's behalf without the desktop app open) cannot touch transcripts, tasks, or speakers. This is by design: the data simply does not exist on the server. The HTTPS surface is intentionally tiny — it lets external agents check the user's quota balance before initiating work and surface billing-tier info, nothing more.

MCP API token `scopes` jsonb is a subset of `["billing", "usage", "settings"]`. There is no `user_data` scope available to HTTPS tokens — even if a user wanted to grant it, the backend has nothing to serve.

HTTPS transport (FastAPI `/mcp` — billing only, ~2 tools):

| Tool | Scope tag | Data source |
|---|---|---|
| `get_usage` | `billing` | backend Postgres |
| `get_settings` | `billing` | backend Postgres (server-side prefs only — locale, default provider) |

HTTPS clients (Cursor, web agents, automation scripts run on user's behalf without the desktop app open) cannot touch transcripts, tasks, or speakers. This is by design: the data simply does not exist on the server. The HTTPS surface is intentionally tiny — it lets external agents check the user's quota balance before initiating work and surface billing-tier info, nothing more.

MCP API token `scopes` jsonb is a subset of `["billing", "usage", "settings"]`. There is no `user_data` scope available to HTTPS tokens — even if a user wanted to grant it, the backend has nothing to serve.

### 6.2 Dual transport

```python
# packages/mcp_tools/__init__.py
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("audio-transcriber")

@mcp.tool(scope="user_data")
async def get_meeting(input: GetMeetingInput, ctx) -> dict:
    # Reads from vault SQLite via ctx.data_source (set per-transport).
    # Optionally hydrates `transcript_md` / `tasks_json` by reading files from the meeting folder.
    return await ctx.data_source.fetch_meeting(input.id, include=input.include)

@mcp.tool(scope="user_data")
async def transcribe_audio(input: TranscribeInput, ctx) -> dict:
    # Proxies backend, then persists result via ctx.data_source.
    request_id = await ctx.backend.start_transcribe(input)
    segments = []
    async for ev in ctx.backend.stream_transcribe(request_id):
        segments.append(ev)
    meeting_id = await ctx.data_source.save_meeting(input, segments)
    return {"meeting_id": meeting_id}

@mcp.tool(scope="billing")
async def get_usage(ctx) -> dict:
    return await ctx.backend.get_usage()

# apps/api/main.py — HTTPS transport, billing-only
from mcp_tools import mcp, make_http_data_source
app.mount(
    "/mcp",
    mcp.streamable_http_app(
        auth=api_token_auth,
        scope_filter=["billing"],
        context_factory=lambda req: McpCtx(
            backend=PostgresBackend(req),
            data_source=None,  # no user data on server
        ),
    ),
)

# apps/desktop/src-tauri/python-sidecar/mcp_stdio_entry.py — stdio, full surface
from mcp_tools import mcp
from .local_sqlite import LocalSqliteSource
from .backend_client import BackendClient

ctx = McpCtx(
    backend=BackendClient(jwt=get_jwt_via_local_bridge()),
    data_source=LocalSqliteSource(db_path=get_sqlite_path_via_bridge()),
)
mcp.run(transport="stdio", context_factory=lambda req: ctx)
```

Same Pydantic schemas, same `@mcp.tool` handler bodies, exposed via two different `context_factory` configurations. The HTTPS mount applies `scope_filter=["billing"]` to drop everything else from the registry. The stdio sidecar gets its JWT and SQLite path from a Tauri-hosted local credential bridge (see §6.3 and §13.1) rather than command-line args.

This satisfies §3.3's "single source of truth" goal — handler logic is not duplicated — while making the data-availability difference between transports explicit in the context plumbing.

### 6.3 Auth + data access per transport

| Transport | Backend auth | Token type | Tool scopes | Local data access |
|---|---|---|---|---|
| HTTPS | `Authorization: Bearer <token>` | MCP API token (bcrypt-hashed) | subset of `{billing,usage,settings}` | none (server has no user data) |
| stdio | Supabase JWT (via local credential bridge — see §13.1) | Session JWT (rotated, lives in OS keychain) | full (`user_data` + `billing`) | direct SQLite read via path from bridge |

The stdio sidecar needs two things from the host Tauri process at startup: (1) the current Supabase JWT to make backend proxy calls, and (2) the path to the local SQLite database to read transcripts/tasks/speakers. Both arrive through a Tauri-hosted local credential bridge (`127.0.0.1:<random>` HTTP server on loopback, one-shot token in argv as the proof-of-spawning, then JWT + SQLite path fetched over the bridge). This avoids exposing the JWT in process listings (the old `--jwt` argv approach) and gives Tauri a single place to revoke sidecar credentials on logout.

SQLite concurrency: the DB is opened with WAL mode by Tauri Rust at startup. The sidecar opens the same file read-only — WAL allows many readers + one writer without locking conflicts on Windows. The sidecar does not write; any write tool routes through the Tauri Rust process via a separate `local_db_command` IPC (the Rust side owns the schema-migration story).

**Open question §13.1:** the local credential bridge design — protocol, token rotation cadence, sidecar restart on JWT rotation — is sketched here but not pinned. Decide during plan.

### 6.4 Client role + external MCP hosts consuming us

**Us as MCP client.** Tauri React UI has a "Connected MCP servers" settings screen. Users add URL + token of external MCP servers (e.g. official Linear MCP, web-search, file-system). Tauri uses `@modelcontextprotocol/sdk` to connect. External tools become invocable inside transcript views via a context menu / command palette.

**Us as MCP server consumed by external hosts.** The stdio sidecar (§6.2) is consumable by any MCP host running on the same machine. Two primary consumers shape testing + UX:

- **Claude Desktop / Cowork (primary target consumer).** Cowork's agentic working sessions over local files match our vault model exactly. **The integration is structural: each `<vault>/<Project>/` folder is a valid Cowork Project** — convertible via Cowork's "convert an existing computer folder into a project" feature. Cowork's 4 components map cleanly: its **Context** = our project folder (auto), **Instructions** = our optional `INSTRUCTIONS.md`, **Memory** = Cowork-scoped (we don't replicate), **Scheduled Tasks** = Cowork's territory (we don't replicate). Full integration flow + Tauri-driven onboarding in §7.10. A user can register our sidecar as an MCP server in their Cowork config and then ask things like "find all open_questions across Project Alpha's last month of standups, draft a follow-up email" — Cowork orchestrates via our tools (`list_meetings`, `get_meeting`, `search_meetings`), reads markdown/json directly from the vault, and writes its own artifacts back to the vault for us to pick up via reconciler. Our stdio surface is **designed and tested first** against Cowork as the canonical host — release smoke includes a Cowork-host integration check.
- **Claude Code / other terminal-based MCP clients.** Same surface, secondary testing target. Less UX polish (no rich UI) but full functional parity.

This isn't a separate integration — it falls out of our existing stdio MCP architecture. No Cowork-specific code paths; just careful tool design (clear names, focused scopes, predictable JSON shapes) so an agent can compose them sensibly without our hand-holding.

## 7. Critical flows

### 7.1 Auth (sign-in via OAuth deep-link)

1. User clicks "Sign in with GitHub" in Tauri.
2. Tauri opens system browser → Supabase Auth URL with `redirect_to=audio-transcriber://auth-callback`.
3. Supabase ↔ GitHub OAuth.
4. Browser redirects to `audio-transcriber://auth-callback?code=...`.
5. OS launches Tauri via deep-link (registered protocol handler via `tauri-plugin-deep-link`).
6. Tauri Rust emits `auth-callback` event to React with `code`.
7. React → Supabase JS client `.exchangeCodeForSession(code)` → `{access_token, refresh_token}`.
8. React → Tauri command `store_session` → Rust persists in OS keychain (`keyring` crate).
9. Subsequent API requests include `Authorization: Bearer <jwt>`. Supabase JS client auto-rotates and updates keychain.

Edge: Windows protocol handler must be registered during installer step. `tauri.conf.json bundle.windows` handles this; portable-mode (.zip) does not — out of scope for v1.0 (installer only).

### 7.2 Audio transcription (streaming proxy, client persists)

1. User selects file via Tauri file dialog (or records via mic). Tauri Rust `probe_audio` (ffmpeg-probe) reads `{duration, sample_rate, channels, codec, audio_ext, mtime}`.
2. **Import dialog opens with 4 fields pre-populated with smart defaults — nothing mandatory, user can press "Go" immediately:**
   - `meeting_date` — defaults to **audio file mtime** (the user usually records and imports within minutes/days; mtime is the best proxy for when the meeting happened, ≠ today's import date).
   - `meeting_type` — defaults to **last used type for this project** (or last used vault-wide if project has no history; or empty if vault has never had a meeting). Dropdown lists current `meeting_types` rows; "+ New type" inline option creates a row and pre-fills selection.
   - `project` — defaults to **currently-active project in sidebar**, else **last used project**, else "+ New project" inline option. **When a project is selected, the dialog also fetches its description (`projects.description`)** — used in step 5 for STT context. If the project's `description_word_count < 20`, a small inline hint appears: «Описание проекта поможет улучшить транскрипцию (5-8% точности)» with one-click "Add now" expander that lets the user write 20-200 words inline without leaving the import flow.
   - `participants` — multiselect from `voice_library_speakers`. Default = **last used participant set for this meeting_type+project combo** (this is the most-loaded default: weekly standups have the same people; weekly customer calls don't). If voice library is empty, the field shows an inline "+ Enroll speaker" affordance that triggers a 3-10 s mic recording → enrollment via `/api/v1/voice/enroll` (§5.1) → adds the new speaker to both the library and this meeting's selection.
3. Tauri Rust creates the meeting folder `<vault>/<Project>/<Meeting>/`, **copies the source audio** into `<meeting>/audio.<ext>` (so the meeting is self-contained even if the user later moves the source), writes the full `meeting.toml` with `status="queued"` and all 4 metadata fields from step 2, inserts a `meetings` row in `index.db`, and inserts one `meeting_participants(meeting_id, speaker_id, is_pre_specified=true)` row per selected participant.
4. **Audio preprocessing pre-pass** — a chain of filters that produces `<meeting>/.cache/audio.trimmed.wav` (the file sent to STT; lives in cache bucket per §3.5 because regenerable from `audio.<ext>` + `meeting.toml` params). Sets `meetings.status="vad_running"`, emits `preprocess-progress` event. Tauri Rust ensures `<meeting>/.cache/` exists (idempotent mkdir) before writing. Pipeline (ffmpeg piped through `ort`-driven inference, all in-memory until final write):

    | Stage | What it does | Default | Toggle location |
    |---|---|---|---|
    | **Decode → 16 kHz mono float32** | ffmpeg pipe load + resample + downmix | always on (mandatory format for downstream) | n/a |
    | **High-pass filter** (`highpass=f=80`) | Removes sub-80Hz rumble (HVAC, fans, table thumps); cheap, makes denoise + VAD cleaner | **on** | Settings → Audio preprocessing (advanced) |
    | **RNNoise denoise** (`ort` ONNX, bundled ~80 KB `sh.rnnn` model, BSD-3-Clause via xiph.org) | Neural noise reduction; helps for HVAC-heavy / cafe / open-office recordings (majority real-world case) | **on** (real-world recordings — Zoom/Meet/conference rooms/mobile mics — almost always have background noise; pure studio is the edge case; user disables for pristine recordings if soft-consonant clipping bothers them) | Settings → Audio preprocessing toggle + per-meeting override at import (advanced expander) |
    | **Loudness normalization** (`loudnorm`, single-pass, EBU R128 target -16 LUFS) | Equalizes volume across speakers; critical when one speaker is close-mic'd and another is far-mic'd | **on** | Settings → Audio preprocessing (advanced) |
    | **Silero VAD trim** (`ort` ONNX, bundled ~1.7 MB model, MIT) | Silence removal; threshold 0.5, min_speech 250 ms, min_silence 500 ms, pad 200 ms — same tested defaults as the current Python `silence_remover.py` | **on** | Settings → Audio preprocessing toggle + per-meeting override at import |

    Order matters: highpass first (removes low-freq noise that confuses RNNoise), then denoise (if on), then loudnorm (after denoising so it normalizes the cleaned signal), then VAD (cleaner signal = better silence detection). The concatenated speech-region output is written to `<meeting>/.cache/audio.trimmed.wav` (16 kHz mono PCM_S16).

    Records into `meeting.toml`: `silence_intervals` (original-time, sorted), VAD params used, denoise_applied bool, denoise_model, loudnorm_target_lufs, highpass_cutoff_hz. Updates `meetings.silence_removal_applied=true`, `silence_removed_seconds`, `trimmed_duration_seconds`.

    **Edge — all silence detected:** Sets `status="audio_silent"`, surfaces "Это аудио, похоже, не содержит речи. Транскрибировать оригинал всё равно?" dialog. If user confirms, fall through to step 5 with original audio; if not, abort job.

    **Edge — RNNoise ONNX runtime failure (model missing, corrupted):** Falls back to skip denoise stage only (other stages proceed), logs warning to `reconcile.log`, sets `denoise_applied=false`. Other stage failures (highpass / loudnorm) are ffmpeg-internal and effectively never fail on valid audio; if they do, abort and fall back to original audio.

    **Edge — Silero VAD ONNX runtime failure:** Falls back to bypass VAD only (other stages still applied), logs warning, sets `silence_removal_applied=false`. STT runs on the cleaned-but-not-trimmed audio.

    **Edge — entire preprocessing disabled (advanced setting):** Skip the whole step; `.cache/audio.trimmed.wav` not created; segments map straight from STT output without time conversion. `.cache/` folder may still be created if `segments.jsonl` is going to be written. Reserved for power users who want raw audio to STT.
5. React → `POST /api/v1/transcribe/start` with metadata. **`duration_seconds` field is the post-trim duration** (`trimmed_duration_seconds` if VAD ran, else original) — what the user pays for; quota check + Stripe billing reflect actual STT load. **`project_description?` field is included if the project has a non-empty `projects.description` and the chosen STT provider supports prompt context** (Groq Whisper, OpenAI Whisper, Gladia, Speechmatics — see provider capability matrix in `apps/api/audio_transcriber_api/providers/`); backend forwards to provider as `initial_prompt`, truncated to provider's token cap.
6. FastAPI **quota check** based on `duration_seconds` (Flow §7.4). No DB row written for the job itself.
7. FastAPI returns `{request_id, stream_url, expires_at}`. The `request_id` is opaque — used only for usage_log correlation on completion. Tauri stashes `request_id` in `meetings.backend_request_id`.
8. Tauri Rust opens chunked HTTPS upload to `stream_url` (which is a `POST /api/v1/transcribe/:request_id/stream`) — request body is the audio read from **`.cache/audio.trimmed.wav` if it exists, else `audio.<ext>`**; response body is an SSE event stream.
9. Rust reads local file in 64 KB chunks, streams via `reqwest`. Simultaneously, an async task on the Rust side reads the SSE response stream.
10. FastAPI receives upload via `await request.stream()` → `asyncio.Queue(maxsize=2)`. Never writes audio to disk.
11. FastAPI background task: dequeues. If `.cache/audio.trimmed.wav` size ≤ provider's `max_upload_bytes`, single-upload directly to STT via lifted `providers/*.py`. Otherwise, full **chunking pipeline** (§7.11): opus-compress → if still too big, split at silence boundaries (reusing `silence_intervals` from VAD step 4 when available) → bounded-parallel per-chunk transcribe → timestamp double-mapping → SSE reorder buffer.
12. Provider returns transcript chunks → FastAPI emits SSE events `{type: "segment", idx, start, end, text, language?, confidence, provider_speaker_tag?, words?: [{text, start, end, confidence?}]}` on the response stream. `provider_speaker_tag` populated when chosen provider does diarization (AssemblyAI / Speechmatics / Gladia / Deepgram per §7.12); absent for Groq / OpenAI Whisper. `words` populated when provider supports word-level timestamps (all current providers do, though formats vary — `speaker_aligner` normalizes). **All timestamps in trimmed-audio time** (provider transcribed audio.trimmed.wav); double-mapping applied on client side in step 13.
13. Tauri Rust receives SSE segment events. **Before persisting, applies the trimmed→original time mapping** using `silence_intervals` from step 4: each `start` and `end` is shifted forward by the cumulative duration of silence intervals that precede them in the original timeline. Then appends to `<meeting>/.cache/segments.jsonl` (one line per segment, fsync'd periodically; `.cache/` ensured by step 4 or created here if preprocessing was disabled) and inserts rows into `meeting_segments` in `index.db`, emits a Tauri event `transcript-progress` → React updates progressively. (When `silence_removal_applied=false`, the mapping is identity — same code path, zero-length intervals list.)
14. On STT completion: FastAPI emits SSE `{type: "done", actual_duration_seconds, cost_usd}` and writes `usage_log` row with `request_id` correlation. Audio buffers garbage-collected.
15. Tauri Rust regenerates `<meeting>/transcript.md` from final segments + speaker labels (template-rendered, **timestamps in original time** for natural playback feel), updates `meeting.toml` with `status="done"`, `completed_at`, `duration_seconds`, updates `index.db` accordingly. **Sets `meetings.readme_dirty=true`** so the meeting `README.md` regenerates on next view (§7.16) — synchronous regeneration deferred because at this point post-processing (step 17) is about to run and will dirty README repeatedly; one lazy regenerate after all of step 17 completes is cheaper than N synchronous ones.
16. Voice library identify pass (client-driven; runs automatically post-STT unless user disabled it for this meeting). Sets `meetings.status="identifying"` for the duration. **Behavior depends on whether STT provider gave diarization tags** (see §7.12 capability matrix):

    **Provider diarization PRESENT** (segments have `provider_speaker_tag`, e.g. AssemblyAI / Speechmatics / Gladia / Deepgram):
    - Group all segments by `provider_speaker_tag` (typically 2-8 unique tags per meeting).
    - For each unique tag, pick ~3 representative segments (longest, mid-meeting — avoids opening pleasantries and closing handoffs).
    - **One `/voice/identify` call per unique tag** (concatenated representative audio chunks): backend computes embedding from the concatenation, scores against candidates, returns best_match.
    - Apply the match to ALL segments with that tag in bulk: `meeting_segment_speakers(segment_idx, speaker_id)`.
    - Massively fewer backend calls (typically 3-8 per meeting instead of 100s) AND massively more accurate (longer audio → more stable embedding).

    **Provider diarization ABSENT** (segments have no `provider_speaker_tag`, e.g. Groq / OpenAI Whisper):
    - Fall back to per-segment identify: slice each segment's audio, identify independently.
    - No cross-segment grouping — same speaker may match in segment 5, miss in segment 23, match in segment 47 (visible inconsistency in UI).
    - **Known limitation** (§13.14): without provider diarization + without voice library matches, speakers stay anonymous (`speaker_id=null`). UI surfaces a hint "Groq doesn't auto-label speakers — consider switching provider or enrolling more speakers".

    **Candidate set comes from `meeting_participants` where `is_pre_specified=true`** — typically 2-5 speakers vs the whole library — so confidence thresholds can be tightened and false matches drop sharply.
    - React reads pre-specified `voice_library_speakers` rows for this meeting (each carries `embedding_version` — see §4.3).
    - For each segment in `segments.jsonl`: slices **`audio.<ext>` (the original)** at the segment's original-time bounds via Tauri ffmpeg invocation. **Per-slice preprocessing then depends on candidates' `embedding_version`:** if all narrowed candidates are `v2-denoised` (the v1.0 default), apply RNNoise via Tauri Rust + `ort` (same model already loaded for step 4) on the slice before sending to backend. If the candidate set has any `v1-raw` legacy entries (migrated Python voice library), send raw slice — this preserves matching against the existing embedding's training characteristics. Mixed candidate sets are split into two identify calls per segment (one denoised batch for v2 candidates, one raw batch for v1) and the best score across both wins. Brief, but plan-phase needs to confirm with empirical test that the mixed-mode complexity is worth the migration friction reduction (alternative: force "Migrate library to denoised" before identify runs at all on mixed libraries).
    - Calls `POST /api/v1/voice/identify` with `{audio_chunk, candidates, audio_was_denoised}` — `audio_was_denoised` tells backend whether to expect denoised characteristics (affects which embedding-extraction normalization applies, if any).
    - Backend scores in memory, returns `{best_match: speaker_id | null, score}`. `null` if no candidate scores above the tightened threshold.
    - Client writes `speaker_id` into `meeting_segment_speakers` (FK to voice_library_speakers).
    - Segments with no match remain anonymous (`speaker_id=null` → labeled Speaker_A/B/C by diarization order). User can manually re-label later in the transcript view.
    - **Fallback for missed speakers (someone the user forgot to pre-specify):** A "Match against full library" button in the transcript view runs a second pass with the full `voice_library_speakers` as candidates. When it matches, a toast offers "Add Иван to this meeting's participants?" — yes → inserts `meeting_participants(speaker_id, is_pre_specified=false)`, future runs of `last used` defaults learn from this signal.
    - Regenerates `transcript.md` with proper labels, updates the speaker_id-to-name map in `meeting.toml`.
17. **LLM post-processing pipeline kicks off** (`meetings.status="postprocessing"`). The **8 passes** run per §7.9: Phase A (summary, tasks, decisions, topics, open_questions, insights, **agenda**) in parallel, then Phase B (protocol) after Phase A's relevant outputs are ready. Each pass writes its artifact file in the **`<meeting>/analysis/` subfolder** (`tasks` is the exception — writes to root `tasks.json` because user-actionable per §3.5) + a `meeting_postprocess_runs` row. Tauri Rust ensures `<meeting>/analysis/` exists before first pass writes. Each pass write additionally re-sets `meetings.readme_dirty=true` (cheap — README regenerator coalesces all dirty events into one lazy pass on view-open). UI shows per-pass progress badges. Failures of one pass don't block others; failed passes can be regenerated via per-pass button in the meeting view. When all enabled passes are done (or terminally errored), `meetings.status="done"`.
18. **Embedding indexing for RAG chat** (§7.14). Triggered automatically after post-processing completes (or after STT if post-processing disabled). Tauri Rust orchestrator chunks the finalized transcript (using `topics.json` boundaries when available, sliding-window fallback otherwise), batches chunks to `/api/v1/embed`, persists `transcript_chunks` rows with vectors in vault `embeddings.db`. Indexing is a single tracked operation — appears as `embed_meeting` in `offline_queue` if user goes offline mid-indexing or before STT done. Idempotent re-indexing supported (e.g. after transcript edit + reconcile). Cost typically $0.0001-0.0005 per meeting. Failure does NOT block `meetings.status="done"` — meeting is usable, only RAG chat over this meeting is degraded until next indexing attempt (auto-retry on next app launch).

Edge — **connection drop mid-stream**: client keeps already-received segments locally (`meetings.status="error"`, partial `.cache/segments.jsonl` preserved on disk). Backend already wrote nothing user-visible — `usage_log` records only the partial billable minutes that streamed successfully. Client retry creates a fresh `request_id` and re-uploads; no server-side dedup needed (server holds no prior state). The meeting folder retains the partial work — user can manually delete or retry.

Edge — **memory pressure**: `asyncio.Queue(maxsize=2)` ensures backend buffers at most ~128 KB upload. SSE response queue is similarly bounded. Slow provider → queue fills → FastAPI stops draining the request stream → TCP back-pressure replicates upstream. Correct flow control without explicit rate limiting.

Edge — **client crash mid-stream**: local `meetings.status="processing"` row stays orphaned and the meeting folder is left in a half-written state. On next app launch, the startup reconciler marks any `processing` row older than 5 minutes as `error` (matching the meeting folder's `meeting.toml.status`) and exposes a retry button. Backend doesn't care — it has no orphaned state.

Edge — **user moved/renamed meeting folder externally**: reconciler detects on next startup via folder mtime, updates `meetings.folder_name`. If the move crossed projects (Project A → Project B), `meetings.project_id` is updated. If the move is into an unknown location (outside vault), reconciler marks `status="orphaned"` and offers to relocate or forget.

Edge — **user deletes `.cache/audio.trimmed.wav` (or `<meeting>/.cache/` entirely) manually** (to reclaim disk): no functional impact — meeting is still browsable, `transcript.md` and SQLite `meeting_segments` remain authoritative. Retry-transcribe regenerates the trimmed file via VAD (params from `meeting.toml.silence_removal`); `segments.jsonl` regenerable from SQLite mirror on demand. The `.cache/` bucket is **the** designed disk-reclaim target (§3.5) — this edge is a feature, not a bug.

Edge — **user toggles silence_removal mid-job retry**: if a previous transcribe with VAD=on failed and the user disables VAD and retries: the new run uses `audio.<ext>` directly; `.cache/segments.jsonl` is overwritten with identity-mapped timestamps; `meeting.toml.silence_removal.applied` flips to false; `.cache/audio.trimmed.wav` is left in place (orphaned but harmless — see edge above; user can `rm .cache/audio.trimmed.wav` to reclaim). Inverse toggle (was off, retry with VAD on) works symmetrically and creates `.cache/audio.trimmed.wav`.

Edge — **VAD on a long file (e.g. 3-hour podcast)**: Silero VAD runs ~30-50× real-time on a modern CPU, so a 3-hour file takes ~5 minutes pre-STT. Surfaced via the `vad-progress` event with ETA. User can cancel — meeting stays in `status="queued"` with `silence_removal_applied=false` flag set so retry can choose to skip VAD.

### 7.3 Billing (Stripe Checkout)

1. User clicks "Upgrade to Pro" → React → `POST /api/v1/billing/checkout`.
2. FastAPI → Stripe `checkout.sessions.create(customer, price_id, success_url=audio-transcriber://billing-success)`.
3. FastAPI returns checkout URL.
4. Tauri opens system browser (PCI: never embed Checkout).
5. User completes payment in Stripe Checkout.
6. Stripe redirects to `audio-transcriber://billing-success?session_id=X`.
7. Deep-link opens Tauri; React shows "Verifying..." spinner.
8. Asynchronously, Stripe fires webhook → `POST /webhooks/stripe` → `checkout.session.completed`.
9. FastAPI verifies signature, upserts `subscriptions` (tier=pro, monthly_quota_minutes=600, etc.).
10. Realtime UPDATE on `subscriptions:user_id=X` → React shows "Upgraded ✓".
11. Renewal: Stripe `invoice.payment_succeeded` → FastAPI rolls usage period.
12. Cancellation: Customer Portal → Stripe → webhook `customer.subscription.deleted` → tier=free.
13. Overage: nightly cron aggregates `usage_log`; if exceeded → Stripe usage_record for metered price.

Edge — **webhook arrives before user returns**: race. React deep-link handler polls `/api/v1/subscriptions/current` with retry-with-backoff (3 × 1s).

### 7.4 Quota enforcement

1. `POST /api/v1/transcribe/start` middleware: get user_id from JWT.
2. Postgres advisory lock per user_id (`pg_advisory_xact_lock(hashtext('quota_' || user_id::text))`) — serializes concurrent submissions in a ~50 ms window.
3. `SELECT SUM(minutes_used) FROM usage_log WHERE user_id=$1 AND created_at > current_period_start` — used minutes.
4. `SELECT monthly_quota_minutes FROM subscriptions WHERE user_id=$1` — quota.
5. Estimated cost = `duration_seconds / 60` + safety margin.
6. If used + estimated > quota:
   - `tier=free` → return 402 `QUOTA_EXCEEDED`.
   - `tier in (pro, business)` → allow, mark for overage billing.
7. Job proceeds. After completion, `usage_log` written with actual minutes.
8. Cron: aggregate `usage_log` → if overage → Stripe metered usage record.

### 7.5 OAuth for task backends + Google Drive (generalized)

Since the backend doesn't persist user data, OAuth refresh tokens for external integrations live in the user's OS keychain on the client, not in the database. The backend handles the OAuth dance because the client_id/secret are managed app credentials. Same flow applies to all OAuth-based providers (Linear, Glide, Notion, Jira-Cloud, Yandex Tracker, Bitrix24, GitHub, Google Drive — §5.1 + §7.7).

**Standard OAuth flow** (per-provider — `:provider ∈ {linear, glide, notion, jira, yandex_tracker, bitrix24, github, google}`):

1. User clicks "Connect <Provider>" in Settings → Integrations → React → `POST /api/v1/oauth/:provider/start`. For Bitrix24, an additional input field "Portal URL" appears first (e.g. `acme.bitrix24.kz`); request body includes it.
2. Backend returns provider authorize URL with `state=<csrf>`, `redirect_uri=audio-transcriber://oauth-callback/:provider`, minimum-privilege scope (§5.1).
3. Tauri opens system browser.
4. User authorizes; provider redirects to `audio-transcriber://oauth-callback/:provider?code=...&state=...`.
5. Deep-link launches Tauri → React → `POST /api/v1/oauth/:provider/exchange {code, state}` (+ portal_url for Bitrix24).
6. Backend validates state, exchanges code for `{access_token, refresh_token, provider_metadata?}` via provider, returns to client. **Backend does not persist the tokens.** `provider_metadata` carries provider-specific extras the client needs for subsequent calls — Jira's cloudid list, Notion workspace info, Bitrix24's canonical portal URL.
7. Tauri Rust stores tokens + metadata in OS keychain (`keyring` crate, service `audio-transcriber-:provider`).
8. For each subsequent `POST /api/v1/tasks/send-to-:backend` call, the client retrieves the access token from keychain and includes it in the request body alongside the per-backend `backend_config` (database_id / project_key / queue / portal_url / etc.). Backend uses token once, then forgets. On 401 → client triggers `POST /api/v1/oauth/:provider/refresh {refresh_token}` (stateless), updates keychain, retries.

**Non-OAuth flows:**

- **Jira Self-hosted (Personal Access Token)**: Settings → Integrations → Jira → "Self-hosted" tab. User enters base URL (`https://jira.company.com`) + PAT. Stored in keychain as `audio-transcriber-jira-self-hosted`. Sent as `Authorization: Bearer <pat>` header by backend per request.
- **GitHub PAT (fine-grained or classic)**: Settings → Integrations → GitHub → "Personal Access Token" tab as alternative to OAuth. User pastes PAT with `project` (and `repo` if Issue mode). Stored in keychain as `audio-transcriber-github-pat`. Used identically to OAuth-issued token in `Authorization: Bearer` header. Useful for users who want explicit token-rotation control or restricted org-managed GitHub Enterprise.
- **Generic Webhook**: no auth flow at all. User pastes destination URL + optional shared secret in Settings → Integrations → Webhooks. Stored in vault `settings.json` (URL is not secret) + keychain (secret only if provided).

**Trade-off (unchanged from earlier draft):** each task-send request includes the access token in the request body, which means the backend sees it. The alternative — having the client call providers directly — fails because most providers' APIs don't enable CORS for arbitrary origins. The middle ground (backend proxies, tokens transient in keychain) is the honest local-first compromise. **Google Drive remains the exception**: Tauri Rust calls `googleapis.com` directly without backend involvement (§7.7).

### 7.6 Vault open / switch / reconcile

**First launch (no vault registered):**

1. App starts → checks `%APPDATA%/audio-transcriber/app.json` for `current_vault_path`. Empty.
2. React renders an onboarding screen: "Choose where to store your meetings. We recommend Documents/Audio Transcriber for portability, or your iCloud/Dropbox/GDrive folder for automatic cross-device sync."
3. User picks a folder via Tauri native dialog. Tauri Rust:
   - Validates writability and free space (warn if < 5 GB).
   - Creates `<chosen>/.audio-transcriber/` if missing.
   - Runs `rusqlite_migration` to initialize `index.db` + `voice_library.db`.
   - Writes `<chosen>/.audio-transcriber/settings.json` defaults.
   - Writes `<chosen>/schema_version` (vault-layout version, separate from `index.db` schema).
   - Updates `%APPDATA%/audio-transcriber/app.json` with `current_vault_path`.
4. React routes to dashboard.

**Subsequent launches:**

1. App reads `current_vault_path` from app.json.
2. If path missing or unmounted (e.g. external drive not connected), show "vault not found" screen with options: Locate, Switch, Create new.
3. Run startup reconcile (§4.3). Show progress if vault has > 100 meetings.
4. Open dashboard.

**Switch vault (Settings → "Change vault location"):**

1. User picks new folder. Two cases:
   - **Existing vault** (has `.audio-transcriber/index.db`): app just points at it. No data migration.
   - **Empty folder** or fresh path: confirmation dialog — "Initialize an empty vault here? Your current vault stays untouched at `<old_path>`." If yes, initialize as in first launch.
2. App restarts to ensure all in-memory state is reset.

**Move vault (Settings → "Reveal vault in Explorer" + manual move):**

App doesn't actively manage moves — user can close the app, copy/move the vault folder anywhere, then change `current_vault_path` to the new location via Settings. This is the same workflow as Obsidian.

**Reconcile (startup + manual):**

1. **Scan `<vault>/People/`**: for each `*.md` file, parse YAML frontmatter, compare hash to last-known. On change, upsert `voice_library_speakers` row + diff `projects:` array against `speaker_projects` rows (delete missing, insert new). Speaker file deletion triggers archive (not hard delete — preserves historical references in `meeting_segment_speakers` + `meeting_participants`).
2. For each project folder under vault: enumerate, hash mtime + child folder names. **Also stat `<vault>/<Project>/README.md`** (if present) and compare to last-known hash.
3. Compare against `reconcile_state.project_signatures` jsonb.
4. For changed projects: re-read `README.md` if its mtime/hash changed → update `projects.description` + `projects.description_word_count`. Then enumerate meetings, compare `meeting.toml.modified_at` + folder mtime.
5. For changed meetings:
   - Re-parse `meeting.toml` (root), `.cache/segments.jsonl`, `tasks.json` (root), all `analysis/*.md`/`.json` (post-process artifacts; for each, read frontmatter and update `meeting_postprocess_runs` row).
   - Update `meetings`, `meeting_segments`, `meeting_tasks`, `meeting_postprocess_runs` rows accordingly.
   - If `transcript.md` mtime > `.cache/segments.jsonl` mtime: user edited transcript externally → run best-effort text-merge back into `meeting_segments` (line alignment heuristic). On ambiguity, mark meeting `needs_review` and surface in UI.
   - Set `meetings.readme_dirty=true` when any meeting field that affects README changed (§7.16.1 trigger list).
   - Rebuild `fts_segments` entries for the meeting.
6. Update `reconcile_state.last_full_reconcile_at`.

Conflict surfaces (during reconcile or fs-watch):

- Two `meeting.toml` files differing only in `last_modified` → merge non-conflicting fields, prefer user-edited fields, log to `reconcile.log`.
- `.cache/segments.jsonl` malformed (bad JSON line) → skip line, append to `reconcile.log`, surface "1 segment couldn't be loaded" hint. Regenerable from SQLite mirror as fallback if file is broken beyond per-line parsing.
- Meeting folder exists without `meeting.toml` → treat as orphaned-import candidate; on user confirmation, generate `meeting.toml` from folder name + audio probe.
- `audio.<ext>` missing → `meetings.status = "audio_missing"`; meeting still browsable for transcript, but re-transcribe disabled.

### 7.7 Vault backup to Google Drive (lift from Python Phase 7.1)

Lifted from the current Python app's `gdrive/backup.py` (PR #47, shipped May 2026), adapted to the vault model. Backup is text-only by default (audio excluded — vault sync via Dropbox/iCloud/GDrive-folder-sync handles audio if the user wants).

**Link Google Drive (one-time, Settings → Cloud Backup):**

1. User clicks "Connect Google Drive" → React → `POST /api/v1/oauth/google/start`.
2. Backend returns Google OAuth URL with `scope=drive.file`, `state=<csrf>`, `redirect_uri=audio-transcriber://oauth-callback/google`.
3. Tauri opens system browser. User authorizes.
4. Browser redirects to `audio-transcriber://oauth-callback/google?code=...&state=...`.
5. Deep-link launches Tauri → React → `POST /api/v1/oauth/google/exchange`.
6. Backend exchanges code → returns `{access_token, refresh_token}` to client. **Backend does not persist tokens.**
7. Tauri Rust stores both in OS keychain (service `audio-transcriber-google`). On first link, also creates the destination folder structure on Drive (`audio-transcriber-backup/<vault-name>/`), caches its `folder_id` in `<vault>/.audio-transcriber/settings.json`.
8. Settings now shows "Connected as `<google-email>` ✓" + the active destination folder path.

**Manual backup (Settings → "Back up now"):**

1. Tauri Rust opens worker thread, emits `backup-progress` event with stage labels.
2. Stage A — **prepare zip**: walks vault, includes everything EXCEPT (a) files matching `audio*.{wav,mp3,m4a,opus,ogg,flac}` (configurable in Settings; default exclude audio), (b) `.audio-transcriber/migration-backups/`, (c) `.audio-transcriber/voice_library.db` (binary embeddings — separate "Include voice library" checkbox, default ON since it's small).
3. Stage B — **build manifest**: SHA-256 + size per included file → `manifest.json` added to zip root.
4. Stage C — **upload**: chunked upload to `googleapis.com/upload/drive/v3/files?uploadType=resumable`. Destination filename `vault-<ISO-timestamp>.zip` inside the cached `folder_id`. Resumable upload protocol — survives connection drops, can resume from byte offset. Token refresh handled inline if expired.
5. Stage D — **bookkeeping**: insert row into local `backup_history` table (`{id, started_at, completed_at, status, gdrive_file_id, bytes_uploaded, file_count, manifest_sha256, error_message?}`).
6. Toast "Backup uploaded — <file_size> in <duration>" with "Open in Drive" link.

**Auto-schedule (Settings — opt-in, default OFF):**

- Frequency: `daily` or `weekly`. Trigger checked on app startup + every 6 hours via `tauri-plugin-positioner`-style scheduler in Tauri Rust.
- If due AND token still valid: trigger backup silently. If token expired: trigger refresh, retry once. If refresh fails: notify user "Google Drive token expired — please reconnect".
- Backup-history table tracks last successful timestamp. Skip if last successful within frequency window.
- Per-backup network failure: log + show notification, do NOT retry until next scheduled trigger (avoid backup-spam on flaky connections).

**Restore from Google Drive (Settings → "Restore from backup"):**

1. React → Tauri command `list_gdrive_backups` → Tauri Rust calls `drive.files.list(q="'<folder_id>' in parents", orderBy="createdTime desc")`. UI shows table of backups (date, size, vault-name from filename).
2. User picks a backup + a **target folder** (empty folder recommended; warn if non-empty).
3. Tauri downloads zip via `drive.files.get(alt=media)` with resumable download.
4. Verify against `manifest.json` (file presence + SHA-256 per file). On mismatch, abort with concrete file list and offer "Continue anyway" or "Cancel".
5. Unzip into target folder.
6. Run vault-layout migration if `<target>/schema_version` < current.
7. Offer "Switch to restored vault now?" — yes → updates `current_vault_path` in `app.json`, restarts app pointing at the restored vault.

Edge — **token refresh fails (refresh_token revoked by user in Google security settings)**: backup attempt fails with `GOOGLE_OAUTH_EXPIRED`, Settings shows "Reconnect Google Drive" button, auto-schedule pauses until reconnect.

Edge — **vault > Drive free quota (e.g. 15 GB free tier, vault is 20 GB of text)**: pre-check via `drive.about.get(fields=storageQuota)`. If insufficient, show "Drive has X GB free; backup needs Y GB. Free up space or upgrade Drive." with link.

Edge — **user has multiple vaults**: each vault stores its own `folder_id` in `<vault>/.audio-transcriber/settings.json`. Backups go to `audio-transcriber-backup/<vault-name>/...` — separate subfolders per vault, so multiple vaults coexist cleanly in one Drive.

Edge — **partial upload + app crash**: resumable upload protocol resumes from byte offset on next attempt. If user gives up, the partial upload eventually expires (Google: 7 days). `backup_history.status="aborted"` recorded.

### 7.8 Smart defaults from usage signals (statistics-as-ML)

The import dialog's defaults (§7.2 step 2) come from observed history of past meetings — not from ML inference or explicit user training. The app gets smarter via `GROUP BY COUNT` queries against local SQLite. **No LLM calls, no model retraining, no telemetry, no cost per suggestion** — pure SQL over the user's own usage. This is the mechanism powering `get_last_used_metadata` (§8.3).

**Inputs.** Three signal sources, each weighted:

- **Declarative** (`speaker_projects` rows): user explicitly declared "this speaker is in this project" via `<vault>/People/<name>.md` frontmatter or UI. Strongest signal — the user said it once, meant it generally.
- **Observed explicit** (`meeting_participants` with `is_pre_specified=true`): user listed this speaker at import for a specific meeting. Strong per-meeting signal.
- **Observed implicit** (`meeting_participants` with `is_pre_specified=false`): voice-identify discovered them post-transcription and user confirmed. Weakest but still positive.

All three are positive evidence with different weights. Declarative is **per-project**; observed signals are per-meeting and aggregate over time by (project, type) combo.

**Algorithm (vault-local SQL, no server involvement):**

```sql
-- Default project
SELECT project_id
FROM meetings
WHERE project_id IS NOT NULL
ORDER BY created_at DESC
LIMIT 1;

-- Default meeting_type for chosen project
SELECT meeting_type_id
FROM meetings
WHERE project_id = ?1
GROUP BY meeting_type_id
ORDER BY COUNT(*) DESC, MAX(created_at) DESC
LIMIT 1;
-- (fallback when project has no history: same query without project filter — vault-wide)

-- Default participants for (project, meeting_type)
-- declarative weight = 3, explicit weight = 2, implicit weight = 1
WITH weighted AS (
  -- Declarative source (speaker_projects)
  SELECT sp.speaker_id, 3 AS w
  FROM speaker_projects sp
  WHERE sp.project_id = ?1
  UNION ALL
  -- Observed source (meeting_participants × meetings filter)
  SELECT mp.speaker_id, CASE WHEN mp.is_pre_specified THEN 2 ELSE 1 END AS w
  FROM meeting_participants mp
  JOIN meetings m ON mp.meeting_id = m.id
  WHERE m.project_id = ?1 AND m.meeting_type_id = ?2
)
SELECT speaker_id
FROM weighted
GROUP BY speaker_id
HAVING SUM(w) >= 2
ORDER BY SUM(w) DESC
LIMIT 10;
-- (fallback ladder: (project, *) → (*, meeting_type) → empty)
-- Declarative alone hits threshold (weight 3 ≥ 2), so a speaker declared in project but never observed still appears as default — the "first standup, defaults already work" case.
```

**The learning loop closes here:** the fallback "Match against full library" button (§7.2 step 16) writes `meeting_participants(is_pre_specified=false)` rows whenever the user confirms a discovered match. Those rows feed back into the next import's defaults — speakers that show up repeatedly start appearing in the pre-selected set without the user ever explicitly enrolling them as defaults. Conversely, when the user **deselects** a default suggestion at import (e.g. someone who left the team), that meeting writes no `meeting_participants` row for them — their cumulative weight stops growing and they organically fall out of the top-10 rank as new meetings accumulate.

**Weighting rationale (declarative=3, explicit=2, implicit=1):** declarative (`speaker_projects`) is the strongest signal because the user took a moment outside any specific meeting to assert "this person belongs in this project" — high deliberation. Explicit per-meeting pre-specification is strong but per-meeting — could be one-off. Implicit (post-hoc voice-identify) is weakest but still positive. 3:2:1 weights mean: a single declarative association overrides ~1.5 explicit appearances or 3 implicit appearances — declarative wins until lots of evidence accumulates contradicting it. This handles team-membership reality: declared team members appear as defaults from meeting 1, observed-but-not-declared visitors slowly accumulate weight, declared members who left can be removed from People/ frontmatter and their declarative weight drops.

**No time decay in v1.0.** All historical meetings count equally. Rationale: simpler implementation, predictable behavior, and the loop self-corrects (user deselects → weight stops accumulating → speaker drops out). Phase 2 may add exponential decay (e.g. 90-day half-life) if real-world team rotation makes stale defaults problematic — `meetings.created_at` is already indexed so a decay-weighted query is one expression away.

**Anti-features (intentionally not tracked):** time-of-day patterns (morning standup vs evening retro), day-of-week patterns, meeting-duration patterns, speakers' "typical projects". Each would add code, query complexity, and "why did it suggest that?" debugging burden — for marginal default-quality wins. Punted to Phase 2 backed by user-feedback evidence, not speculative.

**Privacy:** entirely vault-local. No usage signals leave the device, ever. Server-side `usage_log` (§4.1) only contains aggregate billable-unit counts keyed by opaque `request_id` — no project/meeting-type/speaker references.

### 7.9 LLM post-processing pipeline (8 passes)

After transcription + voice-identify (§7.2 steps 11-16) complete, the client orchestrates **8 LLM passes** that produce structured artifacts in the meeting folder. All passes are stateless-proxied through `/api/v1/postprocess/:pass_type` (§5.1); the backend never persists transcripts or pass outputs. Phase A produces 7 independent artifacts in parallel; Phase B composes them into `protocol.md` using a per-meeting-type 5-block MoM template (this section).

**Pass catalog:**

| Pass | Output file (path relative to meeting folder) | Phase | What it produces | Typical cost (30-min meeting, Llama 3.3 70B via OpenRouter) |
|---|---|---|---|---|
| `summary` | `analysis/summary.md` | A | 2-3 paragraph free-form overview of what was discussed. **Frontmatter additionally carries `next_meeting: {date, topic, confidence}`** when LLM extracts a follow-up-meeting mention from transcript (e.g. «увидимся в четверг», «next call Tuesday 3pm», «давайте созвонимся в next sprint»). Confidence < 0.5 → field omitted, no rendering in protocol Block 5. Confidence ≥ 0.5 → rendered as `- **Следующая встреча:** {date} — {topic}` in protocol.md. The next-meeting prompt is appended to the summary system-prompt as an extra extraction directive — no separate pass needed because (a) `summary` already has full transcript in context, (b) most meetings have ≤ 1 next-meeting mention so dedicated pass would be wasted call. | ~$0.005 |
| `tasks` | `tasks.json` (**root**, not `analysis/` — see §3.5 user-actionable exception) | A | Action items with extracted `{title, description, assignee_speaker_id?, assignee_name?, due_date?, confidence}`. LLM resolves "Иван возьмёт" → speaker_id when participants are pre-specified (§7.8 narrowed list speeds matching too). Natural-language deadlines parsed: "к пятнице" → ISO date relative to `meeting_date`. **When speaker profiles have `role` / `responsibilities` populated** (§3.5 People/ files), backend optionally prepends a compact participant-context block to the prompt ("Participants: Иван (CTO — architecture, hiring); Петр (PM — sprint planning)") — improves assignee attribution accuracy ("action item про tech debt → Иван (CTO)" vs naive "→ Иван"). Settings → Post-processing → "Include speaker context in LLM prompts" toggle (default ON; can be disabled for cost or privacy reasons). | ~$0.01 |
| `decisions` | `analysis/decisions.md` | A | Markdown list of "the team decided X because Y" items. Distinct from tasks (conclusions vs actions). | ~$0.005 |
| `topics` | `analysis/topics.json` | A | Chapter list `[{idx, title, start_seconds, end_seconds, segment_idx_range}]` for timeline navigation. **Skipped for meetings < 10 min** (overhead not worth it). | ~$0.01 |
| `open_questions` | `analysis/open_questions.md` | A | Unresolved questions raised + points of disagreement that didn't reach decision. Often the highest-value artifact for follow-up planning. | ~$0.005 |
| `insights` | `analysis/insights.md` | A | Qualitative observations the LLM surfaces (recurring themes, emphasis patterns, possibly unspoken concerns). Marked "experimental" in UI — easiest pass to disable if user finds noisy. | ~$0.01 |
| **`agenda`** | `analysis/agenda.md` | A | **NEW in v1.0.** Bulleted list of agenda items LLM extracts from first 5-10 minutes of `transcript.md`. Typical output 1-5 items. When LLM cannot identify a clear agenda (impromptu conversation, no «давайте обсудим...» framing), output is the fallback line «*Повестка не зафиксирована в первых минутах встречи. Опционально дополни вручную.*» — gives user a clear edit-or-leave-blank affordance instead of pretending content exists. Drives Block 2 («Повестка дня») of the 5-block MoM protocol template (see below). Distinct from `summary` because (a) structurally different content (bulleted vs prose), (b) some meeting types reuse the same agenda repeatedly (weekly standups, recurring 1-on-1s) — extracting it independently lets user copy-paste between meetings or maintain a project-level «standard agenda» they tweak per meeting. | ~$0.0005-0.001 (only first 5-10 min of transcript in context — much smaller input window than other passes) |
| `protocol` | `analysis/protocol.md` | B | Composed structured minutes following the **5-block MoM (Minutes of Meeting) skeleton** — see "Default protocol template" subsection below. Per-meeting-type template stored at `<vault>/.audio-transcriber/protocol_templates/<TypeName>.md`; templates use `{{placeholder}}` syntax filled at protocol-pass time with Phase-A outputs + metadata. **This is the canonical artifact distributed via Email/Telegram per §7.15** — user's draft-preview-send dialog operates on a copy of this file's body. | ~$0.01-0.03 |

**Phase A / Phase B sequencing.** All **7 Phase-A passes** are independent and run **in parallel** (asyncio gather on the client side; backend handles concurrent SSE streams). Phase-B (`protocol`) waits for `summary` + `tasks` + `decisions` + `open_questions` + `agenda` to complete (it composes their outputs into the 5-block template — `topics` and `insights` are referenced if available but not blocking; `agenda` IS blocking because it's a structural Block of the template). Average wall-clock: Phase A ≈ 8-15 s (longest pass dominates), Phase B ≈ 3-6 s. User sees progressive artifact-ready badges in the meeting view as each pass returns.

**Default protocol template — 5-block MoM skeleton.** All `protocol.md` outputs follow the canonical **5-block Minutes of Meeting** structure: (1) Метаданные / (2) Повестка дня / (3) Ключевые тезисы + решения + разногласия / (4) Action items table / (5) Ссылки + следующая встреча. This is industry-standard meeting-minutes structure — adopted as our default because (a) it composes shareably as a self-contained document, (b) maps cleanly onto our existing Phase-A outputs (one block per output cluster), (c) is recognized by attendees without explanation.

Generic default template at `<vault>/.audio-transcriber/protocol_templates/_default.md` (the seed template all type-specific variants extend):

```markdown
---
schema_version: 1
file_type: analysis_pass
pass_type: protocol
meeting_id: {{meeting_id}}
project: "{{project_name}}"
meeting_date: {{meeting_date}}
derived_from:
  - transcript.md
  - meeting.toml
  - analysis/summary.md
  - analysis/agenda.md
  - analysis/decisions.md
  - analysis/open_questions.md
  - tasks.json
generated_at: {{generated_at}}
generated_by: "llm-pass:protocol"
model_used: "{{model_used}}"
cost_usd: {{cost_usd}}
---

# {{meeting_type}} — {{meeting_date}}

<!-- Block 1: Метаданные -->
**Проект:** [[{{project_full_path}}|{{project_name}}]]
**Дата:** {{meeting_date}}
**Длительность:** {{duration_hms}}
**Участники:** {{participants_wiki_links}}
**Сгенерировано:** audio-transcriber@{{app_version}} на основе записи от {{meeting_date}}

<!-- Block 2: Повестка дня (from analysis/agenda.md) -->
## Повестка дня

{{agenda_content_or_fallback}}

<!-- Block 3: Ключевые тезисы и решения (composed from summary + decisions + open_questions) -->
## Ключевые тезисы и решения

{{summary_first_paragraphs}}

### Принятые решения

{{decisions_list_rendered}}

### Открытые вопросы и разногласия

{{open_questions_list_rendered}}

<!-- Block 4: Action items (rendered as markdown table from tasks.json) -->
## План действий

| Задача | Ответственный | Дедлайн |
|---|---|---|
{{tasks_table_rendered_from_tasks_json}}

<!-- Block 5: Полезные ссылки и следующие шаги -->
## Ссылки и следующие шаги

- [Полный transcript]({{transcript_relative_path}})
- [Аудио]({{audio_relative_path}})
- [Резюме](analysis/summary.md)
{{#if next_meeting}}
- **Следующая встреча:** {{next_meeting.date}} — {{next_meeting.topic}}
{{/if}}
```

**10 seeded type-specific templates** (created at vault-init from copies of `_default.md`, then hand-tuned per type-typical structure):

| Template file | Variation from default |
|---|---|
| `Standup.md` | Лаконичный: drops «Открытые вопросы» section (standups rarely have unresolved questions); Block 4 «План действий» reorganized as «Yesterday / Today / Blockers» per-speaker via `{{tasks_grouped_by_assignee_yesterday_today_blockers}}` placeholder |
| `Customer Call.md` | Adds «Customer key points» section between Blocks 3 and 4 (extracted from `{{summary_customer_emphasis}}` — sub-extracted from summary's frontmatter); Block 1 splits participants into `{{participants_external}}` + `{{participants_internal}}` |
| `Sprint Retro.md` | Block 3 «Решения» expanded with «What went well» + «What didn't» sub-sections (LLM groups decisions accordingly); adds «Action items for next sprint» framing in Block 4 |
| `1-on-1.md` | Replaces «Участники» with «Manager / Report»; drops Block 4 «План действий» entirely (1-on-1s rarely produce formal tasks — conversational); adds optional «Personal development discussion» section |
| `Design Review.md` | Adds «Design proposals discussed» + «Approved variants» sub-sections under Block 3; Block 4 tasks framed as «Implementation tasks» |
| `Sprint Planning.md` | Block 4 renamed «Sprint commitments»; adds «Capacity» metadata line in Block 1; «Carried-over items» sub-section under Block 3 |
| `Demo.md` | Adds «Demo highlights» + «Q&A summary» sections; Block 4 framed as «Follow-up commitments» |
| `Interview.md` | **Structurally different** from default — drops Block 4 entirely (no action items from interviews); adds «Candidate strengths», «Candidate concerns», «Recommendation» sections under Block 3 |
| `Workshop.md` | Adds «Breakout outputs» + «Group conclusions» sections; participants list grouped by breakout (if multi-breakout) |
| `Other.md` | = `_default.md` verbatim (no specialization) |

Templates use `{{placeholder}}` syntax filled at protocol-pass time. The Phase-B prompt instructs the LLM to (a) render available Phase-A outputs into the template's placeholders, (b) handle missing placeholders gracefully — when a referenced Phase-A pass is disabled or errored, the placeholder renders with type-appropriate fallback text (e.g. «*Раздел недоступен — пасс `decisions` отключён или завершился ошибкой*») rather than literal `{{decisions_list_rendered}}`.

**Template editor.** Settings → Meeting Types → click a type opens the template in an in-app markdown editor with placeholder autocomplete + live preview against a sample meeting. Save writes to `<vault>/.audio-transcriber/protocol_templates/<TypeName>.md`. Reset-to-seed button restores the bundled default for that type (preserves user customization until explicit reset). Custom user-created meeting types get a copy of `_default.md` automatically on type creation.

**When user renames or archives a meeting_type**, the template file is renamed (preserved) or moved to `<vault>/.audio-transcriber/protocol_templates/.archived/` (preserves history without cluttering active templates). Hard-deletion of a type prompts «Delete its template too? Recoverable in `.archived/` for 30 days».

**Auto-trigger + per-meeting opt-out.** All **8 passes** run automatically after §7.2 step 16 (voice-identify). The import dialog's advanced expander has a "Post-processing" subsection with **8 checkboxes** (defaults: all on except `insights` which defaults off — experimental). Per-meeting-type defaults override per-vault defaults (e.g. Standup type can have `topics=off, insights=off` as type-level default; Interview type defaults `tasks=off` + `agenda=off` since neither maps cleanly to interview structure). Final resolution priority: per-meeting setting > per-meeting-type default > per-vault default > global default (all-on-except-insights).

**Regenerate workflow.** Each pass has a button in the meeting view that triggers re-run. If `meeting_postprocess_runs.status="user_edited"` (file mtime > last regenerate completed_at), regenerate is gated behind a confirm dialog ("This will overwrite your edits — continue?"). Regenerate uses the latest transcript + latest project_description (so editing the description and regenerating gives improved output).

**Edits respected.** When the user manually edits `summary.md`, `protocol.md`, `decisions.md`, `open_questions.md`, or `insights.md` in the app's editor (or externally in Obsidian), the reconciler detects file-mtime change and updates `meeting_postprocess_runs.status="user_edited"`. Subsequent auto-regenerations (e.g. if user re-transcribes) skip user-edited artifacts unless explicit regenerate confirms overwrite.

**Cost transparency.** Meeting view shows total post-process cost ("$0.05") for this meeting, computed from `SUM(meeting_postprocess_runs.cost_usd)`. Settings → Billing has "Post-processing this month" line item. Per-pass costs visible in detail view for power users.

**Failures.** A failed pass (HTTP error, OpenRouter rate-limit, malformed LLM output) sets `meeting_postprocess_runs.status="error"` with `error_message`. Other passes continue. UI surfaces failed badges with retry. After 3 retries within 10 min, exponential backoff kicks in. Quota-exhaustion → 402 → UI prompts upgrade or manual retry next billing cycle. Backend log includes `request_id` correlation for support debugging — never the prompt/output text.

**Privacy + zero-persistence.** Same model as transcribe/identify: backend sees prompts + completions in memory while routing to OpenRouter, never writes to disk or DB. Outputs persist only in the user's vault. `usage_log` rows record `billable_unit=llm_postprocess_call`, units=token count, cost_usd, opaque request_id — no reference to pass type or meeting.

### 7.10 Cowork Project integration

The vault's `<Project>/` folder structure (§3.5) is intentionally compatible with [Claude Cowork's Projects model](https://support.claude.com/en/articles/14116274-organize-your-tasks-with-projects-in-claude-cowork) — a Cowork Project IS just a folder with optional `INSTRUCTIONS.md`. This section formalizes the integration flow and the UI affordances we ship for it.

**Component mapping (one-line each):**

| Cowork Project component | Our vault equivalent |
|---|---|
| Context (folder, URLs, linked chats) | `<vault>/<Project>/` folder (auto — all meeting subfolders + README + artifacts) |
| Instructions | `<vault>/<Project>/INSTRUCTIONS.md` (optional — seeded by us, fully user-editable) |
| Memory | Cowork-scoped — we do not replicate (avoids duplication + drift) |
| Scheduled Tasks | Cowork-scoped — we do not replicate (cron belongs in the agentic host) |
| MCP tools available within the project | Our stdio sidecar (§6.4), registered separately in Cowork's MCP config (one-time setup) |

**Instructions template seeded at project creation:**

```markdown
# Cowork Instructions for {{project_name}}

You are working inside a meeting workspace. The folder contains one subfolder per meeting; each meeting subfolder contains audio, transcript, segments, summary, decisions, open_questions, insights, and tasks artifacts (see file conventions in audio-transcriber).

When the user asks about:
- **Action items / tasks**: prefer `tasks.json` (at meeting root) over re-parsing `transcript.md`; it has structured `{title, assignee_speaker_id, due_date, confidence}`.
- **What was discussed**: prefer `analysis/summary.md` for the gist, `analysis/protocol.md` for structured minutes, `transcript.md` for verbatim, `README.md` (at meeting root) for at-a-glance metadata + nav links.
- **Unresolved items**: read `analysis/open_questions.md`.
- **Decisions**: read `analysis/decisions.md`.
- **Agenda**: read `analysis/agenda.md` for what was planned at meeting start.
- **Time-based queries** ("what happened at minute 12"): use `.cache/segments.jsonl` (one segment per line, timestamps in original time).

When generating outputs (digests, follow-up emails, reports), write them into a new subfolder `_cowork-outputs/` to keep them separate from app-managed artifacts (which our reconciler treats as source-of-truth).

For **speaker context** (roles, organization, project membership, responsibilities), read `<vault>/People/<name>.md` files. Each speaker file has YAML frontmatter (display_name, full_name, organization, role, projects) + markdown body (responsibilities, notes). Use this when generating personalized follow-ups ("Иван (CTO) — please review architecture decision") or when matching meeting participants to org context ("the customer call had Acme's CTO + 2 PMs").

Project description (from README.md):
{{project_description}}
```

The template is regenerated once at project-create (we substitute `{{project_name}}` and inline the current `README.md` body for `{{project_description}}`). After that, it's just a markdown file — the user owns it fully; our reconciler does NOT keep it in sync with later README changes (would surprise users who customized it). A Settings option "Re-seed INSTRUCTIONS.md from current README" exists for users who want a refresh.

**Onboarding flow — "Open in Cowork" button (Settings → Cowork Integration panel):**

1. App detects whether Cowork is installed by checking for the OS-registered Claude Desktop URL handler (`claude-desktop://` or whatever Cowork advertises — exact protocol TBD, see §13.11) and/or expected install paths (`/Applications/Claude.app` on macOS, `%LOCALAPPDATA%\Programs\claude-desktop\` on Windows).
2. If detected: enable the "Open this project in Cowork" button on each project's detail screen.
3. Click handler: opens the OS file:// URL for the project folder via Tauri shell (`tauri-plugin-shell::open`). This surfaces the folder in OS file explorer. User then uses Cowork's "Convert folder to project" via Cowork's UI to add it.
4. If Cowork exposes a documented deep-link API for "create project from path" (TBD with Anthropic), upgrade the button to a single-click that fires the deep-link.
5. Best-effort fallback when Cowork not detected: button replaced with "Copy folder path" + instructions modal with screenshots of the manual Cowork workflow + link to [Cowork's docs](https://support.claude.com/en/articles/14116274-organize-your-tasks-with-projects-in-claude-cowork).

**MCP server registration helper:**

The same Settings panel offers a "Copy MCP server config for Cowork" button → copies a JSON snippet like:

```json
{
  "mcpServers": {
    "audio-transcriber": {
      "command": "audio-transcriber-mcp-sidecar",
      "args": ["--vault", "C:\\Users\\<user>\\Documents\\Audio Transcriber"]
    }
  }
}
```

paths resolved from the current vault location. User pastes into Cowork's MCP config file (we link to Cowork's docs explaining where that lives). Future Cowork versions may auto-discover MCP servers — if so, this becomes a no-op.

**What this gets us at v1.0:**

- Zero Cowork-specific code in our core paths (vault remains pure files; MCP remains standard).
- One Settings panel + 2-3 Tauri commands (see §8.3) ship the integration surface.
- Each project folder is portable to/from Cowork independently — user can experiment without commitment.
- Open question §13.11 covers partnership-track work (directory listing, brand approval, Cowork beta channel).

**What's NOT in v1.0 (deferred to §14):**

- Bundled Cowork-skill package (hand-tuned prompts + tool-composition recipes specifically for our tool surface).
- Deeper testing against Cowork's exact orchestration patterns (beyond release-smoke check).
- Co-marketing artifacts.

### 7.11 Audio chunking pipeline

When `<meeting>/.cache/audio.trimmed.wav` exceeds the chosen STT provider's `max_upload_bytes` (referenced from §7.2 step 11), backend runs a 4-stage decision pipeline before falling back to chunking. Goal: avoid chunking when possible (lower latency, simpler error model), chunk safely when not.

**Provider capability matrix** (declared as `BaseProvider.max_upload_bytes` ABC attribute, lifted from current Python `providers/base.py`):

| Provider | `max_upload_bytes` | Notes |
|---|---|---|
| Groq Whisper | 25 MB (26214400) | Whisper Large v3 / Turbo |
| OpenAI Whisper | 25 MB | API hard limit |
| Gladia | 200 MB | Soft limit; their docs recommend < 200 MB |
| Deepgram Nova-3 | 2 GB | Effectively unbounded for our use case |
| Speechmatics | 2 GB | Effectively unbounded |
| AssemblyAI | ~no limit | Uses pre-signed S3 URL upload protocol — provider-side chunking; we delegate to their SDK |

The pipeline is per-provider — chunking decision happens AFTER the user has selected (or the default has been resolved to) a specific provider.

**Stage A — compression try (opus).**

Lifted from Python Phase 6.5 PR-A.1 (transparent opus compression). For files > 25 MB headed to Whisper-family providers:

1. ffmpeg encode `.cache/audio.trimmed.wav` (PCM_S16, 16 kHz mono) → opus at 24 kbps, in-memory buffer.
2. Compression ratio: typically 5-10× (PCM 256 kbps → opus 24 kbps). 60-min meeting WAV ≈ 60 MB → opus ≈ 6-10 MB.
3. Whisper accepts opus natively (no quality loss in our regime — speech-only at 24 kbps stays well above intelligibility threshold).
4. If compressed size ≤ `max_upload_bytes` → upload opus blob, **skip chunking entirely**. Most real-world files hit this path.
5. If still > limit → proceed to Stage B.

Compression is skipped for providers with high limits (Deepgram, Speechmatics): pointless since upload fits raw, and provider may prefer uncompressed for accuracy.

**Stage B — chunk-boundary discovery.**

If the upload is still too big after Stage A:

1. Target chunk size = `max_upload_bytes` × 0.85 (15% headroom for HTTP overhead + encoding variance).
2. Read `silence_intervals` from `meeting.toml` (recorded by VAD step 4). These are silence boundaries in the **original** audio — but VAD already removed them, so the trimmed file has no silence. Instead: reuse the **speech-region boundaries** computed by VAD (each VAD region is a contiguous speech chunk in `.cache/audio.trimmed.wav`).
3. Greedy pack speech regions into chunks: append region after region until the sum approaches target size, then close the chunk at the **end of the last fully-included speech region** (never cut mid-speech).
4. Each chunk gets `{start_in_trimmed_seconds, end_in_trimmed_seconds, byte_size}`. Adjacent chunks have **200 ms overlap** added (extend each chunk's end by 200 ms into the next chunk's start) — protects against words clipped at the boundary; dedupe happens in Stage D.
5. Fallback when `silence_intervals` unavailable (user disabled VAD): run a fresh lightweight Silero VAD pass on `.cache/audio.trimmed.wav` (or `audio.<ext>` if preprocessing fully disabled) purely to find boundaries (don't re-trim; we already have the trimmed file).

If VAD finds no boundaries (single contiguous speech blob bigger than chunk size — pathological case like a 30-min monologue): **hard cut at chunk size + 200 ms overlap**, log warning, expect mid-word splits that overlap dedup handles imperfectly.

**Stage C — bounded-parallel per-chunk transcribe.**

1. **Hybrid sequencing**: first chunk is transcribed **synchronously** before any others start. Reason: user-visible first-segment latency. Chunks 2..N then transcribe **in parallel** with bounded concurrency (default 3 simultaneous). Settings → Transcription → "Parallel chunk concurrency" (advanced).
2. Per-chunk call to provider via lifted `providers/*.py`. Each returns segments with **chunk-local timestamps** (relative to chunk start = 0).
3. Each chunk's segments tagged with `chunk_idx` for ordering. Backend in-memory ordered map `{chunk_idx → segments_buffer}`.
4. As each chunk completes, **timestamp double-mapping** applied (Stage D), then SSE-emit if all earlier chunks have already emitted (reorder buffer logic).

**Stage D — timestamp double-mapping + SSE reorder.**

Each segment from a chunk has timestamps relative to chunk start. To produce final segments in `.cache/segments.jsonl` (original-audio time, monotonic), backend applies two mappings:

1. **Chunk-local → trimmed-time**: `trimmed_t = chunk_local_t + chunk.start_in_trimmed_seconds`.
2. **Trimmed-time → original-time**: same algorithm as §7.2 step 13 — walk `silence_intervals` from `meeting.toml`, add cumulative silence duration that precedes the trimmed_t in original timeline.

Then the **SSE reorder buffer** ensures monotonic emission to the client:

- Backend tracks `next_chunk_to_emit = 0`. Buffers segments from chunks `> 0` until earlier chunks complete.
- When chunk `next_chunk_to_emit` completes: emit all its segments (after dedup with previous chunk's overlap region), advance pointer, recursively flush buffered chunks if they're now in-order.
- **Overlap dedup**: when emitting chunk `N`, drop any of its leading segments whose original-time start falls within the trailing 200 ms overlap of chunk `N-1` (those words were already emitted as part of chunk N-1's trailing segments).

User sees segments arrive **in time order** even though backend transcribes in parallel. The reorder buffer adds latency only equal to the slowest chunk in the in-flight set (typically <2× single-chunk latency).

**Stage E — chunk-level retry.**

If a chunk fails (HTTP error, provider 429/5xx, malformed response):

1. Retry that specific chunk up to 3 times with exponential backoff (1s / 4s / 16s). Other chunks unaffected.
2. After 3 failures: optionally fall back to a **secondary provider** for just that chunk (Settings → Transcription → "Fallback provider on chunk failure"; default: same provider, no fallback). Chunk result tagged with `provider=<fallback>` in `meeting_segments` for traceability.
3. If still failed: surface to user as "Chunk 3 of 7 failed — manually retry?" with the option to skip (mark gap in transcript) or abort whole job. SSE emits `{type: "chunk_error", chunk_idx, error_code}`.

**Edge — opus compression failure** (corrupted audio, ffmpeg crash): skip Stage A entirely, fall through to Stage B with the original WAV size. Logged warning.

**Edge — provider doesn't accept opus** (rare; check `BaseProvider.accepts_opus` flag, default true for Whisper-family, false for others): skip Stage A for that provider, go straight to chunking.

**Edge — chunk size 0** (defensive): if greedy packing produces a zero-size chunk (boundary at exactly chunk-start), drop it and merge speech regions into next chunk.

**Performance characteristics:**

- 30-min meeting, VAD-trimmed to 20 min, Groq Whisper provider:
  - After Stage A: 20-min × 16k mono PCM = 38 MB → opus 24 kbps ≈ 3.6 MB → **single upload, no chunking, no reorder buffer**. Wall-clock ≈ 12-18s for first segment.
- 3-hour podcast, VAD-trimmed to 2.5 hours, Groq:
  - After Stage A: opus ≈ 27 MB → still > 25 MB → **Stage B chunks into ~3 chunks**. Stage C transcribes sequential-first + parallel-rest with concurrency 3 → wall-clock ≈ 25-40s for first segment, then continuous streaming.
- 30-min meeting, Deepgram (2 GB limit):
  - File well under limit → no compression, no chunking, single upload. Wall-clock ≈ 8-12s for first segment.

### 7.12 STT provider capabilities + code-switching pipeline

Different STT providers have different feature surfaces. The backend's `providers/*.py` modules each declare a capability matrix on `BaseProvider` ABC (lifted from current Python; matrix already exists from Phase 1 PR-B language-capabilities gate). The transcribe orchestrator branches on these flags so each provider gets called with its native config — no provider-specific glue elsewhere in the pipeline.

**Capability matrix (declared as ABC class attributes):**

| Provider | `max_upload_bytes` | `accepts_opus` | `supports_diarization` | `supports_word_timestamps` | `supports_code_switching` | `code_switching_config` (provider-specific) |
|---|---|---|---|---|---|---|
| Groq Whisper | 25 MB | ✅ | ❌ | ✅ (`timestamp_granularities=["word","segment"]`) | ✅ (multilingual model) | omit `language` field; rely on initial_prompt trilingual hint |
| OpenAI Whisper | 25 MB | ✅ | ❌ | ✅ (`response_format=verbose_json`) | ✅ | omit `language` field; rely on prompt hint |
| Gladia | 200 MB | ✅ | ✅ | ✅ | ✅ (native) | `code_switching: true`, `code_switching_config.languages: ["ru","en","kk"]` |
| Deepgram Nova-3 | 2 GB | ✅ | ✅ (paid) | ✅ | ❌ (no Kazakh support) | runtime guard raises `LANGUAGE_NOT_SUPPORTED` for code_switching=true |
| AssemblyAI | pre-signed URL | n/a | ✅ | ✅ | ✅ | `speech_model: "universal"` |
| Speechmatics | 2 GB | ✅ | ✅ | ✅ | ✅ | `language: "auto"`, `language_identification_config.expected_languages: ["ru","en","kk"]` |

**Code-switching pipeline (when `meetings.code_switching=true`):**

The user selected "Смешанный (KZ+RU+EN)" → `meetings.language="mixed"` sentinel. At step 5 in §7.2:

1. Backend reads provider's `supports_code_switching` from capability matrix.
2. **If false** (Deepgram only currently): backend raises `LANGUAGE_NOT_SUPPORTED` with humanized error "Deepgram не поддерживает казахский — выберите другого провайдера или отключите code-switching". UI gates this at provider selection time too (warning chip in Settings → Transcription).
3. **If true**: backend builds provider-specific request using `code_switching_config` field:
    - Gladia: native `code_switching: true` + `languages: ["ru","en","kk"]`
    - AssemblyAI: `speech_model: "universal"`
    - Speechmatics: `language: "auto"` + `language_identification_config`
    - Whisper-family (Groq, OpenAI Whisper): omit `language` parameter (let Whisper auto-detect per-segment) AND prepend trilingual prompt hint to `initial_prompt`: `"Запись содержит русскую, казахскую и английскую речь. Recording contains Russian, Kazakh, and English speech. Сөйлеу қазақ, орыс және ағылшын тілдерінде."`
4. Provider returns segments with **per-segment `language` field** (auto-detected). Surfaced as `segment.language` in SSE (step 12) and in `.cache/segments.jsonl`.

**`initial_prompt` priority resolution** (the contention case from earlier audit):

A. **`project_description` non-empty + `code_switching=false`**: send `project_description` as initial_prompt.
B. **`project_description` empty + `code_switching=true`**: send trilingual hint as initial_prompt.
C. **`project_description` non-empty + `code_switching=true`** (both):
    - For Whisper-family: prefix with trilingual hint, then concat `project_description`, truncate to 224 tokens. Trilingual hint takes priority because it's load-bearing for accuracy; `project_description` is opportunistic context.
    - For providers with native code-switching config (Gladia / AssemblyAI / Speechmatics): trilingual handling is via API params (not prompt), so `project_description` goes into prompt slot unchanged.
D. **Both empty**: no initial_prompt.

**Word-level timestamps + speaker_aligner.py role:**

All current providers support word-level timestamps in some form, though shapes differ (`words` array vs nested under `segments` vs per-result). Backend's `speaker_aligner.py` (lifted in §15) normalizes these into our segment schema's `words` field (§3.5) on the way to SSE.

`speaker_aligner` also handles the case where provider gives word-level timestamps + diarization tags but they're attached at different granularities (e.g. AssemblyAI returns word-level + speaker per-word; Gladia returns segment-level speaker + words array): aligner walks the timeline, groups words by speaker transition, emits segments with consistent `{speaker_tag, words[]}` shape regardless of source.

When provider gives **no** diarization tags (Groq / OpenAI Whisper), `speaker_aligner` still runs but only normalizes the word-level format — speaker assignment is left to §7.2 step 16's voice-identify pass (or stays null).

**Per-provider WER expectations for KZ+RU+EN mixed audio** (informational; from Phase 1/2 internal testing in Python app — re-validate on real data during plan-phase):

| Provider | RU WER | KK WER | EN WER | Mixed WER (avg) |
|---|---|---|---|---|
| Groq Whisper-large-v3 | ~8% | ~22% | ~6% | ~12% |
| Gladia | ~7% | ~18% | ~6% | ~10% |
| Speechmatics Enhanced | ~6% | ~15% | ~5% | ~9% |
| AssemblyAI Universal | ~7% | n/a (degraded) | ~5% | n/a |
| Deepgram Nova-3 | ~6% | n/a (unsupported) | ~5% | blocked |
| OpenAI Whisper API | ~8% | ~25% | ~6% | ~13% |

Speechmatics + Gladia are best for trilingual content. Recommendation surfaced in Settings → Transcription provider selector with a "Best for KZ+RU+EN" badge.

### 7.13 Offline mode

The vault model + client-side preprocessing mean **~70-80% of app functionality works without internet** — significantly more than typical "cloud SaaS" tools. This section formalizes the design so implementation handles offline cleanly (vs ad-hoc failures + broken UI).

**Online/offline detection.**

Two-layer check (avoids false positives from captive portals + restrictive proxies):

1. **Browser-layer**: `navigator.onLine` event listener (handles physical network state changes — WiFi off, ethernet unplugged).
2. **App-layer ping**: every 60s when "online" (navigator.onLine=true), the app pings `GET /api/v1/healthz` with 5s timeout. Failure → mark `app_online=false` even if navigator says online.

Combined: `effective_online = navigator.onLine && last_ping_ok`. Status published as Zustand `online-status` store; UI subscribes to render banners + button states.

**Per-feature capability matrix.**

| Feature | Offline | Online required | Partial |
|---|---|---|---|
| Read existing meetings (vault files + SQLite) | ✅ | | |
| Search across vault (FTS5) | ✅ | | |
| Edit transcript.md / tasks.json | ✅ | | |
| Browse / create / archive projects | ✅ | | |
| Vault reconcile + switch vault + restore | ✅ | | |
| Audio playback | ✅ | | |
| Voice library browsing | ✅ | | |
| Audio preprocessing (§7.2 step 4) | ✅ (all bundled ONNX/ffmpeg local) | | |
| Audio editor (silence/cut tools) | ✅ | | |
| Settings (any change) | ✅ | | |
| Stdio MCP read-tools (`get_meeting`, `list_*`, `search_meetings`, `list_speakers`, `get_postprocess_status`) | ✅ | | |
| Cowork integration (project folder access, INSTRUCTIONS.md) | ✅ | | |
| Sign-in (Supabase OAuth) | | ✅ | |
| Transcribe new audio | | ✅ | preprocessing runs offline, transcribe queues |
| Post-processing passes (any of 7) | | ✅ | queues |
| Voice library enroll / identify | | ✅ | queues |
| Linear / Glide push tasks | | ✅ | queues |
| Google Drive backup / restore | | ✅ | manual retry on reconnect |
| Billing / quota refresh | | ✅ | last-cached shown stale |
| HTTPS MCP `get_usage` / `get_settings` | | ✅ | |
| Stdio MCP write-tools (`transcribe_audio`, `run_postprocess_pass`, `enroll_speaker`, `identify_speakers`, `send_tasks_to_linear/glide`) | | | fail fast with `OFFLINE` error code so MCP host (Cowork) can degrade gracefully |
| Import new audio file (file copy + folder create + metadata save) | ✅ | | preprocessing runs, transcription queues |

**Offline queue** (the productivity feature).

When an online-required action is invoked offline, instead of failing, the action is enqueued. On reconnect, the queue drains automatically. Schema (new vault SQLite table — see §4.3):

```
offline_queue: id (uuid v7), action_type enum, action_payload jsonb, status enum(pending,running,done,failed,cancelled),
               meeting_id nullable (FK), priority int, retry_count int default 0, last_error nullable,
               queued_at, started_at nullable, completed_at nullable
```

`action_type ∈ {transcribe, postprocess_pass, voice_enroll, voice_identify, send_to_linear, send_to_glide, gdrive_backup}`. `action_payload` is the body that would have gone to the corresponding REST endpoint.

Queue runner (Tauri Rust background task):
- On app start + on `online-status-changed → true`: scan `offline_queue WHERE status=pending`, sort by priority + queued_at.
- Pop one action at a time (sequential — avoids quota spikes); for transcribe-type actions, run through normal §7.2 flow.
- On success → status=done, completed_at; on failure → retry_count++, exponential backoff (1m / 5m / 30m), max 3 retries → status=failed with error message.
- User can manually retry / cancel via Settings → Queue panel.

UI surface: small badge in app header "3 actions queued — will run on reconnect"; click opens queue panel.

**Pre-processing while offline** (free quality-of-life from existing architecture).

When user imports audio offline:

1. §7.2 steps 1-4 run as normal (folder creation, metadata save, preprocessing pipeline). `.cache/audio.trimmed.wav` is produced locally.
2. §7.2 step 5 (`POST /transcribe/start`) detects offline → enqueues `{action_type=transcribe, meeting_id, ...}` instead. `meetings.status="queued_offline"`.
3. UI shows the meeting with status "Queued — will transcribe when online".
4. On reconnect, queue runner picks it up, jumps to §7.2 step 5 with the already-prepared `.cache/audio.trimmed.wav` → no re-preprocessing → transcribe starts immediately.

Savings: 30-60s of preprocessing wall-clock that would have run on reconnect now happens during the offline period (often "free" — user on plane / train).

**Per-MCP-tool offline annotation** (for Cowork + external hosts).

Each `@mcp.tool()` registration in `packages/mcp_tools/` carries an `offline_capable: bool` flag (declared at decorator level). Stdio sidecar's `tools/list` MCP response includes this flag in the tool description metadata so MCP hosts can:

- Show only offline-capable tools when their host detects offline (Cowork agentic loop won't waste turns trying unavailable tools).
- Display a friendly "this tool requires internet" tooltip when user invokes offline.

When a non-offline-capable tool is invoked offline, sidecar returns a structured error `{error: "OFFLINE", retry_after: "auto-on-reconnect"}` — MCP host treats as recoverable, not protocol-level failure.

**JWT-expired-while-offline policy.**

Scenario: user opens app offline; cached Supabase JWT expired 2 hours ago; backend ping unavailable so refresh can't run.

Policy:

1. **Grace period: 7 days after JWT expiry**, all offline-capable features continue working using cached session state. Banner: "Working offline. Sign in needed within 7 days to continue."
2. **Read-write to local vault** allowed (vault has no auth — it's user's filesystem).
3. **Queued actions** continue to queue normally.
4. On reconnect:
   - Token refresh attempted automatically.
   - If success → queue drains, banner clears.
   - If refresh fails (refresh_token revoked, user removed app access) → soft re-auth modal "Please sign in again to send your queued work"; until signed in, queue stays paused (not failed).
5. **Beyond 7 days offline + expired token**: hard-block on online-required features (lock buttons with "Sign in needed"), but never lose data (queue + vault preserved).

**Settings UI affordances.**

- Header status indicator (green dot "Online" / amber dot "Offline — N queued" / red dot "Sign-in needed"). Click opens status panel.
- Settings → Status panel: full queue contents (sortable, filterable), manual retry/cancel, last successful ping time, sign-in state, last 50 connectivity events.
- Settings → Advanced → "Force offline mode" toggle for testing / privacy-by-policy users who want to control when they're online.

**Edge cases.**

- **Partial-upload interrupted by disconnect mid-transcribe (§7.11 chunking pipeline)**: chunks already uploaded keep their segments; remaining chunks re-queue as new entries with `parent_action_id` link. Resume-on-reconnect picks up where left off.
- **Conflicting queue + new action**: user transcribes meeting A offline (queued), then comes online and starts transcribing meeting B directly via UI. Both run; meeting B may complete first (UI doesn't sequence on user intent priority — user can override via Settings → Queue panel "move to top").
- **Captive portal trap**: navigator.onLine=true but only captive portal reachable; healthz ping fails → app correctly treats as offline. Re-pings every 60s; auto-recovers when user authenticates captive portal.
- **Quota-exceeded mid-queue-drain** (free tier user with backlog): action fails with 402 QUOTA_EXCEEDED → status=failed, user notified, queue continues for non-billable actions only; resume after billing cycle.

### 7.14 RAG chat over vault

Chat-only UX (no separate semantic-search panel — retrieval happens inside RAG). User types question; system retrieves top-k relevant chunks from vault embeddings DB; LLM generates answer with inline citations linking to source meetings. **Backend never persists chunks, queries, or answers** — same in-transit-only model as §7.9.

#### 7.14.1 Indexing pipeline

Embeddings produced **client-side flow, backend-side inference**:

1. **Trigger**: §7.2 step 18 — after transcribe + post-processing complete. Also on transcript edit (reconciler detects `transcript.md` mtime change → re-embeds affected chunks only). Also as background batch on first launch / migration (legacy Python meetings get embedded).
2. **Chunking strategy** (priority order):
   - **Topic-based** (when `topics.json` exists from post-process pass): one chunk per topic. Captures semantic boundaries naturally — questions like "what did we decide about Q3 roadmap" naturally align with topic chunks. Typical chunk size 100-400 tokens.
   - **Sliding-window fallback** (no topics — short meetings, post-process disabled, etc.): 200-token chunks with 50-token overlap. Less semantically clean but always works.
   - Each chunk carries metadata: `meeting_id`, `idx`, `segment_idx_start/end`, `text`, `source_topic_id?`.
3. **Embed call**: Tauri Rust batches chunks (up to 100 per request — `text-embedding-3-small` supports batches), POSTs to `/api/v1/embed` with explicit `model_version`. Backend calls OpenAI in-memory, returns vectors, discards inputs.
4. **Persistence**: client writes `transcript_chunks` rows with vectors into vault `<vault>/.audio-transcriber/embeddings.db` (sqlite-vec vec0 virtual table for ANN search). `embedding_model_version` field tracks which model was used (enables future model upgrade migration — §13.16).

Indexing cost: ~$0.0001-0.0005 per meeting (~5000 tokens × $0.02/1M). 100 meetings = ~$0.05 one-time. Background batch for legacy meetings shows progress in Settings → Status: "Indexing 47/100 meetings for chat..."

#### 7.14.2 Query flow (per turn)

1. User types question in /chat UI (§8.1).
2. Client sends question text to `/api/v1/embed` → returns query vector (single text, ~$0.000001).
3. **Local retrieval** via sqlite-vec ANN: `SELECT chunk_id, distance FROM transcript_chunks WHERE meeting_id IN (scope_filter) ORDER BY embedding <-> :query_vector LIMIT 12`. Scope filter applies if user selected project/meeting narrower than vault.
4. Client hydrates top-12 chunks with metadata (meeting name, date, speaker_name from `meeting_segment_speakers` JOIN voice_library_speakers, project name, timestamps) — all local SQL.
5. Client POSTs `/api/v1/chat` with `{question, retrieved_chunks: [...12], conversation_history: [last 6 turns of session]}` — backend forwards to OpenRouter with citation-enforcing system prompt.
6. Backend SSE-streams `{type: "token", payload: "..."}` for incremental rendering + `{type: "citation", payload: {chunk_idx, marker}}` events when LLM emits inline reference markers like `[1]`. Client renders markers as clickable hover-cards → "[Project Alpha — 2026-05-27 Standup — 12:34 — Иван] full chunk preview" + "Open meeting" button.
7. On `done` event: client persists `chat_messages(role=assistant, content, retrieved_chunk_ids, tokens_used)` for audit + session history.

Top-k = 12 chosen because: (a) typical meeting context window is 2-3 chunks per relevant meeting, 12 covers 4-6 meetings comfortably, (b) at 200-400 tokens per chunk → 2400-4800 token context = fits cheap LLM context windows even with multi-turn history.

**Citation enforcement system prompt** (sketched, plan tunes):

```
You are a helpful assistant for meeting Q&A. Answer the user's question using ONLY the provided chunks.
For every factual claim, cite the chunk it came from using [N] syntax (where N is the chunk index, 1-based).
If the chunks don't contain the answer, say so — do not speculate.
Format: markdown. Inline citations REQUIRED. No hallucinated meeting names or dates.
```

#### 7.14.3 Conversational context (multi-turn within session)

Each `chat_session` keeps message history. On each new turn:

- Client includes **last 6 turns** of `(user_question, assistant_answer)` pairs as `conversation_history` (truncated to ~2000 tokens to leave room for chunks + new question).
- LLM can reference prior turns: "you mentioned Q3 roadmap earlier" → understood from history.
- Retrieval re-runs per turn (current question may need different chunks than prior). Optional optimization (plan-phase): query-rewriting LLM call that reformulates "what about it" into "what about Q3 roadmap" before retrieval — costs extra LLM call but improves recall.

Sessions persist across app launches (vault SQLite). User can re-open old sessions from a sidebar list ("Recent chats"). No session limit; archive available for cleanup.

#### 7.14.4 UI surface

Route `/chat` (§8.1). Layout:

- **Left sidebar**: recent chat sessions list (title, last_activity_at), "+ New chat" button, scope selector ("In: All vault / Project ... / Meeting ...").
- **Main pane**: conversation thread (user messages right-aligned, assistant left), input box at bottom (multiline, Cmd+Enter to send).
- **Inline citations** render as `[1]` markers within assistant text; hover shows source preview card; click opens source meeting at the cited timestamp.
- **Source panel** (collapsible on right): shows all retrieved chunks for current/last turn — useful when LLM didn't use a chunk user thought was relevant (helps user understand why).

Session title auto-generated by a cheap LLM call after the first user message ("Q3 roadmap discussion" rather than "What did we decide..."). User can rename inline.

#### 7.14.5 Cost model

| Operation | Typical cost |
|---|---|
| Indexing one new meeting (auto) | $0.0001-0.0005 (one-time after STT) |
| Background indexing of 100 legacy meetings (first launch) | $0.01-0.05 (one-time) |
| Embed user query (per turn) | ~$0.000001 |
| LLM call per chat turn (Llama 3.3 70B via OpenRouter) | $0.005-0.02 |
| Active user: 50 chat turns/month | $0.25-1.00 |

Folded into existing tier cost discussion (§13.4). For Pro tier with 20 meetings/month + 100 chat turns: post-processing $0.80-3.00 + chat $0.50-2.00 = $1.30-5.00 LLM overhead per user per month.

Each chat turn writes a `usage_log` row with `billable_unit=llm_chat_turn`, opaque request_id, no content reference.

#### 7.14.6 Privacy + zero-persistence

Same model as transcribe/post-process: backend sees chunks + question + history in memory while routing to OpenAI/OpenRouter, never persists. Stricter than the brainstorm-draft architecture because chunks never live on backend at all (they're in vault, sent in-memory per request). Server-side `usage_log` has no reference to which chunks were used — only billable token counts.

LLM provider terms-of-service review (plan-phase): confirm OpenAI + OpenRouter do not train on API inputs (current OpenAI API ToS as of 2026: opted out by default for paid API; document for users).

#### 7.14.7 Migration + re-indexing

**Adding a new meeting**: auto-index per §7.2 step 18.

**Editing a transcript**: reconciler detects `transcript.md` mtime change → marks affected chunks as stale (compare segment_idx_range vs changed segments) → re-embeds stale chunks only via background queue. Minimizes cost.

**Model version upgrade** (e.g. switching from text-embedding-3-small to a future v4): old embeddings still searchable but mixing models in same retrieval is meaningless (different vector spaces). Detection: query uses current model → retrieves only chunks with matching `embedding_model_version`. Stale-model chunks excluded from retrieval until re-indexed. Settings → "Re-index all meetings with new embedding model" trigger; runs as background batch with progress.

**Voice library / projects deleted**: `transcript_chunks` rows cascade-delete via meeting_id FK when meeting deleted; otherwise orthogonal.

**Edge cases**:
- Query on empty vault → "No indexed meetings yet — index some by transcribing or wait for background batch".
- Query on meeting with indexing-failed → result includes chunks from other meetings; UI hint "1 meeting couldn't be indexed for chat".
- Chunk with deleted speaker (speaker_id FK broken) → citation still shows transcript context, speaker badge falls back to "Unknown speaker".
- Embeddings DB corrupted → rebuild from `transcript.md` files automatically on next launch (full re-index, surface progress).

### 7.15 Protocol distribution (email + telegram)

After `protocol.md` is generated (§7.9), the user can share it with meeting participants via email or Telegram. The flow is **draft → preview → send** (no auto-send in v1.0) — gives the user a chance to review and edit before LLM-generated content reaches recipients. **Scope locked-in for v1.0:** Email + Telegram only; Slack + Teams in Phase 2; WhatsApp dropped (§14).

#### 7.15.1 Architecture summary

| Decision | Value | Reasoning |
|---|---|---|
| Channels v1.0 | Email + Telegram | Universal coverage for tech-insider/RU/KZ audience; ~2 weeks integration vs ~5-6 for all 5 |
| Transport | Backend-proxy (both channels) | SMTP from desktop is unreliable; Telegram Bot API needs token storage best on backend; consistent with §3.4 in-transit-only model |
| Trigger | Draft → preview → send | Mitigates LLM hallucination risk («команда якобы приняла решение X»); user is final reviewer before send |
| Contact storage | Frontmatter in `<vault>/People/<name>.md` | Plain text; synced with vault to all devices; Cowork-readable. One-time warning at first contact entry |
| Email sender | SES default + Gmail/Outlook OAuth opt-in | SES zero-setup baseline; OAuth gives better deliverability + authentic sender. `oauth_tokens` table holds refresh tokens (§4.1) |
| Telegram bot | Shared `@AudioTranscriberBot` | Backend-held token; recipient `/start`s our bot once. User's own bot deferred to Phase 2 |
| Audit log | SQLite `distributions` + JSONL `<vault>/.audio-transcriber/distributions.log` | Queryable + Cowork-readable; same «files own content, SQLite owns index» pattern as segments/tasks |
| Opt-out | Per-speaker `auto_distribute_protocols: false` frontmatter flag | Default true; blocked speakers excluded by default from recipient list with «BLOCKED — opt-out» chip |

#### 7.15.2 Send flow (10 steps)

1. **Pre-conditions:** `meeting_postprocess_runs[protocol].status="done"` AND user has at least one channel configured (SES is always available; Telegram requires user to know participants' chat_ids). UI gates the «Разослать» button accordingly with humanized blocker hints («Сначала дождись завершения протокола», «Подключи Telegram в Settings» — links to relevant Settings page).
2. **User clicks «Разослать»** on the meeting view (header button next to «Открыть в Cowork»). Tauri opens preview modal in a separate route `/projects/:projectId/meetings/:meetingId/distribute` (modal-style but URL-routed so back button works).
3. **Tauri Rust assembles default draft:**
   - Subject = `«{{meeting_type}} от {{meeting_date}} — {{project_name}}»` (i18n-rendered template; user-editable).
   - Body = full content of `<meeting>/analysis/protocol.md` minus YAML frontmatter (rendered for sharing — `_default.md` template is already designed to be shareable).
   - Recipients = all `meeting_participants` (FK voice_library_speakers) with at least one contact channel populated AND `auto_distribute_protocols != false`. Each recipient shown with their `display_name`, the channel that will be used (first available from `protocol_distribution_channels` priority order), and a checkbox enabled by default.
   - Blocked recipients (opt-out flag set) shown grayed-out with «BLOCKED — opt-out» chip; checkbox disabled. User can manually override per-send by clicking the chip (records `opt_out_override=true` in `distribution_drafts.recipient_overrides` for this send only; doesn't change the speaker's frontmatter flag).
   - Persists to `distribution_drafts` SQLite table (§4.3) with `status="draft"` and the draft id is the modal route's URL parameter — closing app + reopening returns to same draft.
4. **User edits in preview:** subject (single-line input), body (multiline markdown editor with live preview tab), recipients (toggle checkboxes; per-recipient channel selector when speaker has multiple channels populated). Auto-save on every change (300 ms debounce) to `distribution_drafts.body_markdown` / `subject_template` / `recipient_overrides`. Editor supports markdown bold/italic/lists/links (subset — no images, no embedded files for v1.0).
5. **User can add ad-hoc recipients** (someone not in voice library): «+ Add recipient» button → opens a small form to enter `display_name + email/telegram_chat_id`. These don't get persisted to People/ — they're send-only addresses, stored in `recipient_overrides` jsonb with `is_ad_hoc=true`. User can optionally check «Save to voice library» which creates a new minimal `<vault>/People/<name>.md` file (`speaker_id` generated but no voice embedding yet; `embedding_version=null` denotes contact-only entry).
6. **User clicks «Отправить» button:**
   - Tauri Rust collates final recipient list, splits by channel (email recipients into one bucket, telegram into another).
   - Updates `distribution_drafts.status="sending"`.
   - Tauri Rust async-spawns two parallel HTTPS requests: `POST /api/v1/distribute/email` for email bucket (if non-empty), `POST /api/v1/distribute/telegram` for telegram bucket (if non-empty).
   - Each request body includes the relevant subset of recipients with their channel-specific addresses.
7. **Backend processes each request:**
   - Email: for each recipient, lookup user's `oauth_tokens[gmail or outlook]` row if `sender_mode != "ses"`; refresh token if expired; build MIME message (markdown body rendered to HTML with `markdown-it` lib + plaintext fallback); send via Gmail Send API / Microsoft Graph / SES. Returns per-recipient result.
   - Telegram: for each recipient, `sendMessage` Bot API call with `parse_mode=MarkdownV2`, content auto-escaped per Telegram's MarkdownV2 rules. Recipients who haven't `/start`-ed our bot return Telegram error 403 → status `failed` with humanized error message linking to `audiotranscriber.io/tg-start` (a static page explaining the one-time `/start` requirement with a deep-link button).
   - Backend writes `usage_log` row per channel (`billable_unit=email_distribution`/`telegram_distribution`, units=recipient count).
   - **No content or recipient addresses persisted server-side** (§3.4 invariant maintained — only opaque `request_id` in `usage_log`).
8. **Client receives per-recipient results.** Tauri Rust:
   - Writes one `distributions` SQLite row per recipient with status.
   - Appends one JSONL line per recipient to `<vault>/.audio-transcriber/distributions.log` (mirror).
   - Updates `distribution_drafts.status="completed"` (preserved 30 days for «what did I send?» recall, then garbage-collected by reconciler).
9. **Toast notification:** «Отправлено 4 из 5 (1 failed: ivan@unreachable.com — bounce)». Failed recipients show in a collapsible list inside the meeting view's «Recent distributions» panel; user can click «Retry» which creates a new draft with only the failed recipients pre-selected.
10. **Modal closes**, user is back in meeting view. The view now shows a small badge «Sent: 4 email, 0 telegram, 1 failed — 2026-05-27 14:30» linking to the distributions panel.

#### 7.15.3 Edge cases

**No contact channels populated.** When user clicks «Разослать» on a meeting where no participants have email/telegram populated: preview dialog opens with empty recipient list + inline hint «Добавь контакты участников в Settings → Voice Library, или добавь ad-hoc получателя ниже». Send button disabled until at least one recipient is added.

**All recipients opt-out.** Preview dialog opens with all checkboxes disabled. Banner «Все участники отключили auto-distribute. Override individually или send only ad-hoc recipients». User must explicitly opt-in each blocked recipient OR add ad-hoc recipients.

**OAuth token revoked between draft and send.** Sender-mode=gmail draft, user revokes our Gmail app permission in their Google account, then clicks Send. Backend returns 401 → client surfaces «Gmail доступ отозван. Reconnect в Settings → Channels → Email, или используй SES default» with one-click retry-with-SES affordance.

**Per-user rate limit exceeded** (§13.19). Backend returns 429 with `Retry-After` header → client surfaces toast «Rate limit reached: 50 emails/hour. Try again in 23 min, or upgrade tier». No partial-send — request is atomic at the channel level (either all email recipients sent or none).

**Quota exhaustion mid-send** (free-tier user). Backend processes email bucket, hits 402 partway through telegram bucket → returns partial-success response. Client writes `distributions` rows for successful recipients, marks failed ones with `error_message="QUOTA_EXCEEDED"`. User sees «3/5 sent — quota exhausted on remaining 2. Upgrade tier or wait for next billing period».

**Network disconnect mid-send.** Tauri's request handler catches the error, marks all in-flight recipients with `status="failed"`, `error_message="NETWORK_DROPPED"`. User retries via «Recent distributions → Retry failed» button after reconnect.

**Recipient address invalid format** (typo in email, malformed telegram_chat_id). Backend validates format before sending; returns 400 with structured error pointing to which recipient + which field. Client surfaces in preview dialog as inline validation, blocks Send until fixed. User can edit in-place or remove the bad recipient.

**Group / shared mailboxes** (e.g. `team@acme.com` is not a single speaker). Treat as ad-hoc recipient at send time. No voice library link, no speaker_id. Captured in `distributions.recipient_address` without `recipient_speaker_id`.

**Same recipient via multiple channels.** Default behavior: send through the **first available** channel in `protocol_distribution_channels` priority order — not both. User can override in preview dialog to send same recipient via both channels (creates two `distributions` rows, two recipient checkboxes shown in preview). Useful when user wants Telegram-immediate + email-archive for the same person.

**Long protocol body exceeds Telegram message limit (4096 chars).** Backend splits into multi-message thread (first message: «Часть 1 из N: {{intro}}»; subsequent messages: «Часть N из N: {{continuation}}»). Each `sendMessage` call gets its own status; aggregate result reported as one `distributions` row with status reflecting all-or-some. Email has no equivalent limit (mainstream providers cap ~50 MB which we'll never hit with markdown).

**Recipient blocked our Telegram bot post-`/start`.** Telegram API returns 403 on subsequent `sendMessage`. Marked as `status="failed"` with humanized error «User blocked the bot». User cannot retry to same chat_id; needs to ask recipient to unblock.

#### 7.15.4 Privacy + zero-persistence

- **Backend in-flight visibility** of subject + body + recipient list: same model as STT/LLM/Linear (§3.4) — held in memory while routing to email/Telegram API, garbage-collected when request completes. No content or recipient addresses written to disk or DB.
- **`usage_log` rows** record only `{billable_unit, units (recipient count), provider, cost_usd, request_id, created_at}` — no addresses, no subject, no body.
- **Refresh tokens for Gmail/Outlook** persisted in `oauth_tokens` (encrypted per-user; §4.1) because backend needs to refresh between sessions. Other channel credentials (Telegram bot token = our infrastructure secret, not user data) stored in Railway secret.
- **`distributions` SQLite + `distributions.log` JSONL** are vault-local — backed up only via user's vault-backup mechanism (GDrive backup or vault folder sync). NOT sent to our backend.
- **Sender's IP / OS metadata** captured by external email/Telegram providers per their normal logging — out of our control; documented in Privacy Policy.
- **Per-speaker opt-out** is enforced client-side (preview dialog respects flag). Backend has no knowledge of speakers — request body includes recipient addresses + display names only. Backend cannot enforce opt-out on behalf of the user — trust is local.

#### 7.15.5 Settings UI surface (preview)

- **Settings → Channels → Email**: «Default sender» radio (SES / Gmail OAuth / Outlook OAuth — disabled until connected). Connect/Disconnect buttons for OAuth providers. «Test send» button sends a test protocol to user's auth email to verify deliverability.
- **Settings → Channels → Telegram**: «Connected to @AudioTranscriberBot ✓» status. «How recipients connect» help text + copy-paste-friendly invitation message: «Привет! Я буду присылать тебе протоколы наших встреч через @AudioTranscriberBot. Открой бота и напиши /start чтобы начать получать. Spam-free, можно отключить в любой момент.»
- **Settings → Voice Library → per-speaker page**: contact channels editor (email field + telegram chat_id field with «Find chat_id» help link); `auto_distribute_protocols` toggle; channel priority order drag-drop.

Detailed UI mockups in `/settings/channels/*` routes (§8.1) and `/projects/:projectId/meetings/:meetingId/distribute` modal route.

### 7.16 README generation (per-meeting + per-project)

Meeting `README.md` (§3.5) is auto-generated to serve as an entry-point for Obsidian / Finder / VS Code / Cowork agents that open the meeting folder. It composes metadata + summary excerpt + quick links from existing source-of-truth files — never independently authored. **User edits are NOT respected** (backed up + regenerated); user should use `notes.md` for free-form additions.

#### 7.16.1 Regeneration trigger model — lazy via `readme_dirty` flag

Every state change that affects README content sets `meetings.readme_dirty=true` (§4.3). The regenerator runs lazily on `meeting_view_opened` event (and on Cowork-host `read_meeting_readme` MCP tool invocation if Phase 2 surfaces one) — never synchronously after every state change. This pattern (incremental-invalidation + lazy-rebuild) is borrowed from materialized-view maintenance in databases; here it saves ~N redundant regenerations per meeting (8 post-process passes × at-end status update + voice-identify pass + STT-completion = ~10 dirty events per typical successful transcription pipeline).

**Events that set `readme_dirty=true`:**

| Trigger | Set-dirty location |
|---|---|
| `meetings` row INSERT (new meeting created) | `meeting_create` Tauri command |
| `meetings.status` transitions to any new value | §7.2 step transitions |
| `meeting_segments` INSERT/UPDATE/DELETE | reconciler + §7.2 step 13 |
| `meeting_participants` INSERT/DELETE | voice-identify pass + manual edits |
| `meeting_postprocess_runs[summary].status="done"` (summary is README's content source) | §7.9 phase completion |
| `meeting_postprocess_runs[any].status="error"` (failed-pass surfaced in Quick links) | §7.9 phase completion |
| `meeting.toml` reconciler-detected change (mtime > index `last_seen`) | startup + manual reconcile |
| `analysis/summary.md` body change (LLM regenerated OR user edited) | reconciler + §7.9 regenerate |
| `voice_library_speakers.display_name` UPDATE (cascading rename) | speaker_update Tauri command |
| `projects.name` UPDATE (cascading rename) | project_rename Tauri command |
| User adds first `notes.md` file (Quick links section updates to include link) | reconciler picks up file creation |

**Events that do NOT set dirty:** changes within `audio.<ext>` (binary, not referenced in README), changes to `meeting_postprocess_runs.cost_usd` after the run completes (not visible in README), edits to `<vault>/People/<name>.md` bodies (only frontmatter changes matter — speaker display_name renames are caught via the explicit cascade above).

#### 7.16.2 Lazy-regenerate algorithm

When user opens a meeting view (`meeting_view_opened` Tauri event):

1. React → Tauri command `lazy_regen_meeting_readme(meeting_id)`.
2. Tauri Rust reads `meetings.readme_dirty` for the meeting.
3. **If false**: no-op, return cached `<meeting>/README.md` content to React.
4. **If true** (or file doesn't exist):
   a. Read meeting row + participants + project + summary file (if exists) + count `meeting_tasks`/`decisions`/`open_questions` rows.
   b. Render README via Rust template (templating crate, e.g. `tera`) using the `README_template.md` skeleton (bundled, not user-editable for v1.0 — keeps semantics predictable).
   c. Atomic write: tmpfile + fsync + rename(tmp → README.md). Survives crash mid-write.
   d. UPDATE `meetings SET readme_dirty=false WHERE id=?`.
   e. Return rendered content.

This happens synchronously on view-open but completes in <10 ms typically (small content, all data already in SQLite + summary.md file).

**Optimization for multi-meeting list views** (sidebar meeting list, project meeting grid): no lazy regen triggered — these views show only short metadata (name, date, status badge) from SQLite, not README content. README is read only when user clicks into a single meeting's detail view.

#### 7.16.3 README template (bundled, not user-editable in v1.0)

Hardcoded skeleton in Rust source (`apps/desktop/src-tauri/src/readme/template.rs`):

```markdown
---
schema_version: 1
file_type: meeting_readme
meeting_id: {{meeting_id}}
project: "{{project_name}}"
project_id: {{project_id}}
meeting_date: {{meeting_date}}
meeting_type: "{{meeting_type}}"
meeting_type_id: {{meeting_type_id}}
duration_seconds: {{duration_seconds}}
participants:
{{participants_yaml_list}}
status: "{{status}}"
generated_at: {{generated_at}}
generated_by: "audio-transcriber@{{app_version}}"
---

# {{meeting_date}} — {{meeting_type}}

**Проект:** [[{{project_full_path}}|{{project_name}}]]
**Тип:** {{meeting_type}}
**Дата:** {{meeting_date}}
**Длительность:** {{duration_hms}} {{#if silence_removed_seconds}}(сэкономлено {{silence_removed_hms}} silence){{/if}}
**Участники:** {{participants_wiki_links}}

## Кратко

{{summary_first_two_sentences_or_state_message}}

## Ссылки

- [Полный transcript](transcript.md)
- [Задачи](tasks.json) — **{{tasks_active_count}} active**{{#if tasks_done_count}} (+{{tasks_done_count}} done){{/if}}
- [Детальный анализ](analysis/)
  - [Резюме](analysis/summary.md)
  - [Протокол](analysis/protocol.md)
  - [Повестка](analysis/agenda.md)
  - [Решения](analysis/decisions.md) — {{decisions_count}} принято
  - [Открытые вопросы](analysis/open_questions.md) — {{open_questions_count}} unresolved
  - [Инсайты](analysis/insights.md)
  - [Главы](analysis/topics.json) — {{topics_count}} chapters
{{#if has_notes_md}}
- [Заметки](notes.md) — {{notes_entries_count}} entries
{{/if}}
```

**`summary_first_two_sentences_or_state_message`** rendering rules:
- `meetings.status="done"` AND `analysis/summary.md` exists → first 2-3 sentences of summary.md body (stops at first paragraph break, max 400 chars).
- `meetings.status="audio_silent"` → «*Аудио не содержит речи. Транскрибировать пропущена.*»
- `meetings.status="error"` → «*Транскрибация не завершена: {{error_message}}.*» + Retry CTA link `audio-transcriber://meeting/{{meeting_id}}/retry`.
- `meetings.status="processing"`/`postprocessing` → «*В процессе обработки: {{progress_summary}}*» (e.g. «Transcript готов, Summary генерируется...»).
- `meetings.status="done"` AND no summary.md → «*Summary pass отключён для этого митинга.*»

**Failed-pass Quick links** rendering: passes with `meeting_postprocess_runs[X].status="error"` show with «⚠ failed» suffix in their link + a regenerate-icon. Pass-not-yet-completed (`status="queued"`/`"running"`) shown grayed-out with «⏳ in progress» suffix.

#### 7.16.4 User-edit recovery

If reconciler detects `<meeting>/README.md` mtime > stored README-generation timestamp (i.e. user manually edited the file):

1. Read user's current README content, save to `<meeting>/README.md.user-edited-<ISO_ts>.bak`.
2. Regenerate README from current SQLite state, atomic-write to `README.md`.
3. Surface toast in UI: «README восстановлен из метаданных. Если хотел добавить заметки — используй `notes.md` в этой папке (создастся при первом обращении).»
4. Link in toast jumps to a Settings page explaining the README-vs-notes.md design: «README = auto-generated nav index; notes.md = your free-form area».

This is a deliberate UX friction — we want users to use `notes.md` for additions, not fight the README regenerator. If user repeats edit/recovery 3+ times for the same meeting, the toast escalates to a modal with detailed «Why README is auto-managed» explanation.

#### 7.16.5 Project README is NOT auto-generated

`<vault>/<Project>/README.md` is **user-authored** project description (§3.5 file-format conventions). Distinct from meeting README:

| Aspect | Meeting README | Project README |
|---|---|---|
| Authored by | App (auto-generated) | User (free-form) |
| Editing | Edits backed up + reverted | Edits authoritative (reconciler merges back) |
| Content | Metadata + nav links | Free description of project (20-200 words; used as STT prompt + LLM context) |
| Trigger | `meetings.readme_dirty` lazy regen | Reconciler on file change |
| `generated_by` frontmatter | `audio-transcriber@<ver>` | `user` |

This asymmetry is intentional: meeting README is **derived data** (composed from authoritative sources elsewhere) while project README is **authoritative content** (the only place project description lives). Same filename, different semantics — distinguishable by `file_type:` frontmatter field.

#### 7.16.6 Edge cases

**README written before summary.md ready** (status=processing). Body shows «*В процессе обработки: Transcript готов, Summary генерируется...*» — refresh on each phase completion (via dirty flag).

**Cowork agent reads README via file system before lazy-regen ran.** Worst-case: agent sees stale README. Acceptable — README is hint/navigation, not authoritative content. Agent can call `get_meeting` MCP tool (§6.1) for fresh data. Phase 2: add Cowork-host-friendly `read_meeting_readme` tool that triggers regen first.

**Multiple lazy regens race** (user spam-clicks meeting in sidebar). SQL transaction on the read-then-set-false makes this idempotent. Worst case: write happens twice with identical content; second write is wasted but harmless.

**Disk full during regen.** Atomic-write fails at `rename` step → tmpfile cleaned up, dirty flag stays true, error surfaced as banner «Disk full — README not regenerated. Free up space and reload.».

**Reconciler detects external delete of README.md.** Reconciler sets `readme_dirty=true` for that meeting; next view-open regenerates from scratch. Effectively `rm <meeting>/README.md` is the «force regenerate» power-user gesture.

## 8. Frontend (Tauri + React)

### 8.1 Routes (TanStack Router, file-based)

```
src/routes/
├── __root.tsx                                       # layout + auth guard + vault guard
├── index.tsx                                        # dashboard: recent meetings across all projects + search bar
├── onboarding.tsx                                   # first-launch: pick or create vault
├── projects/
│   ├── index.tsx                                    # all projects (grid + create new)
│   └── $projectId/
│       ├── index.tsx                                # meetings in project (list view)
│       └── meetings/
│           ├── $meetingId.tsx                       # transcript view + tasks panel + speaker management + «Разослать» button
│           └── $meetingId/
│               └── distribute.tsx                   # protocol distribution preview-and-send modal (§7.15); URL-routed so back-button works; persists draft in distribution_drafts SQLite
├── record.tsx                                       # mic recording UI (opens import dialog on save)
├── import.tsx                                       # file picker + import-metadata dialog (4-field form, §7.2 step 2)
├── voice-library/
│   ├── index.tsx                                    # speakers list + filter by project/org/role + enroll new
│   └── $speakerId.tsx                               # speaker detail: ФИО / organization / role / responsibilities / projects / meeting history / re-enroll voice
├── search.tsx                                       # full-text search across vault (FTS5); filters by date/type/project/participant
├── chat/                                            # RAG chat over vault (§7.14)
│   ├── index.tsx                                    # New chat / sessions list
│   └── $sessionId.tsx                               # Open existing chat session
├── settings/                                        # 11 sections — see §8.5.1 IA
│   ├── index.tsx                                    # General (locale, theme, startup)
│   ├── vault.tsx                                    # Vault (path, switch, reconcile, fs-watch, backup, restore)
│   ├── audio-preprocessing.tsx                      # Audio preprocessing (highpass / denoise / loudnorm / VAD)
│   ├── transcription.tsx                            # Transcription (default provider, language, prompt-context preview)
│   ├── voice-library.tsx                            # Speakers, migrate v1→v2, export/import, threshold tuning, per-speaker contact fields (email/telegram), opt-out toggle, channel priority
│   ├── meeting-types.tsx                            # CRUD + protocol templates editor (in-app markdown editor with placeholder autocomplete + live preview against sample meeting + Reset-to-seed per type)
│   ├── post-processing.tsx                          # Per-pass enable (8 passes including agenda), per-type defaults, cost preview
│   ├── channels/                                    # Distribution channels (§7.15) — Email + Telegram in v1.0
│   │   ├── index.tsx                                # Channels overview + active distributions ticker
│   │   ├── email.tsx                                # SES default toggle + Gmail/Outlook OAuth connect + «Test send» + sender mode picker
│   │   └── telegram.tsx                             # @AudioTranscriberBot status + recipient invitation message generator + bot link + connection diagnostics
│   ├── integrations/                                # Sub-routes per integration
│   │   ├── linear.tsx                               # OAuth connect / disconnect + default team/project
│   │   ├── glide.tsx                                # OAuth + default table
│   │   ├── notion.tsx                               # OAuth + workspace + default database picker
│   │   ├── jira.tsx                                 # OAuth (Cloud) | PAT (Self-hosted) tabs + default project + issue type
│   │   ├── yandex-tracker.tsx                       # OAuth + default queue
│   │   ├── bitrix24.tsx                             # OAuth + portal URL + default group/responsible
│   │   ├── github.tsx                               # OAuth | PAT tabs + Project picker + destination_type (DraftIssue/Issue) + optional repo
│   │   ├── webhooks.tsx                             # Generic webhook CRUD (URL + name + optional secret + per-project default)
│   │   ├── gdrive.tsx                               # OAuth + backup schedule + restore picker
│   │   ├── cowork.tsx                               # Install detect, "Open in Cowork", MCP-config copy (§7.10)
│   │   └── mcp-servers.tsx                          # External MCP servers (we as client) + HTTPS MCP API tokens
│   ├── billing.tsx                                  # Subscription, usage gauges, payment methods
│   ├── advanced.tsx                                 # Telemetry, raw JSON editor, recent changes log, factory reset
│   └── about.tsx                                    # Version, licenses, changelog, support link
└── auth.callback.tsx                                # OAuth deep-link target
```

A persistent left sidebar shows the project tree (Projects → Meetings), expandable. Clicking a meeting deep-links to `/projects/:projectId/meetings/:meetingId`. The sidebar tree is driven by a `useLiveQuery` subscription so it reflects vault changes in real time (from in-app actions or reconcile events).

### 8.2 Stack

- shadcn/ui primitives + Tailwind v4.
- Zustand stores: `auth`, `vault` (current path + reconcile state), `projects`, `meetings` (hydrated from vault SQLite via `useLiveQuery`), `mcp-connections`, `voice-library`.
- `openapi-fetch` typed REST client via generated `paths` type for backend calls (stateless proxies + billing).
- Supabase JS client for auth + realtime subscription (billing events only).
- **Local data layer**: TS wrappers over Tauri commands (`local_db_query`, `local_db_insert`, `local_db_subscribe`) that talk to SQLite via `tauri-plugin-sql`. A thin `useLiveQuery` hook subscribes to SQL UPDATE events emitted by the Rust side after writes — gives React the same progressive-render feel as the brainstorm draft's Supabase Realtime, but driven by local state.
- i18next + react-i18next for UI strings (`src/i18n/{ru,en,kk}.json`).

### 8.3 Tauri commands (Rust ↔ TS boundary)

| Command | Purpose |
|---|---|
| `start_mic_record` | `cpal` recording → WAV in temp |
| `stop_mic_record` | Returns local WAV path |
| `probe_audio` | FFmpeg probe (duration, sample_rate, channels) |
| `stream_audio_to_api` | Chunked HTTPS POST from meeting folder + SSE response handling; writes incoming segments to `.cache/segments.jsonl` + `meeting_segments` table |
| `open_file_dialog` | Native file picker |
| `store_session` / `get_session` / `clear_session` | OS keychain via `keyring` crate (Supabase JWT, OAuth tokens) |
| `register_deep_link` | OS protocol handler setup |
| `spawn_mcp_sidecar` | Launch Python subprocess in stdio mode; hands off credential-bridge address |
| **`vault_init` / `vault_open` / `vault_switch`** | Initialize new vault, open existing, switch active vault (see §7.6) |
| **`vault_reconcile`** | Manual full rescan; emits `reconcile-progress` events |
| **`backup_vault_to_gdrive`** | Manual backup trigger (§7.7). Emits `backup-progress` events through stages prepare/manifest/upload/done. |
| **`list_gdrive_backups`** | Lists prior backups in user's Drive folder for restore picker. |
| **`restore_vault_from_gdrive`** | Downloads a backup zip from Drive, verifies manifest, unzips into chosen target folder. Emits `restore-progress` events. |
| **`gdrive_disconnect`** | Clears stored OAuth tokens for Google Drive, pauses any scheduled backups. |
| **`fs_watch_start` / `fs_watch_stop`** | Optional live FS watching (opt-in per vault); emits `vault-changed` events |
| **`project_create` / `project_rename` / `project_archive`** | Create project folder + SQLite row / rename both / archive (folder remains; SQLite marks archived) |
| **`project_set_description` / `project_get_description`** | Read/write `<vault>/<Project>/README.md`. Setter validates word count is between 0 and 200 inclusive (0 allowed because description is optional), writes the markdown file, updates denormalized `projects.description` + `projects.description_word_count`. Getter returns `{text, word_count}` for the editor + banner UI. |
| **`meeting_create`** | Create meeting folder + initial files + SQLite row. Body includes the 4 metadata fields from import dialog (§7.2 step 2). |
| **`meeting_rename` / `meeting_move`** | Rename folder + update SQLite; move between projects updates parent folder |
| **`meeting_set_metadata`** | Update `meeting_date` / `meeting_type_id` / `meeting_participants` post-import (e.g. user remembered another participant after seeing the transcript). Triggers `transcript.md` re-render if participants changed. |
| **`meeting_regenerate_transcript_md`** | Re-render `transcript.md` from current `meeting_segments` + speaker map (after speaker re-labelling) |
| **`run_postprocess_pipeline`** | Kick off all enabled passes (§7.9) for a meeting. Phase A in parallel, Phase B after. Emits `postprocess-progress` events per pass. |
| **`run_postprocess_pass`** | Run a single pass (manual regenerate). `:pass_type ∈ {summary, protocol, decisions, topics, tasks, open_questions, insights}`. If file is `user_edited`, requires `force=true` to overwrite. |
| **`list_protocol_templates` / `read_protocol_template` / `write_protocol_template`** | CRUD over `<vault>/.audio-transcriber/protocol_templates/*.md`. Used by Settings → Meeting Types template editor. |
| **`project_set_instructions` / `project_get_instructions`** | Read/write `<vault>/<Project>/INSTRUCTIONS.md` (§7.10). Setter has a special "re-seed from current README" mode for the Settings UI affordance. Empty content = delete file (instructions are fully optional). |
| **`detect_cowork_installed`** | Best-effort detection of Claude Desktop / Cowork on the OS via expected install paths + URL-handler registry probe. Returns `{installed: bool, detected_method: "path"\|"url-handler"\|null, version?: string}`. Used by `/settings/cowork.tsx` to gate the "Open in Cowork" button. |
| **`open_project_in_cowork`** | Best-effort one-click: tries Cowork deep-link first (if discovered API exists at the time of plan), falls back to `tauri-plugin-shell::open` on the project folder so user can drag-drop into Cowork's UI. Emits result events the panel uses to show a follow-up modal with manual-step screenshots if deep-link unavailable. |
| **`copy_cowork_mcp_config`** | Generates a JSON snippet of the MCP-server config for our stdio sidecar with the current vault path baked in, copies to clipboard via Tauri's clipboard plugin. |
| **`get_online_status`** | Returns `{navigator_online, last_ping_at, last_ping_ok, effective_online}` (§7.13). |
| **`list_offline_queue` / `cancel_queued_action` / `retry_queued_action` / `move_to_top`** | Status panel manipulations of `offline_queue` table (§4.3 + §7.13). |
| **`force_offline_mode_toggle`** | Settings → Advanced toggle. When ON, skips ping + treats all actions as offline (testing/privacy-by-policy). |
| **`embed_meeting`** | Re-runs §7.14.1 indexing for a meeting (chunking + embed + persist). Used by §7.2 step 18 auto-trigger, manual re-index button, and migration of legacy meetings. |
| **`chat_send_message`** | RAG turn: embeds query via backend, does local retrieval, calls `/api/v1/chat`, emits SSE token+citation events, persists `chat_messages` row. Body: `{session_id?, question, scope_type, scope_id?}` — session created if id absent. |
| **`chat_list_sessions` / `chat_get_session` / `chat_rename_session` / `chat_archive_session`** | CRUD over `chat_sessions` (§4.3). Pure local. |
| **`reindex_all_meetings`** | Background batch trigger; iterates meetings with stale or missing `embedding_model_version`, re-embeds. Emits `reindex-progress` events. Used for migration + model upgrades (§7.14.7). |
| **`distribution_create_or_get_draft`** | (§7.15) Creates a new `distribution_drafts` row for the given `meeting_id` OR returns the existing in-flight draft if one exists. Body builds default subject + protocol-body + auto-recipient list per §7.15.2 step 3. Returns `{draft_id, subject, body_markdown, recipients_with_channels}`. |
| **`distribution_update_draft`** | Auto-save endpoint called from preview modal on every change (300 ms debounce). Updates `subject_template` / `body_markdown` / `recipient_overrides` on the draft. |
| **`distribution_send`** | Send-button handler (§7.15.2 step 6). Splits recipient list by channel, async-spawns `POST /distribute/email` + `POST /distribute/telegram` requests via `reqwest`, collects per-recipient results, writes `distributions` SQLite rows + JSONL log lines, sets `distribution_drafts.status="completed"`. Emits `distribution-progress` events for per-recipient status updates streamed to React. |
| **`distribution_list_recent`** | Reads `distributions` rows for a meeting_id (or vault-wide with optional filters: project_id / date_range / channel / status). Used for «Recent distributions» panel in meeting view + dashboard. |
| **`distribution_retry_failed`** | Creates a new draft pre-populated with ONLY the failed recipients from a previous `distributions` batch. Linked back to parent batch via `distribution_drafts.parent_batch_id` (nullable jsonb field on the draft row). |
| **`lazy_regen_meeting_readme`** | (§7.16) Reads `meetings.readme_dirty`; if true, renders README from current SQLite state + atomic-writes `<meeting>/README.md` + clears flag. If file doesn't exist, treats as dirty. Returns README content for direct UI rendering. Called automatically on `meeting_view_opened` event. |
| **`oauth_email_connect` / `oauth_email_disconnect`** | (§7.15) Gmail/Outlook OAuth flow specific commands. `_connect` opens system browser to `/api/v1/oauth/:provider/start` → on callback persists access_token + email_address (refresh_token is server-persisted in `oauth_tokens` per §5.1). `_disconnect` revokes server-side token (call to `/api/v1/oauth/:provider/revoke`) + clears local cache. Distinct from generic `oauth_:provider_*` task-backend commands because the persistence semantics differ (server-side vs keychain). |
| **`copy_telegram_invitation_message`** | (§7.15) Copies the «Привет! Я буду присылать тебе протоколы...» onboarding message text to clipboard, with optional user-name substitution. Used by Settings → Channels → Telegram quickstart helper. |
| **`list_meeting_types` / `create_meeting_type` / `rename_meeting_type` / `archive_meeting_type`** | CRUD over `meeting_types`. Archive instead of hard delete to preserve historical references in `meetings.meeting_type_id`. |
| **`get_last_used_metadata`** | Returns smart defaults for the import dialog. Implements the statistics-as-ML algorithm in §7.8 — explicit/implicit-weighted SQL `GROUP BY COUNT` over `meeting_participants` + `meetings`, fallback ladder (project,type) → (project,*) → (*,type) → empty. No LLM, no server roundtrip. |
| `local_db_query` / `local_db_insert` / `local_db_subscribe` | All meeting / segment / task / speaker reads + writes; emits change events back to React |
| `local_db_migrate` | Run `index.db` schema migrations at startup |
| `local_db_export_voice_library` / `local_db_import_voice_library` | Portable JSON dump/load for moving voice library across vaults |
| **`speaker_create` / `speaker_update` / `speaker_archive`** | CRUD over `<vault>/People/<name>.md` (writes YAML frontmatter + markdown body) + SQLite mirror. Archive = sets `archived_at` in frontmatter + SQLite (file remains); hard-delete = remove file too (separate `delete_files=true` confirmation, since historical references to `speaker_id` in old meetings would orphan). |
| **`speaker_associate_with_project` / `speaker_disassociate_from_project`** | Adds/removes `project` entry in speaker's frontmatter + `speaker_projects` row. Used by Settings → Voice Library → Speaker detail page's "Projects" multiselect. |
| **`list_speakers_in_project`** | Returns speakers declared in a project via `speaker_projects` — used by import dialog defaults (§7.8) + project detail page "Team" section. |
| **`audio_preprocess`** | Full audio prep pipeline (§7.2 step 4): ffmpeg decode → highpass → optional RNNoise denoise → loudnorm → Silero VAD trim. Body specifies which stages to enable (defaults from vault settings). Produces `<meeting>/.cache/audio.trimmed.wav` + records params + silence_intervals into `meeting.toml`. Used both (a) automatically by §7.2 step 4 before STT upload, and (b) on demand from the audio editor UI ("Re-run preprocessing" button after user changes settings or wants to A/B-test denoise on/off). Same code path either way. |
| `apply_audio_cut` | ffmpeg trim/cut on local file (replaces server-side audio_cutter) |
| `check_for_updates` | Tauri updater (signed manifest channel) |

The Rust layer owns all writes to vault files + SQLite and all local ffmpeg invocations. Webviews go through these commands; the sidecar reads SQLite directly (read-only) and reads vault files directly for `get_meeting` content responses.

### 8.4 Python sidecar packaging

- **Primary**: PyOxidizer-built embedded Python 3.12 + frozen `mcp-tools` package + deps (pydantic, mcp-sdk, httpx). One binary, ~25 MB. Cold start ~180 ms.
- **Bundled**: artifact lives at `apps/desktop/src-tauri/python-sidecar/venv-embed/`. CI step `pyoxidizer build` runs before `tauri build`.
- **Spawned**: `spawn_mcp_sidecar` Rust command launches the binary with `--mcp-stdio --jwt <token>`. Communication via stdio per MCP protocol.
- **Fallback**: PyInstaller `--onefile` (~45 MB, ~400 ms start) if PyOxidizer flaky on Windows for our deps.
- **Risk**: see §13.2.

### 8.5 Settings architecture

Settings is a first-class product surface — for the tech-insider audience, the polish of the Settings panel signals overall product quality (Linear / Vercel / Notion benchmarks). The design here unifies what's been scattered across §3.4, §5.1, §7.2–§7.10, §8.1, §8.3, and §4.3.

#### 8.5.1 Information architecture — 12 sections

```
Settings (sidebar nav, vertical)
├── General                  — locale, theme (auto/light/dark), startup behavior
├── Vault                    — current vault path, switch, reconcile, fs-watch toggle
├── Audio Preprocessing      — highpass / denoise / loudnorm / VAD toggles + thresholds
├── Transcription            — default provider, language, code-switching, prompt-context preview
├── Voice Library            — speakers list, migrate v1→v2, re-enroll, export/import, threshold tuning,
│                              per-speaker contact fields (email/telegram), opt-out toggle, channel priority
├── Meeting Types            — CRUD + protocol templates editor (per-type, with placeholder autocomplete)
├── Post-Processing          — per-pass enable (8 passes incl. agenda) + per-meeting-type default overrides
│                              + cost preview
├── Channels                 — Email (SES default / Gmail / Outlook OAuth) + Telegram (@AudioTranscriberBot)
│                              distribution-channel configuration (§7.15)
├── Integrations             — Linear / Glide / Notion / Jira / Yandex Tracker / Bitrix24 / GitHub / Webhooks
│                              / Google Drive / Cowork / external MCP servers + API tokens
├── Account & Billing        — subscription tier, usage gauges, payment method, MCP API tokens (HTTPS scope)
├── Advanced                 — telemetry opt-in, raw JSON editor, debug logs, schema_version, factory reset,
│                              force offline mode
├── Status / Queue           — online indicator, offline queue panel (sortable/filterable), manual retry/cancel,
│                              last ping time, sign-in state, connectivity events log (§7.13)
└── About                    — version, licenses (open-source attributions), changelog, support link
```

Top-bar: persistent **search (Cmd/Ctrl+K)** across all settings (§8.5.3), with breadcrumbs showing the current section. Right of each form field: a small `?` icon expands an inline help tooltip with the setting's effect + default + linked spec section.

#### 8.5.2 Storage tiers — explicit rules

Four storage locations, each with a single clear purpose. Plan-phase code reviews enforce these rules; any setting added in the wrong tier is a review block.

| Tier | Location | Purpose | Examples |
|---|---|---|---|
| **Install-global** | `%APPDATA%/audio-transcriber/app.json` (Windows) / `~/Library/Application Support/audio-transcriber/app.json` (macOS) | App-process state independent of any vault | `current_vault_path`, `recent_vaults[]`, `telemetry_opt_in`, `theme`, `keybindings_overrides` |
| **Vault-local** | `<vault>/.audio-transcriber/settings.json` | Anything tied to user content; portable with vault sync | audio preprocessing toggles, default STT provider, post-processing per-pass defaults, meeting-type-level overrides, `gdrive_folder_id` cache, fs-watch toggle, identify thresholds |
| **OS keychain** | `keyring` crate (Windows Credential Manager / macOS Keychain) | Secrets only | OAuth refresh tokens (Linear, Glide, Google Drive), Supabase JWT (current session) |
| **Backend Postgres** | `user_settings` table (§4.1) | Server-visible state used by HTTPS MCP `get_settings` / billing flows | `ui_locale`, server-side `default_provider` (mirror of vault), `email_notifications` opt-in |

**Decision rules** (one-line each):
- Tied to user content / portable with vault → **vault-local**.
- Tied to OS install / not portable → **install-global**.
- Secret (any token, key, password) → **OS keychain**.
- Needed by HTTPS MCP or backend billing flow → **backend Postgres** (mirror vault if useful for offline UI).

**Conflict resolution.** When vault-local and backend Postgres disagree (e.g. user changed `ui_locale` on another device): vault-local wins for in-app UI, backend Postgres value updated on sign-in to match. For settings only in backend Postgres (e.g. `email_notifications`): backend is source of truth.

#### 8.5.3 Search-across-settings (Cmd/Ctrl+K)

Hits when user is anywhere in Settings or via global app shortcut → opens modal with full-text search over `{section_name, field_label, field_help_text}`. Each result row: setting label, current value, section breadcrumb, "Jump to" button.

Implementation: settings schema declared in a single TS file (`apps/desktop/src/settings/registry.ts`) — section/field/label/help/default/storage_tier — driving both the UI and the search index. No string drift between rendered UI and search results.

#### 8.5.4 Per-setting affordances (consistent across all fields)

Every settings field, regardless of section, exposes the same micro-interactions:

- **Current value vs default indicator.** Field shows current value with a small "(default)" badge or "(custom)" badge. Hover reveals the default value.
- **Reset to default** ↺ icon next to each field. Single click reverts that field to default; bulk "Reset section" button at section header.
- **Save discipline.** Most fields auto-save on blur with a toast "Saved". Destructive fields (vault switch, factory reset, OAuth disconnect) require explicit confirmation modal.
- **Validation.** Inline below field, debounced 300 ms. Errors block save. Warnings (e.g. "threshold lower than recommended") don't block but surface visually.
- **Linked spec ref.** `?` tooltip shows "Detailed behavior: §7.2 step 4" linking out to docs if user clicks (opens documentation site, not the spec markdown).

#### 8.5.5 Reset, Import, Export

- **Reset section**: per-section header button. Reverts ALL fields in the section to defaults. Confirmation modal lists what will change.
- **Reset all**: Advanced → "Factory reset" — wipes install-global `app.json` + per-vault `settings.json` (asks per active vault) + clears keychain. Vault content (meetings, voice library) UNTOUCHED. Confirmation requires typing app name.
- **Export settings**: Advanced → "Export settings". Produces `settings-export-<ISO>.json` containing: `{schema_version, app_json, vault_settings_json}` (vault settings only for the current vault). Secrets NEVER exported (no OAuth tokens — user re-connects on import).
- **Import settings**: Advanced → "Import settings from JSON". Pre-import diff modal shows what will change. Apply requires confirmation. Schema version mismatch → migration runs first (§8.5.6).

#### 8.5.6 Settings schema versioning

Both `app.json` and `settings.json` carry top-level `"schema_version": <int>`. On app launch:

1. Load files, check `schema_version` vs current code's expected version.
2. If older: run migrations sequentially (v1→v2→v3) via `apps/desktop/src/settings/migrations/v<N>.ts` functions. Each migration is a pure function `(old) → new`.
3. If newer (user downgraded app): refuse to load with clear message "Settings were created by a newer app version (v1.2). Install ≥ v1.2 or factory-reset settings to continue."
4. Backup of pre-migration file kept at `<location>/migration-backups/<old_version>-<timestamp>.json` for emergency rollback.

Plan-phase rule: any field rename / type change / removal across versions REQUIRES a migration function + test. CI grep blocks merges that change settings schema without a corresponding migration.

#### 8.5.7 Per-meeting / per-meeting-type override pattern

A consistent pattern wherever a setting can be overridden at narrower scope:

- Settings field is the **default** at the broadest scope (vault-level).
- Per-meeting-type override: tri-state radio in Meeting Types editor — `Inherit (vault default)` / `Force ON` / `Force OFF`. Inherit doesn't store a value; force stores a value and shows a chip "Overridden" next to the type name.
- Per-meeting override: same tri-state in the import dialog's "Advanced" expander. Force values stored in `meeting.toml` per-meeting setting block.

Resolution order at runtime: per-meeting setting → per-meeting-type default → vault default → app-level default (hardcoded). Each level's value is queryable via `get_effective_setting(meeting_id, setting_key)`.

Settings page shows badges: "5 meetings have per-meeting overrides for this setting" → click expands a list with meeting names + "revert to default" per-row.

#### 8.5.8 Discoverability and recent-changes log

- **"What's new" badge** on settings added in the last app version (~30 days). Reset on user click.
- **Recent settings changes** log in Advanced — last 50 changes `{timestamp, section, field, old_value, new_value, source: user|migration|import}`. Helps user recover "did I accidentally turn that off?" Sourced from a lightweight append-only log in `<vault>/.audio-transcriber/settings_changes.log`.
- **Hover tooltips** never just repeat the field label — always describe effect, default, and 1-sentence "why this exists".
- **Empty-state hints** in sections user hasn't configured yet: "You haven't connected any task backends. Linear / Glide → click Connect".

#### 8.5.9 Accessibility (foundations for v1.0; full WCAG audit Phase 2)

- All settings reachable via keyboard (Tab/Shift-Tab/Enter/Esc); focus indicator visible.
- ARIA labels on every interactive control; help-tooltip content available to screen readers via `aria-describedby`.
- Color contrast meets WCAG AA; status indicators paired with iconography (not color alone) so colorblind users can distinguish ON/OFF.
- Cmd/Ctrl+K search has full keyboard nav (arrows + Enter); no mouse required.

## 9. i18n

- Library: `i18next` + `react-i18next`.
- Languages MVP: RU (primary), EN, KK (niche differentiator).
- File structure: `src/i18n/{ru,en,kk}.json`. Flat key-value, namespaced (`auth.signin`, `transcript.empty`, `errors.QUOTA_EXCEEDED`).
- Detection: system locale → fallback EN. User override in settings → persisted to `user_settings.ui_locale`.
- Backend errors return `error_code` (machine-readable); client maps to localized message.
- MCP tool descriptions remain English (LLM-facing — uniform).

## 10. Testing strategy

| Layer | Tool | Coverage |
|---|---|---|
| Backend unit | pytest | `providers/`, `tasks/`, `mcp_tools/` (handler bodies, scope-filter logic). Lift the existing ~462 tests; port to the FastAPI shape. Target ≥ 80% branch on business logic. |
| Backend integration | pytest + httpx + Supabase local (docker-compose) | All ~14 FastAPI endpoints + stateless-proxy zero-persistence assertion: after each transcribe/extract/identify call, assert no DB rows reference user content. Real Postgres + RLS enforcement on the 5 billing/auth tables. |
| Frontend unit | Vitest | React hooks, Zustand stores, API client wrappers, **vault SQLite layer** (queries, migrations, voice-library import/export). Use `better-sqlite3` in-memory for fast iteration. |
| Frontend e2e | Playwright | Webview testing. First-launch onboarding, vault switch, file upload + live segment-progressive render from vault, settings, voice enrollment. |
| Tauri commands | `#[cfg(test)]` + cargo test | mic record, deep-link parse, keychain mock, SQLite migration runner, credential bridge, ffmpeg silence/cut wrappers, **vault init / open / reconcile / project + meeting CRUD with FS roundtrip on tempdir**. |
| **Vault file-format roundtrip** | cargo test | `meeting.toml` parse/serialize, `.cache/segments.jsonl` parse/append/replay, `transcript.md` render from segments → re-parse → diff is empty. Property-test with arbitrary segment sequences. 3-bucket layout invariant: meeting folder always has root files + optional `.cache/` + optional `analysis/` subfolders; no other top-level entries created by the app. |
| **Reconcile invariants** | cargo test + tempdir fixtures | (a) Edit `transcript.md` externally → reconcile merges back into `meeting_segments` correctly. (b) Move meeting folder across projects → reconcile updates `meetings.project_id`. (c) Delete `audio.<ext>` → reconcile marks `status="audio_missing"`. (d) Corrupt `.cache/segments.jsonl` → reconcile skips bad lines and reports + regenerable from SQLite. (e) Two reconciles on unchanged vault are no-ops. (f) Delete `.cache/` folder entirely → reconcile does NOT mark meeting as failed (cache is regenerable; meeting remains browsable). (g) Delete `analysis/` folder entirely → reconcile marks each `meeting_postprocess_runs` row as `status="missing"`, UI shows «Restore» CTA. (h) Add meeting `README.md` mtime > stored gen_at → reconciler backs up to `.user-edited-<ts>.bak`, regenerates from SQLite. |
| **Audio preprocessing pipeline (highpass / denoise / loudnorm / VAD + time mapping)** | cargo test + synthesized audio fixtures + property test | (a) **Stage order**: pipeline applies highpass → denoise → loudnorm → VAD in that exact order; intermediate buffers checked at each boundary. (b) Synthesized speech-silence-speech audio: VAD detects intervals within ±50 ms of ground truth. (c) Property: for arbitrary silence_intervals and any trimmed_t ∈ [0, trimmed_duration], `trimmed_to_original(trimmed_t)` is monotonic in trimmed_t and ≤ original_duration. (d) Round-trip: `original_to_trimmed(trimmed_to_original(t)) == t` for any trimmed_t. (e) All-silence input → `trimmed_duration == 0` and `silence_intervals == [[0, original_duration]]`. (f) No-silence input → identity for VAD stage (intervals == []), but loudnorm + highpass still produce a modified WAV. (g) **Per-stage disable**: turning denoise off (default) doesn't load the RNNoise ONNX model — verified by mock-loader call count. Same for loudnorm/highpass advanced toggles. (h) **Denoise quality smoke**: synthetic clean signal + injected pink noise → after RNNoise, SNR improves by ≥ 6 dB (lower bound; real-world recordings often improve 10-15 dB). (i) **Loudnorm correctness**: input at -30 LUFS → output measured at -16 ± 1 LUFS via `ebur128` measurement library. (j) Lift the existing `tests/test_silence_remover.py` numpy test corpus and port to Rust assertions over the same expected outputs — directly verifies VAD-param parity with the Python module. (k) **Param roundtrip**: after preprocessing, `meeting.toml` contains `denoise_applied`, `denoise_model`, `loudnorm_target_lufs`, `highpass_cutoff_hz` — re-running preprocess with these params reproduces the exact same `audio.trimmed.wav` (byte-identical). |
| **Meeting types seed + lifecycle** | cargo test + tempdir vault fixture | (a) Fresh vault init inserts 10 default `meeting_types` rows in the documented order. (b) Schema migration v1→v2 (or any future bump) does NOT re-insert defaults; user-deleted types stay deleted. (c) Archive vs delete: archiving a type keeps it queryable for historical `meetings.meeting_type_id` references but hides it from import-dialog dropdown. (d) Rename propagates to display (denormalized name in transcript.md regeneration) but the underlying `id` stays stable — `meeting.toml.meeting_type` references by name + falls back to id on rename collisions. |
| **Import-dialog defaults logic** | Vitest + Rust unit tests | (a) `get_last_used_metadata` for empty vault returns `{date=mtime, type=null, project=null, participants=[]}`. (b) After 1 standup with Иван+Петр in Project Alpha: importing another file with project=Alpha pre-selects type=Standup + participants=[Иван, Петр]. (c) Cross-project: importing into Project Beta does NOT inherit Alpha's standups' participants. (d) (project, type)-combo specificity: weekly standup vs weekly customer call learn different participant sets even within the same project. |
| **Smart-defaults learning loop (§7.8)** | cargo test + scripted vault fixture | (a) Weighting: declarative weight 3 > 1 explicit appearance (weight 2). (b) 2 explicit (weight 4) > 1 declarative (weight 3) — accumulated observation overrides declared membership. (c) Tie-break: when two speakers have equal cumulative weight, the more recently seen one (`MAX(meetings.created_at)`) wins. (d) Deselection learning: simulate user importing 5 meetings where Иван was a default but they removed him each time → 6th import no longer suggests Иван (his weight stopped accumulating; other speakers overtake by rank). (e) Fallback ladder: (project, type) empty → (project, *) → (*, type) → empty. (f) Threshold floor: a speaker with only 1 implicit signal (weight=1, below `HAVING SUM >= 2`) does NOT appear in defaults until they accumulate more evidence. (g) **Declarative-only first-meeting**: fresh project with 0 meetings, 3 speakers declared via `speaker_projects` → first import dialog pre-selects all 3 (each weight 3, threshold met). (h) Privacy invariant: after seeding 20 meetings with various participants, snapshot Postgres-side usage_log — assert NO row contains speaker_id or project_id references (only opaque request_ids + aggregated minutes). |
| **People directory (§3.5 + §4.3 voice_library_speakers + speaker_projects)** | cargo test + tempdir vault fixture | (a) **File ↔ SQLite roundtrip**: `speaker_create("Иван")` writes `<vault>/People/Иван.md` with YAML frontmatter; SQLite `voice_library_speakers` row created with mirrored fields. (b) **External edit roundtrip**: manually edit `<vault>/People/Иван.md` (change `organization`, add new project to `projects:` array, append responsibility paragraph) → reconcile detects mtime change → SQLite row updated + `speaker_projects` diff applied (new junction row inserted). (c) **YAML frontmatter parser robustness**: missing optional fields (no `organization`, no `role`) → fields stay null in SQLite, no error. Malformed YAML → file marked `needs_review` in reconcile.log, SQLite unchanged. (d) **Speaker deletion / archive semantics**: archive sets `archived_at` in SQLite + frontmatter; speaker doesn't appear in pickers but historical `meeting_segment_speakers` references stay valid. Hard-delete with `delete_files=true` requires confirmation. (e) **Project association sync**: speaker file's `projects: [Alpha, Beta]` reconciled → `speaker_projects` has 2 rows; user removes "Beta" from frontmatter → reconcile deletes that junction row only (other speakers' Beta memberships untouched). (f) **Display-name conflict resolution**: two speakers with same `display_name` → file naming auto-disambiguates (`Иван.md` vs `Иван (2).md`); SQLite uses unique `id`, display layer adds disambiguator. (g) **Rich-context prompt opt-out** (§7.9 tasks pass): with toggle ON, outgoing `/postprocess/tasks` body includes `participants_context: "Иван (CTO — ...); Петр (PM — ...)"`. Toggle OFF → context omitted from prompt. |
| **Voice-identify narrowed candidates** | pytest + cargo test | (a) With 50 enrolled speakers + 3 pre-specified participants: backend `/voice/identify` receives 3 candidates not 50. (b) Same audio, same library, narrowed vs full: narrowed produces ≥ as many confident matches (no regressions). (c) Confidence threshold for narrowed mode is configurably higher (default e.g. 0.7 vs 0.55 for full-library mode) — surface in `vault/settings.json`. (d) Fallback "Match against full library" button performs identify with all 50 candidates and offers "Add to participants" toast on match — adds `meeting_participants(is_pre_specified=false)`. |
| **Project description roundtrip + word-count** | cargo test + tempdir fixture | (a) `project_set_description("…20 words…")` writes README.md and updates `projects.description_word_count=20`. (b) Manual edit of `<vault>/<Project>/README.md` outside the app + reconcile → `projects.description` + `description_word_count` re-sync. (c) Word counter handles RU/EN/KK (CJK-style whitespace not applicable; whitespace-tokenize is correct for our three languages). (d) Setter rejects > 200 words with structured error code. (e) Empty description (0 words) is allowed (no-op write deletes README.md). |
| **STT prompt + LLM post-process context integration** | pytest + httpx mocked providers | (a) `POST /transcribe/start` with `project_description` → backend forwards to Groq/OpenAI Whisper as `prompt` parameter, to Gladia as `context_prompt`. (b) Provider that does NOT support prompt context (Deepgram, AssemblyAI) — `project_description` silently dropped, no error, request succeeds. (c) Whisper-token-cap truncation: 300-word description sent to Groq → backend truncates to first ~170 EN words / ~140 RU words before forwarding (test with real tokenizer). (d) `POST /postprocess/:pass_type` with `project_description` → for every `pass_type`, the outgoing OpenRouter request body's system prompt has description prepended. (e) Zero-persistence invariant: after both call types return, no Postgres row references the description text or post-process output. |
| **Post-process pipeline orchestration (§7.9)** | cargo test + Vitest + mocked SSE | (a) **7 Phase-A passes** run in parallel — assert all 7 SSE streams (summary, tasks, decisions, topics, open_questions, insights, agenda) open concurrently (not sequentially), measured by overlapping `started_at` timestamps. (b) Phase-B `protocol` waits for Phase-A's `summary` + `tasks` + `decisions` + `open_questions` + **`agenda`** before kickoff; topics/insights are referenced if present but not blocking. (c) Per-pass failure isolation: simulate `decisions` returning 500 → `meeting_postprocess_runs[decisions].status="error"` AND other passes proceed unaffected. (d) `meetings.status` transitions queued → vad_running → processing → identifying → postprocessing → done (in order; status-tested at each stage). (e) Per-meeting-type pass defaults override per-vault defaults; per-meeting at-import settings override both. (f) Cost rollup: `SUM(meeting_postprocess_runs.cost_usd) WHERE meeting_id=?` matches total shown in Meeting view footer. (g) **Agenda fallback**: LLM returns empty/uncertain agenda → file contains «*Повестка не зафиксирована...*» literal sentinel (not empty file). (h) **next_meeting extraction** in `summary` pass: prompt fixture with «увидимся в четверг» mention → `summary.md` frontmatter contains `next_meeting:` with parsed date + confidence ≥ 0.5; same fixture without next-meeting mention → field absent. (i) **`protocol.md` is in `analysis/` subfolder**, NOT root (path assertion). `tasks.json` IS at root (intentional exception per §3.5). |
| **README generation + lazy regen (§7.16)** | cargo test + tempdir fixtures + Vitest | (a) `meetings.readme_dirty=true` set on every documented event (meeting create, status change, segment write, participant change, summary completion, speaker rename, project rename); 11+ trigger events from the §7.16.1 table all set the flag. (b) `meeting_view_opened` event triggers `lazy_regen_meeting_readme` Tauri command; flag=false → no-op (assert file mtime unchanged); flag=true → renders README + clears flag (assert mtime updated + flag=false). (c) **Atomic write**: regenerator simulated to crash mid-write → tmpfile cleaned, README.md unchanged (still old version). (d) **User-edit recovery**: manually modify README.md → reconciler detects mtime > stored gen_at → creates `README.md.user-edited-<ts>.bak`, regenerates from SQLite, surfaces toast event. (e) **Template fields** rendered correctly for each `meetings.status` value (audio_silent, error, processing, done with summary, done without summary). (f) **Project README is NOT auto-generated** — `<vault>/Project/README.md` writes by user are authoritative; reconciler doesn't backup-and-regen these (asymmetry with meeting README). (g) **Multi-meeting-list view does NOT trigger lazy-regen** — only single-meeting detail view. |
| **Frontmatter conventions (§3.5.1)** | cargo test + golden fixtures | (a) Per-file-type schema validation: meeting README has required fields {meeting_id, project, meeting_date, participants}; transcript.md has {provider, language, voice_identify_applied}; analysis/*.md has {pass_type, derived_from, model_used}. (b) Unknown frontmatter keys preserved verbatim through reconcile cycle (forward-compat invariant): add `tags: [important]` manually → reconcile → keys remain. (c) Malformed YAML in frontmatter → reconciler restores from SQLite + logs to reconcile.log + marks meeting `needs_review`; body content preserved untouched. (d) `derived_from:` dependency graph: changing `transcript.md` mtime → reconciler queries inverted index → marks all files declaring `derived_from: [transcript.md]` as stale. (e) `schema_version` migration: v1 → v2 frontmatter migration function transforms field shape; golden file comparison. (f) speaker_id-in-frontmatter / name-in-body split: rename speaker → reconciler updates body text references; frontmatter speaker_id unchanged. |
| **Wiki-link resolution (§3.5.2)** | cargo test + golden vault fixtures | (a) `[[Иван Иванов]]` in any file → resolves to `<vault>/People/Иван Иванов.md` (unambiguous). (b) `[[README]]` inside meeting folder → meeting README (proximity); inside project folder → project README (proximity); outside both → documented as «avoid» with warning. (c) `[[Project Alpha/README|Project Alpha]]` syntax: full path resolution + display override correctly parsed and rendered. (d) Cascading rename: speaker «Иван» → «Иван Иванов» → walks all `.md` body content, updates `[[Иван]]` → `[[Иван Иванов]]`, speaker_id in frontmatter unchanged. (e) Wiki-link parser respects markdown code fences (` ```...``` `): `[[ref]]` inside fenced block NOT parsed as wiki-link. (f) Disambiguation: two speakers with display_name «Иван» → second file `Иван (2).md`; wiki-link `[[Иван]]` resolves to first (lower folder ID); `[[Иван (2)]]` resolves to second. (g) Generated content (README, protocol.md) always emits full-path wiki-links; user-authored content allowed proximity-resolution links. |
| **Protocol distribution (§7.15)** | pytest + cargo test + Vitest + httpx mocked email/telegram backends | (a) **Draft create-or-get**: first call for a meeting creates `distribution_drafts` row; second call returns existing draft (idempotent). (b) **Default recipient assembly**: meeting with 5 participants → preview shows 5 recipients with first-available-channel selected from `protocol_distribution_channels` priority. Speaker with `auto_distribute_protocols: false` → recipient shown grayed-out with «BLOCKED — opt-out» chip. (c) **User edit auto-save**: 300 ms debounce, all changes (subject, body, recipient toggles, channel switches) persisted to `distribution_drafts`. (d) **Send splits by channel**: 3 email + 2 telegram → `POST /distribute/email` with 3 recipients + `POST /distribute/telegram` with 2 recipients, in parallel. (e) **Per-recipient result handling**: mock backend returns mixed `{sent, failed, bounced}` results → `distributions` SQLite rows + JSONL log lines written for each; UI shows aggregate toast. (f) **Failed retry**: «Retry failed» button creates new draft with ONLY failed recipients pre-selected; `parent_batch_id` links back. (g) **Telegram /start gate**: mock recipient who hasn't /start-ed our bot → 403 → status `failed` with humanized error pointing to invitation link. (h) **Markdown > 4096 chars Telegram split**: 8000-char protocol → 2 messages, both succeed, aggregate `distributions` row reflects all-success. (i) **OAuth refresh on expired access_token**: gmail token expired → backend auto-refreshes via stored refresh_token in `oauth_tokens` (lookup → refresh → retry send, transparent to client). (j) **Rate-limit 429 surfacing**: backend returns 429 with Retry-After → client displays «Rate limit reached» with countdown. (k) **Privacy invariant**: after 100 distributions to 500 recipients, snapshot Postgres `usage_log` — contains only `{billable_unit, units, request_id}` aggregates; NO addresses, NO subject, NO body content. (l) **Opt-out override per-send**: user clicks BLOCKED chip on opted-out speaker → `distribution_drafts.recipient_overrides` records `opt_out_override=true` for this send; `voice_library_speakers.auto_distribute_protocols` frontmatter flag UNCHANGED (per-send override doesn't mutate persistent state). (m) **Ad-hoc recipient «Save to voice library»**: creates new `<vault>/People/<name>.md` file with `embedding_version=null` (contact-only entry). |
| **Channels Settings UI (§7.15.5)** | Vitest + Playwright | (a) Settings → Channels → Email: SES default radio always available; Gmail/Outlook OAuth buttons disabled until connected; clicking «Connect Gmail» opens OAuth deep-link flow + on callback shows «Connected as user@example.com ✓». (b) «Test send» button sends pre-filled protocol to user's auth email; «Sent ✓» toast on success. (c) Settings → Channels → Telegram: `@AudioTranscriberBot` connection status indicator; «Copy invitation message» button → clipboard contains the standard onboarding text. (d) Voice Library → per-speaker page: contact fields (email + telegram_chat_id) save to `<vault>/People/<name>.md` frontmatter via Tauri `speaker_update` command. (e) `auto_distribute_protocols` toggle: enabled by default; toggling off → frontmatter `auto_distribute_protocols: false` written. (f) Channel priority order drag-drop reordering: `protocol_distribution_channels: [telegram, email]` written when user moves Telegram first. (g) Cmd+K search «email», «telegram», «opt-out» finds the Channels + Voice Library entries. |
| **Pass output schemas + user-edit detection** | cargo test + tempdir fixtures | (a) Each pass's output file has expected schema after a successful run: `summary.md` non-empty markdown, `tasks.json` array conforming to `{id, title, description, assignee_speaker_id?, due_date?, confidence}`, `topics.json` array conforming to `{idx, title, start_seconds, end_seconds}`. (b) User edits file post-generation → reconciler sets `status="user_edited"`; subsequent auto-regenerate skips this file unless `force=true`. (c) Manual click "Regenerate" on user-edited file → confirm dialog shown; after confirm, file overwritten and `status="done"`. (d) Protocol template with `{{placeholder}}` missing from Phase-A outputs (e.g. `{{insights}}` when insights disabled) renders gracefully with placeholder-text fallback, not literal `{{insights}}`. |
| **Protocol templates lifecycle** | cargo test + tempdir fixtures | (a) Vault-init seeds one `<vault>/.audio-transcriber/protocol_templates/<MeetingTypeName>.md` per default meeting_type with built-in structure. (b) `create_meeting_type("Foo")` creates `Foo.md` from generic default template. (c) `rename_meeting_type` renames the template file (not just SQLite); `archive_meeting_type` leaves the template file alone (could be unarchived later). (d) Templates edited externally (Obsidian) are picked up at next reconcile — no `.db` mirror needed (templates are file-only, no SQLite copy). |
| **Cowork integration (§7.10)** | cargo test + Vitest + mocked OS detectors | (a) `project_create` seeds `INSTRUCTIONS.md` from template with `{{project_name}}` + `{{project_description}}` substituted. (b) `project_set_instructions("")` deletes the file (empty = absent semantics). (c) Subsequent edits to `README.md` do NOT auto-update an already-existing `INSTRUCTIONS.md` (instructions are user-owned after seeding); manual "Re-seed from README" button overwrites only on explicit user action. (d) `detect_cowork_installed` returns `{installed: false}` on a tempdir-isolated OS with no Claude Desktop; returns `{installed: true, detected_method}` when fixture sets up a fake URL handler or fake install path. (e) `copy_cowork_mcp_config` produces JSON with current vault path baked in; clipboard mock receives the payload. (f) `open_project_in_cowork` falls back to OS shell `tauri-plugin-shell::open` when deep-link unavailable, emits `"fallback-to-shell"` event the panel uses to surface the manual-step modal. (g) Reconciler treats `INSTRUCTIONS.md` as pure passive file — no parsing, no SQLite mirror, just exists/doesn't-exist state. |
| **Settings registry + storage tiers (§8.5)** | Vitest + cargo test + tempdir fixtures | (a) **Registry exhaustiveness**: every settings field declared in `apps/desktop/src/settings/registry.ts` appears in at least one UI section AND has `{label, help, default, storage_tier}` populated — CI lint rule. (b) **Storage-tier enforcement**: each field's actual read/write path matches declared `storage_tier` (e.g. `theme` declared `install-global` must only touch `app.json`, never `settings.json`). Detected via fixture that intercepts file writes. (c) **Search**: Cmd+K query "denoise" returns the Audio Preprocessing → Denoise field at top rank; query "speakers" returns Voice Library section header. (d) **Reset section**: changes 3 fields in Audio Preprocessing then clicks "Reset section" → all 3 revert to declared defaults; other sections untouched. (e) **Export/Import roundtrip**: export → modify a setting → import → field reverts. Secrets NOT in export payload (keychain access call count = 0 during export). (f) **Per-meeting override resolution order**: per-meeting > per-type > vault > app-default; verified via `get_effective_setting()` called at each level with fixtures. |
| **Settings schema migration (§8.5.6)** | Vitest fixtures + golden files | (a) Loading v1 `settings.json` with current app expecting v3 → runs migrations v1→v2→v3 sequentially; result matches golden file. (b) Pre-migration backup written to `migration-backups/v1-<ts>.json` before any change. (c) Loading v4 file with v3-expecting code → refuses to load with structured error code `SETTINGS_NEWER_VERSION`, app surfaces install-newer-or-reset modal. (d) CI lint: any settings field rename/type-change/removal in a PR triggers a check that a corresponding migration function exists in `apps/desktop/src/settings/migrations/v<N>.ts` (grep-based + test coverage). |
| **Audio chunking pipeline (§7.11)** | pytest + httpx mocked providers + synthesized audio fixtures + property tests | (a) **Stage A opus compression**: 50 MB WAV → opus encode → < 25 MB → no chunking; mock provider receives opus blob. (b) **Stage A skip for high-limit provider**: 50 MB WAV + Deepgram (2 GB limit) → no opus encode (compression call count = 0), single PCM upload. (c) **Stage B boundary discovery**: 100 MB trimmed file + Groq → splits at speech-region boundaries from `silence_intervals` (never mid-region); each chunk ≤ 25 MB × 0.85. (d) **200 ms overlap**: adjacent chunks have last 200 ms of chunk N overlapping first 200 ms of chunk N+1; verified by chunk boundary inspection. (e) **Pathological no-boundary fallback**: synthetic 30-min monologue file (single VAD region) > limit → hard cut + 200 ms overlap + warning logged. (f) **Stage C hybrid sequencing**: 5-chunk job; mock provider with 2s latency per chunk → first segment SSE arrives after ~2s (chunk 1 only), subsequent chunks transcribe with concurrency 3 in parallel. (g) **Stage D timestamp double-mapping property**: for any (silence_intervals, chunk_layout, chunk_local_t) → `chunk_local_t → trimmed_t → original_t` is monotonic across all segments. (h) **SSE reorder buffer**: provider returns chunks out of order (chunk 3 done before chunk 2) → SSE emits in order (chunk 2's segments before chunk 3's). (i) **Overlap dedup**: chunk N's last 200 ms speech segments deduped against chunk N+1's first 200 ms; total emitted segments = sum-per-chunk minus overlap count. (j) **Stage E chunk-level retry isolation**: mock provider fails chunk 3 once, succeeds on retry; chunks 1/2/4/5 transcribe normally; total SSE sequence intact. (k) **Stage E exhausted retries → user surface**: 3 failures of chunk 3 → SSE emits `{type: "chunk_error", chunk_idx: 3}` + user prompt; job continues with gap or aborts per user choice. (l) **Lift `tests/test_cloud_chunker.py` corpus (18 existing tests) and port to async pytest** — protects parity with proven Python behavior. |
| **STT provider capabilities + code-switching (§7.12)** | pytest + httpx mocked per-provider responses | (a) **Capability matrix coverage**: each provider class declares all 6 ABC attributes (`max_upload_bytes`, `accepts_opus`, `supports_diarization`, `supports_word_timestamps`, `supports_code_switching`, `code_switching_config`); CI lint enforces. (b) **Code-switching language=mixed + Deepgram → 422 error** with code `LANGUAGE_NOT_SUPPORTED`. (c) **Code-switching + Gladia** → outgoing request body has `code_switching: true` + `languages: ["ru","en","kk"]`. (d) **Code-switching + Whisper-family** → outgoing request omits `language` field AND `initial_prompt` contains trilingual hint. (e) **Initial-prompt priority resolution**: A/B/C/D cases from §7.12 → for case C (both project_description and code_switching), Whisper-family prompt = trilingual prefix + truncated project_description; non-Whisper providers use project_description as-is in prompt slot. (f) **Word-level normalization**: 3 fixtures (AssemblyAI per-word + per-segment speaker, Gladia per-segment speaker + words array, Groq word array no speaker) → `speaker_aligner` output identical schema `{words[], provider_speaker_tag?}` for each. (g) **Per-segment language tag** populated for code-switching providers, null for non-mixed providers. |
| **Speaker-labeling: provider-grouped vs per-segment (§7.2 step 16)** | pytest + cargo test + fixtures | (a) **Provider with diarization** (AssemblyAI mock): 100 segments with 4 unique `provider_speaker_tag` values → voice-identify makes **4 backend calls** (one per tag, concatenated representative slices), bulk-applies result to all matching segments. (b) **Provider without diarization** (Groq mock): same 100 segments, no tags → falls back to **100 per-segment calls** (one per segment), each independent — inconsistencies allowed. (c) **Representative-segment selection**: for a tag, picks 3 segments by (longest, mid-meeting); covered by golden fixture. (d) **UI surface for non-diarizing provider warning**: when `selected_provider.supports_diarization == false` AND `voice_library_speakers.count() < 2`, Settings + import-dialog show chip "Doesn't auto-label speakers — voice library matches required" (§13.14). (e) **Mixed-mode (cloud diarization OFF user choice + provider supports it)**: not currently supported in v1.0 — `supports_diarization=true` means provider always returns tags. Plan-phase: confirm no providers offer "diarization-off" toggle we'd need to expose. |
| **Offline mode (§7.13)** | Vitest + cargo test + mocked network | (a) **Detection layered**: navigator.onLine=true but ping fails → `effective_online=false`. navigator.onLine=false → immediate offline. Captive portal fixture validates this. (b) **Import offline runs preprocessing**: drop network, import audio → §7.2 steps 1-4 execute fully (audio.trimmed.wav created), step 5 enqueues `transcribe` action with `meetings.status="queued_offline"`. (c) **Queue drains on reconnect**: enqueue 3 transcribe actions offline → reconnect → all 3 process sequentially per priority + queued_at; SSE events emit for each. (d) **Queue persistence across app restarts**: enqueue offline → quit app → restart offline → queue intact; restart online → drains. (e) **Per-MCP-tool offline annotation**: `tools/list` MCP response includes `offline_capable: bool`; mock Cowork host fixture invokes only ✅-tools when fixture is offline. (f) **JWT-expired grace period**: cached JWT expired 5 days ago + offline → all ✅ features work, banner "Sign in within 7 days". 8 days later + offline → ❌ features locked with "Sign in needed". On reconnect → token refresh attempts; if revoked, soft re-auth modal. (g) **Resume mid-chunk on reconnect**: transcribe chunks 1-2 succeed online, network drops, chunks 3-5 pending → on reconnect, queue runner re-issues only chunks 3-5 with same `request_id` (TTL 24h check). (h) **Force-offline-mode toggle**: enable in Settings → all actions queue even if network up; disable → drain resumes. (i) **Quota-exceeded mid-drain**: 402 from one action → status=failed, queue continues for non-billable actions (gdrive_backup). |
| **Task backends — native + webhook (§5.1 + §7.5)** | pytest + httpx mocked providers + signature verification | (a) **OAuth roundtrip per provider**: 7 providers × `start → exchange → refresh` flow → mock-provider returns canned tokens; client receives them; backend never persists. (b) **Per-backend field mapping**: 5-task batch sent through each backend's `send()` → outgoing API request body matches provider's expected schema (mocked endpoints assert payload shape). (c) **Bitrix24 portal URL**: OAuth start includes portal_url; subsequent send-to-bitrix24 calls use that portal — verified via outgoing URL. (d) **Jira self-hosted PAT mode**: bypasses OAuth flow; PAT used as Bearer in send call. (e) **GitHub mode switching**: DraftIssue mode uses GraphQL `addProjectV2DraftIssue` mutation; Issue mode (after scope elevation to `project,repo`) uses `createIssue` + `addProjectV2ItemById`. Switching modes mid-session prompts scope elevation if needed. (f) **Webhook signature**: HMAC-SHA256 of body with shared_secret → header `X-Audio-Transcriber-Signature: sha256=<hex>` matches receiver-side recompute. No secret = no header. (g) **Source meeting back-link**: every task description appends `[Source: ...](audio-transcriber://meeting/...)` markdown; verified across all backends (some support markdown — link clickable; others show as plain text — still usable). (h) **External_id capture**: each backend's send response parsed to extract per-task external_id + external_url; persisted in `tasks.json`. (i) **Token refresh on 401**: per-backend mock returns 401 once → client triggers `/oauth/:provider/refresh`, retries send → succeeds. (j) **Webhook payload schema v1 freeze**: golden snapshot of `tasks/send-to-webhook` request body for a 3-task batch; any future change to the schema triggers test failure (forces explicit /v2 endpoint). |
| **RAG chat over vault (§7.14)** | pytest + cargo test + Vitest + golden corpora | (a) **Topic-based chunking**: meeting with topics.json (5 topics) → 5 chunks; each chunk's `segment_idx_range` matches topic bounds. Without topics → sliding window 200-token / 50-token overlap; chunk count = ceil((tokens - 50) / 150). (b) **Embedding batching**: 100 chunks → 1 backend call (batch). 250 chunks → 3 calls (max 100 per batch). All chunks get same `embedding_model_version`. (c) **Retrieval scope filter**: vault has 50 meetings across 3 projects; query with scope=project_id → SQL filter limits retrieval to that project's chunks only. (d) **Citation rendering**: mock backend SSE emits `{type: "token", payload: "Ivan suggested "}`, `{type: "citation", payload: {chunk_idx: 1, marker: "[1]"}}`, `{type: "token", payload: " refactoring auth"}` → client renders markdown with `[1]` clickable hover-card linked to chunk's source meeting + timestamp. (e) **Conversation history truncation**: 20 prior turns → only last 6 included in next /chat call; total history token count ≤ 2000. (f) **Stale-model exclusion**: after model version upgrade, retrieval query restricts to current `embedding_model_version`; chunks with old version excluded until re-indexed. (g) **Transcript edit invalidation**: edit `transcript.md` → reconciler marks affected `transcript_chunks` as stale → background re-embed only changed chunks (count == changed-segments-only, not full meeting). (h) **Privacy invariant**: after 10 chat turns + 100 meetings indexed, Postgres `usage_log` snapshot contains only billable_unit counts + opaque request_ids; zero references to chunk text, question text, or meeting names. (i) **Vault-wide scope coverage**: query "auth refactor" with vault scope → retrieved chunks span ≥ 2 meetings if topic spans multiple meetings; no per-meeting cap. (j) **Background batch indexing on first launch**: 100 legacy meetings → all eventually have `transcript_chunks` rows; progress events emitted; resumable across app restarts via `offline_queue`. (k) **Embeddings DB corruption recovery**: delete `embeddings.db` → app on next launch detects + triggers full re-index from `transcript.md` files; existing chat sessions preserved (only chunk references rebuild). |
| Sidecar ↔ SQLite | pytest + temp SQLite fixture | Sidecar read-only access, WAL-mode concurrency with mock writer, schema-version mismatch handling, vault-path delivery via credential bridge. |
| Native smoke | WebDriver + Tauri | Pre-release: installer flow on Windows VM + voice library export → fresh-install import roundtrip + vault folder copy → second device opens copied vault. |

CI matrix: lint + tests on every PR. Native smoke runs only on release tags.

**Zero-persistence invariant test (critical):** the backend integration suite includes a property test that, after any sequence of stateless-proxy calls (`/transcribe/*`, `/voice/*`, `/tasks/*`), every server-side table contains only billing/auth/quota rows — no transcript text, task descriptions, audio bytes, or embeddings anywhere. This is the regression-prevention firewall around §3.4.

## 11. CI/CD + deployment

- **Backend**: GitHub Actions → Railway deploy on `main` push. PR previews enabled (one ephemeral Railway env per PR).
- **Frontend installer**: `tauri-action` builds `.msi` on Windows runner. Signed with **Azure Trusted Signing** (~$10/mo, no EV cert needed). Artifacts uploaded to Cloudflare R2; updater manifest published next to them.
- **Auto-updater**: `tauri-plugin-updater` checks signed manifest on R2 at startup + on user demand. Channels: `stable` (default) and `beta`.
- **DB migrations**: Supabase CLI migrations checked into git. Auto-apply on staging branch deploys; production deploys require explicit confirm step.
- **Distribution**: Direct download from `audiotranscriber.io`. Microsoft Store is Phase 2.

## 12. Observability

| Signal | Tool |
|---|---|
| Backend errors + traces | Sentry Python SDK (FastAPI integration) |
| Frontend errors + sessions | Sentry browser SDK |
| Tauri Rust panics | Sentry Rust SDK |
| Structured logs | `structlog` → Railway native logs |
| Metrics | Prometheus exposed at `/metrics`, Grafana Cloud free tier |
| Uptime | BetterStack (free tier, 1-min checks) |

Privacy: telemetry is **opt-in** in Settings. Default OFF (developer audience expects this). Sentry configured with PII scrubber.

## 13. Open questions / risks

### 13.1 stdio MCP credential bridge — JWT + SQLite path delivery

The stdio sidecar needs both (a) the current Supabase JWT (for backend proxy calls) and (b) the path to the local SQLite database (to read transcripts/tasks/speakers — §6.3). The brainstorm draft proposed `--jwt <token>` via argv; this exposes the JWT in process listings. Refined plan:

- Tauri spawns the sidecar with a single short-lived spawn-proof token in argv (`--bridge-token <opaque>`).
- Tauri hosts a tiny HTTP server on `127.0.0.1:<random>` (loopback only).
- Sidecar at startup calls `GET http://127.0.0.1:<port>/credentials` with `Authorization: Bearer <bridge-token>`. Bridge returns `{jwt, sqlite_path}` and rotates the spawn-proof token.
- On JWT rotation (Supabase JS auto-rotates) Tauri pushes a new value via SSE to the sidecar's bridge connection. Sidecar replaces its in-memory JWT.

Open sub-questions to lock in plan:
- Lifetime + revocation of the spawn-proof token (TTL? on what events?).
- Windows-specific alternative: named pipe vs loopback HTTP (named pipe avoids local-port-scanning attacks).
- Sidecar restart behaviour on JWT-refresh failure (degrade to billing-only? exit?).

Decision deferred to writing-plans phase.

### 13.2 PyOxidizer on Windows

PyOxidizer's CPython embedding is mature, but our dep tree includes `pydantic-core` (Rust-backed), `mcp-sdk`, `httpx`. Has to be smoke-tested on a clean Windows VM early. Fallback: PyInstaller. **Plan should include a spike on this in week 1.**

### 13.3 Big-bang risk

6 months solo without intermediate feedback is high-risk. The user chose this; mitigation:

- Weekly demo to self / informal users.
- Internal alpha at month 3 milestone (auth + 1 provider + basic UI + Stripe Checkout) even if not externally promoted.
- Plan should split into 6-8 PRs landing every 2-3 weeks, each independently demoable.

### 13.4 Pricing tiers

Placeholder values during brainstorm: free 60 min/mo, Pro $19 / 600 min/mo, Business $79 / 3000 min/mo + $0.05/min overage. Real numbers need:

- Provider cost analysis (Groq $0.04/h turbo, Deepgram $0.43/h, AssemblyAI $0.65/h, etc.).
- **Post-processing pipeline cost** (§7.9 — 8 LLM passes per meeting now that agenda is added): ~$0.04-0.15 OpenRouter spend on a 30-min meeting with default-enabled passes (agenda's narrower input window keeps incremental cost negligible). At Pro tier (600 min/mo ≈ 20 meetings/mo of 30 min each) = $0.80-3.00/mo in pure LLM overhead per active user. **+ RAG chat (§7.14)**: indexing ~$0.01-0.05/mo (negligible), chat turns ~$0.005-0.02 each → 100 turns/mo = $0.50-2.00. **Combined v1.0 LLM overhead: $1.30-5.00/Pro-user/mo**. NOT trivial — meaningfully shifts gross margin. Two options for plan:
  - (a) Bake into tier price (Pro becomes Pro+LLM at $24/mo).
  - (b) Separate "LLM ops" allowance per tier (free: 0 calls/mo, Pro: 200 calls/mo, Business: 2000 calls/mo) with per-call overage.
  Recommend (a) for simplicity at v1.0; revisit (b) if usage patterns vary wildly between users.
- Margin target.
- Competitor benchmarking (Whisper.app, Otter.ai, Rev.com — all charge $20-30/mo for ~10 hours of transcription + similar AI-features bundle).

Out of scope for this design doc; addressed in pricing-decision doc before launch.

### 13.5 PgBouncer + RLS + `SET LOCAL`

Supabase's transaction-pooling mode (port 6543) is required for `SET LOCAL request.jwt.claims`. Connection-pool churn on every request adds latency (~5 ms). Acceptable for v1.0; monitor and revisit if 99p response time degrades. With the local-first revision, the only queries that hit Postgres at all are billing/quota/MCP-token operations — DB pressure should be minimal.

### 13.6 Cross-device data sync (deferred to Phase 2)

Pure local-first means a second device starts empty. Acceptable for v1.0 (tech-insider audience), but feedback may push for sync. Phase-2 design will be opt-in, E2E-encrypted, with the user's passphrase as the key — backend stores opaque ciphertext blobs only, preserving the §3.4 invariant. The current schema must not be repurposed for sync without explicit re-review (RLS surface is currently tiny because there's no user content to leak).

### 13.7 Disaster recovery — user-side responsibility

If a user's device dies without backup, transcripts and voice library are gone. Mitigation in v1.0:

- "Export voice library" → JSON file the user can store anywhere (we recommend their existing cloud sync — Dropbox, GDrive, OneDrive — explicitly NOT our backend).
- Transcript export (md / txt / srt) reuses the current audio-transcriber feature.
- Settings shows a non-dismissable "your data is local — back it up" banner until the user acknowledges.

Open: do we ship a one-click "back up everything to a folder" command that bundles all transcripts + voice library + audio path index? Probably yes; size estimate small (text + 192-float vectors). Detail in plan.

### 13.8 OAuth access-token round-trip visibility

Per §7.5, the client sends the Linear/Glide access token in the body of each task-send request so the backend can proxy. Backend sees the token in memory — same in-transit visibility model as transcript content, no persistence. Risks to monitor:
- Log scrubbing must redact `access_token` keys (treat like passwords).
- Sentry breadcrumbs must NOT capture request bodies of `/tasks/send-to-*`.
- A future per-user signed proxy URL (backend signs a short-lived URL the client can hit Linear with directly) would close even the in-transit visibility, at the cost of more complex Linear OAuth app config. Phase 2.

### 13.9 Vault-layout migrations across app versions

`<vault>/schema_version` tracks the vault file layout (separate from `index.db` schema). When app v1.1 ships a new layout (e.g. adds `notes.md` per meeting, or splits `meeting.toml` into two files), it must:

1. Detect old `schema_version` at vault open.
2. Back up the vault (or at least `.audio-transcriber/`) to `<vault>/.audio-transcriber/migration-backups/<version>-<timestamp>/`.
3. Run migration script (Rust function per version transition).
4. Bump `schema_version`.
5. Show "Vault upgraded" toast with link to backup folder.

Open questions for plan:
- Backup size cap (full vault could be GB). Maybe migrate in place + keep diff log?
- Downgrade story (user installs v1.0 after using v1.1): bail with "vault was upgraded by a newer app version — install ≥ v1.1" message.
- Migration test corpus: synthesized vaults at each historical layout version, run forward-migrations, assert post-migration invariants.

### 13.10 fs-watch UX trade-offs

Live file-system watching (Settings opt-in, off by default) means picking up external edits to `transcript.md`/`tasks.json` without a manual rescan. Risks:
- Tooling like cloud-sync clients (Dropbox/iCloud) triggers many mtime updates in bursts → debounce required (300 ms? 1 s?).
- Watch APIs are inconsistent across OSes (`ReadDirectoryChangesW` on Windows, `FSEvents` on macOS, `inotify` on Linux). Use `notify` crate.
- Recursive watch on a vault with thousands of meeting folders may exceed OS handle limits — fall back to per-project watch + polling for new project folders.
- Watching breaks if vault is on a network drive (SMB) — detect and degrade to startup-only reconcile with a banner.

Decision: ship with watch off by default + a "Live update from disk" toggle. Re-evaluate based on user feedback after v1.0.

### 13.11 Cowork directory / deep-link / partnership

§6.4 frames Claude Cowork as a primary external consumer; §7.10 specifies the integration flow (project = folder = Cowork Project) + the Settings panel + Tauri commands we ship. Open items for plan + business:

- **Does Cowork have a documented deep-link API for "create project from path"?** Spec assumes "best-effort: open folder via OS shell, user uses Cowork's UI to add it" as the fallback. If a `claude-desktop://create-project?path=<encoded>` (or similar) protocol exists/lands, `open_project_in_cowork` (§8.3) upgrades to single-click. Check with Anthropic during plan-phase; if API exists but isn't public, escalate to partnership track.
- **OS install-path detection across Claude Desktop versions.** Standalone Claude Desktop vs Cowork-as-part-of-Claude-Desktop bundling may differ. `detect_cowork_installed` (§8.3) must be robust to both, on macOS + Windows.
- **Is there an official Anthropic directory/registry of Cowork-compatible apps?** If yes, register at launch. Even a non-curated listing buys discoverability among Cowork's audience (overlaps ours heavily). If no formal directory, document the integration ourselves (landing-page section, blog post) and SEO-link from `support.claude.com`-adjacent resources.
- **Reference Cowork MCP-config snippet copy-paste UX** is in scope (§7.10 + §8.3 `copy_cowork_mcp_config`). Document Cowork's MCP-config file location per OS in the Settings panel's help text.
- **Anthropic brand guidelines around "Cowork-compatible" claims.** Brief Anthropic outreach before launch marketing copy goes live. Plan should include a 1-pager review request.
- **Cowork beta channel.** If Cowork's MCP host API or Project model evolves, we should be on its beta channel so our stdio surface + Tauri commands stay compatible. Track Cowork's release cadence in our compatibility-matrix doc.
- **MCP config auto-install probe.** Some Cowork versions may eventually auto-discover MCP servers in known well-known locations. If/when that lands, `copy_cowork_mcp_config` becomes vestigial — replace with a "Detected ✓" indicator and silently install. Plan should track this as a future simplification.

Functionally not a v1.0 code blocker (integration works out of the box because we use standard MCP + standard folders). Partnership work optimizes distribution + UX, not function.

### 13.12 Voice-library migration from Python audio-transcriber (v1-raw → v2-denoised)

The v1.0 Tauri app enrolls voice library on denoised audio (RNNoise applied client-side before `/voice/enroll`); the Python app trained on raw. `voice_library_speakers.embedding_version` distinguishes the two (§4.3); identify pass routes accordingly (§7.2 step 16). Open items for plan:

- **Import path from Python.** Python's library at `~/.audio-transcriber/voices.db` (sqlite-vec). Migration script reads each row, inserts into vault `voice_library.db` with `embedding_version="v1-raw"`. Settings → Voice Library shows "12 legacy speakers — Migrate to denoised?" banner. Migration = re-enroll (user records a fresh 3-10s sample per speaker → denoised embedding overwrites v1 row). One-time prompt, not blocking.
- **Mixed-library identify performance.** §7.2 step 16 splits mixed-version candidate sets into two backend calls per segment. For a meeting with 5 v1 + 3 v2 candidates × 100 segments → 200 backend calls vs 100 for single-version. 2× cost on `/voice/identify`. Acceptable for migration period; encourage user to complete migration to drop back to 1×.
- **Threshold differences between versions.** v1-raw embedding distances may have different absolute score distributions than v2-denoised (training context differs). Per-version confidence threshold may be needed in `vault/settings.json` (`identify_threshold_v1`, `identify_threshold_v2`) — plan should empirically calibrate from real meetings.
- **Block identify entirely on mixed library?** Alternative simpler path: force "Migrate to denoised" before identify runs at all on mixed library. Plan-phase data (how often do users have mixed libraries for long periods?) decides between "mixed-mode complexity" vs "forced migration friction."

### 13.13 Chunk-parallelism trade-off + provider rate limits

§7.11 specifies hybrid sequencing (first chunk synchronous, rest parallel with concurrency 3 default). Open questions for plan-phase tuning:

- **Optimal concurrency level per provider.** Default 3 is a guess. Groq has aggressive rate limits (e.g. 7200 audio-seconds/min per workspace); concurrency too high → 429 errors → backoff defeats parallelism. Plan-phase should empirically measure per-provider sweet spot and store as `BaseProvider.recommended_chunk_concurrency` defaults; user can override.
- **Reorder buffer worst-case memory.** Pathological case: chunk 1 takes 60s (slow provider response), chunks 2-10 finish in 5s each — buffer holds 9 chunks' segments waiting. For a 3-hour podcast that's ~tens of thousands of segments. Plan should set buffer size limits + degrade-gracefully strategy (e.g. emit out-of-order with a warning rather than OOM).
- **First-chunk latency optimization.** Stage C's "sequential-first" rule trades throughput for UX. Alternative: skip sequential-first, parallel-all from chunk 0 with concurrency 1 effective if reorder buffer holds chunk 1. Edge case to measure: tiny meetings with 2-3 chunks where parallel-all is strictly faster.
- **Cross-chunk segment alignment.** If chunk boundary lands on a long pause (which is rare since VAD trimmed silences), no problem. If somehow lands mid-word (pathological non-VAD fallback), overlap dedup may misalign — should add a sanity-check on segment count + duration coverage vs expected. Plan-phase test fixture.

### 13.14 Non-diarizing provider speaker labeling (Groq / OpenAI Whisper)

§7.2 step 16 + §7.12 capability matrix + §14 jointly establish: cloud-only diarization, no client-side fallback. Providers that don't diarize (Groq, OpenAI Whisper) return segments without `provider_speaker_tag`. Consequences + items for plan:

- **Inconsistent speaker labels** when voice-identify runs per-segment (without provider grouping). Same speaker may match in some segments, miss in others — visible UI inconsistency. Mitigations:
  - Pre-specified participants (§7.2 step 16 candidate set) tighten thresholds enough to hide most inconsistency for known speakers.
  - For unknown speakers in non-diarizing providers: stay anonymous `null` (no Speaker_A/B/C — we can't cluster them).
- **UI guidance at provider-selection time** (Settings → Transcription + import dialog provider override): chip "Doesn't auto-label speakers — voice library matches required" next to Groq + OpenAI Whisper. Encourages cost-aware users to either build voice library OR accept the limitation.
- **Phase 2 reconsideration trigger**: if usage data shows significant Groq/OpenAI Whisper users with frequent unlabeled-speaker complaints, revisit client-side ECAPA-TDNN clustering (uses existing `ort` runtime + voice library backend infra — minimal new code, ~$0.001-0.005/audio-min). Decision is data-driven, not speculative.
- **Documentation contract**: launch marketing for Groq must explicitly say "no auto speaker labels". Hiding this leads to bad reviews; surfacing it as a feature trade-off ("$0.04/h cost — at the cost of speaker labels") is honest and respects the audience.

Not a v1.0 code blocker — architecture works as designed; trade-off is documented.

### 13.15 Offline queue policy + cap

§7.13 specifies offline queue mechanics; tuning parameters for plan:

- **Max queue size**: 100 actions? 1000? Unbounded with disk-based persistence is fine until disk fills. Plan-phase should set a soft warning threshold + hard cap with FIFO eviction (oldest non-running pending action drops). Recommendation: warn at 50, hard cap at 500.
- **Queue ordering policy**: currently FIFO by `queued_at`. Should `transcribe` actions take priority over `postprocess_pass` (transcription unblocks downstream)? Or strict FIFO honors user submission order? Plan should decide; possible default: action_type-priority weight + queued_at tiebreak (`transcribe=1, postprocess_pass=2, voice_enroll/identify=3, send_to_linear/glide=4, gdrive_backup=5`).
- **Queue persistence across vault switches**: queue lives in current vault's SQLite. Switching vault leaves queue behind in old vault — drains when user returns to that vault. Correct behavior? Or migrate to new vault? Plan-phase decision.
- **Resume granularity for chunked transcribes** (§7.11 stage E + §7.13 partial-upload edge): currently queue entry covers the whole transcribe job; on disconnect mid-chunk-3-of-5, we re-queue from chunk 3. Backend supports resume via `request_id` + chunk_idx but timeout for request_id reuse needs definition. Plan: server-side TTL 24h on `request_id`, client surfaces "Resume window expired — re-upload required" UI if exceeded.

Not a v1.0 code blocker — defaults work, plan tunes empirically from launch usage.

### 13.16 RAG chat tuning + embedding model lifecycle

§7.14 specifies the architecture; plan-phase tuning + decisions:

- **Top-k value (default 12)**: empirically calibrate on real meeting corpora. Too low → answers miss context; too high → LLM context bloat + cost + slower.
- **Conversation history length (default last 6 turns / ~2000 tokens)**: trade-off between conversational coherence and context-window pressure. Plan should add UI affordance "Clear context" for users who want to start fresh mid-session.
- **Query-rewriting LLM pass** (mentioned in §7.14.3): adds latency + cost per turn but improves recall on ambiguous follow-ups. Decision: ship without v1.0, add as Settings toggle if user feedback shows recall issues with "what about it" style follow-ups.
- **Embedding model lifecycle**: text-embedding-3-small chosen for v1.0. When OpenAI ships v4 or competitor improves significantly (Cohere, voyage, etc.), migration requires re-embedding entire vault. Migration UX: prompt user "Re-index N meetings with new embedding model? Estimated cost $X, time ~Y minutes" — explicit opt-in, no silent re-billing. Mixed-model search disallowed (different vector spaces).
- **Hybrid search (semantic + BM25/keyword)**: current design is pure semantic via embeddings. Adding BM25 re-ranking on top is established improvement for retrieval quality. Phase 2 if recall complaints.
- **Cross-meeting deduplication in retrieval**: same speaker says similar thing in 5 meetings → retrieval surfaces all 5 chunks → LLM context bloated. Plan-phase: should top-k include deduplication / diversity (MMR — maximal marginal relevance)? Adds complexity.
- **Chat history search**: user has 200 chat sessions, wants to find "that one chat about pricing" — search across chat session titles + messages. Phase 2.

Not v1.0 blockers — defaults work; plan tunes empirically post-launch.

### 13.17 Task backend field mapping + assignee resolution

§5.1 + §7.5 specify 6 native backends + Webhook. v1.0 strategy: ship simple — task title/description/due_date map directly; assignee sent as **string name only** (no lookup in destination's user directory). Plan-phase decisions:

- **Per-backend custom field mapping UI**. Each destination has different field schemas (Notion DB properties are user-defined, Jira custom fields, Bitrix24 task properties). v1.0 maps only standard fields; Phase 2 may add per-backend field-mapper UI ("Map our `due_date` → your `Deadline` property"). Plan: which destinations have most painful field gaps?
- **Assignee mapping**. Tasks have `assignee_speaker_id` (from voice library); destinations have their own user directories. v1.0 punts: sends `assignee_name` as string, user manually re-assigns in destination. Phase 2: per-backend user-mapping table (`voice_library_speakers.external_user_ids jsonb` keyed by backend → user id/email). Plan-phase: empirically check what % of users manually re-assign — if high, prioritize mapping UI.
- **Multi-destination sends**. User wants same task to Notion (for visibility) + Jira (for execution). v1.0: user clicks "Send to Notion" then "Send to Jira" separately. Phase 2: "Send to: [✓ Notion] [✓ Jira] [□ Webhook]" multi-select with one click.
- **Bidirectional sync** (mark complete in Jira → reflects in our `tasks.json`). NOT v1.0 — Phase 2 if demand emerges. Adds polling/webhook setup, conflict resolution, sync state schema.
- **Webhook payload schema versioning**. Once shipped, users build Zapier flows on a specific JSON shape. Breaking changes require versioned endpoints (`/tasks/send-to-webhook/v2`). Plan: declare v1 schema explicitly + commit to backward compat.

Not v1.0 blockers — current scope ships functional; tuning post-launch.

### 13.18 GDPR DSAR / right-to-erasure for distribution logs

§7.15 distributions log captures recipient email addresses + telegram chat_ids + timestamps in `distributions` SQLite + `distributions.log` JSONL. Under GDPR (EU/UK), a recipient who received a protocol via our app can theoretically file a Data Subject Access Request (DSAR) or right-to-erasure against the sender. Open questions:

- **Are we a controller, processor, or neither?** Most likely «processor on behalf of the user» — the user is the controller; we're the technical conduit. Plan should consult a privacy lawyer before launch marketing copy to nail this.
- **DSAR response mechanism for users.** If recipient contacts user demanding «what data have you sent about me?», user needs a one-click report: «Recent distributions to ivan@acme.com from this vault». Tauri command + dashboard view sufficient — already implied by `distribution_list_recent` (§8.3).
- **Right-to-erasure mechanics.** If recipient demands user delete distribution records: user has `rm` on the vault folder + `rm` on `distributions.log` + DELETE on SQLite row. UI affordance «Erase all distribution records for ivan@acme.com» (one-click, with confirmation) probably worth shipping.
- **Backend-side `usage_log` retention.** Even though backend has no recipient content, the existence of a `billable_unit=email_distribution` row with `units=5` proves user sent emails to 5 people on date X. Aggregate but not zero. Plan should set retention policy (e.g. 13 months for billing compliance, then anonymize/delete) + document in Privacy Policy.

Phase 2 if usage data shows recipient complaints emerge. Current scope: ship with the affordances above + Privacy Policy disclosure. Full DSAR automation (cross-vault discovery, automated takedown) out of scope.

### 13.19 Per-user distribution rate limits

To prevent abuse (user spam-sending protocols to mailing lists, or compromise of user account leading to spam), backend enforces per-user rate limits on `/distribute/*` endpoints. Plan-phase tuning needed:

- **Email rate limit defaults.** SES has its own rate limits per account (e.g. 14 emails/second initial, 50/day starter). Within our per-user budget, what's reasonable? Recommendation: free tier 5 recipients/hour, Pro 50/hour, Business 500/hour. Hard ceiling configurable via Settings → Channels → Email.
- **Telegram rate limit defaults.** Telegram Bot API allows 30 messages/sec total per bot — since we use a shared bot for all users, we must internally rate-limit per-user. Recommendation: free 10/hour, Pro 100/hour, Business 500/hour. Shared bot's global limit (30/sec) becomes ceiling for concurrent burst.
- **«Hot send» abuse pattern.** User sends 50 protocols in 5 minutes — legitimate weekly digest behavior for a high-meeting role, OR abuse via compromised account. Plan-phase: shape detection rules + soft-block threshold (notify user «unusual activity — confirm via 2FA?») + hard-block at obvious threshold (e.g. 1000/hour = clearly abuse).
- **Per-tier quotas + overage billing?** Should distribution be billable per-recipient (like STT-minute), or unlimited within tier? Recommendation: unlimited within tier rate limits, billed as part of base subscription. Overage billing adds complexity not justified at v1.0 volumes.

Not v1.0 code blocker — ship with conservative defaults (free 5/hour, Pro 50/hour, Business 500/hour) + Settings tunability for Business tier. Adjust based on real abuse signals.

### 13.20 Telegram bot abuse + impersonation

Shared `@AudioTranscriberBot` is a single bot all users send through. Open risks:

- **Recipient receives spam protocols** from a malicious user who somehow got their `chat_id`. Mitigation: Telegram's design requires recipient to `/start` the bot **first** before bot can send to them — so attacker needs recipient's cooperation to be added. Robust against random spam, vulnerable to social-engineering (attacker convinces recipient to /start by misrepresenting purpose).
- **Impersonation via display name.** Sender's display name in the message body is user-controlled. Plan: standardize the «Привет, это [Имя] из audio-transcriber» prefix on every message so recipient can verify sender identity by cross-reference (the prefix is from our infrastructure; the embedded display name is user-supplied).
- **Bot reputation poisoning.** A bad actor user spams via our bot → Telegram restricts the bot account → all users affected. Mitigation: rate limits (§13.19), abuse-report flow («Report this user» → backend can ban specific user's distribution access while bot stays healthy), user-side bot deployment for Phase 2.
- **Phase 2 mitigation: user's-own-bot.** Settings → Channels → Telegram → «Use your own bot». User creates bot via @BotFather, provides token → backend forwards through their bot. Limits blast radius if any one user goes rogue. Deferred from v1.0 because UX complexity (BotFather flow is intimidating for non-tech users — clashes with «zero setup» goal of shared bot).

Document the trust model clearly in Privacy Policy + Settings → Channels → Telegram help text.

### 13.21 README template internationalization

§7.16 README template is hardcoded with Russian labels («Проект», «Дата», «Длительность», «Кратко», «Ссылки», etc.). For EN/KK locales (§9), the template body labels need to vary by user's `ui_locale`. Open questions:

- **Storage:** locale-specific templates bundled as 3 files (`README_template.{ru,en,kk}.md`) selected at render time? OR i18next-style `{{t:project_label}}` placeholders resolved via the locale bundle?
- **Vault portability across user locales.** If user A (RU locale) shares vault with user B (EN locale): does README regenerate when user B opens the meeting? Recommendation YES — README is render, not authoritative content. Renders in viewer's locale automatically.
- **Mixed-locale vaults** (user works in RU + has English colleague reading via Cowork agent in EN context). Acceptable to have RU README that's regenerated to EN when Cowork-host's locale hint flips? Phase 2 — for v1.0, render in user's primary locale only.

Plan-phase: pick one storage approach + ship for v1.0; mixed-locale considerations for Phase 2.

### 13.22 Distribution analytics + delivery tracking

v1.0 distribution log captures send-side outcomes (`status: sent/failed/bounced`). Doesn't capture:

- **Email open rates** (requires tracking pixel — privacy-hostile, NOT shipping in v1.0).
- **Link clicks within protocol** (requires URL rewriting through our backend with redirect — same privacy concerns).
- **Telegram message read receipts** (Telegram API has `getUserProfilePhotos` for last-seen but no per-message read indicator — not exposed by API).
- **Delivery confirmations from email recipient's server** (requires DSN handling, MX records — complex; SES partially supports).

Phase 2 if user feedback explicitly requests with-consent tracking. Default privacy posture is «we know we sent it, we don't know what happens after». Clearly stated in Privacy Policy.

## 14. Out of scope (Phase 2+)

- Web-app version (browser-only, no desktop). Incompatible with local-first data model — would require server-side persistence.
- Microsoft Store distribution.
- macOS / Linux builds.
- BYOK option (in addition to managed) — currently developer audience prefers managed.
- Team / org accounts (only single-user accounts in v1.0). Multi-user would force server-side transcript sharing, breaking §3.4.
- Real-time transcription streaming (websocket while user speaks). v1.0 is file-based + post-record.
- API-only access without desktop (REST API for direct usage by users' automations). HTTPS surface for v1.0 is billing/quota only — direct API users can't access their own transcripts because we don't have them.
- Self-hosted backend option.
- **App-server-mediated cross-device sync** (i.e. we run the sync, with cloud storage we operate). Pure local on v1.0 (§13.6). Phase 2 may add opt-in E2E-encrypted sync if demand emerges. **NOTE: cross-device usage via the user's own cloud sync (Dropbox, GDrive, iCloud, git, Syncthing, etc.) is supported out of the box — the vault is just a folder; put it in any synced folder and it works. This is not a separate feature, just a side effect of the vault model (§3.5). Caveats: simultaneous edits from two devices may produce conflict files (same behavior as Obsidian); document recommended practice = one active device at a time.**
- **Cloud backup via our backend storage.** Backend has no user-data storage. **IN scope for v1.0: Google Drive backup as a user-driven feature (§7.7) — OAuth via backend, upload client-direct to Drive so backend never sees backup contents.** Lifted from Python Phase 7.1 (PR #47).
- **Notion as backup target or meeting publisher.** Notion **IS** a v1.0 task backend (§5.1 + §7.5) for sending extracted action items. **Out of scope:** using Notion as full-meeting publish target (mirror entire transcripts as Notion pages) — vendor lock-in + fragile export keep us favoring vault + Webhook export instead. Re-evaluate post-launch if demand emerges.
- **Native integrations for ClickUp, Asana, Monday.com, Trello, Todoist, MeisterTask, Microsoft Project, Microsoft Planner.** All covered by the **generic Webhook backend** (§5.1) via user-side Zapier / Make / n8n / Pipedream automation in v1.0 — covers 200+ tools without per-tool engineering on our side. Native versions added in Phase 2 prioritized by usage analytics (highest webhook destinations get promoted to native). Community plugin architecture for backend extensions (sandboxed) also Phase 2.
- **Bundled Cowork-skill package + co-marketing (Phase 2 partnership track).** Base MCP-host integration (§6.4) + Project-folder mapping + Settings panel with "Open in Cowork" / "Copy MCP config" / Instructions seeding (§7.10 + §8.3) are **all in scope for v1.0** — they're the integration surface itself, not optimization. What stays Phase 2: a bundled Cowork-skill package with hand-tuned prompts + tool-composition recipes for our exact tool surface; deeper testing against Cowork's evolving orchestration patterns beyond release-smoke; any official partnership artifacts (directory listings, co-marketing, beta-channel coordination — see §13.11). Pursue if launch signal shows meaningful Cowork-user adoption.
- **Backup targets other than Google Drive** (Dropbox/iCloud/S3 as first-class app-managed backup). Users who want those can rely on syncing the vault folder via their existing cloud-sync clients — no app integration needed (same reasoning as the cross-device-sync bullet above).
- **Advanced audio preprocessing beyond the baseline pipeline** (§7.2 step 4 covers highpass + denoise + loudnorm + VAD). Phase-2 candidates: per-segment AGC (dynamic per-speaker volume balancing — different from global loudnorm; can introduce artifacts but useful for variable-volume meetings); dereverberation (echo cancellation for big conference rooms); DeepFilterNet upgrade from RNNoise (newer transformer-based denoiser, ~10 MB model vs 80 KB — drop-in swap via same `ort` runtime if quality demand justifies the installer-size cost).
- **Client-side diarization (local pyannote / ECAPA-TDNN clustering).** Locked-out by the "diarization = cloud-only" decision (§2 + §7.12). Trade-off: Groq/OpenAI Whisper users get no auto speaker labels (§13.14). Phase-2 reconsideration triggered by usage data (significant non-diarizing-provider adoption + speaker-labeling complaints). Implementation path if revisited: pyannote-segmentation + pyannote-embedding ONNX models (~6 MB + ~17 MB) bundled via `ort` runtime (already loaded for Silero VAD), OR lightweight VAD-region-based ECAPA clustering via existing `/voice/embed` infrastructure (~$0.001-0.005/audio-min cost, no installer growth).
- **Server-side transcript search.** Backend has no transcripts. Search is local-only, using SQLite FTS5 over local SQLite. Cross-device search would require sync (out of scope).
- **Protocol distribution via WhatsApp.** **Dropped from v1.0 + not planned for Phase 2 unless audience signals strong demand.** Reasoning: (a) Meta Cloud API requires Business Account verification (~1-2 weeks); (b) per-conversation billing model ($0.005-0.10) clashes with our flat tier model; (c) template-message approval flow blocks ad-hoc protocol content; (d) tech-insider + Cowork target audience has near-zero WhatsApp usage for work coordination. Email + Telegram cover ~95% of our audience's distribution needs (§7.15).
- **Protocol distribution via Slack.** **Phase 2.** Adds OAuth setup + Slack Blocks API formatting + per-workspace install model. Useful for startup/dev-team audience but not blocking v1.0 — these users can `git`-commit the protocol.md OR pipe via Webhook for now.
- **Protocol distribution via Microsoft Teams.** **Phase 2.** Adds Azure AD app registration + Graph API + per-tenant install. Enterprise audience is not the primary v1.0 target; Teams support unlocks them in Phase 2.
- **Auto-send after protocol pass completes** (no preview/draft step). **Phase 2** advanced Settings toggle for trusted meeting types where user accepts hallucination risk. v1.0 always uses draft→preview→send to mitigate LLM-confidence risk (§7.15).
- **User's-own Telegram bot.** **Phase 2** customization. Some teams want a branded bot (`@AcmeProtocolBot`) instead of our shared `@AudioTranscriberBot`. Adds BotFather onboarding flow + per-user bot-token storage in keychain + backend per-user bot routing. v1.0 ships shared bot only (zero setup) — sufficient for tech-insider audience that prioritizes UX over branding.
- **Custom HTML email templates** (branded headers, signatures, color schemes). v1.0 sends protocol body as markdown rendered to plain HTML via `markdown-it`. Phase 2: Settings → Channels → Email → «Email template» with logo upload + signature + accent color. For users who want their distributions to look polished.
- **Email open-rate / link-click tracking** (§13.22). Requires tracking pixels + URL rewriting through our backend — privacy-hostile, breaks §3.4 in-transit-only model. NOT a v1.0 feature; Phase 2 only if user feedback explicitly requests + with-consent opt-in design.
- **Distribution to non-participant recipients via mailing-list / Notion-page-publish.** v1.0 distribution is participant-only (recipients are linked to voice library speakers + ad-hoc additions). Bulk-publish-to-mailing-list = different UX + different abuse model; defer.
- **Bidirectional distribution sync** (recipient replies in email/Telegram → captured in vault as `replies.jsonl`). Requires IMAP polling + Telegram update-webhook + parser robustness. Phase 2 if user feedback shows demand.
- **Notion as full-meeting publish target** — already deferred (§14 above) but worth re-flagging here since «publishing» is adjacent to «distribution».
- **Manual `notes.md` editor as first-class app feature.** v1.0 ships file-watch + reconciler-aware (user creates/edits `notes.md` externally in Obsidian, app picks up changes via reconcile). Phase 2 may add in-app rich editor for `notes.md`, auto-create button in meeting view, mention-autocomplete (`@Иван`, `[[Project Alpha]]`).
- **Meeting README in-app editor.** v1.0 README is read-only auto-generated. NOT a Phase 2 candidate either — design intent is «README is render, not source-of-truth». User edits go to `notes.md`.
- **User-editable README template** (§7.16.3). Bundled template only in v1.0. Phase 2 if power users demand customization — add `<vault>/.audio-transcriber/readme_template.md` override hook with same locale-resolution semantics.
- **Cross-device distribution sync** (recipient lists, opt-out flags, draft state). Out of scope because vault sync via user's own cloud-sync handles 95% of this — the same files contain the data. Per-device draft state intentional friction (each device has own in-flight drafts).

## 15. Code reuse strategy

The following Python code from `audio-transcriber` lifts into `apps/api/` with minimal change. Note: lifted modules become stateless (no DB writes for user content); their server-side persistence layer is replaced by SSE streaming back to the client.

| Existing module | Destination | Notes |
|---|---|---|
| `providers/*.py` (7 providers + base) | `apps/api/audio_transcriber_api/providers/` | Lift as-is. Imports `pydantic`. |
| `transcriber/cloud_chunker.py` | `apps/api/audio_transcriber_api/chunker.py` | Lift with full 5-stage pipeline (§7.11): Stage A opus compression (Phase 6.5 PR-A.1), Stage B per-provider chunk-boundary discovery reusing `silence_intervals` from `meeting.toml`, Stage C bounded-parallel transcribe with sequential-first hybrid, Stage D timestamp double-mapping + SSE reorder buffer, Stage E chunk-level retry with optional secondary-provider fallback. Provider capability matrix (`max_upload_bytes`, `accepts_opus`) declared on `BaseProvider` ABC — already exists in Python from Phase 6.5 PR-C. The 18 existing `tests/test_cloud_chunker.py` cases lift; +new tests for opus stage, parallel reorder buffer, chunk-level retry isolation. |
| `audio_io.py` (ffmpeg wrap, normalize + 16kHz mono) | `apps/api/audio_transcriber_api/audio_io.py` (partial) **+ Tauri Rust audio preprocessing** | Backend lift: `ensure_16khz_mono` only (becomes streaming-aware — receives 16k mono WAV from client, no further transform). **All other audio preprocessing moves to Tauri Rust** as part of the §7.2 step 4 pipeline: highpass 80Hz (ffmpeg via Rust), RNNoise denoise (ort + bundled ~80 KB `sh.rnnn` model — was download-on-first-use in Python, now bundled in installer for "no external network call" narrative consistency), loudnorm EBU R128 -16 LUFS (ffmpeg). **`_get_rnnoise_model_path` download logic dropped** — model bundled. **`audio_cutter` helpers stay client-side as Tauri Rust ffmpeg invocations**. The **silence-removal logic does NOT lift here** — it's replaced by a Silero VAD implementation in Tauri Rust (see `silence_remover.py` row below) and is the final stage of the §7.2 step 4 pipeline. |
| `tasks/extractor.py` + `openrouter_client.py` | `apps/api/audio_transcriber_api/postprocess/` | Lift extractor as the `tasks` pass implementation + OpenRouter wrapper. **Expanded into an 8-pass family** (§7.9): existing prompt + schema generalize, sibling files `summary.py`, `protocol.py`, `decisions.py`, `topics.py`, `open_questions.py`, `insights.py`, **`agenda.py`** added. The `summary.py` additionally extracts `next_meeting: {date, topic, confidence}` as a structured side-output (system prompt instructs LLM to return both narrative + JSON next_meeting block; backend parses + returns both fields to client). Shared base for prompt assembly (project_description prefix, participants map, retry/backoff). **No `persistence.py` lift** — backend doesn't persist outputs. |
| `tasks/backends/` (Protocol-based dispatch — Linear, Glide) | `apps/api/audio_transcriber_api/tasks/backends/` | Lift the existing Protocol ABC + Linear + Glide implementations. **Extended in v1.0** with 5 new backend implementations: `notion.py`, `jira.py` (Cloud + Self-hosted variants), `yandex_tracker.py`, `bitrix24.py`, `github.py` (Projects v2 GraphQL — DraftIssue + Issue modes), plus `webhook.py` (signed-payload generic dispatcher). Each backend module declares: `name`, `auth_type` (oauth/pat/none), `oauth_scope`, `oauth_url_template`, `task_field_mapping` dict, `send(tasks, config, access_token) -> [external_id]` async method. Backend never stores task payloads or tokens. |
| `voice_library.py` + `enrollment_worker.py` | `apps/api/audio_transcriber_api/voice_library/` | Lift the ECAPA-TDNN inference path. **No pgvector storage** — vectors are returned to client and persist in client `sqlite-vec`. The local multiprocessing wrapping (which existed to avoid blocking the CTk UI) is replaced by FastAPI's async runtime. **v1.0 trains on denoised audio**: Tauri Rust applies RNNoise client-side before sending to `/voice/enroll`, so embeddings have `embedding_version="v2-denoised"` (§4.3). Legacy Python embeddings imported as `v1-raw` (§13.12 migration). Identify pass uses matching-version preprocessing per segment (§7.2 step 16). |
| `transcriber/speaker_aligner.py` | `apps/api/audio_transcriber_api/speaker_aligner.py` | Lift. Role in new architecture (§7.12): normalizes provider-specific word-level + diarization-tag formats into our segment schema. Walks the provider's output timeline, groups words by speaker transition (when diarization tags present at word-level granularity, e.g. AssemblyAI), or just normalizes word arrays (when no diarization tags). Output emitted as SSE segment events with normalized `{idx, start, end, text, language?, confidence?, provider_speaker_tag?, words?[]}` shape regardless of source provider quirks. |
| `transcriber/prompt.py` | `apps/api/audio_transcriber_api/prompt.py` | Lift; trilingual prompt for KZ/RU/EN mixed mode. |
| `transcript_format.py` | **client-side** in TS / Tauri Rust | Re-implemented in TS / Tauri Rust. Renders meeting segments + speaker map → `<meeting>/transcript.md` on every save / speaker change. Also handles user-initiated export-to-srt / export-to-txt. No backend role. |
| `tasks/errors.py` `humanize()` | merged into `error_code` registry | Adjusted to return codes, not Russian strings. |

New components written from scratch (no Python predecessor):

| Component | Location | Notes |
|---|---|---|
| Vault file-format read/write (toml, jsonl, md) | `apps/desktop/src-tauri/src/vault/` | Rust. Owns `meeting.toml`, `.cache/segments.jsonl`, `transcript.md` rendering, 3-bucket layout (root / `.cache/` / `analysis/`) creation + sanitization rules from §3.5. |
| Vault reconciler | `apps/desktop/src-tauri/src/reconcile/` | Rust. Implements §4.3 + §7.6 reconcile policy, file ↔ SQLite diffing, conflict surfacing. |
| Project / meeting CRUD commands | `apps/desktop/src-tauri/src/commands/vault.rs` | Rust. All the `project_*` / `meeting_*` Tauri commands from §8.3. |
| Local SQLite migrations | `apps/desktop/src-tauri/migrations/` | `rusqlite_migration` SQL files, versioned. |
| Vault-layout migrations | `apps/desktop/src-tauri/src/vault/migrations.rs` | Rust functions per version transition (§13.9). |
| FTS5 search query API | `apps/desktop/src-tauri/src/search.rs` | Rust. Wraps SQLite FTS5 with snippet highlighting. |
| RAG indexing + chat orchestrator | `apps/desktop/src-tauri/src/rag/` + `apps/api/audio_transcriber_api/rag.py` | Rust client: chunking (topic-based with sliding-window fallback), batched embed calls, sqlite-vec persistence, top-k retrieval with scope filtering, SSE consumption with citation parsing. Python backend: stateless `/embed` + `/chat` proxies with citation-enforcing system prompts. New from scratch — no Python predecessor. |
| Meeting README renderer + lazy-regen | `apps/desktop/src-tauri/src/readme/` | Rust. Implements §7.16: hardcoded template (`README_template.{ru,en,kk}.md` bundled), Tera-style placeholder substitution from SQLite + `analysis/summary.md` excerpt, atomic file write, `meetings.readme_dirty` flag handling, user-edit detection + backup-and-restore (`.user-edited-<ts>.bak`), `meeting_view_opened` event handler that gates regen on flag state. |
| Frontmatter parser + reconciler integration | `apps/desktop/src-tauri/src/frontmatter/` | Rust. YAML frontmatter parser (`serde_yaml`), per-file-type schema validation (§3.5.1), reconciler diff against SQLite ground-truth, malformed-frontmatter recovery (restore from SQLite + body content preservation), schema-version migration runner (`v<N>.rs` per migration step), unknown-field preservation (forward-compat). |
| Wiki-link resolver + cascading rename | `apps/desktop/src-tauri/src/wiki_links/` | Rust. Parses `[[ref]]` + `[[ref\|display]]` syntax respecting markdown code fences. Resolves to canonical paths via SQLite lookup (display_name → People, folder_name → projects/meetings, with disambiguator handling). Cascading rename: on speaker/project rename, walks vault `.md` body content for matches and updates references. Lazy + cached via `last_name_change_at` checkpoint. |
| Protocol distribution orchestrator | `apps/desktop/src-tauri/src/distribution/` | Rust. Implements §7.15 send flow: default-draft assembly (subject template + protocol-body extraction + recipient resolution from `meeting_participants` + opt-out filtering), `distribution_drafts` SQLite CRUD, channel-split routing (email + telegram parallel requests), per-recipient result aggregation, `distributions` SQLite row writes + JSONL log append, retry-failed-only draft creation. Markdown editor backend (auto-save coalescing). |
| Distribution backend endpoints | `apps/api/audio_transcriber_api/distribute/` | Python. `email.py` (SES integration via `boto3`, Gmail Send API via `google-auth` + `httpx`, Microsoft Graph API for Outlook; per-user-key envelope encryption for `oauth_tokens.refresh_token_encrypted` via AWS KMS), `telegram.py` (shared bot token from Railway secret, `python-telegram-bot` lib for Bot API calls, MarkdownV2 escaping, multi-message splitter for >4096 char content), `rate_limiter.py` (per-user per-channel token bucket — §13.19), `oauth_email.py` (Gmail/Outlook OAuth dance + encrypted refresh-token persistence in `oauth_tokens` table). New from scratch — no Python predecessor (gdrive lift in §7.7 is different shape). |
| Agenda pass + next-meeting extractor | `apps/api/audio_transcriber_api/postprocess/agenda.py` + extensions in `summary.py` | Python. `agenda.py` — Phase A pass, narrower input window (first 5-10 min of transcript), structured-output prompt asking LLM for bullet list + fallback line. Extension in `summary.py` — appends to system prompt: «If transcript mentions a next meeting (date + topic), return it as JSON object alongside the summary markdown.» Output parsing returns `{markdown_body, next_meeting: {date, topic, confidence}}`. |

Dropped (module-level) — feature status noted in parens:

- `app.py` (entry + faulthandler) — replaced by Tauri.
- `diarize_worker.py` — replaced by cloud diarization (provider feature).
- `transcriber/__init__.py` core, `transcriber/cuda_utils.py`, `transcriber/progress.py`, `transcriber/segmenter.py` — local-CUDA orchestration dropped; per-segment language detection done by cloud providers.
- All `ui/` (CTk UI) — replaced by React.
- `recorder.py` — replaced by Tauri `cpal` mic recording.
- `silence_remover.py` (Python module dropped) — **silence-removal feature kept AND promoted** from user-driven editor tool to auto-applied pre-STT pass (§7.2 step 4). Implemented client-side in Tauri Rust using **Silero VAD via the `ort` (ONNX Runtime) crate**, not ffmpeg's `silenceremove` — Silero is neural and robust to quiet speakers; ffmpeg's signal-threshold filter would clip them. The Python module's tested VAD parameters (threshold 0.5, min_speech 250 ms, min_silence 500 ms, pad 200 ms) port verbatim. Silero ONNX model (~1.7 MB, MIT) bundled in the installer — no first-use download, no external network call, consistent with the "we don't talk to anything but our backend" narrative. Test corpus from `tests/test_silence_remover.py` lifts to cargo tests over the same expected intervals (§10).
- `audio_cutter.py` (Python module dropped) — **audio editor feature kept**, replaced by React UI using WaveSurfer.js + local Tauri `ffmpeg` invocation for actual cuts. Local-only operation.
- `gdrive/auth.py` — **logic kept, location changed.** OAuth refresh-loop semantics ported to Tauri Rust (`gdrive_oauth_*` helpers); the brokered exchange goes through backend (`/api/v1/oauth/google/*`). Token storage moves from `~/.audio-transcriber/gdrive-token.json` (file on disk) to OS keychain (consistent with Linear/Glide). The legacy file location is read once on first launch for migration and then deleted.
- `gdrive/client.py` — **dropped.** The `googleapiclient`-based Python wrapper is replaced by Rust calls to `googleapis.com` via `reqwest` (direct upload, no SDK needed for the small surface we use: `files.create`, `files.list`, `files.get`, `about.get`). Eliminates a Python dep on the client.
- `gdrive/backup.py` — **lifted as design pattern, reimplemented in Tauri Rust** (§7.7). The orchestration shape (compose `redact_config` + `zip_history` + `build_manifest` + upload) ports directly: redaction no longer needed (vault contains no API keys), zip walks the vault (excluding audio + migration-backups), manifest is SHA-256 + size per file. The vault model makes the new implementation cleaner than the Python original.

`enrollment_worker.py` is partially lifted: the speechbrain ECAPA-TDNN inference moves to `apps/api/audio_transcriber_api/voice_library/`, but the local multiprocessing wrapping (which existed to avoid blocking the CTk UI thread) is replaced by FastAPI's async runtime. **The persistence side moves to the client** — `voice_library_speakers` is a local SQLite table with `sqlite-vec`, populated by client code after the backend returns each embedding.

Rough estimate: ~2700 LOC of Python business logic is reused (slightly less than the brainstorm draft, because persistence/CRUD layers don't lift). ~5500 LOC of new TS / Rust / Python is written (slightly more — local SQLite layer + Tauri commands + `sqlite-vec` integration on the client side).

## 16. References

- Brainstorm session: `.superpowers/brainstorm/9343-1779808576/content/` (5 HTML mockups).
- Existing audio-transcriber design: `docs/superpowers/specs/2026-04-02-audio-transcriber-design.md`.
- Existing CLAUDE.md (invariants relevant to dropped local-CUDA pipeline).
- Tauri 2 docs: <https://v2.tauri.app/>.
- MCP Python SDK: `mcp.server.fastmcp.FastMCP` — official Python SDK supports both stdio and `streamable_http_app()` transports.
- Supabase RLS + JWT: <https://supabase.com/docs/guides/database/postgres/row-level-security>.
- PyOxidizer Windows status: <https://pyoxidizer.readthedocs.io/en/stable/>.
- Azure Trusted Signing for Tauri: <https://v2.tauri.app/distribute/sign/windows/>.
