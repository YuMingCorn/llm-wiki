"""Tests for the slide-reviewer subagent hook (runs it with node)."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = (Path(__file__).resolve().parent.parent / ".claude" / "hooks"
        / "slide-reviewer-guard.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run(root: Path, tool: str, **tool_input: str) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input,
                          "agent_type": "slide-reviewer"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(["node", str(HOOK)], input=payload, text=True,
                          capture_output=True, encoding="utf-8",
                          env=env).returncode


@pytest.mark.parametrize("tool, kwargs", [
    ("Read", {"file_path": "wiki/demo/sources/2024-a-paper.md"}),
    ("Read", {"file_path": ".llm-wiki/extract/demo/pages/paper-p003.png"}),
    ("Grep", {"pattern": "sink", "path": "wiki"}),
    ("Write", {"file_path": ".llm-wiki/extract/demo/review.tsv"}),
])
def test_allows_reading_and_own_report(tmp_path: Path, tool: str,
                                       kwargs: dict) -> None:
    assert run(tmp_path, tool, **kwargs) == 0


@pytest.mark.parametrize("tool, kwargs", [
    ("Write", {"file_path": "slides/demo/outline.md"}),
    ("Edit", {"file_path": "wiki/demo/sources/2024-a-paper.md"}),
    ("Write", {"file_path": ".llm-wiki/extract/demo/verify.md"}),
    ("Write", {"file_path": ".llm-wiki/extract/demo/sub/review.tsv"}),
    ("Write", {"file_path": "../outside/review.tsv"}),
    ("Write", {"file_path": ".llm-wiki/extract/demo./review.tsv"}),
    ("mcp__hackmd__hackmd_push_note", {}),
])
def test_blocks_other_writes(tmp_path: Path, tool: str, kwargs: dict) -> None:
    assert run(tmp_path, tool, **kwargs) == 2


def test_agent_matcher_leaves_read_tools_alone() -> None:
    agent = HOOK.parent.parent / "agents" / "slide-reviewer.md"
    text = agent.read_text(encoding="utf-8")
    matcher = re.search(r'matcher: "([^"]+)"', text).group(1)
    assert all(re.fullmatch(matcher, t) for t in ("Write", "Edit", "mcp__x"))
    assert not any(re.fullmatch(matcher, t)
                   for t in ("Read", "Grep", "Glob", "SubagentHandback"))
    assert "tools: Read, Grep, Glob, Write" in text
