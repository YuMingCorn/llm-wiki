"""Tests for the steer-to-tools PreToolUse hook (runs it with node)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = (Path(__file__).resolve().parent.parent / ".claude" / "hooks"
        / "steer-to-tools.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run(command: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(["node", str(HOOK)], input=payload, text=True,
                          capture_output=True, encoding="utf-8").returncode


@pytest.mark.parametrize("command", [
    "cat >> wiki/log.md <<'EOF'\n## [2026-11-08] lint | x\nEOF",
    "printf 'x' >> /d/my_wiki/wiki/log.md",
    "echo x | tee -a wiki/log.md",
    "Add-Content -Path wiki\\log.md -Value x",
    "uv run python -I -c \"import pymupdf; d=pymupdf.open('a.pdf')\"",
    "uv run python -I - <<'EOF'\nimport fitz\nEOF",
    "echo x | tee --append wiki/log.md",
    "Out-File -FilePath wiki/log.md -Append -InputObject x",
    "uv run python -I - <<'PY'\nimport sys, fitz\nPY",
    "uv run python -I -c \"__import__('fitz').open('a.pdf')\"",
    "uv run python -I -c \"import sys; from pymupdf import open\"",
])
def test_blocks(command: str) -> None:
    assert run(command) == 2


@pytest.mark.parametrize("command", [
    "uv run python -I tools/wiki.py log lint \"x\" <<'EOF'\n- a\nEOF",
    "tail -5 wiki/log.md",
    "grep -n lint wiki/log.md",
    "uv run python tools/pdf_tool.py toc raw/sources/a.pdf",
    "uv run python -I -c \"import json; print(1)\"",
    "git diff wiki/log.md >> /tmp/x.txt",
    "uv run python -I tools/wiki.py log schema \"hook\" <<'EOF'\n"
    "- hook 擋 python -c 的 import fitz 與 fitz.open\nEOF",
    "uv run python -I tools/wiki.py commit \"chore(config): hook\" .claude"
    " --body \"擋 >> wiki/log.md\"",
    "git commit -m \"fix(hooks): stop echo >> wiki/log.md\"",
    "echo x >> wiki/log.md.bak",
    "git log >> wiki/log.md.txt",
    "cat > t.py <<'EOF'\nrun('cat >> wiki/log.md')\nEOF",
    "uv run python -I -c \"print('import fitz')\"",
])
def test_allows(command: str) -> None:
    assert run(command) == 0


@pytest.mark.parametrize("command", [
    "rm raw/sources/Frog.pdf",
    "rm -f /d/my_wiki/raw/sources/a.pdf",
    "cd /d/my_wiki && mv raw/sources/a.pdf raw/sources/b.pdf",
    r"Remove-Item raw\sources\a.pdf",
    r"Move-Item -Path D:\my_wiki\raw\sources\a.md -Destination x.md",
    "echo x > raw/sources/a.md",
    "printf x >> ./raw/sources/a.md",
    "sed -i 's/a/b/' raw/sources/a.md",
    "echo x | tee raw/sources/a.md",
    "Set-Content -Path raw/sources/a.md -Value x",
    "rm Raw/sources/a.pdf",
    r"Remove-Item D:\my_wiki\RAW\sources\a.pdf",
])
def test_blocks_raw_writes(command: str) -> None:
    assert run(command) == 2


@pytest.mark.parametrize("command", [
    "ls raw/sources",
    "cat raw/sources/a.md | head",
    "grep -rn attention raw/sources/ > /tmp/hits.txt",
    "uv run python -I tools/doc.py show raw/sources/a.pdf --pages 1",
    "md5sum raw/sources/*.pdf",
    "cp /tmp/new.pdf raw/sources/2026-x-new.pdf",
    "rm -rf .llm-wiki/extract/foo",
    "rm tmp/rawdata.txt",
    "sed -n 1,5p raw/sources/a.md",
    "git commit -m \"docs: 說明 rm raw/sources/x 要由使用者執行\"",
    "cat > note.md <<'EOF'\nrm raw/sources/a.pdf\nEOF",
])
def test_allows_raw_reads(command: str) -> None:
    assert run(command) == 0
