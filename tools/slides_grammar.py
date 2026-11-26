"""Checks the lecture structure of a slide outline against a page-type table.

The table (a "grammar") is a YAML file under
.claude/skills/wiki-slides/references/grammars/. It lists the page types,
their order inside a chapter, which types may open a chapter, and which may
come before the first chapter or after the last one.

The checker works on plain data (`Item`, `Chapter`), so it serves both an
outline (slides/<concept>/outline.md) and a reference sequence: a YAML
description of a real lecture's page order (`sequence_items`).

Every message holds its rule code in brackets, e.g. `[S5]`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import yaml

# -I drops the script directory from sys.path; _common lives next to this
# file once it moves into tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _common import split_frontmatter  # noqa: E402
except ImportError:  # pragma: no cover - only outside tools/
    _FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)

    def split_frontmatter(text: str) -> tuple[dict | None, str]:
        """Splits text into its frontmatter mapping and body (fallback)."""
        text = text.lstrip("﻿")
        match = _FM_RE.match(text)
        if not match:
            return None, text
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None, text[match.end():]
        return (data if isinstance(data, dict) else None), text[match.end():]


# Same pattern as slides.LINK_RE.
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# A page note right after a link: （p.4）, (p.4), （p.4–6）, (p.4-6),
# optionally after a label and a comma: （式 2，p.3）, (Fig. 1, p.5).
PAGE_RE = re.compile(
    r"\s*[（(]\s*(?:[^（）()\n]{1,30}?[，,]\s*)?"
    r"p\.\s*(\d+)(?:\s*[–—\-~]\s*(\d+))?\s*[）)]")
INFERENCE = "推論："
CODE_RE = re.compile(r"\[S\d+\]")


@dataclass
class Item:
    """One slide, or one page of a reference sequence."""

    where: str
    type: str
    chapter: str | None = None
    build: str | None = None
    highlight: list[str] = field(default_factory=list)
    # None means "not listed" (reference sequences); [] means listed empty.
    points: list[str] | None = field(default_factory=list)


@dataclass
class Chapter:
    """One chapter (a subsection, optionally inside a section)."""

    id: str
    name: str
    section: str | None = None
    bridge: str | None = None
    # Subsections a roadmap highlights together with the chapter.
    subs: list[str] = field(default_factory=list)


@dataclass
class Grammar:
    """A page-type table; the fields mirror the YAML file."""

    name: str
    order: list[str]
    same_as: dict[str, str] = field(default_factory=dict)
    free: list[str] = field(default_factory=list)
    opening: list[str] = field(default_factory=list)
    head: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    no_source: list[str] = field(default_factory=list)
    # [start, end] in `order`: a chapter may go back to `start` while it is
    # still inside the span, e.g. method → result → method → result.
    repeat: list[str] = field(default_factory=list)

    def rank(self, kind: str) -> int | None:
        """Returns the position of a type in `order`, or None."""
        kind = self.same_as.get(kind, kind)
        return self.order.index(kind) if kind in self.order else None

    def restarts(self, rank: int, best: int) -> bool:
        """Tells whether going back to `rank` after `best` starts a round."""
        if not self.repeat:
            return False
        start, end = (self.order.index(k) for k in self.repeat)
        return rank == start and start <= best <= end

    def known(self) -> set[str]:
        """Returns every type the table names."""
        return (set(self.order) | set(self.same_as) | set(self.free)
                | set(self.opening) | set(self.head) | set(self.tail))


# --- grammar files -----------------------------------------------------------


_LIST_KEYS = ("order", "free", "opening", "head", "tail", "no_source",
              "repeat")
_REQUIRED = ("name", "order", "opening", "head", "tail")


def grammar_path(name: str, root: Path) -> Path:
    """Returns the path of a named grammar file.

    Args:
        name: The grammar name, e.g. `research`.
        root: The repository root.

    Returns:
        root/.claude/skills/wiki-slides/references/grammars/<name>.yaml
    """
    return (Path(root) / ".claude" / "skills" / "wiki-slides" / "references"
            / "grammars" / f"{name}.yaml")


def load_grammar(path: Path) -> Grammar:
    """Reads a grammar file.

    Args:
        path: The YAML file.

    Returns:
        The grammar.

    Raises:
        ValueError: The file is missing, is not YAML, or has a wrong shape.
    """
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"頁型表 {path} 無法讀取：{e}") from e
    except yaml.YAMLError as e:
        raise ValueError(f"頁型表 {path} 不是正確的 YAML：{e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"頁型表 {path} 要是一個對照表（mapping）")
    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ValueError(f"頁型表 {path} 缺少 {'、'.join(missing)}")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ValueError(f"頁型表 {path}：name 要是文字")
    lists: dict[str, list[str]] = {}
    for key in _LIST_KEYS:
        value = data.get(key, [])
        if value is None:
            value = []
        if (not isinstance(value, list)
                or not all(isinstance(v, str) and v for v in value)):
            raise ValueError(f"頁型表 {path}：{key} 要是文字清單")
        lists[key] = value
    same_as = data.get("same_as") or {}
    if (not isinstance(same_as, dict)
            or not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in same_as.items())):
        raise ValueError(f"頁型表 {path}：same_as 要是「頁型: 頁型」")
    for kind, target in same_as.items():
        if target not in lists["order"]:
            raise ValueError(
                f"頁型表 {path}：same_as 的 {kind} 對到 {target}，"
                "但它不在 order 裡")
    span = lists["repeat"]
    if span and (len(span) != 2 or not all(k in lists["order"] for k in span)
                 or lists["order"].index(span[0])
                 >= lists["order"].index(span[1])):
        raise ValueError(f"頁型表 {path}：repeat 要是 order 裡"
                         "由前到後的兩個頁型")
    return Grammar(name=data["name"], same_as=dict(same_as), **lists)


# --- chapters ----------------------------------------------------------------


def parse_chapters(meta_value: object) -> tuple[list[Chapter], list[str]]:
    """Reads the outline frontmatter key `章`.

    Args:
        meta_value: The value of `章`: a list of mappings with `id` and
            `名稱` (required) and `節`, `銜接` (optional). None means the key
            is absent.

    Returns:
        The chapters and the problems found.
    """
    if meta_value is None:
        return [], []
    if not isinstance(meta_value, list):
        return [], ["frontmatter「章」要寫成清單，每項有 id、名稱 [S6]"]
    chapters: list[Chapter] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, entry in enumerate(meta_value, start=1):
        label = f"frontmatter「章」第 {number} 項"
        if not isinstance(entry, dict):
            problems.append(f"{label}：要寫成 {{id: …, 名稱: …}} [S6]")
            continue
        cid, name = entry.get("id"), entry.get("名稱")
        bad = [k for k, v in (("id", cid), ("名稱", name))
               if not isinstance(v, str | int) or not str(v).strip()]
        if bad:
            problems.append(f"{label}：缺少 {'、'.join(bad)} [S6]")
            continue
        cid = str(cid).strip()
        if cid in seen:
            problems.append(f"{label}：id「{cid}」重複 [S6]")
            continue
        seen.add(cid)
        extra = {}
        for key in ("節", "銜接"):
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                problems.append(f"{label}：{key} 要是文字 [S6]")
                value = None
            extra[key] = value.strip() if value and value.strip() else None
        subs = entry.get("子節") or []
        if (not isinstance(subs, list)
                or not all(isinstance(s, str) and s.strip() for s in subs)):
            problems.append(f"{label}：子節 要是文字清單 [S6]")
            subs = []
        unknown = set(entry) - {"id", "名稱", "節", "銜接", "子節"}
        if unknown:
            problems.append(
                f"{label}：不認得的欄位 {'、'.join(map(str, unknown))} [S6]")
        chapters.append(Chapter(cid, str(name).strip(), extra["節"],
                                extra["銜接"], [s.strip() for s in subs]))
    return chapters, problems


def bridge_has_link(text: str) -> list[str]:
    """Returns the Markdown link targets in a 銜接 text."""
    return [target for _, target in LINK_RE.findall(text or "")]


# --- structure ---------------------------------------------------------------


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def check_structure(
    items: list[Item],
    chapters: list[Chapter],
    grammar: Grammar,
    check_bridges: bool = True,
) -> tuple[list[str], list[str]]:
    """Checks the lecture structure against a grammar.

    Args:
        items: The slides in deck order.
        chapters: The chapters in their intended order.
        grammar: The page-type table.
        check_bridges: Check each chapter's 銜接 (rule S7); off for
            reference sequences, which have none.

    Returns:
        The errors and the warnings, each line holding its rule code.
    """
    errors: list[str] = []
    warnings: list[str] = []
    known = grammar.known()
    by_id = {c.id: c for c in chapters}

    for item in items:
        if not item.type:
            errors.append(f"{item.where}：缺少類型 [S4]")
        elif item.type not in known:
            errors.append(f"{item.where}：類型「{item.type}」不在頁型表"
                          f" {grammar.name} 裡 [S4]")

    def ok(item: Item) -> bool:
        return item.type in known

    errors += _check_layout(items, chapters, by_id, grammar, ok)
    members: dict[str, list[Item]] = {c.id: [] for c in chapters}
    for item in items:
        if item.chapter in members:
            members[item.chapter].append(item)
    for chapter in chapters:
        errors += _check_chapter(chapter, members[chapter.id], grammar, ok)
    if check_bridges:
        for chapter in chapters[1:]:
            errors += _check_bridge(chapter)
    errors += _check_builds(items)
    tail_errors, tail_warnings = _check_ends(items, grammar)
    errors += tail_errors
    warnings += tail_warnings
    summary_errors, summary_warnings = _check_summary(items, chapters)
    errors += summary_errors
    warnings += summary_warnings
    for item in items:
        if item.type == "roadmap":
            errors += _check_roadmap(item, by_id)
    return errors, warnings


def _check_layout(items: list[Item], chapters: list[Chapter],
                  by_id: dict[str, Chapter], grammar: Grammar,
                  ok: Callable[[Item], bool]) -> list[str]:
    """Where chapterless items may stand, and chapter contiguity and order."""
    errors: list[str] = []
    marked = [i for i, item in enumerate(items) if item.chapter is not None]
    if marked:
        first, last = marked[0], marked[-1]
    else:
        # No chapters at all: head runs until the first tail-only type.
        tail_only = set(grammar.tail) - set(grammar.head)
        start = next((i for i, item in enumerate(items)
                      if item.type in tail_only), len(items))
        first, last = start, start - 1
    for index, item in enumerate(items):
        if item.chapter is not None or not ok(item):
            continue
        if index < first:
            if item.type not in grammar.head:
                errors.append(f"{item.where}：「{item.type}」不能放在第一章"
                              "之前 [S6]")
        elif index > last:
            if item.type not in grammar.tail:
                errors.append(f"{item.where}：最後一章之後只能放"
                              f"{'、'.join(grammar.tail)}，"
                              f"不能放「{item.type}」 [S9]")
        else:
            errors.append(f"{item.where}：沒有標章，卻夾在兩章之間 [S6]")

    runs: list[str] = []
    for item in items:
        if item.chapter is None:
            continue
        if item.chapter not in by_id:
            errors.append(f"{item.where}：章 id「{item.chapter}」不在"
                          "frontmatter「章」裡 [S6]")
            continue
        if not runs or runs[-1] != item.chapter:
            if item.chapter in runs:
                errors.append(f"{item.where}：章 {item.chapter} 的頁面"
                              "不連續 [S6]")
            runs.append(item.chapter)
    appeared: list[str] = []
    for cid in runs:
        if cid not in appeared:
            appeared.append(cid)
    expected = [c.id for c in chapters if c.id in appeared]
    if appeared != expected:
        wrong = next(a for a, e in zip(appeared, expected, strict=True)
                     if a != e)
        errors.append(f"章 {wrong}：章的順序是 {'、'.join(appeared)}，與 "
                      f"frontmatter「章」的 {'、'.join(expected)} 不同 [S6]")
    errors += [f"章 {c.id}：沒有任何頁面 [S6]"
               for c in chapters if c.id not in appeared]
    return errors


def _check_chapter(chapter: Chapter, members: list[Item], grammar: Grammar,
                   ok: Callable[[Item], bool]) -> list[str]:
    """Rules S5 and S6 inside one chapter."""
    errors: list[str] = []
    if not members:
        return errors
    opener = members[0]
    if ok(opener) and opener.type not in grammar.opening:
        errors.append(f"{opener.where}：章 {chapter.id} 的第一頁要是"
                      f"{'、'.join(grammar.opening)}，"
                      f"不是「{opener.type}」 [S6]")
    inside = (set(grammar.order) | set(grammar.same_as) | set(grammar.free)
              | set(grammar.opening))
    best: tuple[int, Item] | None = None
    for item in members:
        if not ok(item):
            continue
        if item.type not in inside:
            errors.append(f"{item.where}：「{item.type}」不能放在章裡 [S5]")
            continue
        rank = grammar.rank(item.type)
        if rank is None:
            continue
        if best is not None and rank < best[0] and grammar.restarts(rank,
                                                                    best[0]):
            best = (rank, item)
            continue
        if best is not None and rank < best[0]:
            errors.append(f"{item.where}：「{item.type}」排在"
                          f"「{best[1].type}」（{best[1].where}）之後，"
                          "順序倒了 [S5]")
        if best is None or rank > best[0]:
            best = (rank, item)
    return errors


def _check_bridge(chapter: Chapter) -> list[str]:
    """Rule S7: chapters after the first need a sourced 銜接."""
    where = f"章 {chapter.id}"
    if not chapter.bridge:
        return [f"{where}：缺少銜接 [S7]"]
    if bridge_has_link(chapter.bridge):
        return []
    if chapter.bridge.lstrip().startswith(INFERENCE):
        return []
    return [f"{where}：銜接沒有連結出處，也不是「推論：」開頭 [S7]"]


def _check_builds(items: list[Item]) -> list[str]:
    """Rule S8: a build group has at least two consecutive pages."""
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        if item.build is not None:
            groups.setdefault(item.build, []).append(index)
    errors: list[str] = []
    for build, indexes in groups.items():
        if all(items[i].type == "spotlight" for i in indexes):
            continue
        where = items[indexes[0]].where
        if len(indexes) < 2:
            errors.append(f"{where}：逐步顯示「{build}」只有一頁 [S8]")
        elif indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            errors.append(f"{where}：逐步顯示「{build}」的頁面不相鄰 [S8]")
    return errors


def _check_ends(items: list[Item],
                grammar: Grammar) -> tuple[list[str], list[str]]:
    """Rule S9: the deck ends in a summary and starts with motivation."""
    errors: list[str] = []
    warnings: list[str] = []
    if not items:
        return errors, warnings
    summaries = [i for i, item in enumerate(items) if item.type == "summary"]
    if not summaries:
        errors.append(f"{items[-1].where}：整份大綱沒有 summary 頁 [S9]")
    else:
        errors += [f"{item.where}：summary 之後只能接 appendix，"
                   f"不能接「{item.type}」 [S9]"
                   for item in items[summaries[0] + 1:]
                   if item.type != "appendix"]
    last = items[-1]
    if last.type not in ("summary", "appendix"):
        errors.append(f"{last.where}：最後一頁要是 summary 或 appendix，"
                      f"不是「{last.type or '（無類型）'}」 [S9]")
    opener = next((item for item in items if item.type != "cover"), None)
    if opener is not None and opener.type != "motivation":
        warnings.append(f"{opener.where}：封面之後的第一頁通常是 "
                        f"motivation，這裡是「{opener.type}」 [S9]")
    return errors, warnings


def _check_summary(items: list[Item],
                   chapters: list[Chapter]) -> tuple[list[str], list[str]]:
    """Rule S10: the summary covers the chapters in order."""
    summary = next((item for item in items
                    if item.type == "summary" and item.points), None)
    if summary is None or not chapters:
        return [], []
    points = [_fold(p) for p in summary.points or []]
    errors: list[str] = []
    warnings: list[str] = []
    found: list[tuple[int, Chapter]] = []
    for chapter in chapters:
        name = _fold(chapter.name)
        index = next((i for i, p in enumerate(points) if name in p), None)
        if index is None:
            warnings.append(f"{summary.where}：summary 的要點沒有提到"
                            f"章「{chapter.name}」 [S10]")
        else:
            found.append((index, chapter))
    # Equal indexes are fine: one point may name two chapters.
    for (a, prev), (b, cur) in zip(found, found[1:], strict=False):
        if b < a:
            errors.append(f"{summary.where}：summary 先講「{cur.name}」"
                          f"再講「{prev.name}」，與章的順序不同 [S10]")
    return errors, warnings


def _check_roadmap(item: Item, by_id: dict[str, Chapter]) -> list[str]:
    """Rule S11: a roadmap highlights the chapter it opens."""
    errors: list[str] = []
    highlight = [h.strip() for h in item.highlight if h.strip()]
    if item.points is not None:
        shown = {_fold(p) for p in item.points}
        errors += [f"{item.where}：焦點「{h}」不在這頁的要點裡 [S11]"
                   for h in highlight if _fold(h) not in shown]
    if item.chapter is None:
        if highlight:
            errors.append(f"{item.where}：第一章之前的總覽 roadmap "
                          "不標焦點 [S11]")
        return errors
    chapter = by_id.get(item.chapter)
    if chapter is None:
        return errors
    expected = ([chapter.section] if chapter.section else [])
    expected += [chapter.name, *chapter.subs]
    if {_fold(h) for h in highlight} != {_fold(e) for e in expected}:
        errors.append(f"{item.where}：焦點要是 {'、'.join(expected)}，"
                      f"不是 {'、'.join(highlight) or '（空）'} [S11]")
    return errors


# --- page references (S3) ----------------------------------------------------


def page_refs(item_text: str) -> list[tuple[str, int, int]]:
    """Finds the page notes that directly follow a link in one 出處 line.

    Args:
        item_text: One 出處 item, e.g.
            `[H2O](../../wiki/demo/sources/x.md#方法)（p.4）`.

    Returns:
        (link target, first page, last page) for each page note; a single
        page gives first == last. Parentheses not right after a link, such
        as `（Kwon 2023 p.4）`, are ignored.
    """
    refs: list[tuple[str, int, int]] = []
    for link in LINK_RE.finditer(item_text):
        page = PAGE_RE.match(item_text, link.end())
        if page:
            first = int(page.group(1))
            last = int(page.group(2)) if page.group(2) else first
            refs.append((link.group(2), first, last))
    return refs


@lru_cache(maxsize=256)
def _pdf_pages(path: str) -> int:
    """Returns the page count of a PDF, cached per path."""
    import pymupdf  # noqa: PLC0415 - heavy; only when a page note exists

    with pymupdf.open(path) as doc:
        return doc.page_count


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def page_ref_problems(
    outline_dir: Path, repo_root: Path, item_text: str, where: str,
) -> tuple[list[str], list[str]]:
    """Checks the page notes of one 出處 line against the source PDF (S3).

    Args:
        outline_dir: The folder of the outline; links resolve against it.
        repo_root: The repository root.
        item_text: One 出處 item.
        where: The label used in messages.

    Returns:
        The errors and the warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    wiki = (Path(repo_root) / "wiki").resolve()
    for target, first, last in page_refs(item_text):
        note = f"（p.{first}）" if first == last else f"（p.{first}–{last}）"
        file_part = target.partition("#")[0]
        if re.match(r"^[a-z]+://", target) or not file_part:
            errors.append(f"{where}：{note}只能寫在 source 頁的連結上 [S3]")
            continue
        page = (Path(outline_dir) / unquote(file_part)).resolve()
        if (not _inside(page, wiki) or page.suffix != ".md"
                or "sources" not in page.relative_to(wiki).parts[:-1]):
            errors.append(f"{where}：{note}只能寫在 source 頁的連結上 [S3]")
            continue
        if not page.is_file():
            errors.append(f"{where}：{note}的 source 頁不存在 {target} [S3]")
            continue
        if first > last:
            errors.append(f"{where}：{note}的頁碼範圍前大後小 [S3]")
            continue
        try:
            meta, _ = split_frontmatter(page.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None
        sources = (meta or {}).get("sources")
        if (not isinstance(sources, list) or len(sources) != 1
                or not isinstance(sources[0], str)
                or not sources[0].lower().endswith(".pdf")):
            warnings.append(f"{where}：{page.name} 的 sources 不是單一 PDF，"
                            f"無法核對{note} [S3]")
            continue
        pdf = (Path(repo_root) / sources[0]).resolve()
        if not pdf.is_file():
            errors.append(f"{where}：{note}要核對的 PDF 不存在 "
                          f"{sources[0]} [S3]")
            continue
        try:
            count = _pdf_pages(str(pdf))
        except Exception as e:  # noqa: BLE001 - any pymupdf failure
            errors.append(f"{where}：PDF {sources[0]} 無法開啟（{e}） [S3]")
            continue
        if first < 1 or last > count:
            errors.append(f"{where}：{note}超出 {pdf.name} 的頁數"
                          f"（共 {count} 頁） [S3]")
    return errors, warnings


def source_pdf(outline_dir: Path, repo_root: Path, target: str) -> Path | None:
    """Returns the raw PDF behind a source-page link, or None.

    Args:
        outline_dir: The folder of the outline; links resolve against it.
        repo_root: The repository root.
        target: The link target, e.g. `../../wiki/demo/sources/x.md#方法`.

    Returns:
        The PDF when the link is a source page whose `sources` is one PDF
        that exists; otherwise None.
    """
    file_part = target.partition("#")[0]
    if re.match(r"^[a-z]+://", target) or not file_part:
        return None
    page = (Path(outline_dir) / unquote(file_part)).resolve()
    if not page.is_file():
        return None
    try:
        meta, _ = split_frontmatter(page.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sources = (meta or {}).get("sources")
    if (not isinstance(sources, list) or len(sources) != 1
            or not isinstance(sources[0], str)
            or not sources[0].lower().endswith(".pdf")):
        return None
    pdf = (Path(repo_root) / sources[0]).resolve()
    return pdf if pdf.is_file() else None


# --- reference sequences -----------------------------------------------------


def _seq_item(entry: object, label: str, chapter: str | None,
              problems: list[str]) -> Item | None:
    if not isinstance(entry, dict):
        problems.append(f"{label}：每頁要寫成 {{p: 頁碼, type: 頁型}}")
        return None
    page = entry.get("p")
    if not isinstance(page, int) or isinstance(page, bool):
        problems.append(f"{label}：p 要是整數頁碼")
        return None
    kind = entry.get("type")
    highlight = entry.get("highlight") or []
    points = entry.get("points")
    if (not isinstance(highlight, list)
            or (points is not None and not isinstance(points, list))):
        problems.append(f"p.{page}：highlight、points 要是清單")
        return None
    build = entry.get("build")
    return Item(
        where=f"p.{page}",
        type=str(kind).strip() if kind else "",
        chapter=chapter,
        build=str(build) if build is not None else None,
        highlight=[str(h) for h in highlight],
        points=None if points is None else [str(p) for p in points],
    )


def sequence_items(doc: object) -> tuple[list[Item], list[Chapter], list[str]]:
    """Turns a reference-sequence document into items and chapters.

    Args:
        doc: The parsed YAML: `lecture`, `pdf_pages`, `grammar`, `head`,
            `chapters` (each with `id`, `name`, optional `section`, and
            `slides`) and `tail`. Each page is `{p, type}` with optional
            `build`, `highlight`, `points`.

    Returns:
        The items in page order, the chapters, and the shape problems
        (page numbers must rise strictly and stay within 1..pdf_pages).
    """
    problems: list[str] = []
    if not isinstance(doc, dict):
        return [], [], ["參考序列要是一個對照表（mapping）"]
    total = doc.get("pdf_pages")
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        problems.append("pdf_pages 要是正整數")
        total = None
    items: list[Item] = []
    chapters: list[Chapter] = []

    def take(entries: object, label: str, chapter: str | None) -> None:
        if entries is None:
            return
        if not isinstance(entries, list):
            problems.append(f"{label} 要是清單")
            return
        for entry in entries:
            item = _seq_item(entry, label, chapter, problems)
            if item is not None:
                items.append(item)

    take(doc.get("head"), "head", None)
    raw = doc.get("chapters") or []
    if not isinstance(raw, list):
        problems.append("chapters 要是清單")
        raw = []
    seen: set[str] = set()
    for number, entry in enumerate(raw, start=1):
        label = f"chapters 第 {number} 項"
        if (not isinstance(entry, dict) or not entry.get("id")
                or not entry.get("name")):
            problems.append(f"{label}：要有 id 與 name")
            continue
        cid = str(entry["id"])
        if cid in seen:
            problems.append(f"{label}：id「{cid}」重複")
            continue
        seen.add(cid)
        section = entry.get("section")
        subs = entry.get("subs") or []
        if not isinstance(subs, list):
            problems.append(f"{label}：subs 要是清單")
            subs = []
        chapters.append(Chapter(cid, str(entry["name"]),
                                str(section) if section else None, None,
                                [str(s) for s in subs]))
        take(entry.get("slides"), f"章 {cid} 的 slides", cid)
    take(doc.get("tail"), "tail", None)

    previous: int | None = None
    for item in items:
        page = int(item.where[2:])
        if previous is not None and page <= previous:
            problems.append(f"{item.where}：頁碼要遞增，前一頁是 "
                            f"p.{previous}")
        if page < 1 or (total is not None and page > total):
            problems.append(f"{item.where}：頁碼超出 1–{total or '?'}")
        previous = page
    return items, chapters, problems
