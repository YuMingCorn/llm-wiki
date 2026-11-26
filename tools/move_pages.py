"""Moves or renames wiki pages and rewrites every reference to them.

Usage (from the repo root):
  uv run python -I tools/move_pages.py <old> <new> [<old> <new> ...]
  uv run python -I tools/move_pages.py --map moves.tsv
  uv run python -I tools/move_pages.py --to-domain history <page> [<page> ...]
  ... add --apply to perform the move; without it only the plan is printed.

Paths are repo-relative (``wiki/music/concepts/x.md``). ``--map`` reads one
``old<TAB>new`` pair per line (``#`` comments allowed). ``--to-domain`` keeps
the type directory and file name and swaps the domain directory
(schema §8), e.g. ``wiki/music/concepts/x.md`` → ``wiki/history/concepts/x.md``.

For each move the tool uses ``git mv``, rewrites relative links in all wiki
pages (``wiki/log.md`` is append-only and left alone), frontmatter ``sources``
paths, canvas ``file`` paths and the source ledger. When a page lands in a
different domain directory its frontmatter ``domain`` is updated to match.
Topic membership is not changed: the topic page keeps listing the moved page
under its new path, so review the topic pages after a cross-domain move.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import _paths  # noqa: E402
import ledger  # noqa: E402
import migrate_domains as md  # noqa: E402

DOMAIN_DIR_RE = re.compile(
    rf"^wiki/([a-z0-9]+(?:-[a-z0-9]+)*)/({'|'.join(md.TYPE_DIRS)})/[^/]+\.md$")
FLAT_DIR_RE = re.compile(rf"^wiki/({'|'.join(md.TYPE_DIRS)})/[^/]+\.md$")


@dataclass
class MovePlan:
    """What --apply will do; the dry run prints it."""

    moves: dict[str, str]
    texts: dict[str, str] = field(default_factory=dict)
    canvases: dict[str, str] = field(default_factory=dict)
    domain_changes: dict[str, str] = field(default_factory=dict)
    link_changes: int = 0


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def read_map(text: str) -> dict[str, str]:
    """Parses ``old<TAB>new`` lines; blank lines and ``#`` comments skip."""
    moves: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise ValueError(f"第 {number} 行要是「舊路徑<TAB>新路徑」：{line}")
        moves[_norm(parts[0])] = _norm(parts[1])
    return moves


def to_domain(pages: list[str], domain: str) -> dict[str, str]:
    """Maps pages to the same type directory under another domain."""
    if not _common.is_domain_name(domain):
        raise ValueError(f"domain 要是 kebab-case，且不能是類型或共用目錄名稱：{domain}")
    moves = {}
    for page in map(_norm, pages):
        m = DOMAIN_DIR_RE.match(page)
        if not m:
            raise ValueError(f"不是 wiki/<domain>/<類型>/ 底下的頁面：{page}")
        moves[page] = f"wiki/{domain}/{m.group(2)}/{page.rsplit('/', 1)[1]}"
    return moves


def validate(root: Path, moves: dict[str, str]) -> None:
    """Rejects moves that would lose or overwrite pages.

    Raises:
        ValueError: With every problem found, one per line.
    """
    problems = []
    targets = list(moves.values())
    for old, new in moves.items():
        if old == new:
            problems.append(f"來源與目的相同：{old}")
        if not (root / old).is_file():
            problems.append(f"找不到來源：{old}")
        if not (DOMAIN_DIR_RE.match(new) or FLAT_DIR_RE.match(new)):
            problems.append(
                f"目的不在 wiki/<domain>/<類型>/ 或 wiki/<類型>/ 底下：{new}")
        if (root / new).exists() and new not in moves:
            problems.append(f"目的已存在：{new}")
        if targets.count(new) > 1:
            problems.append(f"多個來源搬到同一個目的：{new}")
    if problems:
        raise ValueError("\n".join(dict.fromkeys(problems)))


