import json
from pathlib import Path

import pytest

import status_migrate as sm
from lint import Page, _frontmatter_problems, lint

ROOT = Path(".")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _page(type_: str, status: str, sources: str = "[wiki/music/sources/s.md]",
          extra: str = "", eol: str = "\n") -> str:
    lines = ["---", "title: T", f"type: {type_}", "domain: music", "tags: []",
             f"sources: {sources}", "created: 2026-11-01", "updated: 2026-11-02",
             f"status: {status}", *([extra] if extra else []), "---", "", "# T", "body"]
    return eol.join(lines) + eol


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    _write(tmp_path, "wiki/music/concepts/sourced.md", _page("concept", "draft"))
    _write(tmp_path, "wiki/music/concepts/unsourced.md", _page("concept", "draft", "[]"))
    _write(tmp_path, "wiki/music/concepts/disputed.md", _page("concept", "needs-review"))
    _write(tmp_path, "wiki/music/concepts/done.md", _page("concept", "active"))
    _write(tmp_path, "wiki/music/concepts/crlf.md", _page("concept", "draft", eol="\r\n"))
    _write(tmp_path, "wiki/music/concepts/weird.md", _page("concept", "bogus"))
    _write(tmp_path, "wiki/music/topics/t.md", _page("topic", "draft", "[]"))
    _write(tmp_path, "wiki/music/sources/s.md",
           _page("source", "draft", "[raw/sources/x.md]", "tier: primary"))
    return tmp_path


def _changes(result: dict) -> dict[str, tuple[str, str]]:
    return {c["page"].rsplit("/", 1)[1]: (c["old"], c["new"]) for c in result["changes"]}


def test_plan_follows_the_conversion_table(wiki_root: Path) -> None:
    result = sm.plan(wiki_root)
    assert _changes(result) == {
        "sourced.md": ("draft", "active"),
        "disputed.md": ("needs-review", "disputed"),
        "crlf.md": ("draft", "active"),
        "t.md": ("draft", "active"),
        "s.md": ("draft", "active"),
    }
    assert result["unchanged"] == {"active": 1, "draft": 1}
    assert [e["page"] for e in result["exceptions"]] == ["wiki/music/concepts/weird.md"]


def test_dry_run_writes_nothing(wiki_root: Path) -> None:
    before = {p: p.read_bytes() for p in wiki_root.rglob("*.md")}
    result = sm.plan(wiki_root)
    text = sm.render(result, applied=False)
    assert "needs-review -> disputed" in text and "dry-run" in text
    assert {p: p.read_bytes() for p in wiki_root.rglob("*.md")} == before


def test_apply_changes_only_the_status_line(wiki_root: Path) -> None:
    sm.apply(wiki_root, sm.plan(wiki_root))
    for name, eol in (("sourced.md", "\n"), ("crlf.md", "\r\n")):
        old = _page("concept", "draft", eol=eol)
        new = (wiki_root / "wiki/music/concepts" / name).read_bytes().decode("utf-8")
        assert new == old.replace("status: draft", "status: active")
    disputed = (wiki_root / "wiki/music/concepts/disputed.md").read_text(encoding="utf-8")
    assert "status: disputed" in disputed and "updated: 2026-11-02" in disputed
    # Pages left alone are byte-identical, and a second run has nothing to do.
    assert (wiki_root / "wiki/music/concepts/weird.md").read_bytes().decode() == _page(
        "concept", "bogus")
    assert sm.plan(wiki_root)["changes"] == []


def test_rewrite_keeps_bom_and_rejects_pages_without_status() -> None:
    text = "﻿" + _page("concept", "draft")
    assert sm.rewrite(text, "active").startswith("﻿---\n")
    with pytest.raises(ValueError):
        sm.rewrite("# no frontmatter\n", "active")
    with pytest.raises(ValueError):
        sm.rewrite("---\ntitle: x\n---\nbody\n", "active")


def test_body_line_that_looks_like_status_is_untouched() -> None:
    text = _page("concept", "draft").replace("body", "status: draft")
    assert sm.rewrite(text, "active").endswith("status: draft\n")


def test_ledger_superseded_source_gets_superseded_and_pointer(wiki_root: Path) -> None:
    ledger = [{"page": "wiki/music/sources/s.md", "review_state": "superseded",
               "superseded_by": "wiki/music/sources/new.md"}]
    _write(wiki_root, "wiki/meta/ledgers/source-ledger.json", json.dumps(ledger))
    result = sm.plan(wiki_root)
    assert _changes(result)["s.md"] == ("draft", "superseded")
    sm.apply(wiki_root, result)
    text = (wiki_root / "wiki/music/sources/s.md").read_text(encoding="utf-8")
    assert "status: superseded\nsuperseded_by: wiki/music/sources/new.md\n" in text


def test_ledger_superseded_without_successor_is_an_exception(wiki_root: Path) -> None:
    ledger = [{"page": "wiki/music/sources/s.md", "review_state": "superseded"}]
    _write(wiki_root, "wiki/meta/ledgers/source-ledger.json", json.dumps(ledger))
    result = sm.plan(wiki_root)
    assert "s.md" not in _changes(result)
    assert any(e["page"].endswith("s.md") for e in result["exceptions"])


def test_lint_flags_legacy_status_before_migration_and_not_after(wiki_root: Path) -> None:
    before = lint(wiki_root)
    legacy = {(r["page"].rsplit("/", 1)[1], r["to"]) for r in before["legacy_status"]}
    assert legacy == {("sourced.md", "active"), ("disputed.md", "disputed"),
                      ("crlf.md", "active"), ("t.md", "active"), ("s.md", "active")}
    assert not any("invalid status" in p for e in before["frontmatter_errors"]
                   for p in e["problems"] if "weird" not in e["page"])
    sm.apply(wiki_root, sm.plan(wiki_root))
    after = lint(wiki_root)
    assert after["legacy_status"] == []
    assert after["disputed"] == ["wiki/music/concepts/disputed.md"]


def test_lint_checks_the_new_values() -> None:
    def problems(status: str, sources: list, type_: str = "concept", **extra) -> list[str]:
        fm = {"title": "t", "type": type_, "domain": "music", "tags": [], "sources": sources,
              "created": "2026-11-01", "updated": "2026-11-01", "status": status, **extra}
        return _frontmatter_problems(Page(ROOT, fm, "", [], None))

    assert problems("active", ["wiki/music/sources/s.md"]) == []
    assert problems("draft", []) == []
    assert problems("disputed", ["wiki/music/sources/s.md"]) == []
    assert problems("archived", ["wiki/music/sources/s.md"]) == []
    assert problems("active", []) == ["active without sources"]
    assert problems("superseded", ["x"]) == ["superseded without superseded_by"]
    assert problems("superseded", ["x"], superseded_by="wiki/music/concepts/b.md") == []
    assert problems("active", [], type_="topic") == []
    assert problems("published", ["x"]) == ["invalid status: published"]
