# Transcription queue — handoff (resume at PR-B2)

**Date:** 2026-06-15
**Spec (source of truth):** `docs/superpowers/specs/2026-06-14-voxnote-transcription-queue-design.md`
**Method:** superpowers brainstorming → writing-plans → subagent-driven-development → finishing-a-development-branch. PRs are reviewed/merged by the user (push + PR per slice).

## Status

- **PR-A (storage core)** — MERGED (#153). On `main`: `transcript_format.format_diarized_markdown`, `processing/vault_note.py` (`render_transcript_note` + `write_transcript_note`), `processing/sources.py` (`archive_audio`), `utils.save_segments_sidecar`/`load_segments_sidecar`.
- **PR-B1 (worker supports)** — MERGED (#154). On `main`: `cli.core.run_transcribe` now takes `num_speakers`/`min_speakers`/`max_speakers`; Hermes `audio.transcribed` **v1.1** (`build_audio_transcribed_event` + `emit_audio_transcribed_event` accept `note_path`/`source_path`/`project`); `processing/inbox_watcher.py` (`scan_inbox` + `InboxWatcher.poll()` size-stable debounce). Also fixed a pre-existing date time-bomb test (`tests/test_tasks_extractor.py::test_parse_extracts_well_formed_task`).
- **`main` is GREEN** (suite passes, ruff clean).
- **Current branch:** `feat/transcription-queue-pr-b2` (created off main, only this handoff committed).
- Next after B2: **PR-C (UI wiring)** — see spec §UI.

## PR-B2 scope (NOT yet implemented)

Rework the coupled trio `processing/{model,store,worker}.py` → **1-stage** queue that uses the PR-A/B1 primitives, **+ `processing/preflight.py`** (new), **+ `AGENTS.md`** pipeline update.

### Locked decisions (my recommended defaults — user was confirming when context filled; revisit if needed)
1. **Meeting folder holds only `transcript.md`.** New worker uses `vault_note.write_transcript_note` (NOT `utils.create_history_entry`). DROP the old in-folder artifacts: `description.md`, the audio copy, in-folder `segments.json`.
2. **Audio → Drive `sources`** via `sources.archive_audio`. `move=True` for `source in {record, inbox}` (drains the inbox); `move=False` (copy) for `pick` (leave the user's original). This replaces the old delete-after-transcription toggle.
3. **Keep writing `speakers.json`** (project_id) in the folder via `utils.save_speakers(folder, project_id, [], {})` — preserves compat with «Извлечь задачи» + directory features, and lets `store.build_view` keep reading project from it (smaller change).
4. **Segments → app-data sidecar** via `utils.save_segments_sidecar(item.id, segments)` (not the vault).
5. **`nudged:` frontmatter = whether Hermes is enabled** (intent; single write — the note is written before the best-effort nudge, whose payload carries `note_path`).
6. **Model** (`processing/model.py`): single `status: StageStatus` (use only `PENDING/RUNNING/DONE/ERROR`; drop `AWAITING_REVIEW` + the `transcript`/`protocol`/`tasks` fields + `error_stage`). Add `source: str` (`record|pick|inbox`), `source_path: str | None`, `nudge_delivered: bool`. Keep `meeting_folder`, `auto`, `options`, `project_id`, `error_message`.
7. **`store.build_view`**: status = `transcript.md` present → `DONE` else `PENDING`; surface `protocol.md` / `tasks.json` presence as **Hermes-progress badges** (extra display fields, not stage status); project still read from `speakers.json`. Update `_row_from_folder` + drop `stage_status_from_folder`'s 3-stage shape (or repurpose for badges).

### New worker `_process_item` shape (1-stage)
```
status=RUNNING
cfg = config_loader(); opts = item.options
provider/api_key resolve (raise ValueError if no key)
info = preflight.probe(audio_path)            # {duration_s, size_bytes}
ok, reason = preflight.provider_limit_ok(provider, duration_s, size_bytes) -> raise if not ok
denoise = preflight.should_denoise(duration_s, bool(opts.denoise))
out = core.run_transcribe(audio_path, provider=, api_key=, language=, diarize=,
                          hotwords=, denoise=denoise, num_speakers=, min_speakers=, max_speakers=)
base = <YYYY-MM-DD>_<HHMM>_<slug(title)>  (from item.created_at + title)
project = resolve_project(opts.project_id)
source_path = sources.archive_audio(audio_path, cfg["sources_dir"], base,
                                    move=(item.source in {record,inbox}))  # skip if no sources_dir
hermes_cfg = get_hermes_webhook_config(cfg)
content = vault_note.render_transcript_note(segments=out.segments, title=item.title,
            project_name=getattr(project,"name",None), date=, time=, participants=[],
            provider=provider, language=out.language, voxnote_id=item.id,
            source_path=source_path or audio_path, nudged=hermes_cfg.enabled)
note_path = vault_note.write_transcript_note(meetings_dir, project, base, content)
folder = dirname(note_path); item.meeting_folder=folder; item.source_path=source_path
utils.save_speakers(folder, opts.project_id, [], {})          # compat
utils.save_segments_sidecar(item.id, out.segments)
if hermes_cfg.enabled: emit_audio_transcribed_event(config=hermes_cfg, transcript_text=out.text,
    audio_path=audio_path, history_folder=folder, note_path=note_path, source_path=source_path,
    project={"id":project.id,"name":project.name} if project else None,
    provider=provider, language=out.language)  -> item.nudge_delivered = result.sent  (swallow errors)
status=DONE   (on any exception: status=ERROR, error_message=humanize(e), halt; worker survives)
```
- `enqueue(audio_path, options)` should also capture `source` (record/pick/inbox) into the item (add a param or read `options["source"]`).
- `retry()` resets an `ERROR` item to `PENDING` (single status now).
- Broad-except ratchet: `processing/worker.py` baseline likely changes (was 4 in the 3-stage version; the 1-stage worker has fewer boundaries — update `tests/test_broad_except_ratchet.py`).

### `preflight.py` (new, additive — do as Task 1, green)
- `probe(audio_path) -> {"duration_s": float|None, "size_bytes": int}`. Duration: try `audio_io.get_duration_s` (soundfile — WAV/FLAC/OGG only); on failure parse `ffmpeg -i <path>` stderr `Duration: HH:MM:SS.ss` (ffmpeg via `utils.get_ffmpeg_path()`; ffprobe is NOT bundled). Return `None` duration if both fail.
- `provider_limit_ok(provider, duration_s, size_bytes) -> (bool, reason)` — size cap ~2 GB for AssemblyAI/Speechmatics; Gladia tighter (verify real cap). Past `None` duration → only size-gate.
- `should_denoise(duration_s, requested) -> bool` — `False` if `duration_s` and `duration_s > 45*60`.
- `estimate_cost(provider, duration_s) -> float | None` — small per-provider $/h table (Speechmatics ~1.04/h w/ diarization per provider file comments; others rough). `None` if duration unknown.

### Tests to rewrite/add for the trio
- `tests/test_processing_model.py` — single `status` round-trip; new fields.
- `tests/test_processing_store.py` — `build_view` single status + badges; project from speakers.json.
- `tests/test_processing_worker.py` — 1-stage: probe→transcribe→note(vault)→archive(sources, move/copy by source)→sidecar→nudge→DONE (all deps patched); provider-cap block; denoise auto-off >45min; error→ERROR+halt; nudge fail→DONE+nudge_delivered=False; retry.
- `tests/test_preflight.py` — probe/guard/denoise/cost.
- `tests/test_broad_except_ratchet.py` — update `processing/worker.py` baseline.

## How to resume
1. `git checkout feat/transcription-queue-pr-b2` (or recreate off `main`).
2. Re-read current `processing/{model,store,worker}.py` for exact target diffs.
3. Use **writing-plans** to expand this into a bite-sized PR-B2 plan; then **subagent-driven-development** (preflight first/green; trio as one atomic-green task on a capable model; AGENTS.md last). Run `py -3 -m pytest -q` + `py -3 -m ruff check .` per task.
4. Finish via **finishing-a-development-branch** → push + PR (user reviews/merges).
5. Then PR-C (UI): enqueue (record/file/inbox poll-tick) + main-bar project selector + indicator + remove «Транскрибировать» + «Встречи» = queue+history. Spec §UI / §Phasing.

## Conventions
Commits lowercase-scoped; end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Russian chat to the user, English code/commits/docs. `py -3 -m pytest` (fallback `python -m`).
