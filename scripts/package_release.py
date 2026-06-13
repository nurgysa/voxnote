"""Package the PyInstaller bundle into a client-ready release zip — safely.

Why this exists instead of a one-liner: on Windows PowerShell 5.1 both
``Compress-Archive`` and .NET ``ZipFile.CreateFromDirectory`` write
*backslash* path separators into the zip entry names, producing archives
that fail to extract on macOS / 7-Zip / WinRAR. This script packs via the
Python stdlib ``zipfile`` with explicit forward-slash arcnames and then
verifies the result, so the trap can't recur.

It also enforces the client-bundle invariants before shipping:

* the bundle is *template-only* — no ``_internal/config.json`` and no
  ``gdrive-token.json`` may be present (keys live in
  ``~/.voxnote/config.json`` since PR #92, never in the bundle);
* none of the developer's real API keys (read from the local
  ``~/.voxnote/config.json``) appear anywhere in the bundle's
  text files.

Any violation aborts with a non-zero exit code and the zip is not written.

Usage (from the repo root, on a machine that built the bundle):

    python scripts/package_release.py                 # version 0.1.0
    python scripts/package_release.py --version 0.2.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Text-ish files worth scanning for leaked secrets. Binaries (.dll/.pyd/.exe)
# can't plausibly carry a config secret, and scanning them would be slow.
TEXT_SUFFIXES = {
    ".json", ".txt", ".log", ".cfg", ".ini", ".md",
    ".yaml", ".yml", ".env", ".toml", ".py",
}

# Files that must NOT be present in a client bundle (would leak state/keys).
FORBIDDEN_NAMES = {"config.json", "gdrive-token.json"}


def _load_real_secrets() -> list[str]:
    """Collect the developer's real key values from ~/.voxnote/config.json.

    Returns an empty list (with a warning) if the file is absent — e.g. on a
    CI runner — so packaging still works, just without the leak cross-check.
    """
    cfg_path = Path.home() / ".voxnote" / "config.json"
    if not cfg_path.exists():
        print(f"  ! {cfg_path} not found — skipping real-key leak scan", flush=True)
        return []
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! could not read {cfg_path} ({exc}) — skipping leak scan", flush=True)
        return []

    secrets: set[str] = set()
    for value in (cfg.get("cloud_api_keys") or {}).values():
        if isinstance(value, str) and len(value) >= 12:
            secrets.add(value)
    for key, value in cfg.items():
        if not isinstance(value, str) or len(value) < 12:
            continue
        if any(tok in key.lower() for tok in ("key", "token", "secret")):
            secrets.add(value)
    return sorted(secrets)


def _check_template_only(bundle: Path) -> list[str]:
    """Return a list of violation messages for forbidden files in the bundle."""
    violations = []
    for path in bundle.rglob("*"):
        if path.is_file() and path.name in FORBIDDEN_NAMES:
            violations.append(f"forbidden file in bundle: {path.relative_to(bundle)}")
    return violations


def _scan_for_secrets(bundle: Path, secrets: list[str]) -> list[str]:
    """Return a list of bundle files that contain any real secret value."""
    if not secrets:
        return []
    hits = []
    for path in bundle.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(secret in text for secret in secrets):
            hits.append(str(path.relative_to(bundle)))
    return hits


def _check_required_assets(bundle: Path) -> list[str]:
    """Return violation messages for required bundle assets that are missing.

    Guards two silent shipping regressions:
      * the markitdown doc-grounding stack absent because the build venv is
        stale — the advertised feature is then dead in the .exe (it shipped
        that way once);
      * the bundled ffmpeg GPLv3 license text absent — the vendored ffmpeg is
        a gyan.dev GPLv3 build, so redistribution requires the license.
    """
    violations = []
    internal = bundle / "_internal"
    base = internal if internal.is_dir() else bundle  # older layouts: root
    if not any(base.glob("markitdown*")):
        violations.append(
            "markitdown missing from bundle (no _internal/markitdown*) — build "
            "venv is likely stale; pip install -r requirements.txt then rebuild"
        )
    if not (base / "vendor" / "ffmpeg" / "LICENSE.txt").exists():
        violations.append(
            "ffmpeg GPLv3 license missing (_internal/vendor/ffmpeg/LICENSE.txt) "
            "— required to redistribute the gyan.dev GPLv3 ffmpeg build"
        )
    return violations


def _check_no_bloat(bundle: Path) -> list[str]:
    """Return violation messages for known over-collection bloat that must NOT ship.

    The inverse of _check_required_assets: these directories reappear only when
    ``voxnote.spec`` regresses to an over-broad ``collect_all``. The
    app imports neither at runtime, so each is pure dead weight that once nearly
    doubled the bundle (568 vs 355 MB):

      * ``scipy`` — ~76 MB of BLAS/LAPACK, pulled transitively via
        ``onnxruntime.transformers`` when onnxruntime is collect_all'd. It's
        only pandas' OPTIONAL dep, dropped via ``excludes=["scipy"]``.
      * ``pandas/tests`` — the entire ~12 MB pandas test suite, pulled by
        ``collect_all("pandas")``. The lean fix uses a plain hiddenimport so
        the official ``hook-pandas.py`` collects pandas without its tests.
    """
    violations = []
    internal = bundle / "_internal"
    base = internal if internal.is_dir() else bundle  # older layouts: root
    if (base / "scipy").is_dir():
        violations.append(
            "scipy present in bundle (~76 MB) — the app never imports it; the "
            'spec collect_all likely regressed. Keep excludes=["scipy"] and use '
            "plain hiddenimports for pandas/onnxruntime, not collect_all."
        )
    if (base / "pandas" / "tests").is_dir():
        violations.append(
            'pandas/tests present in bundle (~12 MB) — collect_all("pandas") '
            "regressed; use a plain hiddenimport so hook-pandas.py collects it leanly."
        )
    return violations


def _pack(bundle: Path, out_zip: Path, top_name: str) -> int:
    """Zip the bundle with forward-slash arcnames under ``top_name/``. Returns file count."""
    if out_zip.exists():
        out_zip.unlink()
    written = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(bundle):
            for name in files:
                full = Path(root) / name
                rel = full.relative_to(bundle).as_posix()  # forward slashes
                zf.write(full, f"{top_name}/{rel}")
                written += 1
    return written


def _verify(out_zip: Path, top_name: str) -> tuple[list[str], list[str]]:
    """Return (entry_names, problems). Problems empty == archive is sound."""
    problems = []
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        if any("\\" in n for n in names):
            problems.append("archive contains backslash entry names")
        if zf.testzip() is not None:
            problems.append("archive failed integrity check (testzip)")
    if f"{top_name}/VoxNote.exe" not in names:
        problems.append("VoxNote.exe missing from archive")
    return names, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Package a client-ready release zip.")
    parser.add_argument("--version", default="0.1.0", help="release version (default: 0.1.0)")
    parser.add_argument(
        "--bundle",
        default=str(REPO_ROOT / "dist" / "VoxNote"),
        help="path to the PyInstaller bundle dir",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output zip path (default: dist/VoxNote-v<version>.zip)",
    )
    parser.add_argument(
        "--top-name", default="VoxNote", help="top folder name inside the zip"
    )
    args = parser.parse_args()

    bundle = Path(args.bundle).resolve()
    out_zip = (
        Path(args.out).resolve()
        if args.out
        else REPO_ROOT / "dist" / f"VoxNote-v{args.version}.zip"
    )
    top_name = args.top_name

    if not (bundle / "VoxNote.exe").exists():
        print(
            f"ERROR: no VoxNote.exe under {bundle} — build the bundle first.",
            file=sys.stderr,
        )
        return 2

    print(f"Bundle: {bundle}")
    print(f"Output: {out_zip}")

    # 1. Invariant: template-only, no leaked state files.
    print("Checking template-only invariant...")
    violations = _check_template_only(bundle)
    if violations:
        for v in violations:
            print(f"  X {v}", file=sys.stderr)
        print("ABORT: bundle is not client-safe.", file=sys.stderr)
        return 1
    print("  OK: no config.json / gdrive-token.json in bundle")

    # 2. Cross-check: no real key value leaked into any text file.
    print("Scanning bundle for leaked real keys...")
    secrets = _load_real_secrets()
    hits = _scan_for_secrets(bundle, secrets)
    if hits:
        for h in hits:
            print(f"  X secret found in: {h}", file=sys.stderr)
        print("ABORT: real key value present in bundle.", file=sys.stderr)
        return 1
    print(f"  OK: 0 secrets found (checked {len(secrets)} key values)")

    # 3. Required assets: doc-grounding stack + ffmpeg license must be present.
    print("Checking required bundle assets...")
    asset_violations = _check_required_assets(bundle)
    if asset_violations:
        for v in asset_violations:
            print(f"  X {v}", file=sys.stderr)
        print("ABORT: bundle is missing required assets.", file=sys.stderr)
        return 1
    print("  OK: markitdown + ffmpeg license present")

    # 4. No-bloat: known over-collection markers must never ship (regression
    #    guard for the spec's collect_all — once shipped a 568 MB bundle).
    print("Checking for bundle bloat...")
    bloat_violations = _check_no_bloat(bundle)
    if bloat_violations:
        for v in bloat_violations:
            print(f"  X {v}", file=sys.stderr)
        print("ABORT: bundle contains known bloat (spec collect_all regressed).", file=sys.stderr)
        return 1
    print("  OK: no scipy / pandas.tests bloat")

    # 5. Pack with POSIX separators.
    print("Packing...")
    written = _pack(bundle, out_zip, top_name)

    # 6. Verify the archive is extractable everywhere.
    names, problems = _verify(out_zip, top_name)
    if problems:
        for p in problems:
            print(f"  X {p}", file=sys.stderr)
        print("ABORT: archive verification failed.", file=sys.stderr)
        return 1

    size_mb = out_zip.stat().st_size / (1024 * 1024)
    print("  OK: 0 backslash entries, integrity verified, exe present")
    print()
    print(f"DONE: {out_zip}  ({size_mb:.1f} MB, {written} files)")
    print("Publish this zip as a GitHub Release asset (user guide: docs/CLIENT_SETUP.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
