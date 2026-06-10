# Security Policy

## Supported versions

Only the latest release (see [Releases](https://github.com/nurgysa/audio-transcriber/releases)) receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private reporting on this repository: **Security → Report a vulnerability** (GitHub Security Advisories).

По-русски: о проблемах безопасности сообщайте приватно через вкладку **Security → Report a vulnerability**, а не через публичные issues.

Scope notes for researchers:

- API keys live in `~/.audio-transcriber/config.json` (owner-only ACL applied on Windows in frozen mode).
- The diagnostics log bundle and Google Drive backups redact secret-named config keys deny-by-default (`gdrive/backup.py::redact_config`).
- The CLI / MCP surface confines file-path arguments to outside the secret store (`cli/_paths.py`).
- Audio and transcripts are sent to the user's configured cloud providers by design — see the privacy note in `README.md`.
