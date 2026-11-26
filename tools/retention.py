"""Page retention (forgetting curve) and memory-tier promotion hints.

Retention follows Ebbinghaus: R = exp(-days / S), where ``days`` counts from
the last reinforcement and the stability S grows with every reinforcement:
S = BASE_DAYS[type] x (1 + ln(1 + n)). A reinforcement is an explicit use
recorded with ``touch`` (for example, a page cited in a /wiki-query answer), or the
page's own ``created``/``updated`` dates. Reading a page is not a
reinforcement: subagents read many pages for checks, which would reset every
curve.

Retention never deletes or edits a page. It only nudges search ranking
(``search.py --retention``) and lists faded pages, the "bottom drawer".

The access log lives in ``.llm-wiki/memory/access.jsonl`` (local, not in git).

Usage:
  uv run python -I tools/retention.py touch <page ...> [--kind query]
  uv run python -I tools/retention.py report [--below 0.3] [--json]
  uv run python -I tools/retention.py promote [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths  # noqa: E402
import lint  # noqa: E402

ACCESS_LOG = ".llm-wiki/memory/access.jsonl"
KINDS = ("query", "ingest", "review")
# Days until retention falls to 1/e with no reinforcement. Concepts and
# integrations hold durable knowledge; saved query answers are the most
# situational.
BASE_DAYS = {"concept": 365, "entity": 365, "synthesis": 365,
             "comparison": 270, "source": 180, "query": 60}
DEFAULT_BASE_DAYS = 180
FADED_BELOW = 0.3
# Promotion thresholds (memory tiers, schema §2).
QUERY_PROMOTE_USES = 3
WORKING_STALE_DAYS = 14


def _day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def touch(root: Path, pages: Sequence[str], kind: str = "query",
          today: dt.date | None = None) -> int:
    """Appends one reinforcement per existing content page.

    Raises:
        ValueError: The kind is unknown or a page is not a content page.
    """
    if kind not in KINDS:
        raise ValueError(f"kind 要是 {'、'.join(KINDS)} 之一")
    wiki = lint.load(root)
    keys = [Path(p).as_posix().removeprefix("./") for p in pages]
    missing = [k for k in keys if k not in wiki.pages]
    if missing:
        raise ValueError(f"不是內容頁：{'、'.join(missing)}")
    day = (today or dt.date.today()).isoformat()
    path = root / ACCESS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for key in keys:
            f.write(json.dumps({"date": day, "page": key, "kind": kind},
                               ensure_ascii=False) + "\n")
    return len(keys)


class Retention:
    """Computes retention for every content page of one wiki."""

    def __init__(self, root: Path, today: dt.date | None = None) -> None:
        """Loads pages and the access log."""
        self.wiki = lint.load(root)
        self.today = today or dt.date.today()
        self.events: dict[str, list[dt.date]] = {}
        path = root / ACCESS_LOG
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                day = _day(entry.get("date"))
                if day and entry.get("page"):
                    self.events.setdefault(entry["page"], []).append(day)

    def details(self, key: str) -> dict:
        """Returns {page, type, retention, last, uses} for one page."""
        page = self.wiki.pages[key]
        fm = page.frontmatter or {}
        dates = [d for d in (_day(fm.get("created")), _day(fm.get("updated")))
                 if d]
        uses = self.events.get(key, [])
        dates += uses
        last = max(dates) if dates else self.today
        days = max((self.today - last).days, 0)
        base = BASE_DAYS.get(str(page.type), DEFAULT_BASE_DAYS)
        stability = base * (1 + math.log1p(len(uses)))
        return {"page": key, "type": page.type,
                "retention": round(math.exp(-days / stability), 3),
                "last": last.isoformat(), "uses": len(uses)}

    def score(self, key: str) -> float:
        """Retention in [0, 1]; pages not in the wiki count as 1."""
        if key not in self.wiki.pages:
            return 1.0
        return self.details(key)["retention"]

    def report(self, below: float = FADED_BELOW) -> list[dict]:
        """Lists pages below the retention threshold, faintest first."""
        rows = [self.details(k) for k in self.wiki.pages]
        return sorted((r for r in rows if r["retention"] < below),
                      key=lambda r: (r["retention"], r["page"]))


def promote(root: Path, today: dt.date | None = None) -> dict:
    """Suggests moving knowledge up the memory tiers (schema §2 記憶分層).

    Returns:
        ``queries``: saved query pages used at least QUERY_PROMOTE_USES times
        or linked from two content pages; candidates for a synthesis or
        comparison page. ``working``: inbox and extract items older than
        WORKING_STALE_DAYS; ingest them or drop them.
    """
    today = today or dt.date.today()
    ret = Retention(root, today)
    incoming: dict[str, set[str]] = {}
    for key, page in ret.wiki.pages.items():
        for link in page.links:
            if link.resolved != key:
                incoming.setdefault(link.resolved, set()).add(key)
    queries = []
    for key, page in ret.wiki.pages.items():
        if page.type != "query":
            continue
        uses = len(ret.events.get(key, []))
        linked = len(incoming.get(key, set()))
        if uses >= QUERY_PROMOTE_USES or linked >= 2:
            queries.append({"page": key, "uses": uses, "linked_from": linked})
    working = []
    cutoff = time.time() - WORKING_STALE_DAYS * 86400
    for folder in (".llm-wiki/inbox", ".llm-wiki/extract"):
        base = root / folder
        if not base.is_dir():
            continue
        for item in sorted(base.iterdir()):
            if item.name.startswith(".") or item.name == "manifest.tsv":
                continue
            mtime = item.stat().st_mtime
            if mtime < cutoff:
                working.append({
                    "path": item.relative_to(root).as_posix(),
                    "modified": dt.date.fromtimestamp(mtime).isoformat()})
    return {"queries": queries, "working": working}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("touch")
    t.add_argument("pages", nargs="+")
    t.add_argument("--kind", default="query", choices=KINDS)
    r = sub.add_parser("report")
    r.add_argument("--below", type=float, default=FADED_BELOW)
    r.add_argument("--json", action="store_true")
    p = sub.add_parser("promote")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = _paths.ROOT
    try:
        if args.command == "touch":
            print(f"已登記 {touch(root, args.pages, args.kind)} 頁")
        elif args.command == "report":
            rows = Retention(root).report(args.below)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                for row in rows:
                    print(f"{row['retention']:.3f}\t{row['last']}\t"
                          f"{row['uses']}\t{row['page']}")
                print(f"共 {len(rows)} 頁低於 {args.below}")
        else:
            print(json.dumps(promote(root), ensure_ascii=False, indent=1))
    except ValueError as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
