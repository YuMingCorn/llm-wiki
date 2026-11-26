"""Moves page ``status`` from draft | needs-review to five values (schema §3).

Usage (from the repo root):
  uv run python -I tools/status_migrate.py          # dry run
  uv run python -I tools/status_migrate.py --apply  # write the status line

Rules:
  needs-review                              -> disputed
  draft with sources, or any topic page     -> active
  draft without sources                     -> draft (unchanged)
  source page the ledger marks superseded   -> superseded (and superseded_by)
  active / disputed / superseded / archived -> unchanged
Anything else (missing or unknown status) is listed as an exception and left
alone. ``--apply`` rewrites only the ``status:`` line inside the frontmatter,
so ``updated`` and the body stay as they are. The one addition is a
``superseded_by:`` line under ``status:`` for a ledger-superseded page that
has none, because ``superseded`` requires it. The script never commits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import lifecycle  # noqa: E402
import lint  # noqa: E402

STATUS_LINE_RE = re.compile(r"^status:[ \t]*[^\r\n]*", re.M)


def decide(
    page: lint.Page, ledger_state: dict | None
) -> tuple[str | None, str | None]:
    """Returns (new status, superseded_by) for a page.

    The new status is None when the page needs no change or cannot be
    classified; ``plan`` tells those two cases apart.
    """
    fm = page.frontmatter or {}
    old = fm.get("status")
    if (ledger_state and ledger_state.get("reason") == "superseded"
            and page.type == "source" and old != "superseded"):
        return "superseded", ledger_state.get("superseded_by")
    if old == _common.LEGACY_DISPUTED:
        return "disputed", None
    if old == "draft" and (page.type == "topic" or page.sources):
        return "active", None
    return None, None


def plan(root: Path) -> dict:
    """Classifies every authored page.

    Returns:
        {"changes": [{page, old, new, superseded_by}], "unchanged": {status:
        count}, "exceptions": [{page, status, reason}]}.
    """
    wiki = lint.load(root)
    states = lifecycle.source_states(root)
    changes: list[dict] = []
    unchanged: Counter = Counter()
    exceptions: list[dict] = []
    for key, page in sorted(wiki.authored_pages().items()):
        if page.frontmatter is None:
            exceptions.append({"page": key, "status": None,
                               "reason": "frontmatter 讀不到"})
            continue
        old = page.frontmatter.get("status")
        new, by = decide(page, states.get(key))
        if new is not None:
            has_by = by or page.frontmatter.get("superseded_by")
            if new == "superseded" and not has_by:
                exceptions.append({
                    "page": key, "status": old,
                    "reason": "帳本標為 superseded，但沒有 superseded_by"})
                continue
            changes.append({"page": key, "old": old, "new": new,
                            "superseded_by": by})
        elif old in _common.STATUS_VALUES:
            unchanged[old] += 1
        else:
            exceptions.append({"page": key, "status": old,
                               "reason": "status 缺少或不是已知的值"})
    return {"changes": changes, "unchanged": dict(sorted(unchanged.items())),
            "exceptions": exceptions}


def rewrite(text: str, new: str, superseded_by: str | None = None) -> str:
    """Returns ``text`` with its frontmatter status line replaced.

    Raises:
        ValueError: The text has no frontmatter or no status line in it.
    """
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom):]
    match = _common.FRONTMATTER_RE.match(body)
    if not match:
        raise ValueError("沒有 frontmatter")
    block = match.group(1)
    line = STATUS_LINE_RE.search(block)
    if not line:
        raise ValueError("frontmatter 沒有 status 行")
    eol = "\r\n" if "\r\n" in body[:match.end()] else "\n"
    replacement = f"status: {new}"
    if superseded_by and not re.search(r"^superseded_by:", block, re.M):
        replacement += f"{eol}superseded_by: {superseded_by}"
    block = block[:line.start()] + replacement + block[line.end():]
    start, end = match.span(1)
    return bom + body[:start] + block + body[end:]


def apply(root: Path, result: dict) -> int:
    """Writes the planned changes; returns the number of files written."""
    written = 0
    for change in result["changes"]:
        path = root / change["page"]
        text = path.read_bytes().decode("utf-8")
        path.write_bytes(rewrite(text, change["new"],
                                 change["superseded_by"]).encode("utf-8"))
        written += 1
    return written


def render(result: dict, applied: bool) -> str:
    """Formats the plan as text: one line per change, then the counts."""
    lines = [f"{c['old']} -> {c['new']}\t{c['page']}"
             for c in result["changes"]]
    counts = Counter(f"{c['old']} -> {c['new']}" for c in result["changes"])
    lines += ["", "## 摘要", ""]
    lines += [f"- {k}：{v} 頁" for k, v in sorted(counts.items())]
    lines.append(f"- 會改的頁合計：{len(result['changes'])}")
    lines.append("- 不用改（已是新值）："
                 + (json.dumps(result["unchanged"], ensure_ascii=False)
                    if result["unchanged"] else "無"))
    lines.append(f"- 例外頁：{len(result['exceptions'])}")
    lines += [f"  - {e['page']}（status={e['status']}）：{e['reason']}"
              for e in result["exceptions"]]
    lines.append("")
    lines.append("已寫入。" if applied
                 else "dry-run：沒有寫任何檔案。加 --apply 才寫。")
    return "\n".join(lines)


def main() -> int:
    """Runs the dry run, or the migration with --apply."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--apply", action="store_true",
                        help="write the status lines (default: dry run)")
    args = parser.parse_args()
    if not (args.root / "wiki").is_dir():
        print(f"wiki/ not found under {args.root}", file=sys.stderr)
        return 2
    result = plan(args.root)
    if args.apply:
        apply(args.root, result)
    print(render(result, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
