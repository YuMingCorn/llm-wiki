"""Builds a PowerPoint deck from a slide outline (slides/<concept>/outline.md).

Subcommands:
  check <outline>             mechanical checks; exit 1 on any problem
  build <outline> [-o OUT]    check, then write the .pptx
  render <pptx> [--out DIR]   export each slide as PNG through PowerPoint
  animate <source.py>         render a Manim scene to .mp4 and a poster .png
  formula <latex>             print the OMML for one formula (debugging)

The outline format and the six layouts are described in
.claude/skills/wiki-slides/SKILL.md.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

# -I drops the script directory from sys.path; _common lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import slides_grammar  # noqa: E402
from _common import split_frontmatter  # noqa: E402

if TYPE_CHECKING:
    from lxml.etree import _Element
    from pptx.slide import Slide as PptxSlide
    from pptx.text.text import TextFrame, _Paragraph, _Run

LAYOUTS = ("封面", "圖片", "公式", "比較表", "條列", "流程圖")
FIELDS = ("版型", "重點", "副標", "要點", "圖", "圖說", "影片", "焦點",
          "公式", "符號", "表格", "流程", "備忘稿", "出處",
          "類型", "章", "漸進", "反白")
LIST_FIELDS = {"要點", "符號", "出處", "流程"}
REQUIRED = {
    "封面": (),
    "圖片": ("圖",),
    "公式": ("公式",),
    "比較表": ("表格",),
    "條列": ("要點",),
    "流程圖": ("流程",),
}
LATIN_FONT = "Helvetica"
EA_FONT = "微軟正黑體"
MATH_FONT = "Cambria Math"

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
}
A, M, MC, A14 = (f"{{{NS[k]}}}" for k in ("a", "m", "mc", "a14"))
XSL_CANDIDATES = (
    r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL",
    r"C:\Program Files (x86)\Microsoft Office\root\Office16\MML2OMML.XSL",
    r"C:\Program Files\Microsoft Office\Office16\MML2OMML.XSL",
    r"C:\Program Files (x86)\Microsoft Office\Office16\MML2OMML.XSL",
)

SLIDE_RE = re.compile(r"^##\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^(" + "|".join(FIELDS) + r")\s*[:：]\s*(.*)$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
RULE_CELL_RE = re.compile(r":?-+:?")
# A line starting like `名稱: …` that is not a known field.
FIELD_LIKE_RE = re.compile(r"^([^\s:：|#*\-`$\[]{1,6})\s*[:：]")
# Old or simplified-Chinese field names, caught even inside 備忘稿.
MISSPELLED = {"白話", "重点", "要点", "符号", "出处", "备忘稿", "图", "图说",
              "副标", "版式"}
# Inline math `$x$`. A `$` before a number is a price unless the span has no
# space (`$1/m$`) or holds LaTeX syntax (`$10^{-2}$`), so `$100 與 $5` stays
# plain text.
INLINE_MATH_RE = re.compile(
    r"\$((?![\d\s])[^$\n]+?|\d[^$\s]*|\d[^$\n]*?[\\^_{}][^$\n]*?)\$")
# LaTeX left outside `$…$`: a command or a braced sub/superscript.
BARE_LATEX_RE = re.compile(r"\\[A-Za-z]+|[\^_]\{")
# Fields whose text may hold inline `$…$` math; 備忘稿 must stay plain.
RICH_FIELDS = ("重點", "副標", "要點", "圖說", "表格")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
SUB_ITEM = "  "  # prefix marking a second-level 要點 item

BLACK = (0x00, 0x00, 0x00)
GRAY = (0x59, 0x59, 0x59)
DIM = (0xBF, 0xBF, 0xBF)  # items not highlighted on a roadmap or build slide
ACCENT = (0xA3, 0x1F, 0x34)  # dark red: titles, left bar, footer band
HEADER = (0xD9, 0xDE, 0xE3)  # table header fill
WHITE = (0xFF, 0xFF, 0xFF)
NO_STYLE_GRID = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # thin black grid
EMU_PER_INCH = 914400


class FormulaError(ValueError):
    """A LaTeX string could not be turned into an equation."""


@dataclass
class Slide:
    """One `##` section of the outline."""

    title: str
    line: int
    fields: dict[str, str | list[str]] = field(default_factory=dict)
    unknown: list[tuple[int, str]] = field(default_factory=list)

    @property
    def layout(self) -> str:
        """The 版型 value."""
        return self.text("版型")

    def text(self, name: str) -> str:
        """Returns a text field, stripped; empty if missing or a list."""
        value = self.fields.get(name, "")
        return value.strip() if isinstance(value, str) else ""

    def items(self, name: str) -> list[str]:
        """Returns a list field; empty if missing or plain text."""
        value = self.fields.get(name, [])
        return value if isinstance(value, list) else []


@dataclass
class Outline:
    """A parsed outline file."""

    path: Path
    meta: dict
    slides: list[Slide]

    @property
    def title(self) -> str:
        """The frontmatter title, or the folder name."""
        return str(self.meta.get("title", self.path.parent.name))


# --- outline parsing -------------------------------------------------------


def parse_outline(path: Path) -> Outline:
    """Reads an outline file. Text before the first `##` is ignored.

    Args:
        path: The outline file.

    Returns:
        The frontmatter and the slides, each with its 1-based line number.
    """
    text = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    offset = text.count("\n") - body.count("\n") + 1
    slides: list[Slide] = []
    current: Slide | None = None
    name: str | None = None
    for number, line in enumerate(body.splitlines(), start=offset):
        heading = SLIDE_RE.match(line)
        if heading:
            title = re.sub(r"^\d+\.\s+", "", heading.group(1))
            current = Slide(title, number)
            slides.append(current)
            name = None
            continue
        if current is None:
            continue
        found = FIELD_RE.match(line)
        if found:
            name, rest = found.groups()
            rest = rest.strip()
            if name in LIST_FIELDS:
                current.fields[name] = [rest] if rest else []
            else:
                current.fields[name] = rest
            continue
        guess = FIELD_LIKE_RE.match(line)
        if guess and (name != "備忘稿" or guess.group(1) in MISSPELLED):
            # Looks like a field but is not one: it would silently become
            # part of the previous field.
            current.unknown.append((number, guess.group(1)))
            continue
        if name is None:
            continue
        value = current.fields[name]
        if isinstance(value, list):
            item = re.match(r"^(\s*)[-*]\s+(.*)$", line)
            if item:
                indent = item.group(1).expandtabs(4)
                level = SUB_ITEM if len(indent) >= 2 else ""
                value.append(level + item.group(2).strip())
            elif line.strip() and value:
                value[-1] += " " + line.strip()
        else:
            current.fields[name] = value + "\n" + line
    return Outline(path, meta or {}, slides)


def highlights(slide: Slide) -> list[str]:
    """Returns the 反白 items, split on commas or 、."""
    return [h.strip() for h in re.split(r"[,，、]", slide.text("反白"))
            if h.strip()]


def _norm_item(text: str) -> str:
    """Normalizes a 要點 or 反白 item for comparison."""
    return " ".join(text.split()).casefold()


def table_rows(text: str) -> list[list[str]]:
    """Splits a Markdown table into rows of cells, dropping the rule row."""
    rows, lines = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        lines += 1
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Only the line right after the header is the rule row.
        if lines == 2 and all(RULE_CELL_RE.fullmatch(c) for c in cells):
            continue
        rows.append(cells)
    return rows


def headings(path: Path) -> set[str]:
    """Returns the normalized heading texts of a Markdown file."""
    out, fenced = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else HEADING_RE.match(line)
        if found:
            out.add(" ".join(found.group(1).split()).casefold())
    return out


def source_label(item: str) -> str:
    """Turns `[Switch §3](x.md#a)（p.7）` into `Switch §3（p.7）`."""
    return LINK_RE.sub(lambda m: m.group(1), item).strip()


def split_symbol(item: str) -> tuple[str, str]:
    """Splits a 符號 item `f_i：說明` at its first colon, either width."""
    cuts = [i for i in (item.find("："), item.find(":")) if i >= 0]
    if not cuts:
        return item.strip().strip("`"), ""
    cut = min(cuts)
    return item[:cut].strip().strip("`"), item[cut + 1:].strip()


# --- flow charts -------------------------------------------------------------
# Each 流程 item is a chain of nodes joined by arrows, spaces around arrows:
#   A[標籤] -> B[標籤]! -(箭頭文字)-> C ~> D
# `id[label]` gives a node its label once; later mentions use the bare id.
# A trailing `!` highlights the node. `->` is a solid arrow, `~>` a dashed
# one; `-(text)->` and `~(text)~>` write text on the arrow. `<br>` breaks a
# label into lines. Nodes go into columns left to right by longest path.
# Arrow text may hold one level of parentheses, as in `$f(x)$`. Ids are
# ASCII; a label cannot contain ` -> ` (the chain is split on arrows first).
ARROW_RE = re.compile(r"\s+([-~])(?:\(((?:[^()]|\([^()]*\))*)\)\1)?>\s+")
NODE_RE = re.compile(r"^([A-Za-z0-9_]+)(?:\[(.*)\])?(!)?$")
BREAK_RE = re.compile(r"<br\s*/?>")
FLOW_MAX_COLUMNS, FLOW_MAX_ROWS = 7, 6
FLOW_SIZES = (18, 16, 14, 12)
EDGE_SIZE = 12


class FlowError(ValueError):
    """A 流程 field could not be read as a graph."""


@dataclass
class FlowNode:
    """A box in a flow chart."""

    key: str
    label: str
    strong: bool = False


@dataclass
class FlowEdge:
    """An arrow from `src` to `dst`."""

    src: str
    dst: str
    label: str = ""
    dashed: bool = False
    via: list[str] = field(default_factory=list)  # waypoints, one per column


@dataclass
class Flow:
    """A parsed flow chart: nodes, arrows and the column of each node.

    An arrow that skips columns gets a waypoint in each column it crosses;
    waypoints take a slot in their column like a node, so the arrow can bend
    around the boxes instead of running through them. Their keys start
    with `~`.
    """

    nodes: dict[str, FlowNode]
    edges: list[FlowEdge]
    columns: list[list[str]]

    def labels(self) -> list[str]:
        """All node and arrow texts, for the LaTeX checks."""
        out = [n.label for n in self.nodes.values()]
        return out + [e.label for e in self.edges if e.label]


def _flow_node(token: str, nodes: dict[str, FlowNode],
               named: set[str]) -> str:
    found = NODE_RE.match(token.strip())
    if not found:
        raise FlowError(f"看不懂節點「{token.strip()}」，寫成 `代號[標籤]`")
    key, label, bang = found.groups()
    if label is not None:
        if key in named and nodes[key].label != label.strip():
            raise FlowError(f"節點 {key} 的標籤寫了兩種")
        named.add(key)
    node = nodes.setdefault(key, FlowNode(key, key))
    if label is not None:
        node.label = label.strip()
    node.strong = node.strong or bool(bang)
    return key


def parse_flow(items: list[str]) -> Flow:
    """Reads 流程 items into nodes, arrows and columns.

    Args:
        items: The 流程 list, one arrow chain per item.

    Returns:
        The graph; columns hold node keys top to bottom.

    Raises:
        FlowError: A node or arrow cannot be read, or the arrows loop.
    """
    nodes: dict[str, FlowNode] = {}
    named: set[str] = set()
    edges: list[FlowEdge] = []
    for item in items:
        parts = ARROW_RE.split(" " + item.strip() + " ")
        parts[0], parts[-1] = parts[0].strip(), parts[-1].strip()
        prev = _flow_node(parts[0], nodes, named)
        for i in range(1, len(parts), 3):
            dash, label, token = parts[i], parts[i + 1], parts[i + 2]
            key = _flow_node(token, nodes, named)
            if key == prev:
                raise FlowError(f"節點 {key} 的箭頭指回自己")
            edges.append(FlowEdge(prev, key, (label or "").strip(),
                                  dash == "~"))
            prev = key
    preds: dict[str, list[str]] = {k: [] for k in nodes}
    for edge in edges:
        preds[edge.dst].append(edge.src)
    column: dict[str, int] = {}
    pending = list(nodes)
    while pending:
        ready = [k for k in pending if all(p in column for p in preds[k])]
        if not ready:
            raise FlowError("箭頭形成循環：" + "、".join(pending))
        for key in ready:
            column[key] = max((column[p] + 1 for p in preds[key]), default=0)
            pending.remove(key)
    columns: list[list[str]] = [[] for _ in range(max(column.values()) + 1)]
    for key in nodes:  # first-appearance order
        columns[column[key]].append(key)
    for number, edge in enumerate(edges):
        prev = edge.src
        for c in range(column[edge.src] + 1, column[edge.dst]):
            point = f"~{number}.{c}"
            preds[point] = [prev]
            columns[c].append(point)
            edge.via.append(point)
            prev = point
    # One sweep left to right: sit each node near the mean height of the
    # nodes pointing to it, which removes most crossings in small charts.
    place: dict[str, float] = {}
    for col in columns:
        guess = {k: (sum(place[p] for p in preds[k]) / len(preds[k])
                     if preds[k] else (i + 0.5) / len(col))
                 for i, k in enumerate(col)}
        col.sort(key=lambda k: guess[k])
        for i, k in enumerate(col):
            place[k] = (i + 0.5) / len(col)
    return Flow(nodes, edges, columns)


def flow_boxes(flow: Flow, width: float, height: float
               ) -> dict[str, tuple[float, float, float, float]]:
    """Places each node; returns (left, top, width, height) in inches.

    Positions are relative to the chart area's top-left corner.
    """
    count = len(flow.columns)
    col_w = width / count
    ratio = 0.55 if any(e.label for e in flow.edges) else 0.72
    box_w = min(col_w * ratio, 3.6)
    box_h = min(1.3, height / max(len(c) for c in flow.columns) * 0.66)
    boxes = {}
    for c, col in enumerate(flow.columns):
        for r, key in enumerate(col):
            mid_x = col_w * (c + 0.5)
            mid_y = height * (r + 0.5) / len(col)
            boxes[key] = (mid_x - box_w / 2, mid_y - box_h / 2, box_w, box_h)
    return boxes


def label_height(label: str, width: float, size: float) -> float:
    """Height in inches of a label, one paragraph per `<br>`."""
    parts = [p.strip() for p in BREAK_RE.split(label)]
    lines = sum(text_lines(p, width, size) for p in parts)
    tall = 0.2 * sum(bool(re.search(r"\\(frac|sum)", p)) for p in parts)
    return lines * size * 1.3 / 72 + tall


def node_size(label: str, box_w: float, box_h: float) -> float | None:
    """The largest font size that fits the label in its box, or None.

    Latin words break only after a hyphen, so the widest piece must fit on
    one line. Bold Latin measured ~0.5 em per character, but a rounded box
    insets its text, so count 0.6 em and 0.3 inch of padding.
    """
    words = re.findall(r"[A-Za-z0-9.]+-?", INLINE_MATH_RE.sub("", label))
    longest = max((len(w) for w in words), default=0)
    for size in FLOW_SIZES:
        if (label_height(label, box_w - 0.25, size) <= box_h - 0.1
                and longest * 0.6 * size / 72 <= box_w - 0.3):
            return size
    return None


def flow_font(flow: Flow, boxes: dict[str, tuple[float, float, float,
                                                  float]]) -> float | None:
    """One font size for every box of a chart: the size the tightest fits."""
    sizes = []
    for key, node in flow.nodes.items():
        size = node_size(node.label, boxes[key][2], boxes[key][3])
        if size is None:
            return None
        sizes.append(size)
    return min(sizes)


def edge_room(flow: Flow, boxes: dict[str, tuple[float, float, float, float]],
              width: float) -> float:
    """Width in inches for arrow text: the gap between two columns."""
    box_w = next(iter(boxes.values()))[2]
    return width / len(flow.columns) - box_w - 0.1


def flow_area(slide: Slide) -> tuple[float, float]:
    """Width and height in inches left for the chart on a 流程圖 slide."""
    width = FULL_W - (FULL_W * 0.32 + 0.3 if slide.items("要點") else 0)
    return width, BODY_BOTTOM - body_top(slide) - 0.1


# --- layout estimates --------------------------------------------------------
# Inches on the 13.333 × 7.5 page; DeckBuilder uses the same numbers.
PAGE_W, MARGIN_IN, BODY_BOTTOM = 13.333, 0.45, 7.5 - 0.42 - 0.42
FULL_W = PAGE_W - 2 * MARGIN_IN


def _braced(latex: str, start: int) -> tuple[str, int]:
    """Returns the `{...}` group starting at `start` and the index after it."""
    depth = 0
    for i in range(start, len(latex)):
        depth += {"{": 1, "}": -1}.get(latex[i], 0)
        if depth == 0:
            return latex[start + 1:i], i + 1
    return latex[start + 1:], len(latex)


def _math_chars(latex: str) -> float:
    """Visible width of a formula in characters.

    A fraction counts its wider part (numerator and denominator are stacked);
    sub- and superscripts count 0.6, since they are set smaller.
    """
    out, i = 0.0, 0
    while i < len(latex):
        if latex.startswith(r"\frac", i) and latex[i + 5:i + 6] == "{":
            num, j = _braced(latex, i + 5)
            den, i = _braced(latex, j) if latex[j:j + 1] == "{" else ("", j)
            out += max(_math_chars(num), _math_chars(den))
            continue
        if latex[i] in "^_":
            command = re.match(r"\\[A-Za-z]+", latex[i + 1:])
            if latex[i + 1:i + 2] == "{":
                script, i = _braced(latex, i + 1)
            elif command:  # `x^\alpha`: the script is the whole command
                script, i = command.group(0), i + 1 + len(command.group(0))
            else:
                script, i = latex[i + 1:i + 2], i + 2
            out += 0.6 * _math_chars(script)
            continue
        command = re.match(r"\\[A-Za-z]+|\\.", latex[i:])
        if command:
            word = command.group(0)
            out += 0 if word in VISIBLE_SKIP else 1
            i += len(word)
            continue
        out += 0 if latex[i] in "{}" else 1
        i += 1
    return out


VISIBLE_SKIP = {r"\mathrm", r"\text", r"\mathbf", r"\left", r"\right", r"\,",
                r"\ ", r"\mathcal"}


def _units(text: str) -> float:
    """Approximate width of text in em: CJK is 1, Latin and digits ~0.55.

    Equation characters count ~0.6 each (measured on rendered slides).
    """
    math = 0.0
    for found in INLINE_MATH_RE.finditer(text):
        try:
            math += 0.6 * _math_chars(found.group(1))
        except RecursionError:  # absurdly deep nesting: count raw length
            math += 0.6 * len(found.group(1))
    text = INLINE_MATH_RE.sub("", text)
    return math + sum(1.0 if ord(c) >= 0x2E80 else 0.55 for c in text)


def text_lines(text: str, width_in: float, size: float) -> int:
    """Estimates how many lines `text` takes in a box of that width."""
    per_line = width_in * 72 / size
    return max(1, math.ceil(_units(text) / per_line))


def points_height(items: list[str], width_in: float, size: float) -> float:
    """Estimated height in inches of a bullet list (see DeckBuilder._points)."""
    total = 0.0
    for item in items:
        level = 1 if item.startswith(SUB_ITEM) else 0
        pt = size - 3 * level
        lines = text_lines(item.strip(), width_in - 0.4 - 0.45 * level, pt)
        tall = 0.35 if re.search(r"\\(frac|sum|prod|int)", item) else 0
        total += lines * pt * 1.25 / 72 + (8 if level == 0 else 2) / 72 + tall
    return total


def body_top(slide: Slide) -> float:
    """Where the body starts, below the title and the 重點 line."""
    return 1.8 if slide.text("重點") else 1.35


# --- checks ----------------------------------------------------------------


def check(outline: Outline) -> list[str]:
    """Runs the mechanical checks.

    Args:
        outline: The parsed outline.

    Returns:
        Human-readable problems; an empty list means the outline is OK.
    """
    problems: list[str] = []
    if not outline.slides:
        problems.append("沒有任何投影片（每張用 `## 標題` 開頭）")
    grammar, grammar_problems = _grammar(outline)
    no_source = set(grammar.no_source) if grammar else set()
    for slide in outline.slides:
        exempt = slide.text("類型") in no_source
        problems += _check_slide(outline.path.parent, slide, exempt)
    problems += grammar_problems
    problems += structure(outline, grammar)[0]
    if not grammar:
        problems += _highlight_points(outline)
    return problems + term_problems(outline)


def repo_root(path: Path) -> Path:
    """The nearest folder above `path` that holds `wiki/`, else this repo."""
    for folder in Path(path).resolve().parents:
        if (folder / "wiki").is_dir():
            return folder
    return Path(__file__).resolve().parent.parent


def _grammar(outline: Outline) -> tuple[slides_grammar.Grammar | None,
                                         list[str]]:
    """Loads the grammar named by the frontmatter `文法`, if any."""
    name = outline.meta.get("文法")
    if name is None:
        return None, []
    if not isinstance(name, str) or not re.fullmatch(r"[\w-]+", name):
        return None, [f"frontmatter「文法」要是頁型表的名稱，不是 {name!r}"]
    try:
        return _load_grammar(name, outline.path), []
    except ValueError as e:
        return None, [str(e)]


def _load_grammar(name: str, near: Path) -> slides_grammar.Grammar:
    """Loads a grammar from the repo holding `near`, else from this repo."""
    path = slides_grammar.grammar_path(name, repo_root(near))
    if not path.is_file():
        path = slides_grammar.grammar_path(
            name, Path(__file__).resolve().parent.parent)
    return slides_grammar.load_grammar(path)


def _items(outline: Outline) -> list[slides_grammar.Item]:
    return [slides_grammar.Item(
        where=f"第 {s.line} 行「{s.title}」", type=s.text("類型"),
        chapter=s.text("章") or None, build=s.text("漸進") or None,
        highlight=highlights(s), points=list(s.items("要點")))
        for s in outline.slides]


def structure(outline: Outline, grammar: slides_grammar.Grammar | None = None,
              ) -> tuple[list[str], list[str]]:
    """Checks the （p.N） page notes (S3), plus S4–S11 when `文法` is set.

    Args:
        outline: The parsed outline.
        grammar: The grammar; loaded from the frontmatter when None.

    Returns:
        The errors and the warnings.
    """
    if grammar is None:
        grammar, _ = _grammar(outline)
    errors: list[str] = []
    warnings: list[str] = []
    if grammar is not None:
        chapters, errors = slides_grammar.parse_chapters(
            outline.meta.get("章"))
        more, warnings = slides_grammar.check_structure(
            _items(outline), chapters, grammar)
        errors = errors + more
    base, root = outline.path.parent, repo_root(outline.path)
    for slide in outline.slides:
        where = f"第 {slide.line} 行「{slide.title}」"
        for item in slide.items("出處"):
            e, w = slides_grammar.page_ref_problems(base, root, item, where)
            errors += e
            warnings += w
    return errors, warnings


def _highlight_points(outline: Outline) -> list[str]:
    """Without a grammar: every 反白 item must be one of the 要點."""
    problems = []
    for slide in outline.slides:
        points = {_norm_item(p) for p in slide.items("要點")}
        problems += [f"第 {slide.line} 行「{slide.title}」：反白「{h}」"
                     "不在這頁的要點裡"
                     for h in highlights(slide) if _norm_item(h) not in points]
    return problems


# Fields shown on the slide itself; 備忘稿 and 出處 do not count as a use.
VISIBLE_FIELDS = ("重點", "副標", "要點", "圖說", "符號", "表格", "流程")


def _visible_text(slide: Slide) -> str:
    parts = [slide.title]
    for name in VISIBLE_FIELDS:
        value = slide.fields.get(name, "")
        parts += value if isinstance(value, list) else [value]
    return "\n".join(parts)


def _term_re(name: str) -> re.Pattern[str]:
    """Case-insensitive; names made of Latin letters match whole words."""
    pattern = re.escape(name)
    if re.search(r"[A-Za-z0-9]", name[:1]):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if re.search(r"[A-Za-z0-9]", name[-1:]):
        pattern += r"(?![A-Za-z0-9])"
    return re.compile(pattern, re.IGNORECASE)


def term_problems(outline: Outline) -> list[str]:
    """Checks that each term is defined before it is used on a slide.

    The frontmatter `名詞` maps a slide's position (1-based, in outline
    order) to the names defined there, aliases included, e.g.
    `名詞: {2: [Foo buffer, 緩衝區]}`. A use is a match in the fields shown
    on the slide (`VISIBLE_FIELDS` and the title); a use on the defining
    slide itself is fine.

    Args:
        outline: The parsed outline.

    Returns:
        One line per problem; empty when the outline has no `名詞`.
    """
    spec = outline.meta.get("名詞")
    if spec is None:
        return []
    if not isinstance(spec, dict):
        return ["frontmatter「名詞」要寫成「頁序: [名稱, 別名…]」"]
    texts = [_visible_text(s) for s in outline.slides]
    problems = []
    for page, names in spec.items():
        if (not isinstance(page, int) or isinstance(page, bool)
                or not isinstance(names, list)
                or not all(isinstance(n, str) and n.strip() for n in names)):
            problems.append(f"名詞「{page}」：要寫成「頁序: [名稱, 別名…]」")
            continue
        if not 1 <= page <= len(texts):
            problems.append(f"名詞 {names}：定義頁第 {page} 頁不存在"
                            f"（共 {len(texts)} 頁）")
            continue
        for name in names:
            found = _term_re(str(name))
            if not found.search(texts[page - 1]):
                problems.append(f"名詞「{name}」：定義頁第 {page} 頁"
                                f"「{outline.slides[page - 1].title}」"
                                "的投影片上沒有出現這個詞")
            first = next(i for i, t in enumerate(texts, 1)
                         if found.search(t) or i == page)
            if first < page:
                problems.append(f"名詞「{name}」：第 {first} 頁"
                                f"「{outline.slides[first - 1].title}」"
                                f"就用到，定義在第 {page} 頁")
    return problems


def _check_slide(base: Path, slide: Slide, exempt: bool = False,
                 ) -> list[str]:
    where = f"第 {slide.line} 行「{slide.title}」"
    known = "、".join(FIELDS)
    problems = [f"第 {line} 行：「{name}」不是欄位名稱（可用：{known}）"
                for line, name in slide.unknown]
    if slide.layout not in LAYOUTS:
        names = "、".join(LAYOUTS)
        return problems + [
            f"{where}：版型「{slide.layout}」不在 {names} 之中"]
    problems += [f"{where}：{slide.layout}版型缺少「{name}」"
                 for name in REQUIRED[slide.layout]
                 if not slide.fields.get(name)]
    if slide.layout != "封面" and not exempt and not slide.items("出處"):
        problems.append(f"{where}：缺少出處")
    if "$" in slide.text("公式"):
        problems.append(f"{where}：「公式」不加 `$`")
    problems += [f"{where}：符號「{s}」沒有冒號，寫成 `LaTeX：說明`"
                 for s in slide.items("符號") if not split_symbol(s)[1]]
    problems += _check_fit(slide, where)
    texts: dict[str, str] = {"標題": slide.title}
    for name in RICH_FIELDS:
        value = slide.fields.get(name, "")
        texts[name] = " ".join(value) if isinstance(value, list) else value
    meanings = (split_symbol(s)[1] for s in slide.items("符號"))
    texts["符號說明"] = " ".join(meanings)
    if slide.layout == "流程圖" and slide.items("流程"):
        flow_problems, flow = _check_flow(slide, where)
        problems += flow_problems
        if flow:
            texts["流程"] = " ".join(flow.labels())
    inline: list[str] = []
    for name, text in texts.items():
        inline += INLINE_MATH_RE.findall(text)
        if BARE_LATEX_RE.search(INLINE_MATH_RE.sub("", text)):
            problems.append(f"{where}：「{name}」有 LaTeX 寫在 `$…$` 外面")
    notes = slide.text("備忘稿")
    if INLINE_MATH_RE.search(notes) or BARE_LATEX_RE.search(notes):
        problems.append(f"{where}：「備忘稿」含 LaTeX；備忘稿不顯示公式，"
                        "改用 Unicode（例如 10⁻²、α、gᵢ）")
    image = slide.text("圖")
    if image:
        target = (base / image).resolve()
        problems += _check_image(base, target, image, where)
    if slide.text("影片"):
        problems += _check_video(base, slide, where)
    if slide.text("焦點"):
        problems += _check_focus(slide, where)
    formulas = [slide.text("公式")] if slide.text("公式") else []
    formulas += [split_symbol(s)[0] for s in slide.items("符號")]
    formulas += inline
    for latex in formulas:
        try:
            to_omml(latex)
        except FormulaError as e:
            problems.append(f"{where}：公式 `{latex}` 無法轉換（{e}）")
    if slide.layout == "比較表":
        rows = table_rows(slide.text("表格"))
        if len(rows) < 2:
            problems.append(f"{where}：表格至少要有標題列和一列內容")
        elif len({len(r) for r in rows}) != 1:
            problems.append(f"{where}：表格各列的欄數不同")
    for item in slide.items("出處"):
        links = LINK_RE.findall(item)
        if not links:
            problems.append(f"{where}：出處「{item}」沒有連結")
        for _, target in links:
            problems += _check_link(base, target, where)
    return problems


def _check_flow(slide: Slide, where: str) -> tuple[list[str], Flow | None]:
    """Reads the 流程 field and checks that the chart fits the page."""
    try:
        flow = parse_flow(slide.items("流程"))
    except FlowError as e:
        return [f"{where}：流程 {e}"], None
    problems = []
    if len(flow.columns) > FLOW_MAX_COLUMNS:
        problems.append(f"{where}：流程有 {len(flow.columns)} 欄，最多 "
                        f"{FLOW_MAX_COLUMNS} 欄，請拆頁或合併節點")
    if max(len(c) for c in flow.columns) > FLOW_MAX_ROWS:
        problems.append(f"{where}：流程同一欄最多 {FLOW_MAX_ROWS} 格（跨欄的"
                        "箭頭在經過的欄也佔一格），請拆頁或合併節點")
    if problems:
        return problems, flow
    width, height = flow_area(slide)
    boxes = flow_boxes(flow, width, height)
    for key, node in flow.nodes.items():
        _, _, box_w, box_h = boxes[key]
        if node_size(node.label, box_w, box_h) is None:
            problems.append(f"{where}：流程節點 {key} 的文字太長，請縮短")
    room = edge_room(flow, boxes, width)
    for edge in flow.edges:
        if edge.label and label_height(edge.label, room, EDGE_SIZE) > 0.5:
            problems.append(f"{where}：流程 {edge.src}→{edge.dst} 的箭頭文字"
                            "太長，最多兩行")
    points = slide.items("要點")
    if points and points_height(points, FULL_W * 0.32, 20) > height:
        problems.append(f"{where}：內容太多會超出頁面，請刪字或拆頁")
    return problems, flow


def _check_fit(slide: Slide, where: str) -> list[str]:
    """Estimates whether the text fits its boxes; rough, errs on safe side."""
    too_much = f"{where}：內容太多會超出頁面，請刪字或拆頁"
    problems = []
    title_size = 44 if slide.layout == "封面" else 36
    if text_lines(slide.title, FULL_W, title_size) > (2 if slide.layout ==
                                                      "封面" else 1):
        problems.append(f"{where}：標題太長會換行，請縮短")
    if slide.layout == "封面":
        return problems
    if text_lines(slide.text("重點"), FULL_W, 22) > 1:
        problems.append(f"{where}：「重點」太長會換行，請縮短成一行")
    labels = "；".join(dict.fromkeys(
        source_label(s) for s in slide.items("出處")))
    if text_lines("出處：" + labels, FULL_W, 10) > 1:
        problems.append(f"{where}：出處文字太長，請縮短連結的標題")
    top = body_top(slide)
    room = BODY_BOTTOM - top
    points = slide.items("要點")
    if slide.layout == "條列" and points_height(points, FULL_W, 24) > room:
        problems.append(too_much)
    if slide.layout == "圖片" and points and points_height(
            points, FULL_W * 0.36, 20) > room:
        problems.append(too_much)
    if slide.layout == "公式":
        col = FULL_W * 0.55 if slide.text("圖") else FULL_W
        used = 1.45 + points_height(points, col, 20) + 0.15
        if slide.items("符號"):
            # Header row, then each row as tall as its wrapped symbol cell;
            # the symbol column is 30% of the table width.
            table_w = col if slide.text("圖") else min(col, 8.0)
            used += 0.42 + sum(
                max(1, text_lines(f"${split_symbol(s)[0]}$",
                                  table_w * 0.3 - 0.2, 16)) * 0.33 + 0.09
                + (0.3 if r"\frac" in s else 0)
                for s in slide.items("符號"))
        if used > room:
            problems.append(too_much)
    if slide.layout == "比較表":
        rows = table_rows(slide.text("表格"))
        if rows and len({len(r) for r in rows}) == 1:
            size = 17 if len(rows) <= 6 else 14
            cell_w = FULL_W / len(rows[0]) - 0.2
            height = sum(
                max(text_lines(c, cell_w, size) for c in row)
                * size * 1.25 / 72 + 0.12 for row in rows)
            if height > room - 0.1:
                problems.append(too_much)
    return problems


def _check_image(base: Path, target: Path, image: str,
                 where: str) -> list[str]:
    """Checks that a figure exists, opens, and comes from an allowed place.

    Allowed places, under a folder that also contains the outline:
    `wiki/assets/<source>/` and `raw/sources/<name>_images/`. A diagram
    drawn by `tools/diagram.py` may also sit in the deck's own `diagrams/`
    folder, next to the `.json` spec it was drawn from.
    """
    from PIL import Image

    if not target.is_file():
        return [f"{where}：找不到圖檔 {image}"]
    roots = base.resolve().parents
    in_assets = any(target.is_relative_to(r / "wiki" / "assets")
                    for r in roots)
    in_raw = target.parent.name.endswith("_images") and any(
        target.parent.parent == r / "raw" / "sources" for r in roots)
    in_diagrams = target.parent == base.resolve() / "diagrams"
    if in_diagrams and not target.with_suffix(".json").is_file():
        return [f"{where}：自繪圖要有同名的 .json 規格檔：{image}"]
    in_animations = target.parent == base.resolve() / "animations"
    if in_animations and not target.with_suffix(".py").is_file():
        return [f"{where}：動畫封面要有同名的 .py 原始檔：{image}"]
    if not (in_assets or in_raw or in_diagrams or in_animations):
        return [f"{where}：圖檔要放在 wiki/assets/<source>/、"
                f"raw/sources/<名稱>_images/、diagrams/ 或 animations/："
                f"{image}"]
    if target.suffix.lower() not in IMAGE_SUFFIXES:
        return [f"{where}：不是支援的圖檔格式 {image}"]
    try:
        with Image.open(target) as img:
            img.verify()
    except Exception as e:  # PIL raises several types for broken files
        return [f"{where}：圖檔無法開啟 {image}（{type(e).__name__}）"]
    return []


def parse_focus(text: str) -> tuple[float, float, float, float] | None:
    """Reads a 焦點 value `x0, y0, x1, y1`: fractions of the figure's width
    and height. Returns None when it is not four numbers that make a box
    inside the figure."""
    parts = re.split(r"\s*[,，]\s*", text.strip())
    try:
        x0, y0, x1, y1 = (float(v) for v in parts)
    except ValueError:
        return None
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return x0, y0, x1, y1


def _check_focus(slide: Slide, where: str) -> list[str]:
    """A 焦點 dims a figure except one box, so a slide can walk through
    the parts of a paper figure without saving edited copies."""
    if not slide.text("圖") or slide.text("影片"):
        return [f"{where}：「焦點」只能用在有「圖」、沒有「影片」的投影片"]
    if parse_focus(slide.text("焦點")) is None:
        return [f"{where}：「焦點」要寫成 x0, y0, x1, y1，"
                "都是 0–1 的圖片比例，且 x0 < x1、y0 < y1"]
    return []


def _check_video(base: Path, slide: Slide, where: str) -> list[str]:
    """Checks a 影片 field: an mp4 made by `animate` from a same-name .py.

    The video sits in the deck's `animations/` folder, and the slide's 圖
    is its poster frame (the same name with .png), so the layout, the fit
    check and the PNG render all work on the poster.
    """
    video = slide.text("影片")
    target = (base / video).resolve()
    if slide.layout not in ("圖片", "公式"):
        return [f"{where}：「影片」只能用在圖片或公式版型"]
    if target.parent != base.resolve() / "animations":
        return [f"{where}：影片要放在 animations/：{video}"]
    if target.suffix.lower() != ".mp4":
        return [f"{where}：影片要是 .mp4：{video}"]
    if not target.is_file():
        return [f"{where}：找不到影片 {video}（先跑 slides.py animate）"]
    if not target.with_suffix(".py").is_file():
        return [f"{where}：影片要有同名的 .py 原始檔：{video}"]
    image = slide.text("圖")
    if not image or (base / image).resolve() != target.with_suffix(".png"):
        return [f"{where}：有「影片」時，「圖」要寫同名的封面 "
                f"{Path(video).with_suffix('.png').as_posix()}"]
    return []


def _check_link(base: Path, target: str, where: str) -> list[str]:
    """Checks that a relative link and its heading anchor exist."""
    if re.match(r"^[a-z]+://", target):
        return []
    file_part, _, fragment = target.partition("#")
    path = (base / unquote(file_part)).resolve()
    if not path.is_file():
        return [f"{where}：出處連結的檔案不存在 {target}"]
    if fragment and path.suffix == ".md":
        wanted = " ".join(unquote(fragment).split()).casefold()
        if wanted not in headings(path):
            return [f"{where}：出處錨點 #{unquote(fragment)} 不是 "
                    f"{path.name} 的標題"]
    return []


# --- formulas --------------------------------------------------------------


def find_xsl() -> Path | None:
    """Locates Office's MathML-to-OMML stylesheet.

    Returns:
        The stylesheet path (MML2OMML_XSL overrides the usual places), or
        None when Office is not installed.
    """
    env = os.environ.get("MML2OMML_XSL")
    for candidate in ([env] if env else []) + list(XSL_CANDIDATES):
        if Path(candidate).is_file():
            return Path(candidate)
    return None


@lru_cache(maxsize=1)
def _transform() -> object:
    from lxml import etree

    xsl = find_xsl()
    if xsl is None:
        raise FormulaError("找不到 Office 的 MML2OMML.XSL（可用環境變數 "
                           "MML2OMML_XSL 指定）")
    return etree.XSLT(etree.parse(str(xsl)))


@lru_cache(maxsize=256)
def _omml_text(latex: str) -> str:
    from latex2mathml.converter import convert
    from lxml import etree

    if "\\\\" in latex:
        # latex2mathml reads `\\` as a space or a stray backslash, so a
        # cases/aligned block silently collapses into one row.
        raise FormulaError("不支援換行 `\\\\`（會被併成一列）；拆成多條公式，"
                           "或改寫成單列，例如 "
                           "`g=s \\text{ if } … \\text{, otherwise } 0`")
    if _unbalanced(latex):
        raise FormulaError("大括號 { } 不成對")
    if re.search(r"\\(begin|end)\{", latex) or "\\color" in latex:
        # Environments need `\\` rows (see above) or are dropped silently.
        raise FormulaError("不支援 \\begin…\\end 環境（cases、aligned 等）與 "
                           "\\color；改寫成單列，例如 "
                           "`g=s \\text{ if } … \\text{, otherwise } 0`")
    try:
        mathml = convert(latex)
        tree = etree.fromstring(mathml.encode("utf-8"))
    except Exception as e:  # latex2mathml and lxml raise many unrelated types
        raise FormulaError(f"LaTeX 語法錯誤：{type(e).__name__}") from e
    # latex2mathml copies unknown commands into the output instead of failing.
    leftovers = sorted({t for t in tree.itertext() if "\\" in t})
    if leftovers:
        raise FormulaError(f"不認得的指令 {'、'.join(leftovers)}")
    try:
        omml = _transform()(tree).getroot()
    except etree.XSLTApplyError as e:
        raise FormulaError(f"XSL 轉換失敗：{e}") from e
    if omml is None or etree.QName(omml).localname != "oMath":
        raise FormulaError("XSL 沒有產生 m:oMath")
    _fill_empty_nary(omml)
    _overline_as_bar(omml)
    _isolate_astral(omml)
    return etree.tostring(omml, encoding="unicode")


ASTRAL_RE = re.compile(r"[\U00010000-\U0010FFFF]|[^\U00010000-\U0010FFFF]+")


def _isolate_astral(omml: _Element) -> None:
    """Gives each character above U+FFFF (𝒯, 𝔼, 𝟙) a run of its own.

    PowerPoint drops such a character when it shares a run with other text,
    as in the `i∈𝒯` under a sum.
    """
    from lxml import etree

    for run in list(omml.iter(M + "r")):
        text_el = run.find(M + "t")
        text = text_el.text if text_el is not None else None
        if not text or len(text) < 2 or all(ord(c) <= 0xFFFF for c in text):
            continue
        for piece in ASTRAL_RE.findall(text):
            clone = etree.fromstring(etree.tostring(run))
            clone.find(M + "t").text = piece
            run.addprevious(clone)
        run.getparent().remove(run)


OVERLINE_CHARS = {"―", "‾", "¯", "̅", "_"}


def _overline_as_bar(omml: _Element) -> None:
    """Turns the XSL's overline accent into a top bar.

    The XSL emits `m:acc` with a horizontal-bar character, which PowerPoint
    draws through the letter like a strike; `m:bar` draws a proper overline.
    """
    from lxml import etree

    for acc in list(omml.iter(M + "acc")):
        chr_el = acc.find(f"{M}accPr/{M}chr")
        if chr_el is None or chr_el.get(M + "val") not in OVERLINE_CHARS:
            continue
        bar = etree.Element(M + "bar")
        etree.SubElement(etree.SubElement(bar, M + "barPr"), M + "pos").set(
            M + "val", "top")
        bar.append(acc.find(M + "e"))
        acc.getparent().replace(acc, bar)


def _unbalanced(latex: str) -> bool:
    """True when the braces, apart from escaped ones, do not pair up."""
    depth = 0
    for brace in re.sub(r"\\[{}]", "", latex):
        if brace in "{}":
            depth += 1 if brace == "{" else -1
            if depth < 0:
                return True
    return depth != 0


STOP_CHARS = "=<>≤≥≈+−-,"


def _fill_empty_nary(omml: _Element) -> None:
    """Moves the terms after a ∑ into its empty body.

    The XSL leaves `m:e` empty when the summand is a sibling of the operator,
    which PowerPoint draws as a placeholder box. The body runs until the next
    relation, plus/minus or comma at the same level. The XSL can merge such a
    sign into a longer run (`)=0`), so a run is split at the sign.
    """
    from lxml import etree

    for nary in omml.iter(M + "nary"):
        body = nary.find(M + "e")
        if body is None or len(body):
            continue
        parent = nary.getparent()
        depth = 0  # open brackets carried across runs, as in `P(x,i)`
        while (sibling := nary.getnext()) is not None:
            text_el = sibling.find(M + "t") if sibling.tag == M + "r" else None
            if text_el is None:
                # A script or fraction can hold a bracket, as in `(a_i)^2`
                # where the `)` sits inside the m:sSup base.
                for ch in "".join(sibling.itertext()):
                    if ch in "([{":
                        depth += 1
                    elif ch in ")]}":
                        depth = max(depth - 1, 0)
                parent.remove(sibling)
                body.append(sibling)
                continue
            text = text_el.text or ""
            cut = None
            for i, ch in enumerate(text):
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    if depth == 0:  # closes a bracket opened before the ∑
                        cut = i
                        break
                    depth -= 1
                elif depth == 0 and ch in STOP_CHARS:
                    cut = i
                    break
            if cut == 0:
                break
            if cut is not None:
                head = etree.fromstring(etree.tostring(sibling))
                head.find(M + "t").text = text[:cut]
                text_el.text = text[cut:]
                body.append(head)
                break
            parent.remove(sibling)
            body.append(sibling)


def to_omml(latex: str) -> _Element:
    """Converts LaTeX to an `m:oMath` element styled for PowerPoint.

    Args:
        latex: One formula, without `$` delimiters.

    Returns:
        A new `m:oMath` element whose runs use Cambria Math.

    Raises:
        FormulaError: the LaTeX is malformed, uses an unknown command, or the
            stylesheet is missing.
    """
    from lxml import etree

    omml = etree.fromstring(_omml_text(latex))
    for run in omml.iter(M + "r"):
        rpr = etree.Element(A + "rPr", lang="en-US")
        if run.find(f"{M}rPr/{M}nor") is None:
            rpr.set("i", "1")
        etree.SubElement(rpr, A + "latin", typeface=MATH_FONT)
        etree.SubElement(rpr, A + "ea", typeface=EA_FONT)
        run.find(M + "t").addprevious(rpr)
    etree.cleanup_namespaces(omml)
    return omml


def math_block(latex: str, *, display: bool) -> _Element:
    """Wraps an equation so PowerPoint reads it and older readers see LaTeX.

    Args:
        latex: One formula.
        display: True for a centred equation paragraph, False for inline.

    Returns:
        An `mc:AlternateContent` element to append to an `a:p`.
    """
    from lxml import etree

    alt = etree.Element(MC + "AlternateContent",
                        nsmap={k: NS[k] for k in ("mc", "a14", "m")})
    choice = etree.SubElement(alt, MC + "Choice", Requires="a14")
    holder = etree.SubElement(choice, A14 + "m")
    omml = to_omml(latex)
    if display:
        para = etree.SubElement(holder, M + "oMathPara")
        ppr = etree.SubElement(para, M + "oMathParaPr")
        etree.SubElement(ppr, M + "jc").set(M + "val", "center")
        para.append(omml)
    else:
        holder.append(omml)
    fallback = etree.SubElement(alt, MC + "Fallback")
    run = etree.SubElement(fallback, A + "r")
    etree.SubElement(run, A + "rPr", lang="en-US")
    etree.SubElement(run, A + "t").text = latex
    return alt


# --- deck building ---------------------------------------------------------


def _inches(value: float) -> int:
    return int(value * EMU_PER_INCH)


def _style(run: _Run, size: float, *, bold: bool = False,
           color: tuple[int, int, int] | None = None) -> None:
    """Sets size, weight, colour and the Latin/East Asian fonts of a run."""
    from lxml import etree
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    rpr = run._r.get_or_add_rPr()
    for tag in ("latin", "ea", "cs"):
        old = rpr.find(A + tag)
        if old is not None:
            rpr.remove(old)
    # a:rPr children are ordered: fills and effects, then latin, ea, cs, then
    # sym and hyperlinks. Insert before the first of the later group.
    later = next((el for tag in ("sym", "hlinkClick", "hlinkMouseOver", "rtl",
                                 "extLst")
                  if (el := rpr.find(A + tag)) is not None), None)
    for tag, face in (("latin", LATIN_FONT), ("ea", EA_FONT),
                      ("cs", LATIN_FONT)):
        font = etree.Element(A + tag, typeface=face)
        if later is not None:
            later.addprevious(font)
        else:
            rpr.append(font)


def _append(para: _Paragraph, element: _Element) -> None:
    """Appends to an a:p, keeping a:endParaRPr last as the schema requires."""
    end = para._p.find(A + "endParaRPr")
    if end is not None:
        end.addprevious(element)
    else:
        para._p.append(element)


def _rich(para: _Paragraph, text: str, size: float, *, bold: bool = False,
          color: tuple[int, int, int] | None = None) -> None:
    """Writes text into a paragraph, turning `$…$` spans into equations."""
    from pptx.dml.color import RGBColor

    pos = 0
    for found in INLINE_MATH_RE.finditer(text):
        if found.start() > pos:
            run = para.add_run()
            run.text = text[pos:found.start()]
            _style(run, size, bold=bold, color=color)
        _append(para, math_block(found.group(1), display=False))
        pos = found.end()
    if pos < len(text):
        run = para.add_run()
        run.text = text[pos:]
        _style(run, size, bold=bold, color=color)
    _math_size(para, size)
    if color:
        for rpr in para._p.iter(A + "rPr"):
            if rpr.getparent().tag == M + "r":
                fill = rpr.makeelement(A + "solidFill", {})
                fill.append(fill.makeelement(
                    A + "srgbClr", {"val": str(RGBColor(*color))}))
                rpr.insert(0, fill)


def _para(frame: TextFrame, first: bool) -> _Paragraph:
    return frame.paragraphs[0] if first else frame.add_paragraph()


def _bullet(para: _Paragraph, level: int) -> None:
    from lxml import etree

    ppr = para._p.get_or_add_pPr()
    margin = 342900 + level * 400050
    ppr.set("marL", str(margin))
    ppr.set("indent", "-342900")
    etree.SubElement(ppr, A + "buFont", typeface="Arial")
    etree.SubElement(ppr, A + "buChar", char="•")


def _borders(cell: object) -> None:
    """Thin grey solid sides and dotted top/bottom, as in lecture tables."""
    tc_pr = cell._tc.get_or_add_tcPr()
    for tag in ("lnL", "lnR", "lnT", "lnB"):
        old = tc_pr.find(A + tag)
        if old is not None:
            tc_pr.remove(old)
    # a:tcPr wants the borders before the fill.
    first = tc_pr[0] if len(tc_pr) else None
    for tag in ("lnL", "lnR", "lnT", "lnB"):
        line = tc_pr.makeelement(A + tag, {"w": "9525", "cmpd": "sng"})
        fill = line.makeelement(A + "solidFill", {})
        fill.append(fill.makeelement(A + "srgbClr", {"val": "8C8C8C"}))
        line.append(fill)
        dash = "sysDot" if tag in ("lnT", "lnB") else "solid"
        line.append(line.makeelement(A + "prstDash", {"val": dash}))
        if first is not None:
            first.addprevious(line)
        else:
            tc_pr.append(line)


def _math_size(para: _Paragraph, size: float) -> None:
    """Sets the font size on the equation runs inside a paragraph."""
    for rpr in para._p.iter(A + "rPr"):
        if rpr.getparent().tag == M + "r":
            rpr.set("sz", str(int(size * 100)))


def _as_pptx(template: Path) -> Path:
    """Returns a .pptx copy of a .potx template (python-pptx needs one)."""
    if template.suffix.lower() != ".potx":
        return template
    out = Path(tempfile.mkdtemp()) / (template.stem + ".pptx")
    with zipfile.ZipFile(template) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"presentationml.template.main+xml",
                                    b"presentationml.presentation.main+xml")
            dst.writestr(item, data)
    return out


class DeckBuilder:
    """Lays out each outline slide on a 16:9 deck.

    The look follows lecture slides: a dark red title with a bar on its left,
    an optional bold takeaway line (重點) under it, figures taking most of
    the page, a symbol table under each formula, small grey source notes and
    a dark red footer band with the deck name and page number.
    """

    def __init__(self, outline: Outline, template: Path | None = None) -> None:
        """Opens the template, or a blank 16:9 deck.

        Args:
            outline: A checked outline.
            template: Optional .pptx or .potx whose emptiest layout is used.
        """
        from pptx import Presentation

        self.outline = outline
        self.base = outline.path.parent
        self.prs = Presentation(str(_as_pptx(template)) if template else None)
        # A .pptx used as a template keeps only its masters and layouts.
        slide_ids = self.prs.slides._sldIdLst
        for slide_id in list(slide_ids):
            self.prs.part.drop_rel(slide_id.rId)
            slide_ids.remove(slide_id)
        if template is None:
            self.prs.slide_width = _inches(13.333)
            self.prs.slide_height = _inches(7.5)
        self.layout = min(self.prs.slide_layouts,
                          key=lambda layout: len(layout.placeholders))
        self.width = self.prs.slide_width
        self.height = self.prs.slide_height
        self.margin = _inches(0.45)
        self.band = _inches(0.42)
        self.bottom = self.height - self.band - _inches(0.42)
        self.footer = str(outline.meta.get("footer", outline.title))

    def build(self, out: Path) -> None:
        """Adds every slide and saves the deck to `out`."""
        draw = {"封面": self._cover, "圖片": self._image,
                "公式": self._formula, "比較表": self._table,
                "條列": self._bullets, "流程圖": self._flow}
        for number, slide in enumerate(self.outline.slides, start=1):
            page = self.prs.slides.add_slide(self.layout)
            for placeholder in list(page.placeholders):
                placeholder._element.getparent().remove(placeholder._element)
            draw[slide.layout](page, slide)
            self._band(page, None if slide.layout == "封面" else number)
            if slide.items("出處") and slide.layout != "封面":
                self._sources(page, slide)
            notes = slide.text("備忘稿")
            if notes:
                frame = page.notes_slide.notes_text_frame
                frame.text = ""
                for i, line in enumerate(notes.splitlines()):
                    run = _para(frame, i == 0).add_run()
                    run.text = line
                    _style(run, 12)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(str(out))

    # Shapes ----------------------------------------------------------------

    def _box(self, page: PptxSlide, left: int, top: int, width: int,
             height: int) -> TextFrame:
        box = page.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = 0
        return frame

    def _rect(self, page: PptxSlide, left: int, top: int, width: int,
              height: int, color: tuple[int, int, int]) -> object:
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE

        shape = page.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width,
                                      height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*color)
        shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    def _title(self, page: PptxSlide, slide: Slide) -> int:
        """Draws the title bar and takeaway; returns where the body starts."""
        self._rect(page, 0, _inches(0.3), _inches(0.12), _inches(0.75),
                   ACCENT)
        frame = self._box(page, self.margin, _inches(0.22),
                          self.width - 2 * self.margin, _inches(0.9))
        _rich(_para(frame, True), slide.title, 36, bold=True, color=ACCENT)
        takeaway = slide.text("重點")
        if not takeaway:
            return _inches(1.35)
        frame = self._box(page, self.margin, _inches(1.08),
                          self.width - 2 * self.margin, _inches(0.6))
        _rich(_para(frame, True), takeaway, 22, bold=True, color=BLACK)
        return _inches(1.8)

    def _band(self, page: PptxSlide, number: int | None) -> None:
        from pptx.enum.text import PP_ALIGN

        top = self.height - self.band
        self._rect(page, 0, top, self.width, self.band, ACCENT)
        frame = self._box(page, self.margin, top + _inches(0.06),
                          self.width - 2 * self.margin, _inches(0.3))
        run = _para(frame, True).add_run()
        run.text = self.footer
        _style(run, 13, color=WHITE)
        if number is not None:
            frame = self._box(page, self.width - _inches(1.2),
                              top + _inches(0.06), _inches(0.8), _inches(0.3))
            para = _para(frame, True)
            para.alignment = PP_ALIGN.RIGHT
            run = para.add_run()
            run.text = str(number)
            _style(run, 13, color=WHITE)

    def _sources(self, page: PptxSlide, slide: Slide) -> None:
        from pptx.enum.text import PP_ALIGN

        unique = dict.fromkeys(source_label(s) for s in slide.items("出處"))
        labels = "；".join(unique)
        frame = self._box(page, self.margin, self.bottom + _inches(0.08),
                          self.width - 2 * self.margin, _inches(0.3))
        para = _para(frame, True)
        para.alignment = PP_ALIGN.RIGHT
        run = para.add_run()
        run.text = "出處：" + labels
        _style(run, 10, color=GRAY)

    def _points(self, frame: TextFrame, items: list[str], size: float,
                first: bool = True, highlight: list[str] = ()) -> None:
        """Adds bullet items; with `highlight`, other items are greyed.

        A highlighted item is bold dark red; `slides.py highlights` reads
        exactly this styling back from the .pptx.
        """
        from pptx.util import Pt

        wanted = {_norm_item(h) for h in highlight}
        for i, item in enumerate(items):
            level = 1 if item.startswith(SUB_ITEM) else 0
            para = _para(frame, first and i == 0)
            _bullet(para, level)
            para.space_before = Pt(8 if level == 0 else 2)
            if not wanted:
                _rich(para, item.strip(), size - 3 * level)
            elif _norm_item(item) in wanted:
                _rich(para, item.strip(), size - 3 * level, bold=True,
                      color=ACCENT)
            else:
                _rich(para, item.strip(), size - 3 * level, color=DIM)

    def _picture(self, page: PptxSlide, path: Path, left: int, top: int,
                 width: int, height: int, caption: str,
                 video: Path | None = None,
                 focus: tuple[float, float, float, float] | None = None
                 ) -> None:
        """Fits a figure into a box, centred, with a small caption below.

        With `video`, the box holds that mp4 and `path` is its poster frame,
        shown until the video plays. With `focus` (fractions of the figure),
        the rest of the figure is covered by translucent white and the box
        gets an accent outline.
        """
        from PIL import Image
        from pptx.enum.text import PP_ALIGN

        if caption:
            height -= _inches(0.4)
        with Image.open(path) as img:
            ratio = img.width / img.height
        w = min(width, int(height * ratio))
        h = int(w / ratio)
        x = left + (width - w) // 2
        y = top + (height - h) // 2
        if video:
            page.shapes.add_movie(str(video), x, y, w, h,
                                  poster_frame_image=str(path),
                                  mime_type="video/mp4")
        else:
            page.shapes.add_picture(str(path), x, y, w, h)
        if focus:
            self._focus(page, x, y, w, h, focus)
        if caption:
            frame = self._box(page, left, y + h + _inches(0.05), width,
                              _inches(0.35))
            para = _para(frame, True)
            para.alignment = PP_ALIGN.CENTER
            _rich(para, caption, 12, color=GRAY)

    def _focus(self, page: PptxSlide, x: int, y: int, w: int, h: int,
               focus: tuple[float, float, float, float]) -> None:
        """Dims a picture outside one box and outlines the box."""
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Pt

        fx0, fy0 = x + int(w * focus[0]), y + int(h * focus[1])
        fx1, fy1 = x + int(w * focus[2]), y + int(h * focus[3])
        covers = [(x, y, w, fy0 - y), (x, fy1, w, y + h - fy1),
                  (x, fy0, fx0 - x, fy1 - fy0),
                  (fx1, fy0, x + w - fx1, fy1 - fy0)]
        for cx, cy, cw, ch in covers:
            if cw <= 0 or ch <= 0:
                continue
            shape = page.shapes.add_shape(MSO_SHAPE.RECTANGLE, cx, cy, cw, ch)
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor(*WHITE)
            colour = shape.fill._xPr.find(".//" + A + "srgbClr")
            colour.append(colour.makeelement(A + "alpha", {"val": "72000"}))
            shape.line.fill.background()
            shape.shadow.inherit = False
        box = page.shapes.add_shape(MSO_SHAPE.RECTANGLE, fx0, fy0, fx1 - fx0,
                                    fy1 - fy0)
        box.fill.background()
        box.line.color.rgb = RGBColor(*ACCENT)
        box.line.width = Pt(2.5)
        box.shadow.inherit = False

    def _notation(self, page: PptxSlide, symbols: list[str], left: int,
                  top: int, width: int) -> int:
        """Draws the symbol table; returns its bottom edge."""
        rows = [split_symbol(s) for s in symbols]
        row_h = _inches(0.42)
        shape = page.shapes.add_table(len(rows) + 1, 2, left, top, width,
                                      row_h * (len(rows) + 1))
        table = shape.table
        self._plain_table(table)
        table.columns[0].width = int(width * 0.3)
        table.columns[1].width = width - int(width * 0.3)
        header = table.cell(0, 0)
        header.merge(table.cell(0, 1))
        self._cell(header, "符號", 15, bold=True, fill=HEADER, center=True)
        for r, (latex, meaning) in enumerate(rows, start=1):
            self._cell(table.cell(r, 0), "", 16, center=True, math=latex)
            self._cell(table.cell(r, 1), meaning, 15)
        return top + row_h * (len(rows) + 1)

    def _plain_table(self, table: object) -> None:
        tbl_pr = table._tbl.tblPr
        tbl_pr.set("bandRow", "0")
        tbl_pr.set("firstRow", "0")
        style = tbl_pr.find(A + "tableStyleId")
        if style is None:
            style = tbl_pr.makeelement(A + "tableStyleId", {})
            tbl_pr.append(style)
        style.text = NO_STYLE_GRID

    def _cell(self, cell: object, text: str, size: float, *,
              bold: bool = False, fill: tuple[int, int, int] = WHITE,
              center: bool = False, math: str | None = None) -> None:
        from pptx.dml.color import RGBColor
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

        cell.text = ""
        cell.text_frame.word_wrap = True
        cell.margin_top = cell.margin_bottom = _inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        para = cell.text_frame.paragraphs[0]
        if center:
            para.alignment = PP_ALIGN.CENTER
        if math is not None:
            _append(para, math_block(math, display=False))
            _math_size(para, size)
        else:
            _rich(para, text, size, bold=bold, color=BLACK)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(*fill)
        _borders(cell)

    # Layouts ---------------------------------------------------------------

    def _cover(self, page: PptxSlide, slide: Slide) -> None:
        frame = self._box(page, _inches(0.9), _inches(2.3),
                          self.width - _inches(1.8), _inches(1.6))
        _rich(_para(frame, True), slide.title, 44, bold=True, color=ACCENT)
        lines = [slide.text(n) for n in ("重點", "副標") if slide.text(n)]
        frame = self._box(page, _inches(0.9), _inches(4.0),
                          self.width - _inches(1.8), _inches(2.2))
        first = True
        for line in lines:
            _rich(_para(frame, first), line, 24, bold=True, color=BLACK)
            first = False
        for item in slide.items("要點"):
            _rich(_para(frame, first), item.strip(), 18, color=GRAY)
            first = False

    def _bullets(self, page: PptxSlide, slide: Slide) -> None:
        top = self._title(page, slide)
        frame = self._box(page, self.margin, top,
                          self.width - 2 * self.margin, self.bottom - top)
        self._points(frame, slide.items("要點"), 24,
                     highlight=highlights(slide))

    def _video(self, slide: Slide) -> Path | None:
        video = slide.text("影片")
        return (self.base / video).resolve() if video else None

    def _image(self, page: PptxSlide, slide: Slide) -> None:
        top = self._title(page, slide)
        points = slide.items("要點")
        full = self.width - 2 * self.margin
        path = (self.base / slide.text("圖")).resolve()
        focus = parse_focus(slide.text("焦點")) if slide.text("焦點") else None
        if points:
            text_w = int(full * 0.36)
            frame = self._box(page, self.margin, top, text_w,
                              self.bottom - top)
            self._points(frame, points, 20, highlight=highlights(slide))
            left = self.margin + text_w + _inches(0.3)
            self._picture(page, path, left, top, full - text_w - _inches(0.3),
                          self.bottom - top, slide.text("圖說"),
                          self._video(slide), focus)
        else:
            self._picture(page, path, self.margin, top, full,
                          self.bottom - top, slide.text("圖說"),
                          self._video(slide), focus)

    def _formula(self, page: PptxSlide, slide: Slide) -> None:
        top = self._title(page, slide)
        full = self.width - 2 * self.margin
        image = slide.text("圖")
        col = int(full * 0.55) if image else full
        frame = self._box(page, self.margin, top + _inches(0.15), col,
                          _inches(1.2))
        para = _para(frame, True)
        latex = slide.text("公式")
        _append(para, math_block(latex, display=True))
        # Rough width guard: long formulas in a half column wrap otherwise.
        size = 30 if not image else 26
        if len(latex) > (130 if not image else 70):
            size -= 6
        _math_size(para, size)
        y = top + _inches(1.45)
        points = slide.items("要點")
        if points:
            frame = self._box(page, self.margin, y, col, _inches(1.0))
            self._points(frame, points, 20, highlight=highlights(slide))
            y += _inches(points_height(points, col / EMU_PER_INCH, 20) + 0.15)
        symbols = slide.items("符號")
        if symbols:
            width = col if image else min(col, _inches(8.0))
            left = self.margin if image else self.margin + (col - width) // 2
            self._notation(page, symbols, left, y, width)
        if image:
            left = self.margin + col + _inches(0.3)
            self._picture(page, (self.base / image).resolve(), left, top,
                          full - col - _inches(0.3), self.bottom - top,
                          slide.text("圖說"), self._video(slide),
                          parse_focus(slide.text("焦點"))
                          if slide.text("焦點") else None)

    def _flow(self, page: PptxSlide, slide: Slide) -> None:
        """Draws a left-to-right flow chart of rounded boxes and arrows."""
        from pptx.dml.color import RGBColor
        from pptx.enum.dml import MSO_LINE
        from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Pt

        top = self._title(page, slide) + _inches(0.1)
        width, height = flow_area(slide)
        left = self.margin
        points = slide.items("要點")
        if points:
            text_w = FULL_W * 0.32
            frame = self._box(page, self.margin, top, _inches(text_w),
                              self.bottom - top)
            self._points(frame, points, 20, highlight=highlights(slide))
            left += _inches(text_w + 0.3)
        flow = parse_flow(slide.items("流程"))
        boxes = flow_boxes(flow, width, height)

        def at(x: float, y: float) -> tuple[int, int]:
            return left + _inches(x), top + _inches(y)

        size = flow_font(flow, boxes) or FLOW_SIZES[-1]
        room = edge_room(flow, boxes, width)
        labels = []
        for edge in flow.edges:  # arrows first, so boxes cover their ends
            sx, sy, sw, sh = boxes[edge.src]
            dx, dy, _, dh = boxes[edge.dst]
            points = [(sx + sw, sy + sh / 2)]
            for point in edge.via:  # pass straight through each waypoint
                px, py, pw, ph = boxes[point]
                points += [(px, py + ph / 2), (px + pw, py + ph / 2)]
            points.append((dx, dy + dh / 2))
            if len(points) == 2:
                line = page.shapes.add_connector(
                    MSO_CONNECTOR.STRAIGHT, *at(*points[0]), *at(*points[1]))
            else:
                builder = page.shapes.build_freeform(*at(*points[0]))
                builder.add_line_segments([at(*p) for p in points[1:]],
                                          close=False)
                line = builder.convert_to_shape()
                line.fill.background()
                line.shadow.inherit = False
            line.line.color.rgb = RGBColor(*GRAY)
            line.line.width = Pt(1.75)
            if edge.dashed:
                line.line.dash_style = MSO_LINE.DASH
            ln = line.line._get_or_add_ln()
            ln.append(ln.makeelement(A + "tailEnd",
                                     {"type": "triangle", "w": "med",
                                      "len": "med"}))
            if edge.label:  # on the middle of the first segment
                (x1, y1), (x2, y2) = points[0], points[1]
                labels.append((edge.label, (x1 + x2) / 2, (y1 + y2) / 2))
        for key, node in flow.nodes.items():
            x, y, w, h = boxes[key]
            shape = page.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                          *at(x, y), _inches(w), _inches(h))
            shape.adjustments[0] = 0.12
            shape.shadow.inherit = False
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor(
                *(ACCENT if node.strong else HEADER))
            shape.line.color.rgb = RGBColor(*(ACCENT if node.strong
                                              else GRAY))
            shape.line.width = Pt(1.25)
            frame = shape.text_frame
            frame.word_wrap = True
            frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            frame.margin_left = frame.margin_right = _inches(0.08)
            frame.margin_top = frame.margin_bottom = _inches(0.04)
            color = WHITE if node.strong else BLACK
            for i, part in enumerate(BREAK_RE.split(node.label)):
                para = _para(frame, i == 0)
                para.alignment = PP_ALIGN.CENTER
                _rich(para, part.strip(), size, bold=node.strong or i == 0,
                      color=color)
        for label, mid_x, mid_y in labels:
            # Shrink the white box to the text so it hides little of the line.
            # _units underestimates Latin (ABCD wrapped as ABC/D), so pad.
            parts = [p.strip() for p in BREAK_RE.split(label)]
            wide = min(room, max(_units(p) for p in parts) * EDGE_SIZE / 72
                       * 1.2 + 0.2)
            tall = label_height(label, wide, EDGE_SIZE) + 0.06
            box = page.shapes.add_textbox(*at(mid_x - wide / 2,
                                              mid_y - tall / 2),
                                          _inches(wide), _inches(tall))
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor(*WHITE)
            frame = box.text_frame
            frame.word_wrap = True
            frame.margin_left = frame.margin_right = 0
            frame.margin_top = frame.margin_bottom = _inches(0.02)
            for i, part in enumerate(parts):
                para = _para(frame, i == 0)
                para.alignment = PP_ALIGN.CENTER
                _rich(para, part.strip(), EDGE_SIZE, color=GRAY)

    def _table(self, page: PptxSlide, slide: Slide) -> None:
        top = self._title(page, slide) + _inches(0.1)
        rows = table_rows(slide.text("表格"))
        size = 17 if len(rows) <= 6 else 14
        row_h = _inches(0.55)
        shape = page.shapes.add_table(
            len(rows), len(rows[0]), self.margin, top,
            self.width - 2 * self.margin,
            min(self.bottom - top, row_h * len(rows)))
        self._plain_table(shape.table)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self._cell(shape.table.cell(r, c), value, size,
                           bold=r == 0 or c == 0,
                           fill=HEADER if r == 0 else WHITE)


# --- rendering -------------------------------------------------------------

RENDER_PS = r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject PowerPoint.Application
try {
  $p = $app.Presentations.Open('{pptx}', $true, $false, $false)
  $i = 0
  foreach ($s in $p.Slides) {
    $i++
    $s.Export(('{out}\slide-{0:D2}.png' -f $i), 'PNG', 1600, 900)
  }
  if ('{pdf}' -ne '') { $p.SaveCopyAs('{pdf}', 32) }  # 32 = ppSaveAsPDF
  $p.Close()
  Write-Output $i
} finally { $app.Quit() }
"""


