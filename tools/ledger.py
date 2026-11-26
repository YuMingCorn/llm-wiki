"""Source ledger (schema §9), adapted from claude-obsidian.

Usage:
  ledger.py check <raw 檔>
  ledger.py add-source <raw 檔> <source 頁> [--independence-key K]
                       [--refresh-due YYYY-MM-DD]
  ledger.py backfill [--apply]
  ledger.py rekey <source 頁> [--independence-key K]
  ledger.py supersede <舊 source 頁> --by <新 source 頁> --reason "<原因>"
                      [--date YYYY-MM-DD]
  ledger.py refresh <source 頁> (--due YYYY-MM-DD | --clear)
  ledger.py validate
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

LEDGER_DIR = "wiki/meta/ledgers"
SOURCE_LEDGER = f"{LEDGER_DIR}/source-ledger.json"

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

Record = dict[str, Any]


def sha256_file(path: Path) -> str:
    """Returns the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(root: Path, rel: str) -> list[Record]:
    path = root / rel
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(root: Path, rel: str, records: list[Record]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_suffix(".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def _rel(root: Path, path: str) -> str:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root / resolved
    return resolved.resolve().relative_to(root.resolve()).as_posix()


def _frontmatter(path: Path) -> dict:
    return _common.split_frontmatter(path.read_text(encoding="utf-8"))[0] or {}


def check_source(root: Path, raw: str) -> Record:
    """Classifies a raw file against the source ledger.

    Args:
        root: Repository root.
        raw: Path of the raw file.

    Returns:
        A record whose status is `new`, `duplicate` (same bytes already
        recorded) or `changed` (same path, different bytes).
    """
    rel = _rel(root, raw)
    digest = sha256_file(root / rel)
    sources = _load(root, SOURCE_LEDGER)
    for record in sources:
        if record["sha256"] == digest:
            return {
                "status": "duplicate",
                "sha256": digest,
                "page": record["page"],
                "locator": record["locator"],
            }
    for record in sources:
        if record["locator"] == rel:
            return {
                "status": "changed",
                "sha256": digest,
                "page": record["page"],
                "locator": rel,
            }
    return {"status": "new", "sha256": digest, "locator": rel}


def _source_record(
    root: Path, rel: str, page: str, key: str | None, refresh_due: str | None
) -> Record:
    return {
        "sha256": sha256_file(root / rel),
        "locator": rel,
        "page": page,
        "independence_key": key or page,
        "review_state": "active",
        "refresh_due": refresh_due,
        "recorded": dt.date.today().isoformat(),
    }


def add_source(
    root: Path,
    raw: str,
    page: str,
    key: str | None = None,
    refresh_due: str | None = None,
) -> Record:
    """Records a raw file; refuses bytes that are already in the ledger.

    Raises:
        ValueError: If the page is missing or the file is not new.
    """
    rel, page_rel = _rel(root, raw), _rel(root, page)
    if not (root / page_rel).is_file():
        raise ValueError(f"找不到 source 頁：{page_rel}")
    state = check_source(root, rel)
    if state["status"] != "new":
        raise ValueError(f"{state['status']}：已對應 {state['page']}")
    sources = _load(root, SOURCE_LEDGER)
    record = _source_record(root, rel, page_rel, key, refresh_due)
    _save(root, SOURCE_LEDGER, [*sources, record])
    return record


def rekey(root: Path, page: str, key: str | None = None) -> Record:
    """Sets one independence key on every raw file of a source page.

    Args:
        root: Repository root.
        page: Source page whose records change.
        key: New key; defaults to the page path.

    Returns:
        The page, the key and how many records were updated.

    Raises:
        ValueError: If the ledger has no record for the page.
    """
    page_rel = _rel(root, page)
    sources = _load(root, SOURCE_LEDGER)
    records = [record for record in sources if record["page"] == page_rel]
    if not records:
        raise ValueError(f"帳本沒有這個 source 頁：{page_rel}")
    for record in records:
        record["independence_key"] = key or page_rel
    _save(root, SOURCE_LEDGER, sources)
    return {
        "page": page_rel,
        "independence_key": key or page_rel,
        "updated": len(records),
    }


def _page_records(sources: list[Record], page_rel: str) -> list[Record]:
    records = [record for record in sources if record["page"] == page_rel]
    if not records:
        raise ValueError(f"帳本沒有這個 source 頁：{page_rel}")
    return records


def supersede(
    root: Path, page: str, by: str, reason: str, date: str | None = None
) -> Record:
    """Marks a source page as superseded by a newer one (schema §9).

    The old records stay in the ledger; only their state changes, so the
    older claims remain readable but are treated as stale.

    Args:
        root: Repository root.
        page: The older source page.
        by: The newer source page that replaces it; must be in the ledger.
        reason: One sentence on what the newer source changes.
        date: Day of the decision; defaults to today.

    Returns:
        The page, its successor and how many records changed.

    Raises:
        ValueError: If either page is unknown, they are the same page, the
            reason is empty or the newer page is itself superseded.
    """
    page_rel, by_rel = _rel(root, page), _rel(root, by)
    if page_rel == by_rel:
        raise ValueError("來源不能取代自己")
    if not reason.strip():
        raise ValueError("要寫取代的原因")
    day = _parse_day(date, "--date") if date else dt.date.today()
    if day > dt.date.today():
        raise ValueError(f"--date 不能是未來的日期：{day}")
    sources = _load(root, SOURCE_LEDGER)
    successors = _page_records(sources, by_rel)
    if any(r.get("review_state") == "superseded" for r in successors):
        raise ValueError(f"{by_rel} 本身已被取代，請指向最新的來源")
    records = _page_records(sources, page_rel)
    if any(r.get("review_state") == "superseded" for r in records):
        current = records[0].get("superseded_by")
        raise ValueError(f"{page_rel} 已被 {current} 取代；要改取代者請先"
                         "回報使用者，不覆蓋舊紀錄")
    for record in records:
        record["review_state"] = "superseded"
        record["superseded_by"] = by_rel
        record["superseded_on"] = day.isoformat()
        record["superseded_reason"] = reason.strip()
    # Keep chains flat: pages that pointed at the old page now point at the
    # newest successor, so readers are never sent to a stale page.
    repointed = 0
    for record in sources:
        if record.get("superseded_by") == page_rel:
            record["superseded_by"] = by_rel
            repointed += 1
    _save(root, SOURCE_LEDGER, sources)
    return {"page": page_rel, "superseded_by": by_rel,
            "updated": len(records), "repointed": repointed}


def _parse_day(value: str, flag: str) -> dt.date:
    """Parses YYYY-MM-DD, naming the flag in the error."""
    if not (len(value) == 10 and value[4] == value[7] == "-"):
        raise ValueError(f"{flag} 要寫成 YYYY-MM-DD：{value}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{flag} 不是有效日期：{value}") from None


def set_refresh(root: Path, page: str, due: str | None) -> Record:
    """Sets or clears the refresh date of every record of a source page.

    Args:
        root: Repository root.
        page: Source page whose records change.
        due: New date (YYYY-MM-DD), or None to clear it.

    Returns:
        The page, the date and how many records changed.

    Raises:
        ValueError: If the page is unknown or the date is malformed.
    """
    page_rel = _rel(root, page)
    value = _parse_day(due, "--due").isoformat() if due else None
    sources = _load(root, SOURCE_LEDGER)
    records = _page_records(sources, page_rel)
    for record in records:
        record["refresh_due"] = value
    _save(root, SOURCE_LEDGER, sources)
    return {"page": page_rel, "refresh_due": value, "updated": len(records)}


def relocate(root: Path, moves: dict[str, str]) -> int:
    """Rewrites page paths after pages move (schema §8 migration).

    Both ``page`` and an ``independence_key`` that still equals the old page
    path follow the move; keys that point at raw files are left alone.

    Args:
        root: Repository root.
        moves: {old repo-relative page path: new path}.

    Returns:
        How many records changed.
    """
    sources = _load(root, SOURCE_LEDGER)
    changed = 0
    for record in sources:
        new = moves.get(record["page"])
        if new is None:
            continue
        if record.get("independence_key") == record["page"]:
            record["independence_key"] = new
        record["page"] = new
        changed += 1
    for record in sources:
        new = moves.get(record.get("superseded_by") or "")
        if new is not None:
            record["superseded_by"] = new
            changed += 1
    if changed:
        _save(root, SOURCE_LEDGER, sources)
    return changed


def backfill(root: Path, apply: bool = False) -> Record:
    """Records source pages through their frontmatter raw paths.

    Never extracts claims from page prose. Duplicates and missing files are
    reported, not guessed.

    Args:
        root: Repository root.
        apply: Writes the ledger when True; otherwise a dry run.

    Returns:
        Counts plus the added, skipped and duplicate paths.
    """
    sources = _load(root, SOURCE_LEDGER)
    known = {record["sha256"]: record for record in sources}
    added: list[Record] = []
    missing: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    for path in sorted((root / "wiki" / "sources").glob("*.md")):
        page = path.relative_to(root).as_posix()
        fields = _frontmatter(path)
        raws = [
            str(s)
            for s in fields.get("sources") or []
            if str(s).startswith("raw/")
        ]
        if not raws:
            missing.append(
                {"page": page, "reason": "frontmatter 沒有 raw 路徑"}
            )
        for rel in raws:
            if not (root / rel).is_file():
                missing.append({"page": page, "reason": f"找不到 {rel}"})
                continue
            record = _source_record(root, rel, page, None, None)
            if record["sha256"] in known:
                if known[record["sha256"]]["page"] != page:
                    duplicates.append(
                        {
                            "page": page,
                            "same_as": known[record["sha256"]]["page"],
                        }
                    )
                continue
            known[record["sha256"]] = record
            added.append(record)
    if apply and added:
        _save(root, SOURCE_LEDGER, [*sources, *added])
    return {
        "applied": apply,
        "added": len(added),
        "pages": [record["page"] for record in added],
        "missing": missing,
        "duplicates": duplicates,
    }


def _is_fresh(record: Record, today: dt.date) -> bool:
    due = record.get("refresh_due")
    return not due or dt.date.fromisoformat(str(due)) >= today


def validate(root: Path, today: dt.date | None = None) -> Record:
    """Checks the source ledger against the §9 rules.

    Args:
        root: Repository root.
        today: Date used for freshness; defaults to today.

    Returns:
        Violations and stale source pages.
    """
    today = today or dt.date.today()
    source_records = _load(root, SOURCE_LEDGER)
    violations: list[Record] = []
    for digest, count in Counter(r["sha256"] for r in source_records).items():
        if count > 1:
            violations.append(
                {
                    "ledger": "source",
                    "sha256": digest,
                    "problems": ["duplicate sha256"],
                }
            )
    pages = {record["page"] for record in source_records}
    superseded_pages = {
        r["page"] for r in source_records
        if r.get("review_state") == "superseded"
    }
    for record in source_records:
        problems = []
        if not (root / record["page"]).is_file():
            problems.append("page not found")
        if record.get("review_state") == "superseded":
            by = record.get("superseded_by")
            if not by:
                problems.append("superseded without superseded_by")
            elif by not in pages:
                problems.append(f"superseded_by not in ledger: {by}")
            elif by in superseded_pages:
                problems.append(f"superseded_by is itself superseded: {by}")
        if problems:
            violations.append(
                {"ledger": "source", "page": record["page"],
                 "problems": problems}
            )
    superseded: dict[str, str | None] = {
        r["page"]: r.get("superseded_by")
        for r in source_records
        if r.get("review_state") == "superseded"
    }
    return {
        "violations": violations,
        "stale_sources": sorted(
            {r["page"] for r in source_records if not _is_fresh(r, today)}
        ),
        "superseded_sources": [
            {"page": page, "superseded_by": by}
            for page, by in sorted(superseded.items())
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("raw")
    add = sub.add_parser("add-source")
    add.add_argument("raw")
    add.add_argument("page")
    add.add_argument("--independence-key")
    add.add_argument("--refresh-due")
    sub.add_parser("backfill").add_argument("--apply", action="store_true")
    rekey_parser = sub.add_parser("rekey")
    rekey_parser.add_argument("page")
    rekey_parser.add_argument("--independence-key")
    sup = sub.add_parser("supersede")
    sup.add_argument("page")
    sup.add_argument("--by", required=True)
    sup.add_argument("--reason", required=True)
    sup.add_argument("--date")
    ref = sub.add_parser("refresh")
    ref.add_argument("page")
    group = ref.add_mutually_exclusive_group(required=True)
    group.add_argument("--due")
    group.add_argument("--clear", action="store_true")
    sub.add_parser("validate")
    return parser


def _run(args: argparse.Namespace) -> object:
    root = args.root
    if args.command == "check":
        return check_source(root, args.raw)
    if args.command == "add-source":
        return add_source(
            root, args.raw, args.page, args.independence_key, args.refresh_due
        )
    if args.command == "backfill":
        return backfill(root, args.apply)
    if args.command == "rekey":
        return rekey(root, args.page, args.independence_key)
    if args.command == "supersede":
        return supersede(root, args.page, args.by, args.reason, args.date)
    if args.command == "refresh":
        return set_refresh(root, args.page, None if args.clear else args.due)
    return validate(root)


def main() -> int:
    """Runs the command line; exit 1 on a rejected write."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    try:
        result = _run(args)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
