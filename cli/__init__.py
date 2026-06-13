"""Command-line interface for voxnote.

A thin, headless adapter over the existing pipeline (``transcriber`` /
``tasks`` / ``providers``) so external agents (e.g. Hermes Agent) can drive
transcription, task-extraction, protocol generation and task-send from a
shell — without importing the CustomTkinter GUI.

Invoked as ``python -m cli <subcommand> ...``. See ``cli.app`` for the
argparse front-end and ``cli.core`` for the reusable orchestration seam that
a future stdio MCP server can import directly.
"""
from __future__ import annotations

__version__ = "0.1.0"
