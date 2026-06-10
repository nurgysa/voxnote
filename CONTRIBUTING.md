# Contributing

Thanks for your interest! This is a Windows desktop app (CustomTkinter)
for cloud audio transcription with a Russian-language UI. Contributions
are welcome — bug reports, fixes, providers, docs.

## Dev setup

See the «Установка (для разработки)» section of [README.md](README.md):
Python 3.12, ffmpeg in `PATH`, `pip install -r requirements.txt`
(+ `requirements-dev.txt` for pytest/ruff). Windows 10/11 is the target
platform; the app runs from source on it via `python app.py`.

## Before you open a PR

```bash
pytest                  # must be green (baseline ≈ 748 tests)
python -m ruff check .  # must be clean
```

CI runs both on ubuntu-latest **and** windows-latest. Two rules that
catch most newcomer failures:

- Always pass `encoding="utf-8"` to `open()` / `read_text()` /
  `write_text()` — stock Windows defaults to cp1252 and Cyrillic
  content will crash there even when Linux CI is green.
- Tests must not import `ui/` (sounddevice loads PortAudio at import
  time, absent on Linux runners) — assert on source text instead, like
  `tests/test_bundle_ui_only.py` does.

## Conventions

[CLAUDE.md](CLAUDE.md) is the canonical briefing — hard invariants
(no local CUDA/torch reintroduction; `requirements.txt` pins are
load-bearing), code conventions (narrow `except` classes, Russian UI
strings / English code, docstrings and commits), and a map of where
things live. It is written for AI coding agents but applies to humans
equally.

PR workflow: topic branch (`feat/...`, `fix/...`, `docs/...`), one
concern per PR, lowercase scoped commit messages, and a PR description
with `## Summary` + `## Test plan` (markdown checkboxes). UI-touching
changes should state what manual smoke was done — there is no automated
GUI testing.

## Bugs & security

File bugs via GitHub Issues. For crashes, attach the redacted log
bundle: Настройки → Диагностика → «Сохранить лог для отправки»
(API keys are stripped automatically). Please report security issues
privately via GitHub Security Advisories instead of public issues.
