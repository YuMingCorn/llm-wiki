"""Knowledge lifecycle checks: stale pages and superseded claims (schema §9).

A page goes stale when a source it cites is superseded or past its
``refresh_due`` in the source ledger. A claim that a newer source replaced
is not deleted: it moves into the page's ``## 已被取代的說法`` section as

    - 2026-11-15｜舊說法（見 [A](../sources/a.md)）；新說法見 [B](…)

Each bullet stays on one line. Only top-level bullets are claims; indented
sub-bullets are notes and are not checked.

lint imports this module; type hints name lint's classes only under
TYPE_CHECKING, so there is no import cycle.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote

import ledger

if TYPE_CHECKING:  # type hints only; lint imports this module
    from lint import Page, Wiki

SUPERSEDED_SECTION = "已被取代的說法"
_SECTION_RE = re.compile(
    rf"^##\s+{SUPERSEDED_SECTION}\s*$(.*?)(?=^#{{1,2}}\s|\Z)", re.M | re.S)
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*[｜|]")
_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?")
_SOURCE_DIR_RE = re.compile(r"^wiki/(?:[a-z]+/)?sources/")


def source_states(
    root: object, today: dt.date | None = None
) -> dict[str, dict]:
    """Maps each stale source page to why it is stale.

    Args:
        root: Repository root.
        today: Date used for ``refresh_due``; defaults to today.

    Returns:
        {source page: {"reason": "superseded" | "refresh_due", ...}}.
        A superseded entry carries ``superseded_by``; an overdue entry
        carries ``refresh_due``. Superseded wins when both apply.
    """
    today = today or dt.date.today()
    out: dict[str, dict] = {}
    for record in ledger._load(root, ledger.SOURCE_LEDGER):
        page = record["page"]
        if record.get("review_state") == "superseded":
            out[page] = {"reason": "superseded",
                         "superseded_by": record.get("superseded_by")}
        elif page not in out and not ledger._is_fresh(record, today):
            out[page] = {"reason": "refresh_due",
                         "refresh_due": record.get("refresh_due")}
    return out


def stale_pages(wiki: Wiki, today: dt.date | None = None) -> list[dict]:
    """Lists content pages that cite a stale source in ``sources``.

    Source pages themselves are listed by the ledger check, not here.
    """
    states = source_states(wiki.root, today)
    out = []
    for key, page in wiki.pages.items():
        if page.type == "source":
            continue
        for source in page.sources:
            if source in states:
                out.append({"page": key, "source": source, **states[source]})
    return out


def superseded_claims(key: str, page: Page) -> list[dict]:
    """Parses the bullets of a page's superseded-claims section.

    Returns:
        One dict per bullet: {date, text, sources} where ``sources`` are the
        repo-relative source pages it links. ``date`` is None when missing.
    """
    match = _SECTION_RE.search(page.body)
    if not match:
        return []
    page_dir = posixpath.dirname(key)
    out = []
    for line in match.group(1).split("\n"):
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        text = bullet.group(1)
        date = _DATE_RE.match(text)
        targets = {
            posixpath.normpath(posixpath.join(
                page_dir, unquote(t.split("#", 1)[0])))
            for t in _LINK_RE.findall(text)
            if not t.startswith(("http://", "https://", "#"))
        }
        out.append({
            "date": date.group(1) if date else None,
            "text": text,
            "sources": sorted(t for t in targets if _SOURCE_DIR_RE.match(t)),
        })
    return out


def check_supersession(
    wiki: Wiki, today: dt.date | None = None
) -> list[dict]:
    """Lists malformed bullets in ``## 已被取代的說法`` sections.

    Problems: missing_date, bad_date, future_date, no_source_link.
    """
    today = today or dt.date.today()
    out = []
    for key, page in wiki.pages.items():
        for claim in superseded_claims(key, page):
            base = {"page": key, "claim": claim["text"][:60]}
            if claim["date"] is None:
                out.append({**base, "problem": "missing_date"})
            else:
                try:
                    if dt.date.fromisoformat(claim["date"]) > today:
                        out.append({**base, "problem": "future_date"})
                except ValueError:
                    out.append({**base, "problem": "bad_date"})
            if not claim["sources"]:
                out.append({**base, "problem": "no_source_link"})
    return out
