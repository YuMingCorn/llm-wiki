"""Research lineage: who builds on whom, from each source page's 承先.

A source page's ``## 研究脈絡`` section starts with a ``**承先**`` block that
links the earlier works it continues. This module reads those links as
edges ``page -> earlier work`` and derives the reverse (啟後) itself, so no
page has to be edited when a later source arrives.

The fixed 承先 lines are::

    - 接續 [A](a.md)：卡在 X（p.N）→ 本文改為 Y（p.N）
    - 沿用 [A](a.md)：本文沿用它的 Z（p.N）

A link outside those lines (all pages written before the format) becomes an
edge labelled 無說明. One file per topic, ``wiki/meta/lineage/<topic>.md``,
is generated from the topic page's ``## 來源`` list; ``check_stale`` compares
existing files with a fresh render.

``lint`` imports this module, so ``lint`` is imported inside functions only.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type hints only; lint imports this module
    from lint import Page, Wiki

LINEAGE_DIR = "wiki/meta/lineage"
CONTEXT_HEADING_RE = re.compile(r"^##\s+研究脈絡\s*$")
H2_RE = re.compile(r"^##\s")
PRIOR_LABEL_RE = re.compile(r"^\s*\*\*承先\*\*\s*[：:]?\s*")
OTHER_LABEL_RE = re.compile(r"^\s*\*\*(這份改了什麼|啟後)\*\*")
SOURCES_HEADING_RE = re.compile(r"^##\s+來源\s*$")
YEAR_RE = re.compile(r"^(\d{4})-")
CONTINUES_RE = re.compile(
    r"^\s*[-*]\s+接續\s+(?P<work>.+?)：卡在\s*(?P<stuck>.+?)\s*→\s*"
    r"本文改為\s*(?P<change>.+?)\s*$")
REUSES_RE = re.compile(
    r"^\s*[-*]\s+沿用\s+(?P<work>.+?)：本文沿用它的\s*(?P<part>.+?)\s*$")
NO_PRIOR_RE = re.compile(r"^(無|wiki 尚無)[。.]?$")
# Words that suggest a compared baseline rather than a continued work.
COMPARISON_RE = re.compile(r"比較|對照|baseline|基準", re.I)

CONTINUES, REUSES, UNDESCRIBED = "接續", "沿用", "無說明"


@dataclass(frozen=True)
class Edge:
    """``page`` builds on the earlier source page ``target``."""

    page: str
    target: str
    kind: str  # CONTINUES, REUSES or UNDESCRIBED
    note: str  # the fixed line's text after the link, or the sentence


def year(key: str) -> str:
    """Returns the four-digit year that starts the file name, or ''."""
    m = YEAR_RE.match(PurePosixPath(key).name)
    return m.group(1) if m else ""


def _section(body: str, heading: re.Pattern) -> list[str] | None:
    """Returns the prose lines under one ``##`` heading, or None."""
    import lint

    out: list[str] | None = None
    for _, original, line in lint._prose_lines(body):
        if H2_RE.match(line):
            if out is not None:
                break
            if heading.match(line):
                out = []
            continue
        if out is not None:
            out.append(original)
    return out


def prior_block(page: Page) -> list[str] | None:
    """Returns the 承先 lines of a page, or None without a 研究脈絡 section.

    The block starts at ``**承先**`` (the label itself is removed) and ends
    at ``**這份改了什麼**``, ``**啟後**`` or the end of the section. A
    section without the label gives an empty list.
    """
    section = _section(page.body, CONTEXT_HEADING_RE)
    if section is None:
        return None
    block: list[str] = []
    inside = False
    for line in section:
        if OTHER_LABEL_RE.match(line):
            if inside:
                break
            continue
        m = PRIOR_LABEL_RE.match(line)
        if m and not inside:
            inside = True
            line = line[m.end():]
        if inside:
            block.append(line)
    return block