def render(pptx: Path, out: Path, timeout: int = 180,
           pdf: Path | None = None) -> int:
    """Exports every slide to PNG with PowerPoint.

    Args:
        pptx: The deck.
        out: Folder for slide-NN.png; old slide images there are removed.
        timeout: Seconds before PowerPoint is killed. A repair dialog blocks
            the non-interactive session, so a hang means a malformed file.
        pdf: Also save the whole deck as this PDF, exported by PowerPoint.

    Returns:
        The number of slides exported.

    Raises:
        RuntimeError: PowerPoint failed or timed out, or `pptx` is not a
            .pptx file (PowerPoint would open an outline.md as text and
            hang until the timeout).
    """
    if pptx.suffix.lower() != ".pptx" or not pptx.is_file():
        raise RuntimeError(f"要給 build 產生的 .pptx：{pptx}")
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("slide-*.png"):
        old.unlink()
    script = (RENDER_PS.replace("{pptx}", str(pptx.resolve()))
              .replace("{out}", str(out.resolve()))
              .replace("{pdf}", str(pdf.resolve()) if pdf else ""))
    if pdf:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.unlink(missing_ok=True)
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             script], capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        subprocess.run(["taskkill", "/IM", "POWERPNT.EXE", "/F"],
                       capture_output=True)
        raise RuntimeError("PowerPoint 逾時（可能跳出修復對話框，檔案格式"
                           "有誤）") from e
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip())
    return int(done.stdout.strip().splitlines()[-1])


