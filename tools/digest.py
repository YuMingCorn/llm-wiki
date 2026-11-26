"""Short digest of what changed in the wiki, for a human to catch up.

Compares the content pages at an earlier commit with HEAD and prints
Markdown: new source pages with their first conclusions, other new pages,
claims newly moved to ``## 已被取代的說法``, pages newly marked
``disputed``, and the titles of other modified pages. Every list is
capped, because the point is to read less.

The default start is the commit recorded by the last ``--mark`` in
``.llm-wiki/memory/digest-mark.json`` (local, not in git). Without a usable
mark it starts from the newest commit older than FALLBACK_DAYS days, or from
the first commit.

The tool reads git and prints; ``--mark`` and ``--save`` only write under
``.llm-wiki/``. It never writes ``wiki/``.

Usage:
  uv run python -I tools/digest.py [--since REV] [--limit 2] [--mark] [--save]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402
import _paths  # noqa: E402
import lifecycle  # noqa: E402

MARK = ".llm-wiki/memory/digest-mark.json"
SAVE_DIR = ".llm-wiki/digests"
FALLBACK_DAYS = 7
DEFAULT_LIMIT = 2
MAX_ITEMS = 10
MAX_CHARS = 120
# New source pages head their findings 結論; older pages use 可驗證的主張.
CONCLUSION_SECTIONS = ("結論", "可驗證的主張")
_CONTENT_RE = re.compile(
    r"^wiki/(?:[a-z0-9]+(?:-[a-z0-9]+)*/)?"
    r"(?:sources|concepts|entities|synthesis|comparisons|queries)/[^/]+\.md$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_ITEM_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+(.*)$")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _sections(body: str) -> dict[str, str]:
    """Maps each H2 heading to the text under it."""
    marks = list(_H2_RE.finditer(body))
    ends = [m.start() for m in marks[1:]] + [len(body)] if marks else []
    return {m.group(1): body[m.end():end]
            for m, end in zip(marks, ends, strict=True)}


def _items(text: str) -> list[str]:
    """Top-level list items of a section, one line each."""
    out = []
    for line in text.splitlines():
        match = _ITEM_RE.match(line)
        if match and match.group(1).strip():
            out.append(match.group(1).strip())
    return out


def _findings(text: str) -> list[str]:
    """List items of a section, or its paragraphs when it has no list.

    Many source pages write 結論 as paragraphs. Subheadings, tables, images
    and formulas are skipped; each paragraph is joined into one line.
    """
    items = _items(text)
    if items:
        return items
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].startswith(("#", "|", "!", "$$", ">", "```")):
            continue
        out.append(" ".join(lines))
    return out


def _plain(text: str) -> str:
    """Drops link targets and bold marks, and shortens long lines."""
    text = _MD_LINK_RE.sub(r"\1", text).replace("**", "")
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1] + "…"


def _parse(text: str | None) -> tuple[dict, dict[str, str]]:
    if text is None:
        return {}, {}
    fm, body = _common.split_frontmatter(text)
    return fm or {}, _sections(body)


def _show(root: Path, rev: str, path: str) -> str | None:
    proc = _common.run_git(root, "show", f"{rev}:{path}")
    return proc.stdout if proc.returncode == 0 else None


def _commit_exists(root: Path, rev: str) -> bool:
    proc = _common.run_git(root, "rev-parse", "--verify", "--quiet",
                           f"{rev}^{{commit}}")
    return proc.returncode == 0


def resolve_since(root: Path, since: str | None) -> tuple[str, str]:
    """Picks the start commit.

    Returns:
        (full hash, how it was chosen).

    Raises:
        ValueError: ``since`` was given and is not a commit.
    """
    if since:
        if not _commit_exists(root, since):
            raise ValueError(f"找不到 commit：{since}")
        return _common.git(root, "rev-parse", since).strip(), "指定"
    mark = root / MARK
    try:
        head = json.loads(mark.read_text(encoding="utf-8")).get("head")
    except (OSError, json.JSONDecodeError, AttributeError):
        head = None
    if head and _commit_exists(root, head):
        return head, "上次 --mark"
    older = _common.git(root, "rev-list", "-1",
                        f"--before={FALLBACK_DAYS}.days", "HEAD").strip()
    if older:
        return older, f"沒有可用的標記，從 {FALLBACK_DAYS} 天前開始"
    first = _common.git(root, "rev-list", "--max-parents=0", "HEAD")
    return first.split()[-1], "沒有可用的標記，從第一個 commit 開始"


def build(root: Path, since: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Collects the changes between ``since`` and HEAD.

    Returns:
        {"since", "head", "commits", "sources": [{page, title, conclusions,
        more}], "new_pages": [{page, title, type}], "superseded": [{page,
        title, claim}], "disputed": [{page, title}], "modified":
        [{page, title}], "renamed": [{page, title, from}] (moved without
        content changes), "deleted": [paths]}.
    """
    head = _common.git(root, "rev-parse", "HEAD").strip()
    commits = int(_common.git(root, "rev-list", "--count",
                              f"{since}..HEAD").strip() or 0)
    status = _common.git(root, "diff", "--name-status", "-M", since, "HEAD",
                         "--", "wiki/")
    out: dict = {"since": since, "head": head, "commits": commits,
                 "sources": [], "new_pages": [], "superseded": [],
                 "disputed": [], "modified": [], "renamed": [],
                 "deleted": []}
    for line in status.splitlines():
        parts = line.split("\t")
        code, path = parts[0][0], parts[-1]
        old_path = parts[1] if code == "R" else path
        if code == "D":
            if _CONTENT_RE.match(path):
                out["deleted"].append(path)
            continue
        if not _CONTENT_RE.match(path):
            continue
        new_text = _show(root, "HEAD", path)
        new_fm, new_sec = _parse(new_text)
        old_text = None if code == "A" else _show(root, since, old_path)
        old_fm, old_sec = _parse(old_text)
        row = {"page": path, "title": str(new_fm.get("title") or path)}
        if old_text is None:
            kind = str(new_fm.get("type") or "")
            if kind == "source":
                found = next((new_sec[s] for s in CONCLUSION_SECTIONS
                              if s in new_sec), None)
                items = _findings(found or "")
                out["sources"].append({**row, "conclusions": [
                    _plain(i) for i in items[:limit]],
                    "more": max(len(items) - limit, 0),
                    "has_section": found is not None})
            else:
                out["new_pages"].append({**row, "type": kind})
        elif code == "R" and old_text == new_text:
            out["renamed"].append({**row, "from": old_path})
            continue
        else:
            out["modified"].append(row)
        section = lifecycle.SUPERSEDED_SECTION
        before = set(_items(old_sec.get(section, "")))
        for claim in _items(new_sec.get(section, "")):
            if claim not in before:
                out["superseded"].append({**row, "claim": _plain(claim)})
        if (new_fm.get("status") in _common.DISPUTED_STATUSES
                and old_fm.get("status") not in _common.DISPUTED_STATUSES):
            out["disputed"].append(row)
    return out


