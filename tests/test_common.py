"""Tests for _common.py (frontmatter parsing and git calls)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import _common  # noqa: E402


@pytest.mark.parametrize("text", [
    "---\ntier: personal\n---\nbody\n",
    "---\r\ntier: personal\r\n---\r\nbody\r\n",
    "﻿---\ntier: personal\n---\nbody\n",
    '---\ntier: "personal"\n---  \nbody\n',
])
def test_split_reads_all_supported_forms(text: str) -> None:
    meta, body = _common.split_frontmatter(text, strict=True)
    assert meta == {"tier": "personal"}
    assert body.strip() == "body"


def test_tier_in_body_is_not_frontmatter() -> None:
    assert _common.split_frontmatter("# t\ntier: personal\n")[0] is None


@pytest.mark.parametrize("text", [
    "---\ntier: [personal\n---\nbody\n",
    "---\ntier: personal\nno closing fence\n",
])
def test_unreadable_frontmatter_depends_on_strict(text: str) -> None:
    assert _common.split_frontmatter(text)[0] is None
    with pytest.raises(_common.FrontmatterError):
        _common.split_frontmatter(text, strict=True)


def test_non_mapping_frontmatter_is_none() -> None:
    assert _common.split_frontmatter("---\n- a\n---\nb\n", strict=True)[0] is None


def test_git_prints_non_ascii_paths_unquoted(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "中文.md").write_text("x\n", encoding="utf-8")
    _common.git(tmp_path, "add", "--", "中文.md")
    assert _common.git(tmp_path, "diff", "--cached", "--name-only").strip() == "中文.md"


def test_git_raises_on_failure(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(RuntimeError):
        _common.git(tmp_path, "rev-parse", "--verify", "HEAD")
    assert _common.run_git(tmp_path, "rev-parse", "--verify", "HEAD").returncode != 0
