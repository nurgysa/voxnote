"""AST + source-text checks for utils.get_meetings_dir."""
from __future__ import annotations

import ast
from pathlib import Path

UTILS_PATH = Path(__file__).resolve().parent.parent / "utils.py"


def _get_function_def(source: str, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_get_meetings_dir_function_exists():
    source = UTILS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "get_meetings_dir")
    assert fn is not None, "get_meetings_dir must be defined in utils.py"


def test_get_meetings_dir_uses_config_key():
    source = UTILS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "get_meetings_dir")
    assert fn is not None
    body = ast.unparse(fn)
    assert "meetings_dir" in body, (
        "Resolver must read config['meetings_dir']"
    )


def test_get_meetings_dir_expands_env_and_user():
    """expanduser + expandvars must be called so ~/X and %USERPROFILE%\\X work."""
    source = UTILS_PATH.read_text(encoding="utf-8")
    # Check at module level — the helper that normalizes paths may be
    # outside get_meetings_dir but still in utils.py.
    assert "expanduser" in source, "Resolver must expanduser() for ~ prefix"
    assert "expandvars" in source, "Resolver must expandvars() for %VAR% / $VAR"


def test_default_meetings_dir_constant_present():
    source = UTILS_PATH.read_text(encoding="utf-8")
    assert "_DEFAULT_MEETINGS_DIR" in source, (
        "Module-level _DEFAULT_MEETINGS_DIR constant required"
    )
    assert "Documents" in source, (
        "Default path should include 'Documents' (per spec)"
    )


def test_legacy_history_locations_constant_present():
    source = UTILS_PATH.read_text(encoding="utf-8")
    assert "_LEGACY_HISTORY_LOCATIONS" in source, (
        "Module-level _LEGACY_HISTORY_LOCATIONS constant required for "
        "first-launch detection (consumed by App.__init__)"
    )


def test_config_example_has_meetings_dir_key():
    """config.example.json must list meetings_dir with empty-string default."""
    import json
    config_path = UTILS_PATH.parent / "config.example.json"
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "meetings_dir" in cfg, (
        "config.example.json must include 'meetings_dir' key"
    )
    assert cfg["meetings_dir"] == "", (
        "Default value must be '' (sentinel for 'use default')"
    )