def _set_domain(text: str, domain: str) -> str:
    match = _common.FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not match:
        return text
    offset = len(text) - len(text.lstrip("﻿"))
    start, end = offset + match.start(1), offset + match.end(1)
    block = re.sub(r"(?m)^domain:[ \t]*\S*", f"domain: {domain}",
                   text[start:end], count=1)
    return text[:start] + block + text[end:]


def build_plan(root: Path, moves: dict[str, str]) -> MovePlan:
    """Computes the rewritten texts without touching the disk."""
    validate(root, moves)
    plan = MovePlan(moves=dict(moves))
    for file in sorted((root / "wiki").rglob("*.md")):
        rel = file.relative_to(root).as_posix()
        if rel == md.LOG or rel.startswith(md.SKIP_PREFIXES):
            continue
        text = md._read(root, rel)
        new_rel = moves.get(rel, rel)
        new_text, count = md.rewrite_links(text, rel, new_rel, moves, root)
        new_text = md.rewrite_frontmatter_sources(new_text, moves)
        dest = DOMAIN_DIR_RE.match(new_rel)
        if rel in moves and dest:
            fm, _ = _common.split_frontmatter(new_text)
            if (fm or {}).get("domain") != dest.group(1):
                new_text = _set_domain(new_text, dest.group(1))
                plan.domain_changes[new_rel] = dest.group(1)
        plan.link_changes += count
        if new_text != text or new_rel != rel:
            plan.texts[new_rel] = new_text
    canvas_dir = root / md.CANVAS_DIR
    canvases = (sorted(canvas_dir.glob("*.canvas"))
                if canvas_dir.is_dir() else [])
    for file in canvases:
        rel = file.relative_to(root).as_posix()
        text = md._read(root, rel)
        new = text
        for old, dest_path in moves.items():
            new = new.replace(f'"file":"{old}"', f'"file":"{dest_path}"')
        if new != text:
            plan.canvases[rel] = new
    return plan


def apply_plan(root: Path, plan: MovePlan, use_git: bool = True) -> None:
    """Moves the files, writes the rewritten texts and relocates the ledger.

    Moves go through a temporary name first so that swaps and chains
    (a → b, b → c) never overwrite a page that has not moved yet.
    """
    staged = {}
    for index, old in enumerate(plan.moves):
        temp = f"{old}.moving-{index}"
        _move(root, old, temp, use_git)
        staged[temp] = plan.moves[old]
    for temp, new in staged.items():
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        _move(root, temp, new, use_git)
    for rel, text in {**plan.texts, **plan.canvases}.items():
        md._write(root, rel, text)
    ledger.relocate(root, plan.moves)


def _move(root: Path, old: str, new: str, use_git: bool) -> None:
    if use_git:
        _common.git(root, "mv", old, new)
    else:
        (root / old).rename(root / new)


def main(argv: list[str] | None = None) -> int:
    """Entry point; prints a JSON summary."""
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", nargs="*",
                    help="old new [old new ...]，或頁面清單")
    ap.add_argument("--map", type=Path, help="每行「舊路徑<TAB>新路徑」的檔案")
    ap.add_argument("--to-domain", metavar="DOMAIN",
                    help="搬到另一個領域（不存在時會建立）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", type=Path, default=_paths.ROOT)
    args = ap.parse_args(argv)
    try:
        if args.to_domain:
            moves = to_domain(args.pairs, args.to_domain)
        elif args.map:
            moves = read_map(args.map.read_text(encoding="utf-8"))
        else:
            if not args.pairs or len(args.pairs) % 2:
                raise ValueError("要給成對的「舊路徑 新路徑」")
            pairs = list(map(_norm, args.pairs))
            moves = dict(zip(pairs[::2], pairs[1::2], strict=True))
        plan = build_plan(args.root, moves)
        if args.apply:
            apply_plan(args.root, plan)
    except (ValueError, RuntimeError, OSError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    print(json.dumps({
        "applied": args.apply,
        "moves": plan.moves,
        "domain_changes": plan.domain_changes,
        "rewritten_pages": len(plan.texts),
        "rewritten_links": plan.link_changes,
        "canvases": sorted(plan.canvases),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
