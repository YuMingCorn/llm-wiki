"""Tests for digest.py (what changed in the wiki since a commit)."""

import json
import subprocess
from pathlib import Path

import pytest

import digest

SRC = "wiki/music/sources/2026-x-new.md"
CON = "wiki/music/concepts/c.md"
OLD = "wiki/music/concepts/old.md"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True,
                          encoding="utf-8").stdout


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


def _page(title: str, type_: str, body: str, status: str = "draft") -> str:
    return (f"---\ntitle: {title}\ntype: {type_}\nstatus: {status}\n---\n"
            f"# {title}\n\n{body}")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, CON, _page("概念 C", "concept", "## 定義\n舊內容\n"))
    _write(tmp_path, OLD, _page("舊頁", "concept", "x\n"))
    _write(tmp_path, "wiki/log.md", "# Log\n")
    start = _commit(tmp_path, "start")
    _write(tmp_path, SRC, _page("新來源", "source",
           "## 結論\n\n第一段，見 [C](../concepts/c.md)。\n\n| 表 |\n|---|\n\n"
           "第二段。\n\n第三段。\n\n## 方法與限制\n- 不算\n"))
    _write(tmp_path, CON, _page("概念 C", "concept",
           "## 定義\n新內容\n\n## 已被取代的說法\n- 2026-11-17｜舊說法\n",
           status="disputed"))
    (tmp_path / OLD).unlink()
    _write(tmp_path, "wiki/log.md", "# Log\n\n## [2026-11-17] ingest | x\n")
    _commit(tmp_path, "change")
    return tmp_path, start


def test_build_reports_each_kind_of_change(repo: tuple[Path, str]) -> None:
    root, start = repo
    data = digest.build(root, start, limit=2)
    assert data["commits"] == 1
    [src] = data["sources"]
    assert src["conclusions"] == ["第一段，見 C。", "第二段。"]
    assert src["more"] == 1
    assert data["superseded"] == [{"page": CON, "title": "概念 C",
                                   "claim": "2026-11-17｜舊說法"}]
    assert [r["page"] for r in data["disputed"]] == [CON]
    assert [r["page"] for r in data["modified"]] == [CON]
    assert data["deleted"] == [OLD]
    text = digest.render(data, "指定")
    assert "## 新來源（1）" in text and "另有 1 條結論" in text
    assert "log.md" not in text


def test_list_conclusions_win_over_paragraphs() -> None:
    assert digest._findings("說明\n\n- 甲\n- 乙\n") == ["甲", "乙"]
    assert digest._plain("**粗**[t](u)" + "字" * 200).endswith("…")


def test_since_uses_mark_then_falls_back(repo: tuple[Path, str]) -> None:
    root, start = repo
    head = _git(root, "rev-parse", "HEAD").strip()
    assert digest.resolve_since(root, start) == (start, "指定")
    with pytest.raises(ValueError):
        digest.resolve_since(root, "nope")
    _, how = digest.resolve_since(root, None)
    assert "沒有可用的標記" in how
    digest.mark(root, head)
    assert digest.resolve_since(root, None) == (head, "上次 --mark")
    (root / digest.MARK).write_text(json.dumps({"head": "0" * 40}),
                                    encoding="utf-8")
    assert digest.resolve_since(root, None)[0] != "0" * 40
    assert "沒有變動" in digest.render(digest.build(root, head), "x")


def test_pure_rename_and_limit_zero(repo: tuple[Path, str]) -> None:
    root, _ = repo
    head = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "mv", SRC, "wiki/music/sources/2026-x-moved.md")
    _commit(root, "move")
    data = digest.build(root, head)
    assert data["renamed"] == [{"page": "wiki/music/sources/2026-x-moved.md",
                                "title": "新來源", "from": SRC}]
    assert data["modified"] == []
    zero = digest.build(root, _git(root, "rev-list", "--max-parents=0",
                                   "HEAD").strip(), limit=0)
    text = digest.render(zero, "x")
    assert "另有 3 條結論" in text and "沒有「結論」小節" not in text


def test_sections_without_h2_is_empty() -> None:
    """A body with no H2 heading has no sections (and does not raise)."""
    assert digest._sections("no headings here\n") == {}
