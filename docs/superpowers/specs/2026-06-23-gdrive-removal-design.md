# Remove the `gdrive/` Google Drive API from VoxNote — Design

**Date:** 2026-06-23
**Status:** Approved (brainstorming) — ready for implementation plan
**Topic:** Rip out the API-based Google Drive backup subsystem (`gdrive/` package +
its Google dependencies, UI, config keys, docs, tests). Keep the transcription
queue's `sources/`/`inbox/` Drive-Desktop *filesystem* convention and the
`redact_config` helper.

## 1. Why

VoxNote is the audio-input surface of the user's Hermes-native Mini-AGI (see the
`voxnote-in-mini-agi` memory). Backup / restore of config + history has been moved
to the **Hermes Desktop** layer, which owns cloud storage in the Mini-AGI division
of labor. VoxNote should not duplicate it. The Drive backup feature (Phase 7.0 auth
+ 7.1 backup; 7.2–7.4 never started) is now dead weight: ~750 LOC + 3 heavy Google
dependencies + a PyInstaller discovery-cache dance. This is a clean rip-out in the
spirit of the 2026-05-28 cloud-only removal.

## 2. Scope boundary (confirmed)

There are **two distinct "Google Drive" things** in VoxNote:

1. **The `gdrive/` API package** — OAuth (`gdrive/auth.py`), a `googleapiclient`
   wrapper (`gdrive/client.py`), and the backup orchestrator (`gdrive/backup.py`).
   This is what moves to Hermes. **→ REMOVE.**
2. **The queue's `sources/`/`inbox/` folders** — plain filesystem paths that Google
   Drive *Desktop* syncs. `processing/sources.py` is `shutil`/`os` only; its
   docstring states *"A plain filesystem write — Google Drive Desktop syncs it; no
   gdrive API."* Zero dependency on the `gdrive/` package. **→ KEEP** (core queue
   plumbing, shipped in #153–#162).

Verified: the only production imports of `gdrive` are `support_bundle.py` (for
`redact_config`), `ui/app/builder.py` (`GDriveAuth`), and `ui/dialogs/settings.py`
(`run_backup`). The Google libraries (`google-auth`, `google-auth-oauthlib`,
`google-api-python-client`) are imported in production **only** under `gdrive/`.

## 3. Removal inventory

### 3.1 Delete outright
- The `gdrive/` package: `__init__.py`, `auth.py`, `client.py`, `backup.py`.
- Five test files: `tests/test_gdrive_auth.py`, `tests/test_gdrive_client.py`,
  `tests/test_gdrive_backup.py`, `tests/test_gdrive_backup_cleanup.py`,
  `tests/test_settings_gdrive.py`.
- Three dependencies from `requirements.txt`: `google-auth==2.46.0`,
  `google-auth-oauthlib==1.3.0`, `google-api-python-client==2.196.0`.

### 3.2 Relocate `redact_config` (the one real refactor)
`support_bundle.py` (the live «Сохранить лог для отправки» feature) imports
`redact_config` from `gdrive.backup`. The function is self-contained — a deep copy
plus a deny-by-default secret-name heuristic, with no Drive coupling. Move it,
together with its module constants and helper, into `support_bundle.py` (its sole
surviving consumer):

- `REDACTION_PLACEHOLDER`, `REDACTED_KEYS`, `_SECRET_NAME_HINTS`, `_looks_like_secret`,
  `redact_config`.

Update `support_bundle.py`'s `from gdrive.backup import redact_config` to use the
local definition. Re-home the direct `redact_config` unit tests from
`tests/test_gdrive_backup.py` into `tests/test_support_bundle.py` so secret-redaction
coverage is preserved (the deny-by-default behavior, `cloud_api_keys` nested
redaction, non-string survival, input-not-mutated).

The other `gdrive/backup.py` helpers (`zip_history`, `_iso_timestamp`,
`build_manifest`, `run_backup`, `_count_history_subdirs`) are Drive-specific and are
deleted with the package.

### 3.3 Remove the Drive UI
- `ui/app/builder.py` — the "Google Drive (Phase 7.0)" block: the `GDriveAuth`
  instance plus the `_gdrive_enabled_var` / `_gdrive_account_email_var` (and any
  sibling `gdrive_*` Vars) it builds.
- `ui/dialogs/settings.py` + `ui/dialogs/settings_builder.py` — the "Google Drive"
  Settings section (sign-in / account / «Сделать бэкап» / backup-frequency) and the
  `from gdrive.backup import run_backup` import.
- `ui/app/settings_mixin.py` — the Drive worker handlers (sign-in, backup-now,
  status marshalling) wired to that section.

### 3.4 Packaging
- `voxnote.spec` — remove the Google hidden imports (`googleapiclient.discovery`,
  `googleapiclient.discovery_cache`, `googleapiclient.discovery_cache.file_cache`,
  `google_auth_oauthlib.flow`) and the entire discovery-cache trim block (it existed
  solely to keep `drive.v3.json` and drop the other 580 docs — moot once the Google
  libs are gone). Update the bundle-manifest comment that lists `gdrive/*`.
