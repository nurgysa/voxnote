#!/usr/bin/env python3
"""Stage-0 ASR evaluation CLI.

Loads a corpus manifest and a provider result file (see
`evaluation/asr_benchmark.py` for the JSON contracts), computes WER /
key-term recall / segment and cost metrics, and writes a machine-readable
JSON report and/or a Markdown summary. Stdlib only — no network calls, no
VoxNote provider/UI imports, no config or key reads.

Usage (from repo root):
    python scripts/asr_stage0_evaluate.py --manifest m.json --result r.json
    python scripts/asr_stage0_evaluate.py --manifest m.json --result r.json \
        --out-json report.json --out-md report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from evaluation.asr_benchmark import (  # noqa: E402, I001
    build_report,
    load_manifest,
    load_result,
    render_markdown,
)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the Stage-0 ASR evaluation harness.")
    ap.add_argument("--manifest", required=True, help="path to the corpus manifest JSON")
    ap.add_argument("--result", required=True, help="path to the provider result JSON")
    ap.add_argument("--out-json", help="write the full report as JSON to this path")
    ap.add_argument("--out-md", help="write the Markdown summary to this path")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        result = load_result(args.result, manifest)
        report = build_report(manifest, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)

    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as f:
            f.write(markdown)

    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
