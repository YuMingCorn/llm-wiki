"""Tests for the verifier-guard subagent hook (runs it with node)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = (Path(__file__).resolve().parent.parent / ".claude" / "hooks"
        / "verifier-guard.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run(root: Path, tool: str, **tool_input: str) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input,
                          "agent_type": "source-verifier"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(["node", str(HOOK)], input=payload, text=True,
                          capture_output=True, encoding="utf-8",
                          env=env).returncode


@pytest.mark.parametrize("command", [
    "uv run python -I tools/doc.py grep \"third sub-layer\" raw.md -C 2",
    "uv run python -I tools/doc.py quote --from verify.md 2>&1 | tail -5",
    "git diff -- wiki/sources/2017-x.md",
    "git log --oneline -5 --grep commit",
    "git show HEAD~3:wiki/sources/2017-x.md | head -40",
    "cat .llm-wiki/extract/2017-x/verify.md 2>/dev/null",
    "Get-Content wiki/index.md | Select-Object -First 20",
    "grep -n \"a > b\" wiki/sources/2017-x.md",
])
def test_allows_read_only_and_listed_tools(tmp_path: Path,
                                           command: str) -> None:
    assert run(tmp_path, "Bash", command=command) == 0


@pytest.mark.parametrize("command", [
    "git commit -m x",
    "git add wiki",
    "git -c user.name=x push",
    "uv run python -I tools/wiki.py log ingest \"x\"",
    "uv run python -I tools/wiki.py commit \"docs(wiki): x\" wiki",
    "uv run python -I tools/ledger.py add-source a b",
    "uv run python -I tools/agy_extract.py raw/sources/a.pdf",
    "uv run python -I -c \"open('raw/a','w')\"",
    "uv run python -I - <<'EOF'\nprint(1)\nEOF",
    "echo x > raw/sources/a.md",
    "cat a >> wiki/log.md",
    "rm -rf raw",
    "sed -i 's/a/b/' wiki/sources/2017-x.md",
    "find . -name x -delete",
    "Set-Content -Path wiki/x.md -Value x",
    "echo $(git commit -m x)",
    "ls; git push",
    "agy -p hi --model gemini-4-argon-high",
    "claude -p hi",
    "uv run python tools/pdf_tool.py split a.pdf",
])
def test_blocks_forbidden_commands(tmp_path: Path, command: str) -> None:
    assert run(tmp_path, "Bash", command=command) == 2


@pytest.mark.parametrize(("file_path", "code"), [
    (".llm-wiki/extract/2017-x/verify.md", 0),
    (".llm-wiki/extract/2017-x/extraction.md", 2),
    (".llm-wiki/extract/verify.md", 2),
    ("wiki/sources/2017-x.md", 2),
    ("wiki/concepts/harmony.md", 2),
    ("raw/sources/a.md", 2),
    ("../outside.md", 2),
])
def test_writes_only_verify_md(tmp_path: Path, file_path: str,
                               code: int) -> None:
    assert run(tmp_path, "Edit", file_path=str(tmp_path / file_path)) == code
    assert run(tmp_path, "Write", file_path=file_path) == code


def test_blocks_mcp_tools(tmp_path: Path) -> None:
    assert run(tmp_path, "mcp__hackmd__hackmd_push_note") == 2