def _plain(text: str) -> str:
    """Replaces Markdown links by their text; their paths would break here."""
    import lint

    def text_of(m: re.Match) -> str:
        return m.group(0)[len(m.group(1)) + 1:m.group(0).rindex("](")]

    return lint.LINK_RE.sub(text_of, text).strip()


def _sentence(line: str, start: int, end: int) -> str:
    """Returns the sentence of ``line`` around ``line[start:end]``."""
    left = max(line.rfind(mark, 0, start) for mark in "。；") + 1
    rights = [i for i in (line.find(mark, end) for mark in "。；") if i >= 0]
    right = min(rights) + 1 if rights else len(line)
    return _plain(line[left:right])


def page_edges(key: str, page: Page, sources: set[str]) -> list[Edge]:
    """Reads one page's 承先 links to other source pages.

    Args:
        key: Repo-relative path of the page.
        page: A ``lint.Page``.
        sources: Keys of every source page; other targets are skipped.

    Returns:
        One edge per distinct target, in the order first linked.
    """
    import lint

    block = prior_block(page)
    if not block:
        return []
    page_dir = posixpath.dirname(key)
    seen: set[str] = set()
    out: list[Edge] = []
    for line in block:
        fixed = CONTINUES_RE.match(line) or REUSES_RE.match(line)
        for m in lint.LINK_RE.finditer(line):
            if m.group(1):  # image
                continue
            target = lint._resolve(page_dir, m.group(2).strip("<>"))
            if target is None or target == key or target not in sources:
                continue
            if target in seen:
                continue
            seen.add(target)
            if fixed and fixed.start("work") <= m.start() < fixed.end("work"):
                kind = CONTINUES if fixed.re is CONTINUES_RE else REUSES
                # The text after the work's "：", as the page wrote it.
                note = _plain(line[fixed.end("work") + 1:])
                out.append(Edge(key, target, kind, note))
            else:
                out.append(Edge(key, target, UNDESCRIBED,
                                _sentence(line, m.start(), m.end())))
    return out


def all_edges(wiki: Wiki) -> list[Edge]:
    """Returns every 承先 edge between source pages."""
    sources = wiki.source_pages()
    keys = set(sources)
    out: list[Edge] = []
    for key in sorted(sources):
        out.extend(page_edges(key, sources[key], keys))
    return out


def format_problems(page: Page) -> list[str]:
    """Lists the 承先 lines that do not follow the fixed format.

    The label line may carry only 無 or wiki 尚無; every other non-blank
    line must be a 接續 or 沿用 list item.
    """
    block = prior_block(page)
    if block is None:
        return []
    if not block:
        return ["missing **承先**"]
    problems = []
    head = block[0].strip()
    if head and not NO_PRIOR_RE.match(head):
        problems.append(f"承先標籤後要換行列清單：{head[:40]}")
    for line in block[1:]:
        text = line.strip()
        if not text or CONTINUES_RE.match(line) or REUSES_RE.match(line):
            continue
        problems.append(f"不符合接續／沿用寫法：{text[:40]}")
    return problems


def topic_members(topic: Page, wiki: Wiki) -> list[str]:
    """Returns the source pages listed under the topic's ``## 來源``."""
    import lint

    section = _section(topic.body, SOURCES_HEADING_RE) or []
    topic_dir = topic.path.parent.as_posix()
    sources = wiki.source_pages()
    out: list[str] = []
    for line in section:
        for m in lint.LINK_RE.finditer(line):
            target = lint._resolve(topic_dir, m.group(2).strip("<>"))
            if target in sources and target not in out:
                out.append(target)
    return out


def lineage_path(topic_key: str) -> str:
    """Returns the generated file's repo-relative path for a topic."""
    return f"{LINEAGE_DIR}/{PurePosixPath(topic_key).stem}.md"