def read_highlights(pptx: Path) -> list[tuple[list[str], list[str]]]:
    """Reads back which bullet items each slide shows highlighted.

    Only bullet paragraphs count. A paragraph is highlighted when every run
    with text is bold and dark red (`ACCENT`), the styling `_points` uses.

    Args:
        pptx: A deck made by `build`.

    Returns:
        Per slide: (highlighted items, all bullet items).
    """
    from pptx import Presentation

    out = []
    for page in Presentation(str(pptx)).slides:
        lit, every = [], []
        for shape in page.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                ppr = para._p.pPr
                if ppr is None or ppr.find(A + "buChar") is None:
                    continue
                text = "".join(r.text for r in para.runs).strip()
                every.append(text)
                runs = [r for r in para.runs if r.text.strip()]
                if runs and all(_is_lit(r) for r in runs):
                    lit.append(text)
        out.append((lit, every))
    return out


def _is_lit(run: _Run) -> bool:
    from pptx.dml.color import RGBColor

    font = run.font
    try:
        rgb = font.color.rgb
    except AttributeError:
        return False
    return bool(font.bold) and rgb == RGBColor(*ACCENT)


def highlight_problems(outline: Outline, pptx: Path) -> list[str]:
    """Compares the highlighted items in the deck with each 反白 field.

    Args:
        outline: The outline the deck was built from.
        pptx: The built deck.

    Returns:
        One line per mismatch, plus a count mismatch if the decks differ.
    """
    pages = read_highlights(pptx)
    if len(pages) != len(outline.slides):
        return [f"投影片 {len(pages)} 頁，大綱 {len(outline.slides)} 頁"]
    problems = []
    for number, (slide, (lit, _)) in enumerate(
            zip(outline.slides, pages, strict=True), start=1):
        want = sorted(_norm_item(h) for h in highlights(slide))
        got = sorted(_norm_item(t) for t in lit)
        if want != got:
            problems.append(f"第 {number} 頁「{slide.title}」：大綱反白 "
                            f"{want or '（無）'}，投影片上是 {got or '（無）'}")
    return problems