def _capped(rows: list, render: Callable) -> list[str]:
    lines = [render(r) for r in rows[:MAX_ITEMS]]
    if len(rows) > MAX_ITEMS:
        lines.append(f"- 另有 {len(rows) - MAX_ITEMS} 項")
    return lines


def render(data: dict, how: str, today: dt.date | None = None) -> str:
    """Formats the digest as Markdown."""
    today = today or dt.date.today()
    lines = [f"# wiki 最近新增（{today.isoformat()}）", "",
             f"範圍：`{data['since'][:7]}`..`{data['head'][:7]}`，"
             f"{data['commits']} 個 commit（起點：{how}）", ""]
    if not any(data[k] for k in ("sources", "new_pages", "superseded",
                                 "disputed", "modified", "renamed",
                                 "deleted")):
        return "\n".join(lines + ["內容頁沒有變動。", ""])
    if data["sources"]:
        lines += [f"## 新來源（{len(data['sources'])}）", ""]
        for s in data["sources"][:MAX_ITEMS]:
            lines.append(f"- **{s['title']}**（`{s['page']}`）")
            lines += [f"  - {c}" for c in s["conclusions"]]
            if s["more"]:
                lines.append(f"  - 另有 {s['more']} 條結論")
            if not s["has_section"]:
                lines.append("  - 沒有「結論」小節")
            elif not s["conclusions"] and not s["more"]:
                lines.append("  - 「結論」小節沒有可摘錄的內容")
        if len(data["sources"]) > MAX_ITEMS:
            lines.append(f"- 另有 {len(data['sources']) - MAX_ITEMS} 份來源")
        lines.append("")
    if data["new_pages"]:
        lines += [f"## 新頁（{len(data['new_pages'])}）", ""]
        lines += _capped(data["new_pages"], lambda r: (
            f"- {r['title']}（{r['type'] or '不明類型'}，`{r['page']}`）"))
        lines.append("")
    if data["superseded"]:
        lines += [f"## 被取代的說法（{len(data['superseded'])}）", ""]
        lines += _capped(data["superseded"],
                         lambda r: f"- {r['title']}：{r['claim']}")
        lines.append("")
    if data["disputed"]:
        lines += [f"## 新標成 disputed 的頁（{len(data['disputed'])}）",
                  "", "來源之間說法不一致，讀這幾頁時要看各方的說法。", ""]
        lines += _capped(data["disputed"],
                         lambda r: f"- {r['title']}（`{r['page']}`）")
        lines.append("")
    if data["modified"]:
        titles = [r["title"] for r in data["modified"]]
        shown = "、".join(titles[:MAX_ITEMS])
        rest = (f" 等 {len(titles)} 頁" if len(titles) > MAX_ITEMS else "")
        lines += [f"## 有修改的頁（{len(titles)}）", "", shown + rest, ""]
    if data["renamed"]:
        lines += [f"## 只改名或搬移的頁（{len(data['renamed'])}）", ""]
        lines += _capped(data["renamed"],
                         lambda r: f"- `{r['from']}` → `{r['page']}`")
        lines.append("")
    if data["deleted"]:
        lines += [f"## 刪除的頁（{len(data['deleted'])}）", ""]
        lines += _capped(data["deleted"], lambda p: f"- `{p}`")
        lines.append("")
    return "\n".join(lines)


def mark(root: Path, head: str) -> None:
    """Records head as the start of the next digest."""
    path = root / MARK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"head": head,
                                "date": dt.date.today().isoformat()}),
                    encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="start commit (default: last --mark)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="conclusions shown per new source")
    ap.add_argument("--mark", action="store_true",
                    help="start the next digest at HEAD")
    ap.add_argument("--save", action="store_true",
                    help=f"also write the digest to {SAVE_DIR}/")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = _paths.ROOT
    try:
        since, how = resolve_since(root, args.since)
        data = build(root, since, max(args.limit, 0))
    except (ValueError, RuntimeError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    text = render(data, how)
    print(json.dumps(data, ensure_ascii=False, indent=1) if args.json
          else text)
    if args.save:
        folder = root / SAVE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{dt.date.today().isoformat()}-{data['head'][:7]}.md"
        path.write_text(text, encoding="utf-8")
        print(f"已存：{path.relative_to(root).as_posix()}", file=sys.stderr)
    if args.mark:
        mark(root, data["head"])
        print(f"下次摘要從 {data['head'][:7]} 開始", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