def _title(wiki: Wiki, key: str) -> str:
    page = wiki.pages.get(key) or wiki.topics.get(key)
    title = ((page.frontmatter or {}).get("title") if page else None) or key
    return str(title).replace("[", "(").replace("]", ")")


def _link(wiki: Wiki, key: str, out_dir: str) -> str:
    return f"[{_title(wiki, key)}]({posixpath.relpath(key, out_dir)})"


def _order(key: str) -> tuple[str, str]:
    return (year(key) or "9999", PurePosixPath(key).name)


def _edge_text(e: Edge) -> str:
    return e.kind if e.kind == UNDESCRIBED else f"{e.kind}：{e.note}"


def render(wiki: Wiki, topic_key: str) -> str:
    """Renders one topic's lineage file as Markdown.

    The tree runs forward in time: each source sits under the earlier work it
    builds on. A source with several earlier works appears under each; only
    the first occurrence lists its later works, the others end in （見上）.
    Pages outside the topic are drawn one step out and marked （其他主題）.
    """
    topic = wiki.topics[topic_key]
    out_dir = LINEAGE_DIR
    members = topic_members(topic, wiki)
    member_set = set(members)
    edges = [e for e in all_edges(wiki)
             if e.page in member_set or e.target in member_set]
    later: dict[str, list[Edge]] = {}
    has_prior: set[str] = set()
    for e in edges:
        later.setdefault(e.target, []).append(e)
        has_prior.add(e.page)
    outside_roots = {e.target for e in edges if e.target not in member_set}
    roots = sorted({m for m in members if m not in has_prior} | outside_roots,
                   key=_order)

    def label(key: str) -> str:
        mark = "" if key in member_set else "（其他主題）"
        return f"{year(key) or '年份不明'} {_link(wiki, key, out_dir)}{mark}"

    lines: list[str] = []
    expanded: set[str] = set()

    def walk(key: str, depth: int) -> None:
        expanded.add(key)  # an outside page too, so a repeat says （見上）
        if key not in member_set and depth > 0:
            return  # one step out: shown, never expanded
        for e in sorted(later.get(key, []), key=lambda x: _order(x.page)):
            again = e.page in expanded
            lines.append(f"{'  ' * (depth + 1)}- {label(e.page)}"
                         f"｜{_edge_text(e)}{'（見上）' if again else ''}")
            if not again:
                walk(e.page, depth + 1)

    for root in roots:
        # An outside root may already have been shown as a later work; it is
        # still listed here, because only here do its topic members show.
        if root in expanded and root in member_set:
            continue
        lines.append(f"- {label(root)}")
        walk(root, 0)
    # Members only reachable through a cycle are never roots; list them too.
    for m in sorted(member_set - expanded, key=_order):
        lines.append(f"- {label(m)}（循環）")
        walk(m, 0)

    review = []
    for e in sorted(edges, key=lambda x: (_order(x.page), _order(x.target))):
        if e.page not in member_set:
            continue
        reasons = []
        if year(e.target) > year(e.page) and year(e.page):
            reasons.append(f"連到較新的頁（{year(e.target)} > {year(e.page)}）")
        if e.kind == UNDESCRIBED and COMPARISON_RE.search(e.note):
            reasons.append(f"句子像比較對象：「{e.note}」")
        if reasons:
            review.append(f"- {_link(wiki, e.page, out_dir)} → "
                          f"{_link(wiki, e.target, out_dir)}："
                          + "；".join(reasons))
    no_context = [f"- {_link(wiki, m, out_dir)}"
                  for m in sorted(members, key=_order)
                  if prior_block(wiki.pages[m]) is None]
    undescribed: dict[str, int] = {}
    for e in edges:
        if e.page in member_set and e.kind == UNDESCRIBED:
            undescribed[e.page] = undescribed.get(e.page, 0) + 1
    old_format = [f"- {_link(wiki, k, out_dir)}：{n} 條"
                  for k, n in sorted(undescribed.items(),
                                     key=lambda kv: _order(kv[0]))]

    topic_link = _link(wiki, topic_key, out_dir)
    parts = [
        f"# 研究系譜：{_title(wiki, topic_key)}",
        "",
        f"主題：{topic_link}。這個檔由 `uv run python -I tools/wiki.py "
        "lineage` 從各 source 頁「研究脈絡」的承先產生，不要手改。",
        "",
        "每一行是一份來源，放在它接續或沿用的前人工作底下，同一層依年份排列。"
        "「接續」是作者說前人卡在哪裡、這份因此改了什麼；「沿用」是直接拿前人的"
        "元件來用；「無說明」是承先還沒用固定寫法，只知道有關係。",
        "",
        "## 系譜",
        "",
        *(lines or ["- 無"]),
        "",
        "## 待確認的連結",
        "",
        *(review or ["- 無"]),
        "",
        "## 尚無研究脈絡的來源",
        "",
        *(no_context or ["- 無"]),
        "",
        "## 承先尚未用固定寫法的來源",
        "",
        "重新 ingest 時改成接續／沿用的寫法，箭頭才會有說明。",
        "",
        *(old_format or ["- 無"]),
        "",
    ]
    return "\n".join(parts)


