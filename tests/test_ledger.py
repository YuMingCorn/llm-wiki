"""Tests for the source ledger."""

import datetime as dt
from pathlib import Path

import pytest

import ledger


def _source_page(root: Path, name: str, raw: str | None, tier: str = "primary") -> str:
    rel = f"wiki/sources/{name}.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = f"[{raw}]" if raw else "[]"
    path.write_text(f"---\ntier: {tier}\nsources: {sources}\n---\n\n# x\n", encoding="utf-8")
    return rel


def _raw(root: Path, name: str, data: bytes) -> str:
    rel = f"raw/sources/{name}"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for name, data in (("a", b"a"), ("b", b"b")):
        raw = _raw(tmp_path, f"{name}.pdf", data)
        ledger.add_source(tmp_path, raw, _source_page(tmp_path, name, raw))
    raw = _raw(tmp_path, "me.md", b"me")
    ledger.add_source(tmp_path, raw, _source_page(tmp_path, "me", raw, tier="personal"))
    return tmp_path


def test_check_reports_new_duplicate_and_changed(repo: Path) -> None:
    assert ledger.check_source(repo, _raw(repo, "c.pdf", b"c"))["status"] == "new"
    same = ledger.check_source(repo, _raw(repo, "copy.pdf", b"a"))
    assert same["status"] == "duplicate" and same["page"] == "wiki/sources/a.md"
    _raw(repo, "a.pdf", b"edited")
    assert ledger.check_source(repo, "raw/sources/a.pdf")["status"] == "changed"


def test_add_source_refuses_duplicates(repo: Path) -> None:
    raw = _raw(repo, "copy.pdf", b"a")
    with pytest.raises(ValueError, match="duplicate"):
        ledger.add_source(repo, raw, "wiki/sources/a.md")


def test_raw_files_of_one_page_share_the_independence_key(repo: Path) -> None:
    record = ledger.add_source(repo, _raw(repo, "a-zh.pdf", b"zh"), "wiki/sources/a.md")
    assert record["independence_key"] == "wiki/sources/a.md"


def test_rekey_updates_only_the_given_page(repo: Path) -> None:
    ledger.add_source(repo, _raw(repo, "a-zh.pdf", b"zh"), "wiki/sources/a.md")
    result = ledger.rekey(repo, "wiki/sources/a.md", "study-a")
    records = ledger._load(repo, ledger.SOURCE_LEDGER)
    assert result["updated"] == 2
    assert {r["independence_key"] for r in records if r["page"] == "wiki/sources/a.md"} == {"study-a"}
    assert {r["independence_key"] for r in records if r["page"] == "wiki/sources/b.md"} == {"wiki/sources/b.md"}
    with pytest.raises(ValueError, match="帳本沒有"):
        ledger.rekey(repo, "wiki/sources/gone.md")


def test_backfill_is_dry_run_by_default_and_flags_gaps(tmp_path: Path) -> None:
    raw = _raw(tmp_path, "a.pdf", b"a")
    _source_page(tmp_path, "a", raw)
    _source_page(tmp_path, "a2", _raw(tmp_path, "a2.pdf", b"a"))
    _source_page(tmp_path, "gone", "raw/sources/gone.pdf")
    _source_page(tmp_path, "none", None)
    result = ledger.backfill(tmp_path)
    assert result["added"] == 1 and not (tmp_path / ledger.SOURCE_LEDGER).exists()
    assert len(result["missing"]) == 2 and len(result["duplicates"]) == 1
    ledger.backfill(tmp_path, apply=True)
    assert ledger.backfill(tmp_path)["added"] == 0


def test_validate_flags_stale_sources_and_missing_pages(tmp_path: Path) -> None:
    raw = _raw(tmp_path, "a.pdf", b"a")
    ledger.add_source(tmp_path, raw, _source_page(tmp_path, "a", raw), refresh_due="2030-01-01")
    assert ledger.validate(tmp_path, dt.date(2029, 1, 1)) == {
        "violations": [], "stale_sources": [], "superseded_sources": []}
    assert ledger.validate(tmp_path, dt.date(2031, 1, 1))["stale_sources"] == ["wiki/sources/a.md"]
    (tmp_path / "wiki/sources/a.md").unlink()
    assert ledger.validate(tmp_path)["violations"][0]["problems"] == ["page not found"]