# --- animations ------------------------------------------------------------

# Manim runs in its own throwaway uv environment: it does not support the
# project's Python and pulls in scipy, av and others the wiki never needs.
MANIM = ["uv", "run", "--no-project", "--python", "3.12",
         "--with", "manim==0.22.1", "manim"]


def animate(source: Path, timeout: int = 600) -> tuple[Path, Path]:
    """Renders one Manim scene file to `<stem>.mp4` and `<stem>.png`.

    The PNG is the last frame: the slide shows it before the video plays
    and the PNG render of the deck uses it.

    Args:
        source: `animations/<name>.py` with exactly one Scene.
        timeout: Seconds before Manim is stopped.

    Returns:
        The paths of the video and the poster frame.

    Raises:
        RuntimeError: Manim or ffmpeg failed or timed out.
    """
    source = source.resolve()
    video = source.with_suffix(".mp4")
    poster = source.with_suffix(".png")
    with tempfile.TemporaryDirectory() as media:
        cmd = MANIM + ["render", "-a", "--format", "mp4", "--media_dir",
                       media, "--disable_caching", str(source)]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, encoding="utf-8",
                                  errors="replace",
                                  env=os.environ | {
                                      "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Manim 逾時") from e
        found = sorted(Path(media).rglob("*.mp4"))
        found = [f for f in found if "partial_movie_files" not in f.parts]
        if done.returncode != 0 or len(found) != 1:
            tail = (done.stderr or done.stdout).strip()[-1500:]
            raise RuntimeError(f"Manim 失敗（找到 {len(found)} 個影片）：\n"
                               f"{tail}")
        video.write_bytes(found[0].read_bytes())
    done = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.1", "-i",
         str(video), "-frames:v", "1", "-update", "1", str(poster)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0 or not poster.is_file():
        raise RuntimeError(f"ffmpeg 擷取最後一格失敗：{done.stderr.strip()}")
    return video, poster


