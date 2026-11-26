import json
from pathlib import Path

import pytest

import move_pages as mp
from lint import lint


def _page(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(domain: str, page_type: str, sources: str = "[]") -> str:
    return (f"---\ntitle: x\ntype: {page_type}\ndomain: {domain}\n"
            f"sources: {sources}\n---\n")


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    _page(tmp_path, "wiki/index.md", "[music](music/index.md) [fin](history/index.md)\n")
    _page(tmp_path, "wiki/music/index.md", "[t](topics/t.md)\n")
    _page(tmp_path, "wiki/history/index.md", "[f](topics/f.md)\n")
    _page(tmp_path, "wiki/overview.md", "[a](music/concepts/a.md#Head%20One)\n")
    _page(tmp_path, "wiki/log.md", "# Log\n[old](music/concepts/a.md)\n")
    _page(tmp_path, "wiki/music/topics/t.md",
          _fm("music", "topic") + "- [A](../concepts/a.md)\n- [S](../sources/2024-s.md)\n")
    _page(tmp_path, "wiki/history/topics/f.md",
          _fm("history", "topic") + "- [B](../concepts/b.md)\n")
    _page(tmp_path, "wiki/music/concepts/a.md",
          _fm("music", "concept", "[wiki/music/sources/2024-s.md]")
          + "## Head One\n[B](../../history/concepts/b.md) ![f](../../assets/x/f.png)\n")
    _page(tmp_path, "wiki/history/concepts/b.md",
          _fm("history", "concept", "\n  - wiki/music/sources/2024-s.md") + "[A](../../music/concepts/a.md)\n")
    _page(tmp_path, "wiki/music/sources/2024-s.md",
          _fm("music", "source") + "[raw](../../../raw/sources/my%20file.pdf)\n\n"
          "## 本來源更新的頁面\n\n- [A](../concepts/a.md)\n- [B](../../history/concepts/b.md)\n")
    _page(tmp_path, "wiki/assets/x/f.png", "png")
    _page(tmp_path, "raw/sources/my file.pdf", "pdf")
    _page(tmp_path, "wiki/canvases/m.canvas", '{"nodes":[{"file":"wiki/music/concepts/a.md"}]}')
    _page(tmp_path, "wiki/meta/ledgers/source-ledger.json", json.dumps([
        {"sha256": "x", "locator": "raw/sources/my file.pdf", "page": "wiki/music/sources/2024-s.md",
         "independence_key": "wiki/music/sources/2024-s.md", "review_state": "active", "refresh_due": None}]))
    return tmp_path


def _clean(root: Path) -> None:
    report = lint(root)
    for key in ("broken_links", "broken_anchors", "backlink_mismatches", "index_missing"):
        assert report[key] == [], (key, report[key])


def test_rename_within_domain(wiki: Path) -> None:
    plan = mp.build_plan(wiki, {"wiki/music/concepts/a.md": "wiki/music/concepts/a2.md"})
    mp.apply_plan(wiki, plan, use_git=False)
    assert not (wiki / "wiki/music/concepts/a.md").exists()
    assert "[A](../concepts/a2.md)" in (wiki / "wiki/music/topics/t.md").read_text(encoding="utf-8")
    assert "[A](../../music/concepts/a2.md)" in (wiki / "wiki/history/concepts/b.md").read_text(encoding="utf-8")
    assert "(music/concepts/a2.md#Head%20One)" in (wiki / "wiki/overview.md").read_text(encoding="utf-8")
    assert "[old](music/concepts/a.md)" in (wiki / "wiki/log.md").read_text(encoding="utf-8")
    assert '"file":"wiki/music/concepts/a2.md"' in (wiki / "wiki/canvases/m.canvas").read_text(encoding="utf-8")
    assert plan.domain_changes == {}
    _clean(wiki)


def test_cross_domain_move_updates_domain_sources_and_ledger(wiki: Path) -> None:
    moves = mp.to_domain(["wiki/music/sources/2024-s.md"], "cross")
    plan = mp.build_plan(wiki, moves)
    mp.apply_plan(wiki, plan, use_git=False)
    s = (wiki / "wiki/cross/sources/2024-s.md").read_text(encoding="utf-8")
    assert "domain: cross" in s
    assert "(../../../raw/sources/my%20file.pdf)" in s
    assert "[A](../../music/concepts/a.md)" in s
    assert "[wiki/cross/sources/2024-s.md]" in (wiki / "wiki/music/concepts/a.md").read_text(encoding="utf-8")
    assert "- wiki/cross/sources/2024-s.md" in (wiki / "wiki/history/concepts/b.md").read_text(encoding="utf-8")
    record = json.loads((wiki / "wiki/meta/ledgers/source-ledger.json").read_text(encoding="utf-8"))[0]
    assert record["page"] == record["independence_key"] == "wiki/cross/sources/2024-s.md"
    assert plan.domain_changes == {"wiki/cross/sources/2024-s.md": "cross"}
    _clean(wiki)


def test_swap_two_pages(wiki: Path) -> None:
    a, b = "wiki/music/concepts/a.md", "wiki/history/concepts/b.md"
    other_a, other_b = "wiki/history/concepts/a.md", "wiki/music/concepts/b.md"
    mp.apply_plan(wiki, mp.build_plan(wiki, {a: other_a, b: other_b}), use_git=False)
    assert "domain: history" in (wiki / other_a).read_text(encoding="utf-8")
    assert "domain: music" in (wiki / other_b).read_text(encoding="utf-8")
    assert "[B](../../music/concepts/b.md)" in (wiki / other_a).read_text(encoding="utf-8")
    _clean(wiki)


@pytest.mark.parametrize("moves, message", [
    ({"wiki/music/concepts/missing.md": "wiki/music/concepts/z.md"}, "找不到來源"),
    ({"wiki/music/concepts/a.md": "wiki/music/sources/2024-s.md"}, "目的已存在"),
    ({"wiki/music/concepts/a.md": "wiki/elsewhere/a.md"}, "目的不在"),
    ({"wiki/music/concepts/a.md": "wiki/music/concepts/z.md",
      "wiki/history/concepts/b.md": "wiki/music/concepts/z.md"}, "同一個目的"),
])
def test_validation_rejects_unsafe_moves(wiki: Path, moves: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        mp.build_plan(wiki, moves)


def test_read_map_and_to_domain_parse_inputs() -> None:
    assert mp.read_map("# c\nwiki/music/concepts/a.md\twiki/music/concepts/b.md\n\n") == {
        "wiki/music/concepts/a.md": "wiki/music/concepts/b.md"}
    with pytest.raises(ValueError, match="第 1 行"):
        mp.read_map("only-one-column\n")
    assert mp.to_domain(["wiki/music/concepts/a.md"], "history") == {
        "wiki/music/concepts/a.md": "wiki/history/concepts/a.md"}
    with pytest.raises(ValueError):
        mp.to_domain(["wiki/index.md"], "music")


def test_to_domain_accepts_any_kebab_case_domain() -> None:
    assert mp.to_domain(["wiki/home-cooking/concepts/a.md"], "gardening") == {
        "wiki/home-cooking/concepts/a.md": "wiki/gardening/concepts/a.md"}
    for bad in ("Cooking", "concepts", "assets", "a_b"):
        with pytest.raises(ValueError, match="kebab-case"):
            mp.to_domain(["wiki/home-cooking/concepts/a.md"], bad)
