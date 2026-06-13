"""The Hermes skill is a shipped artifact — keep it valid and in sync.

The strongest check ties the skill to the live MCP tool surface: if a tool is
renamed/added in cli/mcp_server.py, the skill must mention it or this fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SKILL = (
    Path(__file__).resolve().parent.parent
    / "integrations" / "hermes" / "skills" / "voxnote" / "SKILL.md"
)


def _read() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_skill_exists_with_required_frontmatter():
    assert _SKILL.is_file()
    text = _read()
    assert text.startswith("---\n"), "SKILL.md must open with a YAML frontmatter block"
    frontmatter = text.split("---", 2)[1]
    assert "name:" in frontmatter
    assert "description:" in frontmatter


def test_skill_mentions_every_mcp_tool():
    pytest.importorskip("mcp")
    import asyncio

    import cli.mcp_server as srv

    tool_names = [tool.name for tool in asyncio.run(srv.mcp.list_tools())]
    text = _read()
    missing = [name for name in tool_names if name not in text]
    assert not missing, f"SKILL.md is out of sync — missing MCP tools: {missing}"


def test_skill_references_cli_entrypoint():
    assert "python -m cli" in _read()