# --- CLI -------------------------------------------------------------------


def _check_pdf(pdf: Path, outline_path: Path) -> int:
    """Prints the PDF page count against the outline; returns exit code."""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        pages = doc.page_count
    if not outline_path.is_file():
        print(f"PDF {pages} 頁 → {pdf}（找不到大綱 {outline_path}，沒有核對）")
        return 1
    slides = len(parse_outline(outline_path).slides)
    if pages != slides:
        print(f"錯誤：PDF {pages} 頁，大綱 {slides} 頁")
        return 1
    print(f"PDF {pages} 頁，和大綱一致 → {pdf}")
    return 0


# --- reference sequences and review helpers ---------------------------------


def _load_sequence(path: Path) -> tuple[dict, slides_grammar.Grammar]:
    """Reads a reference sequence and the grammar it names."""
    import yaml  # noqa: PLC0415

    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}：YAML 讀不懂（{e}）") from e
    default = "research"
    name = doc.get("grammar", default) if isinstance(doc, dict) else default
    return doc, _load_grammar(str(name), path)


def _check_sequence(path: Path) -> int:
    try:
        doc, grammar = _load_sequence(path)
    except (OSError, ValueError) as e:
        print(f"錯誤：{e}")
        return 1
    items, chapters, problems = slides_grammar.sequence_items(doc)
    errors, warnings = slides_grammar.check_structure(
        items, chapters, grammar, check_bridges=False)
    for warning in warnings:
        print(f"警告：{warning}")
    for problem in problems + errors:
        print(problem)
    if problems or errors:
        print(f"共 {len(problems) + len(errors)} 個問題")
        return 1
    print(f"OK：{len(items)} 頁、{len(chapters)} 章")
    return 0


