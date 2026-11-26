"""Tests for the reader-guard subagent hook (runs it with node)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = (Path(__file__).resolve().parent.parent / ".claude" / "hooks"
        / "reader-guard.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run(root: Path, tool: str, **tool_input: str) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input,
                          "agent_type": "concept-reader"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(["node", str(HOOK)], input=payload, text=True,
                          capture_output=True, encoding="utf-8",
                          env=env).returncode


@pytest.mark.parametrize("tool, kwargs", [
    ("Read", {"file_path": "wiki/music/concepts/counterpoint.md"}),
    ("Read", {"file_path": "wiki/music/topics/rl.md"}),
    ("Read", {"file_path": "GLOSSARY.md"}),
    ("Read", {"file_path": ".claude/skills/wiki-ingest/references/concept-writing.md"}),
    ("Read", {"file_path": ".llm-wiki/extract/counterpoint/student.md"}),
    ("Grep", {"pattern": "advantage", "path": "wiki/music/concepts"}),
    ("Grep", {"pattern": "x", "path": "wiki/music/concepts", "glob": "*draw*.md"}),
    ("Glob", {"pattern": "*.md", "path": "wiki/music/concepts"}),
    ("Write", {"file_path": ".llm-wiki/extract/counterpoint/student.md"}),
])
def test_allows_wiki_reading_and_own_report(tmp_path: Path, tool: str,
                                            kwargs: dict) -> None:
    assert run(tmp_path, tool, **kwargs) == 0


def test_allows_absolute_path_inside_wiki(tmp_path: Path) -> None:
    target = tmp_path / "wiki" / "music" / "concepts" / "counterpoint.md"
    assert run(tmp_path, "Read", file_path=str(target)) == 0


@pytest.mark.parametrize("tool, kwargs", [
    ("Read", {"file_path": "raw/sources/2402.03300.pdf"}),
    ("Read", {"file_path": "Raw/sources/a.md"}),
    ("Read", {"file_path": "wiki/music/sources/2024-kim-counterpoint.md"}),
    ("Read", {"file_path": "WIKI/music/Sources/a.md"}),
    ("Read", {"file_path": "wiki/music/sources./a.md"}),
    ("Read", {"file_path": "wiki/music/SOURCE~1/a.md"}),
    ("Read", {"file_path": ".llm-wiki/extract/counterpoint/verify.md"}),
    ("Read", {"file_path": ".mcp.json"}),
    ("Read", {"file_path": ".claude/settings.local.json"}),
    ("Read", {"file_path": "hackmd_note/a.md"}),
    ("Read", {"file_path": "../outside.md"}),
    ("Grep", {"pattern": "Counterpoint", "path": "raw"}),
    ("Grep", {"pattern": "Counterpoint"}),
    ("Grep", {"pattern": "Counterpoint", "path": "wiki"}),
    ("Grep", {"pattern": "Counterpoint", "path": "wiki/music", "type": "md"}),
    ("Grep", {"pattern": "Counterpoint", "path": "wiki/music/concepts", "glob": "../sources/*"}),
    ("Glob", {"pattern": "**/*.md", "path": "wiki"}),
    ("Glob", {"pattern": "../s*/*", "path": "wiki/music/concepts"}),
    ("Glob", {"pattern": "../SOURCES/*", "path": "wiki/music/concepts"}),
    ("Glob", {"pattern": "/raw/*", "path": "wiki/music/concepts"}),
    ("Write", {"file_path": "wiki/music/concepts/counterpoint.md"}),
    ("Write", {"file_path": ".llm-wiki/extract/counterpoint/verify.md"}),
    ("Bash", {"command": "cat raw/sources/a.md"}),
    ("mcp__hackmd__hackmd_get_note", {}),
])
def test_blocks_sources_raw_and_other_writes(tmp_path: Path, tool: str,
                                             kwargs: dict) -> None:
    assert run(tmp_path, tool, **kwargs) == 2


def test_blocks_absolute_path_to_sources_in_upper_case(tmp_path: Path) -> None:
    target = str(tmp_path / "WIKI" / "music" / "SOURCES" / "a.md")
    assert run(tmp_path, "Read", file_path=target) == 2


def test_agent_matcher_leaves_report_tool_alone() -> None:
    import re
    agent = HOOK.parent.parent / "agents" / "concept-reader.md"
    matcher = re.search(r'matcher: "([^"]+)"', agent.read_text(encoding="utf-8")).group(1)
    assert all(re.fullmatch(matcher, t) for t in ("Read", "Grep", "Glob", "Write"))
    assert not re.fullmatch(matcher, "SubagentHandback")