- `scripts/package_release.py` — **keep** `gdrive-token.json` in `FORBIDDEN_NAMES`
  as cheap defense-in-depth (a stale orphaned token on a dev machine must never ship
  in a client bundle). Comments referencing it stay accurate.

### 3.5 Config + existing-install state (leave-stale, no migration)
- `config.example.json` — remove the five keys: `gdrive_enabled`,
  `gdrive_account_email`, `gdrive_last_backup`, `gdrive_backup_frequency`,
  `gdrive_root_folder_id`.
- Existing `~/.voxnote/config.json` files: these keys become inert — the config
  loader tolerates unknown keys and nothing reads them after removal. **No active
  migration / pruning.**
- The orphaned `~/.voxnote/gdrive-token.json` (if present) is left on disk; it is
  harmless and outside the bundle. No cleanup code.

### 3.6 Comment cleanup + two real test fixes
Comment-only (these mention `gdrive-token.json` as an example secret-store / token
cache; the ACL/confinement/migration logic covers the whole `~/.voxnote/` dir and
does not change):
- `cli/_paths.py`, `utils.py`, `audio_io.py`, and the docstrings of
  `tests/test_secret_dir_acl.py` and `tests/test_cli_paths.py`.

Real test fixes:
- `tests/test_broad_except_ratchet.py` — remove the `"gdrive/backup.py": 1` entry
  from the allowed-broad-except map (the file is gone; its one `except Exception`
  was the `on_status` UI-callback isolation in `run_backup`).
- `tests/test_settings_worker_ui_guards.py` — the assertion
  `SETTINGS.count("self._post_to_ui(") >= 8` will fail once the Drive UI is removed
  (it drops the gdrive sign-in + backup marshalling sites). Lower the threshold to
  the post-removal count (stats + log-bundle marshalling) and update the inline
  breakdown comment. The implementer recounts `self._post_to_ui(` in the edited
  `settings_mixin.py` and sets the threshold to that exact number.

### 3.7 Docs
- `CLAUDE.md` — drop the two "Google Drive" rows in "Where things live"; update the
  "Current status" Google Drive bullet to record the removal; update invariant #3
  (it cites "google-auth versions are load-bearing" — the dep is gone).
- `docs/ARCHITECTURE.md` — remove the Google Drive section.
- `docs/CLIENT_SETUP.md` — remove the Russian Google Drive setup section.
- `.github/SECURITY.md` — remove the Drive-token / OAuth-scope mention.

## 4. Architecture after removal

Nothing depends on `gdrive/` afterward. `support_bundle.py` becomes self-contained
(owns its redaction). The queue is untouched: record / pick / inbox →
`ProcessingQueue` → `transcript.md` + `processing/sources.py` archive to the
Drive-Desktop `sources/` folder → Hermes nudge. Settings loses one section; the
main window loses the Drive auth element.

## 5. Global constraints (repo invariants)

- Cloud-only: no local CUDA / pyannote / torch (invariant #2) — unaffected; this
  only removes code.
- `encoding="utf-8"` on all text I/O — `support_bundle.py` already complies; keep it.
- Narrow `except` only; the broad-except **count goes down** (one fewer file).
- Russian user-facing strings; English code/comments/commits.
- Invariant #3 (don't liberalize `requirements.txt` pins): this **removes** three
  now-unused pins — the opposite of liberalizing, and aligned with the packaging
  de-bloat goal. Update invariant #3's text so it no longer names google-auth.
- One concern per PR; branch `chore/remove-gdrive`; the user merges.
- Commit messages lowercase-scoped (`chore(gdrive):` / `refactor(gdrive):` /
  `docs:` / `test:`), ending with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 6. Verification

- `py -3 -m pytest -q` green after deleting the five gdrive test files and applying
  the two guard-test fixes (baseline ≈ 1085 minus the deleted gdrive tests, plus the
  re-homed `redact_config` tests in `test_support_bundle.py`).
- `py -3 -m ruff check .` clean (watch for newly-unused imports in the edited UI
  files).
- No manual smoke required — this is a deletion; the queue, support bundle, and all
  other features are untouched. A quick sanity check that the app still imports
  (`py -3 -c "import app"`) and that `support_bundle.build_log_bundle` still redacts
  is covered by the relocated tests.

## 7. Decisions locked

- `redact_config` (+ its constants/helper) relocates to **`support_bundle.py`**
  (sole consumer), not `utils.py`.
- Config: **leave-stale** — drop keys from `config.example.json`, no migration of
  existing `config.json`, orphaned token left in place.
- Packaging guard: **keep** `gdrive-token.json` in `FORBIDDEN_NAMES`.
- `sources/`/`inbox/` and the whole transcription queue are **out of scope** (kept).

## 8. Out of scope

- Removing or renaming the `sources_dir` / `inbox_dir` config keys or their Settings
  folder-pickers (they are generic queue paths, not the Drive API).
- Any change to the queue, `processing/`, or the Hermes webhook.
- Active migration tooling for existing installs.
