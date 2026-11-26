"""Tests for retention (forgetting curve) and work coordination."""

import datetime as dt
import math
import os
import time
from pathlib import Path

import pytest

import retention
import work


def _page(root: Path, rel: str, type_: str, updated: str, body: str = "# x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: x\ntype: {type_}\ncreated: {updated}\nupdated: {updated}\n"
                    f"sources: []\n---\n{body}", encoding="utf-8")


C, Q = "wiki/music/concepts/c.md", "wiki/music/queries/2026-01-01-q.md"
TODAY = dt.date(2026, 11, 15)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _page(tmp_path, C, "concept", "2025-11-15")
    _page(tmp_path, Q, "query", "2026-09-16")
    return tmp_path


def test_retention_decays_by_type_and_touch_resets(root: Path) -> None:
    r = retention.Retention(root, TODAY)
    assert r.details(C)["retention"] == pytest.approx(math.exp(-365 / 365), abs=1e-3)
    assert r.details(Q)["retention"] == pytest.approx(math.exp(-60 / 60), abs=1e-3)
    retention.touch(root, [C, C], "query", TODAY - dt.timedelta(days=10))
    after = retention.Retention(root, TODAY).details(C)
    assert after["uses"] == 2 and after["last"] == "2026-11-05"
    assert after["retention"] == pytest.approx(math.exp(-10 / (365 * (1 + math.log(3)))), abs=1e-3)


def test_touch_rejects_unknown_pages_and_kinds(root: Path) -> None:
    with pytest.raises(ValueError):
        retention.touch(root, ["wiki/music/concepts/none.md"])
    with pytest.raises(ValueError):
        retention.touch(root, [C], "read")


def test_report_lists_faded_pages_faintest_first(root: Path) -> None:
    rows = retention.Retention(root, TODAY).report(below=0.5)
    assert [r["page"] for r in rows] == [Q, C] or [r["page"] for r in rows] == [C, Q]
    assert rows[0]["retention"] <= rows[-1]["retention"]


def test_promote_suggests_used_queries_and_stale_working_items(root: Path) -> None:
    retention.touch(root, [Q, Q, Q])
    inbox = root / ".llm-wiki/inbox"
    inbox.mkdir(parents=True)
    old = inbox / "old.pdf"
    old.write_bytes(b"x")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))
    (inbox / "new.pdf").write_bytes(b"x")
    hints = retention.promote(root)
    assert [q["page"] for q in hints["queries"]] == [Q]
    assert [w["path"] for w in hints["working"]] == [".llm-wiki/inbox/old.pdf"]


NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.UTC)


def test_claims_are_exclusive_until_expiry(tmp_path: Path) -> None:
    work.claim(tmp_path, ["wiki/index.md", "wiki/music/topics/a.md"], "session-a", 60, now=NOW)
    with pytest.raises(ValueError, match="session-a"):
        work.claim(tmp_path, ["wiki/music/topics/b.md", "wiki/index.md"], "session-b", now=NOW)
    # all-or-nothing: b.md was not claimed by the refused call
    assert [c["path"] for c in work.status(tmp_path, NOW)] == ["wiki/index.md", "wiki/music/topics/a.md"]
    later = NOW + dt.timedelta(minutes=61)
    work.claim(tmp_path, ["./wiki/index.md"], "session-b", now=later)
    assert {c["path"]: c["by"] for c in work.status(tmp_path, later)} == {"wiki/index.md": "session-b"}


def test_release_only_removes_own_claims(tmp_path: Path) -> None:
    work.claim(tmp_path, ["wiki/index.md"], "a", now=NOW)
    assert work.release(tmp_path, ["wiki/index.md"], "b") == 0
    assert work.release(tmp_path, ["wiki/index.md"], "a") == 1
    assert work.status(tmp_path, NOW) == []


def test_snapshot_changed_separates_own_edits(tmp_path: Path) -> None:
    _page(tmp_path, C, "concept", "2026-01-01")
    _page(tmp_path, Q, "query", "2026-01-01")
    work.snapshot(tmp_path, "batch")
    (tmp_path / C).write_text("changed", encoding="utf-8")
    (tmp_path / Q).write_text("mine", encoding="utf-8")
    _page(tmp_path, "wiki/music/concepts/new.md", "concept", "2026-01-01")
    result = work.changed(tmp_path, "batch", mine=[Q])
    assert result == {"others": {"added": ["wiki/music/concepts/new.md"], "deleted": [],
                                 "modified": [C]}, "mine": [Q], "allowed": []}
    with pytest.raises(ValueError):
        work.changed(tmp_path, "nope")


def test_changed_sees_writes_outside_wiki_and_honours_allow(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("a", encoding="utf-8")
    work.snapshot(tmp_path, "agent")
    (tmp_path / "tools" / "x.py").write_text("bb", encoding="utf-8")
    extract = tmp_path / ".llm-wiki" / "extract" / "2024-a"
    extract.mkdir(parents=True)
    (extract / "extraction.md").write_text("x", encoding="utf-8")
    other = tmp_path / ".llm-wiki" / "extract" / "2024-b"
    other.mkdir()
    (other / "extraction.md").write_text("x", encoding="utf-8")
    cache = tmp_path / ".llm-wiki" / "ocr"
    cache.mkdir()
    (cache / "page.txt").write_text("x", encoding="utf-8")
    result = work.changed(tmp_path, "agent", allow=[".llm-wiki/extract/2024-a/"])
    assert result["others"] == {"added": [".llm-wiki/extract/2024-b/extraction.md"],
                                "deleted": [], "modified": ["tools/x.py"]}
    assert result["allowed"] == [".llm-wiki/extract/2024-a/extraction.md"]


def test_claim_paths_are_normalised_and_inputs_checked(tmp_path: Path) -> None:
    work.claim(tmp_path, ["wiki/index.md"], "a", now=NOW)
    for alias in ("wiki/../wiki/index.md", "wiki\\index.md"):
        with pytest.raises(ValueError, match="已被認領"):
            work.claim(tmp_path, [alias], "b", now=NOW)
    with pytest.raises(ValueError, match="ttl"):
        work.claim(tmp_path, ["wiki/x.md"], "a", ttl_minutes=0, now=NOW)
    with pytest.raises(ValueError, match="快照名稱"):
        work.snapshot(tmp_path, "../x")


def test_malformed_claim_file_is_taken_over(tmp_path: Path) -> None:
    file = work._claim_file(tmp_path, "wiki/index.md")
    file.parent.mkdir(parents=True)
    file.write_text('{"path": "wiki/index.md"}', encoding="utf-8")
    assert work.claim(tmp_path, ["wiki/index.md"], "a", now=NOW)[0]["by"] == "a"


def test_claim_lost_to_a_locked_file_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work.claim(tmp_path, ["wiki/b.md"], "old", ttl_minutes=1, now=NOW)
    monkeypatch.setattr(work, "_remove", lambda file: False)
    later = NOW + dt.timedelta(minutes=5)
    with pytest.raises(ValueError, match="同時有人認領"):
        work.claim(tmp_path, ["wiki/a.md", "wiki/b.md"], "new", now=later)
    monkeypatch.undo()
    # a.md was rolled back; the old (expired) claim on b.md is untouched.
    assert work.status(tmp_path, NOW)[0]["by"] == "old"
    assert work._read(work._claim_file(tmp_path, "wiki/a.md")) is None