def chapter_shape(items: list[slides_grammar.Item], chapter: str,
                  grammar: slides_grammar.Grammar) -> list[str]:
    """The ordered page types of one chapter, as `compare` sees them.

    A 漸進 group counts as one page; free and opening types are dropped;
    ablation-like types become the type they are ranked as; and repeats in
    a row merge into one.
    """
    shape: list[str] = []
    seen_builds: set[str] = set()
    for item in items:
        if item.chapter != chapter:
            continue
        if item.build:
            if item.build in seen_builds:
                continue
            seen_builds.add(item.build)
        kind = grammar.same_as.get(item.type, item.type)
        if kind not in grammar.order:
            continue
        if not shape or shape[-1] != kind:
            shape.append(kind)
    return shape


def compare(outline: Outline, sequence: Path, wanted: str | None) -> int:
    """Compares an outline with a reference sequence; returns the exit code.

    Passes when every wanted chapter is in the outline, the wanted chapters
    keep the reference order, and each one has the same shape (see
    `chapter_shape`) as in the reference. Chapters are matched by id.
    """
    try:
        doc, grammar = _load_sequence(sequence)
    except (OSError, ValueError) as e:
        print(f"錯誤：{e}")
        return 1
    ref_items, ref_chapters, problems = slides_grammar.sequence_items(doc)
    if problems:
        print("\n".join(problems))
        return 1
    ids = ([c.strip() for c in wanted.split(",") if c.strip()] if wanted
           else [c.id for c in ref_chapters])
    names = {c.id: c.name for c in ref_chapters}
    mine, _ = slides_grammar.parse_chapters(outline.meta.get("章"))
    order = [c.id for c in mine]
    items = _items(outline)
    failed = False
    missing = [i for i in ids if i not in order]
    for i in missing:
        print(f"缺章：{i}（{names.get(i, '參考序列沒有這一章')}）")
    present = [i for i in order if i in ids]
    expected = [i for i in ids if i in order]
    if present != expected:
        print(f"章的順序不同：大綱 {present}，參考 {expected}")
        failed = True
    for i in ids:
        if i in missing:
            continue
        ours = chapter_shape(items, i, grammar)
        ref = chapter_shape(ref_items, i, grammar)
        failed |= ours != ref
        mark = "相同" if ours == ref else "不同"
        print(f"章 {i}：{mark}\n  大綱 {' → '.join(ours) or '（空）'}"
              f"\n  參考 {' → '.join(ref) or '（空）'}")
    if missing or failed:
        print("不通過")
        return 1
    print("通過")
    return 0


