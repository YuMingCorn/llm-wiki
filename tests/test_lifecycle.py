"""Tests for stale pages and superseded claims."""

import datetime as dt
import json
from pathlib import Path, PurePosixPath

import lifecycle
import ledger
from lint import Page, Wiki

OLD, NEW = "wiki/music/sources/2020-old.md", "wiki/music/sources/2024-new.md"
LATE = "wiki/music/sources/2021-late.md"
C = "wiki/music/concepts/c.md"


def _ledger(root: Path, records: list[dict]) -> None:
    path = root / ledger.SOURCE_LEDGER
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _page(key: str, sources: list[str], body: str = "", type_: str = "concept") -> Page:
    return Page(PurePosixPath(key), {"type": type_, "sources": sources}, body, [], None)


def test_stale_pages_name_the_reason(tmp_path: Path) -> None:
    _ledger(tmp_path, [
        {"page": OLD, "review_state": "superseded", "superseded_by": NEW, "refresh_due": "2000-01-01"},
        {"page": NEW, "review_state": "active", "refresh_due": None},
        {"page": LATE, "review_state": "active", "refresh_due": "2025-01-01"},
    ])
    wiki = Wiki(tmp_path, {
        C: _page(C, [OLD, NEW, LATE]),
        OLD: _page(OLD, [], type_="source"),
    }, {})
    found = lifecycle.stale_pages(wiki, dt.date(2026, 1, 1))
    assert found == [
        {"page": C, "source": OLD, "reason": "superseded", "superseded_by": NEW},
        {"page": C, "source": LATE, "reason": "refresh_due", "refresh_due": "2025-01-01"},
    ]
    assert lifecycle.stale_pages(wiki, dt.date(2024, 1, 1))[1:] == []


BODY = """# C

text

## 已被取代的說法

- 2026-11-01｜舊說法（見 [Old](../sources/2020-old.md) p.3）；新說法見 [New](../sources/2024-new.md#結果)
- 舊說法沒有日期（見 [Old](../sources/2020-old.md)）
- 2026-13-01｜日期錯
- 2099-01-01｜未來（見 [New](../sources/2024-new.md)）

## 相關頁面

- 2026-01-01｜不在小節裡，不檢查
"""


def test_superseded_claims_parse_dates_and_source_links() -> None:
    claims = lifecycle.superseded_claims(C, _page(C, [], BODY))
    assert len(claims) == 4
    assert claims[0]["date"] == "2026-11-01"
    assert claims[0]["sources"] == [OLD, NEW]


def test_check_supersession_flags_malformed_bullets(tmp_path: Path) -> None:
    wiki = Wiki(tmp_path, {C: _page(C, [], BODY)}, {})
    problems = [p["problem"] for p in lifecycle.check_supersession(wiki, dt.date(2026, 11, 15))]
    assert problems == ["missing_date", "bad_date", "no_source_link", "future_date"]
