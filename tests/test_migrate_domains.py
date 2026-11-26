import json
from pathlib import Path

import pytest

import migrate_domains as md
from lint import lint


def _page(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(domain: str, page_type: str, sources: str = "[]") -> str:
    return f"---\ntitle: x\ntype: {page_type}\ndomain: {domain}\nsources: {sources}\n---\n"


@pytest.fixture
def flat(tmp_path: Path) -> Path:
    _page(tmp_path, "wiki/index.md",
          "---\ntitle: Index\ntype: index\nupdated: 2026-01-01\n---\n\n# Index\n\n"
          "## Overview\n- [Overview](overview.md)\n\n"
          "## Music\n- [T](topics/t.md) — music topic\n\n### 跨主題\n- [S](synthesis/s.md) — s\n\n"
          "## History\n- [F](topics/f.md) — history topic\n")
    _page(tmp_path, "wiki/overview.md", "# O\n[a](concepts/a.md) [b](concepts/b.md#Some%20Head)\n")
    _page(tmp_path, "wiki/log.md", "# Log\n[old](concepts/a.md)\n")
    _page(tmp_path, "wiki/topics/t.md", _fm("music", "topic") + "- [A](../concepts/a.md)\n- [S](../sources/2024-s.md)\n")
    _page(tmp_path, "wiki/topics/f.md", _fm("history", "topic") + "- [B](../concepts/b.md)\n")
    _page(tmp_path, "wiki/synthesis/s.md", _fm("music", "synthesis") + "[A](../concepts/a.md)\n")
    _page(tmp_path, "wiki/concepts/a.md",
          _fm("music", "concept", "[wiki/sources/2024-s.md]")
          + "[B](b.md) ![f](../assets/x/f.png) `[code](b.md)`\n```\n[fenced](b.md)\n```\n")
    _page(tmp_path, "wiki/concepts/b.md",
          _fm("history", "concept", "\n  - wiki/sources/2024-s.md") + "## Some Head\n[A](a.md)\n")
    _page(tmp_path, "wiki/sources/2024-s.md",
          _fm("music", "source") + "[raw](../../raw/sources/my%20file.pdf)\n\n## 本來源更新的頁面\n\n- [A](../concepts/a.md)\n- [B](../concepts/b.md)\n")
    _page(tmp_path, "wiki/assets/x/f.png", "png")
    _page(tmp_path, "raw/sources/my file.pdf", "pdf")
    _page(tmp_path, "wiki/canvases/m.canvas", '{"nodes":[{"file":"wiki/concepts/a.md"}]}')
    _page(tmp_path, "wiki/meta/ledgers/source-ledger.json", json.dumps([
        {"sha256": "x", "locator": "raw/sources/my file.pdf", "page": "wiki/sources/2024-s.md",
         "independence_key": "wiki/sources/2024-s.md", "review_state": "active", "refresh_due": None}]))
    return tmp_path


def test_migration_moves_rewrites_and_keeps_lint_clean(flat: Path) -> None:
    plan = md.build_plan(flat, "2026-11-11")
    assert plan.moves["wiki/concepts/b.md"] == "wiki/history/concepts/b.md"
    md.apply_plan(flat, plan, use_git=False)
    a = (flat / "wiki/music/concepts/a.md").read_text(encoding="utf-8")
    assert "[B](../../history/concepts/b.md)" in a
    assert "![f](../../assets/x/f.png)" in a
    assert "`[code](b.md)`" in a and "[fenced](b.md)" in a
    assert "[wiki/music/sources/2024-s.md]" in a
    b = (flat / "wiki/history/concepts/b.md").read_text(encoding="utf-8")
    assert "- wiki/music/sources/2024-s.md" in b and "[A](../../music/concepts/a.md)" in b
    s = (flat / "wiki/music/sources/2024-s.md").read_text(encoding="utf-8")
    assert "(../../../raw/sources/my%20file.pdf)" in s
    assert "[B](../../history/concepts/b.md)" in s
    assert "(music/concepts/a.md)" in (flat / "wiki/overview.md").read_text(encoding="utf-8")
    assert "(history/concepts/b.md#Some%20Head)" in (flat / "wiki/overview.md").read_text(encoding="utf-8")
    assert "[old](concepts/a.md)" in (flat / "wiki/log.md").read_text(encoding="utf-8")
    root_index = (flat / "wiki/index.md").read_text(encoding="utf-8")
    assert "(music/index.md)" in root_index and "(history/index.md)" in root_index
    assert "[Overview](overview.md)" in root_index and "topics/" not in root_index
    music_index = (flat / "wiki/music/index.md").read_text(encoding="utf-8")
    assert "[T](topics/t.md)" in music_index and "[S](synthesis/s.md)" in music_index
    assert '"file":"wiki/music/concepts/a.md"' in (flat / "wiki/canvases/m.canvas").read_text(encoding="utf-8")
    record = json.loads((flat / "wiki/meta/ledgers/source-ledger.json").read_text(encoding="utf-8"))[0]
    assert record["page"] == record["independence_key"] == "wiki/music/sources/2024-s.md"
    assert not (flat / "wiki/concepts").exists()
    report = lint(flat)
    for key in ("broken_links", "broken_anchors", "backlink_mismatches", "index_missing"):
        assert report[key] == [], (key, report[key])
    assert report["page_count"]["layout"] == "domain"


def test_migration_refuses_pages_without_domain(tmp_path: Path) -> None:
    _page(tmp_path, "wiki/concepts/a.md", "---\ntype: concept\n---\nx\n")
    with pytest.raises(ValueError, match="domain"):
        md.plan_moves(tmp_path)


def test_migration_refuses_when_already_split(flat: Path) -> None:
    md.apply_plan(flat, md.build_plan(flat, "2026-11-11"), use_git=False)
    with pytest.raises(ValueError, match="已經"):
        md.plan_moves(flat)
