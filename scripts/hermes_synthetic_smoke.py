#!/usr/bin/env python3
"""Run the offline VoxNote → Hermes Mini-AGI synthetic smoke.

This script performs no external side effects. It only builds a synthetic
``audio.transcribed`` event, signs the would-be webhook body, renders the
Hermes route prompt template, and prints a JSON summary.

Usage from repo root:

    python scripts/hermes_synthetic_smoke.py
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from integrations.hermes.synthetic_smoke import run_synthetic_smoke  # noqa: E402


def main() -> int:
    print(json.dumps(run_synthetic_smoke(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
