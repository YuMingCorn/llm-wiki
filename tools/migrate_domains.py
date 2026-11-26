"""Moves the flat wiki into per-domain directories (schema §8).

Usage (from the repo root):
  uv run python -I tools/migrate_domains.py          # dry run: print the plan
  uv run python -I tools/migrate_domains.py --apply  # move, rewrite, git mv

Content pages and topic pages move from ``wiki/<type-dir>/`` to
``wiki/<domain>/<type-dir>/`` according to their frontmatter ``domain``.
Every relative Markdown link in wiki pages is rewritten for the new locations
(``wiki/log.md`` is append-only and left alone), frontmatter ``sources`` paths
and canvas ``file`` paths are updated, the source ledger is relocated, and the
root index is split into one sub-index per domain. ``wiki/assets/``,
``wiki/meta/`` and ``wiki/canvases/`` stay where they are.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import _paths  # noqa: E402
import ledger  # noqa: E402

TYPE_DIRS = _common.PAGE_TYPE_DIRS
INDEX = "wiki/index.md"
LOG = "wiki/log.md"
SKIP_PREFIXES = ("wiki/assets/", "wiki/meta/")
CANVAS_DIR = "wiki/canvases"
LINK_RE = re.compile(
    r'(!?\[(?:[^\]\\]|\\.)*\]\()(\s*)(<[^>]*>|[^\s)]+)((?:\s+"[^"]*")?\s*\))')
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")
_SAFE = "/-_.~!$&'()*+,;=:@"
H2_RE = re.compile(r"^## (.+?)\s*$")
SUB_INDEX_NOTE = ("本領域的主題目錄。主題頁列出成員與一句話摘要，"
                  "規則見 schema §2、§8。回到[總目錄](../index.md)。")
OLD_ROOT_NOTE = "領域與主題目錄。主題頁列出成員與一句話摘要，規則見 schema §2。"
NEW_ROOT_NOTE = "領域目錄。各領域的子 index 列出主題頁，規則見 schema §2、§8。"


@dataclass
class Plan:
    """What --apply will do; the dry run prints it."""

    moves: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    sub_indexes: dict[str, str] = field(default_factory=dict)
    root_index: str = ""
    canvases: dict[str, str] = field(default_factory=dict)
    link_changes: int = 0


def _read(root: Path, rel: str) -> str:
    with (root / rel).open(encoding="utf-8", newline="") as f:
        return f.read()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


def plan_moves(root: Path) -> dict[str, str]:
    """Maps each flat content or topic page to its domain directory.

    Raises:
        ValueError: A page has no valid domain, or the wiki is already split.
    """
    if _common.discover_domains(root):
        raise ValueError("wiki 已經依領域分目錄，不需要搬遷")
    moves: dict[str, str] = {}
    errors = []
    for type_dir in TYPE_DIRS:
        for file in sorted((root / "wiki" / type_dir).glob("*.md")):
            rel = file.relative_to(root).as_posix()
            fm, _ = _common.split_frontmatter(_read(root, rel))
            domain = (fm or {}).get("domain")
            if not _common.is_domain_name(domain):
                errors.append(f"{rel}：domain={domain!r}")
                continue
            moves[rel] = f"wiki/{domain}/{type_dir}/{file.name}"
    if errors:
        raise ValueError("以下頁面沒有有效的 domain：" + "；".join(errors))
    return moves


def _encode(new_path: str, original: str) -> str:
    """Encodes a rewritten path in the same style as the original link."""
    if "%" in original:
        return quote(new_path, safe=_SAFE)
    return new_path.replace(" ", "%20")


def rewrite_links(text: str, old_rel: str, new_rel: str,
                  moves: dict[str, str], root: Path) -> tuple[str, int]:
    """Rewrites relative links for a page that moves from old_rel to new_rel.

    A link is rewritten when its target moves or the page itself moves.
    Targets that do not exist are left untouched, so broken links stay visible
    to lint instead of being silently "fixed" into another broken path.

    Returns:
        The new text and the number of rewritten links.
    """
    old_dir, new_dir = posixpath.dirname(old_rel), posixpath.dirname(new_rel)
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group(3)
        wrapped = target.startswith("<") and target.endswith(">")
        bare = target[1:-1] if wrapped else target
        if bare.startswith(EXTERNAL_PREFIXES):
            return m.group(0)
        path_part, sep, fragment = bare.partition("#")
        if not path_part:
            return m.group(0)
        resolved = posixpath.normpath(
            posixpath.join(old_dir, unquote(path_part)))
        dest = moves.get(resolved, resolved)
        if dest == resolved and new_dir == old_dir:
            return m.group(0)
        if resolved not in moves and not (root / resolved).exists():
            return m.group(0)
        rel = posixpath.relpath(dest, new_dir)
        new_target = _encode(rel, path_part) + (sep + fragment if sep else "")
        if new_target == bare:
            return m.group(0)
        count += 1
        if wrapped:
            new_target = f"<{new_target}>"
        return m.group(1) + m.group(2) + new_target + m.group(4)

    out_lines = []
    fence = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0]:
                fence = None
            out_lines.append(line)
            continue
        if marker:
            fence = marker.group(1)
            out_lines.append(line)
            continue
        out_lines.append(_sub_outside_code(line, _sub))
    return "\n".join(out_lines), count


def _sub_outside_code(
        line: str, sub: Callable[[re.Match[str]], str]) -> str:
    """Applies the link substitution only outside inline code spans."""
    parts = re.split(r"(`+[^`]*`+)", line)
    return "".join(
        part if part.startswith("`") else LINK_RE.sub(sub, part)
        for part in parts)


def rewrite_frontmatter_sources(text: str, moves: dict[str, str]) -> str:
    """Replaces moved repo-relative paths inside the frontmatter block."""
    match = _common.FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not match:
        return text
    offset = len(text) - len(text.lstrip("﻿"))
    start, end = offset + match.start(1), offset + match.end(1)
    block = text[start:end]
    for old, new in moves.items():
        block = re.sub(
            rf"(?<![\w/.-]){re.escape(old)}(?![\w/.-])", new, block)
    return text[:start] + block + text[end:]


def _section_domain(lines: list[str], index_dir: str,
                    moves: dict[str, str]) -> str | None:
    for line in lines:
        for m in LINK_RE.finditer(line):
            target = m.group(3).strip("<>").split("#", 1)[0]
            resolved = posixpath.normpath(
                posixpath.join(index_dir, unquote(target)))
            dest = moves.get(resolved)
            if dest:
                return dest.split("/")[1]
    return None


def split_index(text: str, moves: dict[str, str], root: Path,
                today: str) -> tuple[str, dict[str, str]]:
    """Splits the flat root index into a root list and per-domain indexes.

    Each ``##`` section whose links point at pages of one domain moves into
    ``wiki/<domain>/index.md``; other sections (such as Overview) stay.

    Returns:
        The new root index text and {sub-index path: text}.
    """
    fm_match = _common.FRONTMATTER_RE.match(text)
    head = text[:fm_match.end()] if fm_match else ""
    body = text[fm_match.end():] if fm_match else text
    head = re.sub(r"(?m)^updated: .*$", f"updated: {today}", head)
    lines = body.split("\n")
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        h2 = H2_RE.match(line)
        if h2:
            sections.append((h2.group(1), [line]))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    keep: list[str] = []
    by_domain: dict[str, list[tuple[str, list[str]]]] = {}
    for title, sec in sections:
        domain = _section_domain(sec, "wiki", moves)
        if domain is None:
            keep.extend(sec)
        else:
            by_domain.setdefault(domain, []).append((title, sec))
    subs: dict[str, str] = {}
    entries = []
    for domain in sorted(by_domain):
        rel = f"wiki/{domain}/index.md"
        titles = [t for t, _ in by_domain[domain]]
        sec_lines: list[str] = []
        for _, sec in by_domain[domain]:
            body_lines = sec[1:]
            sec_lines.extend(body_lines)
        sec_text = "\n".join(sec_lines).strip("\n")
        sec_text, _ = rewrite_links(sec_text, INDEX, rel, moves, root)
        sec_text = re.sub(r"(?m)^### ", "## ", sec_text)
        title = "、".join(titles)
        subs[rel] = (
            "---\n"
            f"title: {title} Index\n"
            "type: index\n"
            f"domain: {domain}\n"
            "tags: [llm-wiki]\n"
            f"updated: {today}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"> {SUB_INDEX_NOTE}\n\n"
            f"{sec_text}\n")
        count = sum(1 for line in sec_lines if line.startswith("- ["))
        entries.append(f"- [{title}]({domain}/index.md) — {count} 項")
    pre = "\n".join(preamble).rstrip("\n")
    pre = pre.replace(OLD_ROOT_NOTE, NEW_ROOT_NOTE)
    kept = "\n".join(keep).strip("\n")
    root_text = (head + pre + "\n\n" + (kept + "\n\n" if kept else "")
                 + "## 領域\n" + "\n".join(entries) + "\n")
    return root_text, subs


def build_plan(root: Path, today: str) -> Plan:
    """Computes every move and rewritten text without touching the disk."""
    plan = Plan(moves=plan_moves(root))
    for file in sorted((root / "wiki").rglob("*.md")):
        rel = file.relative_to(root).as_posix()
        if rel == LOG or rel.startswith(SKIP_PREFIXES):
            continue
        text = _read(root, rel)
        if rel == INDEX:
            plan.root_index, plan.sub_indexes = split_index(
                text, plan.moves, root, today)
            continue
        new_rel = plan.moves.get(rel, rel)
        new_text, count = rewrite_links(text, rel, new_rel, plan.moves, root)
        new_text = rewrite_frontmatter_sources(new_text, plan.moves)
        plan.link_changes += count
        if new_text != text or new_rel != rel:
            plan.texts[new_rel] = new_text
    canvas_dir = root / CANVAS_DIR
    canvases = (sorted(canvas_dir.glob("*.canvas"))
                if canvas_dir.is_dir() else [])
    for file in canvases:
        rel = file.relative_to(root).as_posix()
        text = _read(root, rel)
        new = text
        for old, dest in plan.moves.items():
            new = new.replace(f'"file":"{old}"', f'"file":"{dest}"')
        if new != text:
            plan.canvases[rel] = new
    return plan


def apply_plan(root: Path, plan: Plan, use_git: bool = True) -> None:
    """Moves the files, writes the rewritten texts and relocates the ledger."""
    for old, new in plan.moves.items():
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        if use_git:
            _common.git(root, "mv", old, new)
        else:
            (root / old).rename(root / new)
    for rel, text in {**plan.texts, **plan.canvases}.items():
        _write(root, rel, text)
    for rel, text in plan.sub_indexes.items():
        _write(root, rel, text)
    _write(root, INDEX, plan.root_index)
    for type_dir in TYPE_DIRS:
        folder = root / "wiki" / type_dir
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    ledger.relocate(root, plan.moves)


def _summary(plan: Plan) -> dict:
    by_domain: dict[str, int] = {}
    for dest in plan.moves.values():
        by_domain[dest.split("/")[1]] = by_domain.get(dest.split("/")[1], 0) + 1
    return {
        "moves": len(plan.moves),
        "by_domain": by_domain,
        "rewritten_pages": len(plan.texts),
        "rewritten_links": plan.link_changes,
        "sub_indexes": sorted(plan.sub_indexes),
        "canvases": sorted(plan.canvases),
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point; prints a JSON summary."""
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", type=Path, default=_paths.ROOT)
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    import datetime
    today = args.date or datetime.date.today().isoformat()
    try:
        plan = build_plan(args.root, today)
        if args.apply:
            apply_plan(args.root, plan)
    except (ValueError, RuntimeError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    print(json.dumps({"applied": args.apply, **_summary(plan)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