def page_refs(outline: Outline) -> list[tuple[Slide, str, Path | None, int,
                                              int]]:
    """Every （p.N） note: (slide, target, raw PDF or None, first, last)."""
    base, root = outline.path.parent, repo_root(outline.path)
    out = []
    for slide in outline.slides:
        for item in slide.items("出處"):
            for target, first, last in slides_grammar.page_refs(item):
                pdf = slides_grammar.source_pdf(base, root, target)
                out.append((slide, target, pdf, first, last))
    return out


def page_image(out: Path, pdf: Path, page: int) -> Path:
    """Where `pages` saves one PDF page."""
    return out / f"{pdf.stem}-p{page:03d}.png"


def _pages_cmd(outline: Outline, out: Path, dpi: int) -> int:
    import pymupdf  # noqa: PLC0415

    out.mkdir(parents=True, exist_ok=True)
    made, bad = 0, 0
    for slide, target, pdf, first, last in page_refs(outline):
        if pdf is None:
            print(f"第 {slide.line} 行：找不到 {target} 的原始 PDF")
            bad += 1
            continue
        with pymupdf.open(pdf) as doc:
            for n in range(first, last + 1):
                path = page_image(out, pdf, n)
                if path.exists():
                    continue
                if not 1 <= n <= doc.page_count:
                    print(f"第 {slide.line} 行：{pdf.name} 沒有第 {n} 頁")
                    bad += 1
                    continue
                doc[n - 1].get_pixmap(dpi=dpi).save(path)
                made += 1
    print(f"{made} 張 → {out}")
    return 1 if bad else 0


