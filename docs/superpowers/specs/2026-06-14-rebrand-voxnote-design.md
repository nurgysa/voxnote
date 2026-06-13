# Rebrand: audio-transcriber → VoxNote (design)

Date: 2026-06-14
Status: approved (brainstorming), pending implementation plan

## Goal

Rename the project from "audio-transcriber" / "Audio Transcriber" /
"AudioTranscriber" to **VoxNote** across all *live* surfaces: user-facing
strings, code identifiers, the on-disk secret-store directory, build
artifacts, live docs, and the GitHub repo reference. Existing client
installs must NOT lose their API keys, tokens, or settings.

## Decisions (locked during brainstorming)

1. **Secret-store dir is renamed** `~/.audio-transcriber/` → `~/.voxnote/`,
   with a one-time **move**-based migration shim so deployed clients keep
   their `config.json` (keys), `gdrive-token.json`, `directory.json`,
   `queue.json`, and model cache.
2. **Historical docs are frozen.** Everything under
   `docs/superpowers/specs/`, `docs/superpowers/plans/`, and
   `docs/superpowers/handoffs/` is a dated chronicle and is left untouched
   (except this new spec). Only *live* docs are updated.
3. **GitHub repo** `nurgysa/audio-transcriber` → `nurgysa/voxnote`:
   in-repo references are updated now; the actual `gh repo rename` + local
   `git remote set-url` is a final, separately-confirmed step.

## Casing-aware replacement map

The rename is case-sensitive — a single find/replace is wrong. Apply per form:

| From | To | Surface |
|---|---|---|
| `audio-transcriber` | `voxnote` | kebab: paths, repo refs |
| `Audio Transcriber` | `VoxNote` | window title, OpenRouter `X-Title`, docs |
| `AudioTranscriber` | `VoxNote` | `.exe` name, `dist/` dir, zip artifact |
| `audio_transcriber` | `voxnote` | snake: the `.spec` filename |
| `.audio-transcriber` (home dir) | `.voxnote` | secret store |
| `nurgysa/audio-transcriber` | `nurgysa/voxnote` | git remote + URL refs |

Drive-by fix: `tasks/openrouter_client.py` `HTTP-Referer` is currently the
malformed `https://github.com/audio-transcriber` (no owner) → set to
`https://github.com/nurgysa/voxnote`. `X-Title` → `VoxNote`.

## Migration shim (the only new logic)

New idempotent helper in `utils.py`:

```
migrate_legacy_secret_dir() -> None
    home = Path.home()
    new = home / ".voxnote"
    old = home / ".audio-transcriber"
    if not new.exists() and old.exists():
        shutil.move(str(old), str(new))   # ACL + contents travel with it
```

**Ordering invariant (load-bearing):** the shim MUST run before any code
reads config or tokens, otherwise the renamed code reads an empty
`~/.voxnote` and may overwrite it with defaults, silently orphaning live
keys. Call sites:

- `app.py` — once, immediately after the faulthandler bootstrap block,
  before importing/constructing anything that touches config.
- `cli/core.py` — once at CLI entry, before path resolution.

Properties: idempotent (no-op once `~/.voxnote` exists), best-effort
(swallow `OSError` with a logged warning — a failed move must not crash
launch; the app then starts fresh rather than dying). Dev mode keeps its
repo-root `config.json` for app config, but tokens/`directory.json`/
`queue.json`/model cache still live under the home dir, so the shim runs in
both dev and frozen.

Covered by a new unit test: (a) old exists / new absent → moved; (b) both
exist → no-op; (c) neither → no-op; (d) move raises `OSError` → swallowed.

## Scope — files touched

**Code (~22 modules):** `utils.py`, `audio_io.py`, `cli/{_paths,core,app,__init__,mcp_server}.py`,
`gdrive/{auth,client,backup}.py`, `directory/store.py`, `processing/store.py`,
`recorder.py`, `runtime_hook_imports.py`, `tasks/openrouter_client.py`,
`integrations/hermes/{schema,client}.py`, `ui/app/{__init__,builder}.py`,
`ui/dialogs/{directory,settings,settings_builder}.py`.

**Build/packaging:** `git mv audio_transcriber.spec voxnote.spec` (+ `name=`
fields), `scripts/build_exe.ps1` (spec ref + output names), `scripts/package_release.py`
(artifact names + secret-path guard), `scripts/gen_icon.py`, `scripts/smoke_dedup_live.py`.

**Hermes skill:** `git mv integrations/hermes/skills/audio-transcriber/ .../voxnote/`
+ `SKILL.md` content.

**Live docs:** `README.md`, `CLAUDE.md`, `AGENTS.md`,
`docs/{ARCHITECTURE,CLIENT_SETUP,PROTOCOL_TEMPLATE}.md`,
`THIRD_PARTY_LICENSES.md`, `.github/ISSUE_TEMPLATE/bug_report.md`,
`.github/SECURITY.md`.

**Tests:** every test that hardcodes the path/name —
`test_cli_paths`, `test_audio_io`, `test_secret_dir_acl`,
`test_utils_config_path`, `test_gdrive_auth`, `test_config_example_no_ghost_keys`,
`test_package_release`, `test_recorder_output_dir`, `test_ffmpeg_path_resolution`,
`test_hermes_*` — plus a new `test_secret_dir_migration` (or co-located).

**Explicitly NOT touched:** `docs/superpowers/specs|plans|handoffs/*`
(historical archive), `vendor/*` (third-party license text verbatim).

## Verification

1. `py -3 -m pytest` green (baseline ≈ 939, +1 migration test).
2. `py -3 -m ruff check .` clean.
3. Final grep sweep: zero matches of `audio-transcriber|Audio Transcriber|AudioTranscriber|audio_transcriber`
   outside `docs/superpowers/` and `vendor/`.

## Out of scope (follow-ups)

- Renaming the local checkout dir `Documents\audio-transcriber` (risky
  mid-session; cosmetic).
- Redeploying `C:\Apps\AudioTranscriber` (rebuild artifact; next build).
- `gh repo rename voxnote` + `git remote set-url` — final confirmed step.
- Rebuilding the `.exe` / repackaging the release zip under the new name.

## Risks

- **Client key loss** if the migration shim is wrong or runs too late —
  mitigated by the ordering invariant + the four-case unit test + `move`
  preserving ACLs.
- **Casing slips** from a naive replace — mitigated by the per-form map and
  the final grep sweep gate.
- **GitHub link rot** — mitigated by GitHub's automatic old-URL redirect
  after `repo rename`; release-asset URLs continue to resolve.