def write(wiki: Wiki, topic_keys: list[str]) -> list[tuple[str, bool]]:
    """Writes the lineage files; returns (path, changed) per topic."""
    out = []
    for key in topic_keys:
        rel = lineage_path(key)
        path = Path(wiki.root) / rel
        text = render(wiki, key)
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        out.append((rel, old != text))
    return out


def resolve_topics(wiki: Wiki, names: list[str]) -> list[str]:
    """Maps topic names (stem or path) to topic keys; all topics if empty.

    Raises:
        ValueError: a name matches no topic page.
    """
    if not names:
        return sorted(wiki.topics)
    by_stem = {PurePosixPath(k).stem: k for k in wiki.topics}
    out = []
    for name in names:
        key = Path(name).as_posix().removeprefix("./")
        if key not in wiki.topics:
            key = by_stem.get(PurePosixPath(key).stem)
        if key is None:
            raise ValueError(f"找不到主題頁：{name}")
        out.append(key)
    return out


def check_stale(wiki: Wiki) -> list[dict]:
    """Lineage files that differ from a fresh render, or match no topic.

    Topics without a file are not reported: files are made on demand.
    """
    folder = Path(wiki.root) / LINEAGE_DIR
    if not folder.is_dir():
        return []
    by_path = {lineage_path(k): k for k in wiki.topics}
    out = []
    for path in sorted(folder.glob("*.md")):
        rel = f"{LINEAGE_DIR}/{path.name}"
        key = by_path.get(rel)
        if key is None:
            out.append({"file": rel, "problem": "no_topic"})
        elif path.read_text(encoding="utf-8") != render(wiki, key):
            out.append({"file": rel, "topic": key, "problem": "stale"})
    return out


def check_newer_targets(wiki: Wiki) -> list[dict]:
    """承先 links to a source page with a strictly later year."""
    return [{"page": e.page, "target": e.target, "kind": e.kind}
            for e in all_edges(wiki)
            if year(e.page) and year(e.target) > year(e.page)]


# Source pages created before FORMAT_SINCE were written before the fixed
# 承先 format became an ingest rule and are not checked.
# None turns the check off.
FORMAT_SINCE: str | None = "2026-11-23"


def check_format(wiki: Wiki) -> list[dict]:
    """Source pages created since FORMAT_SINCE whose 承先 breaks the format."""
    if FORMAT_SINCE is None:
        return []
    out = []
    for key, page in sorted(wiki.source_pages().items()):
        if str((page.frontmatter or {}).get("created", "")) < FORMAT_SINCE:
            continue
        out.extend({"page": key, "problem": p} for p in format_problems(page))
    return out