def _sample_cmd(outline: Outline, n: int, seed: int,
                pages: Path | None) -> int:
    import random  # noqa: PLC0415

    cited = [s for s in outline.slides if s.items("出處")]
    picked = sorted(random.Random(seed).sample(cited, min(n, len(cited))),
                    key=lambda s: s.line)
    refs = page_refs(outline)
    for number, slide in enumerate(picked, start=1):
        index = outline.slides.index(slide) + 1
        claim = slide.text("重點") or slide.text("副標") or "（沒有主張）"
        print(f"{number}. 第 {index} 頁「{slide.title}」\n   主張：{claim}")
        for item in slide.items("出處"):
            print(f"   出處：{item}")
        if pages:
            for s, _, pdf, first, last in refs:
                if s is slide and pdf is not None:
                    for page in range(first, last + 1):
                        print(f"   原頁：{page_image(pages, pdf, page)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Runs the command line; returns the exit code."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="機械檢查大綱")
    p.add_argument("outline", type=Path, nargs="?")
    p.add_argument("--sequence", type=Path,
                   help="改為檢查一份參考序列 YAML 的講課結構")
    p = sub.add_parser("compare", help="大綱和參考序列比對章的順序與頁型")
    p.add_argument("outline", type=Path)
    p.add_argument("sequence", type=Path)
    p.add_argument("--chapters",
                   help="必須出現的章 id，逗號分開（預設：參考序列的每一章）")
    p = sub.add_parser("pages", help="把大綱引用的原始 PDF 頁轉成 PNG")
    p.add_argument("outline", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dpi", type=int, default=80)
    p = sub.add_parser("sample", help="隨機抽幾頁給人核對出處")
    p.add_argument("outline", type=Path)
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--pages", type=Path,
                   help="pages 指令的輸出資料夾；有給就列出原頁影像路徑")
    p = sub.add_parser("build", help="檢查後產生 .pptx")
    p.add_argument("outline", type=Path)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--template", type=Path, help=".pptx 或 .potx 模板")
    p = sub.add_parser("render", help="用 PowerPoint 把每頁轉成 PNG")
    p.add_argument("pptx", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--pdf", nargs="?", const=True, default=None,
                   help="另外匯出 PDF（預設和 pptx 同名），並核對頁數")
    p.add_argument("--outline", type=Path,
                   help="核對 PDF 頁數用的大綱（預設是 pptx 旁的 outline.md）")
    p = sub.add_parser("highlights", help="核對投影片上的反白和大綱一致")
    p.add_argument("pptx", type=Path)
    p.add_argument("outline", type=Path)
    p = sub.add_parser("formula", help="印出一條公式的 OMML")
    p.add_argument("latex")
    p = sub.add_parser("animate", help="用 Manim 把動畫原始檔轉成 mp4 與封面")
    p.add_argument("source", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "animate":
        try:
            video, poster = animate(args.source)
        except RuntimeError as e:
            print(f"錯誤：{e}")
            return 1
        print(f"{video}\n{poster}")
        return 0

    if args.cmd == "formula":
        from lxml import etree

        try:
            print(etree.tostring(to_omml(args.latex), encoding="unicode"))
        except FormulaError as e:
            print(f"錯誤：{e}")
            return 1
        return 0
    if args.cmd == "render":
        out = args.out or args.pptx.parent / "render"
        pdf = None
        if args.pdf:
            pdf = (args.pptx.with_suffix(".pdf") if args.pdf is True
                   else Path(args.pdf))
        try:
            count = render(args.pptx, out, pdf=pdf)
        except RuntimeError as e:
            print(f"錯誤：{e}")
            return 1
        print(f"{count} 頁 → {out}")
        if pdf:
            return _check_pdf(pdf, args.outline
                              or args.pptx.parent / "outline.md")
        return 0
    if args.cmd == "highlights":
        problems = highlight_problems(parse_outline(args.outline), args.pptx)
        for problem in problems:
            print(problem)
        if problems:
            print(f"共 {len(problems)} 個問題")
            return 1
        print("OK：反白和大綱一致")
        return 0

    if args.cmd == "check" and args.sequence:
        return _check_sequence(args.sequence)
    if args.cmd == "check" and args.outline is None:
        parser.error("check 需要大綱路徑，或 --sequence")
    if args.cmd == "compare":
        return compare(parse_outline(args.outline), args.sequence,
                       args.chapters)
    if args.cmd == "pages":
        return _pages_cmd(parse_outline(args.outline), args.out, args.dpi)
    if args.cmd == "sample":
        return _sample_cmd(parse_outline(args.outline), args.n, args.seed,
                           args.pages)

    outline = parse_outline(args.outline)
    problems = check(outline)
    for warning in structure(outline)[1]:
        print(f"警告：{warning}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"共 {len(problems)} 個問題")
        return 1
    if args.cmd == "check":
        print(f"OK：{len(outline.slides)} 頁")
        return 0
    folder = args.outline.parent
    out = args.out or folder / f"{folder.name}.pptx"
    DeckBuilder(outline, args.template).build(out)
    print(f"{len(outline.slides)} 頁 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