def test_supersede_marks_old_page_and_validate_lists_it(repo: Path) -> None:
    result = ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/b.md", "新版修正表 3", "2026-11-15")
    assert result == {"page": "wiki/sources/a.md", "superseded_by": "wiki/sources/b.md",
                      "updated": 1, "repointed": 0}
    record = next(r for r in ledger._load(repo, ledger.SOURCE_LEDGER) if r["page"] == "wiki/sources/a.md")
    assert (record["review_state"], record["superseded_on"], record["superseded_reason"]) == (
        "superseded", "2026-11-15", "新版修正表 3")
    report = ledger.validate(repo)
    assert report["violations"] == []
    assert report["superseded_sources"] == [{"page": "wiki/sources/a.md", "superseded_by": "wiki/sources/b.md"}]


@pytest.mark.parametrize("page, by, reason", [
    ("wiki/sources/a.md", "wiki/sources/a.md", "x"),
    ("wiki/sources/a.md", "wiki/sources/gone.md", "x"),
    ("wiki/sources/a.md", "wiki/sources/b.md", "  "),
])
def test_supersede_rejects_bad_input(repo: Path, page: str, by: str, reason: str) -> None:
    with pytest.raises(ValueError):
        ledger.supersede(repo, page, by, reason)


def test_supersede_refuses_a_successor_that_is_itself_superseded(repo: Path) -> None:
    ledger.supersede(repo, "wiki/sources/b.md", "wiki/sources/a.md", "x")
    with pytest.raises(ValueError, match="已被取代"):
        ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/b.md", "x")


def test_relocate_follows_superseded_by(repo: Path) -> None:
    ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/b.md", "x")
    ledger.relocate(repo, {"wiki/sources/b.md": "wiki/music/sources/b.md"})
    record = next(r for r in ledger._load(repo, ledger.SOURCE_LEDGER) if r["page"] == "wiki/sources/a.md")
    assert record["superseded_by"] == "wiki/music/sources/b.md"


def test_set_refresh_sets_and_clears(repo: Path) -> None:
    assert ledger.set_refresh(repo, "wiki/sources/a.md", "2020-01-01")["refresh_due"] == "2020-01-01"
    assert "wiki/sources/a.md" in ledger.validate(repo)["stale_sources"]
    ledger.set_refresh(repo, "wiki/sources/a.md", None)
    assert ledger.validate(repo)["stale_sources"] == []
    with pytest.raises(ValueError):
        ledger.set_refresh(repo, "wiki/sources/a.md", "2020-13-01")


def test_supersede_chain_repoints_to_newest(repo: Path) -> None:
    raw = _raw(repo, "c.pdf", b"c")
    ledger.add_source(repo, raw, _source_page(repo, "c", raw))
    ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/b.md", "x")
    result = ledger.supersede(repo, "wiki/sources/b.md", "wiki/sources/c.md", "y")
    assert result["repointed"] == 1
    a = next(r for r in ledger._load(repo, ledger.SOURCE_LEDGER) if r["page"] == "wiki/sources/a.md")
    assert a["superseded_by"] == "wiki/sources/c.md"
    assert ledger.validate(repo)["violations"] == []


def test_supersede_refuses_to_overwrite_and_bad_dates(repo: Path) -> None:
    ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/b.md", "x")
    with pytest.raises(ValueError, match="已被"):
        ledger.supersede(repo, "wiki/sources/a.md", "wiki/sources/me.md", "x")
    for bad in ("2099-01-01", "20261115", "2026-13-01"):
        with pytest.raises(ValueError, match="--date"):
            ledger.supersede(repo, "wiki/sources/me.md", "wiki/sources/b.md", "x", bad)


def test_validate_flags_pointer_to_superseded_page(repo: Path) -> None:
    records = ledger._load(repo, ledger.SOURCE_LEDGER)
    for r in records:
        if r["page"] in ("wiki/sources/a.md", "wiki/sources/b.md"):
            r.update(review_state="superseded")
    records[0]["superseded_by"] = "wiki/sources/b.md"
    records[1]["superseded_by"] = "wiki/sources/me.md"
    ledger._save(repo, ledger.SOURCE_LEDGER, records)
    problems = [p for v in ledger.validate(repo)["violations"] for p in v["problems"]]
    assert "superseded_by is itself superseded: wiki/sources/b.md" in problems
