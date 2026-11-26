"""Drafts a reference sequence for a lecture PDF: repeated-slide groups.

A lecture often repeats a slide: a roadmap page that comes back with a
different item highlighted, a build-up page that grows one bullet at a time,
or a spotlight page that zooms into one part of a figure. This tool finds
those groups two ways, by title and by image, and writes a DRAFT YAML for a
human to confirm. It does not guess page types; every ``type`` is null.

Title groups are usually more accurate, but a shared title does not always
mean a build (several result slides can share one title). Pages where the
two methods disagree get a flag so that the human looks at them.

Subcommands:
  draft <pdf> --out <draft.yaml> [--sheet DIR] [--ignore REGEX]...
        [--threshold 0.035] [--dpi 20]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# -I drops the script directory from sys.path; _limits lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _limits  # noqa: E402, F401  (must come before numpy)
import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
import yaml  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

TITLE_ZONE = 0.35    # titles are looked for in the top 35% of the page
FOOTER_CUT = 0.08    # the bottom 8% (page number) is left out of the image
TITLE_KEY_LEN = 30   # titles are compared on their first 30 characters
SHEET_DPI = 40
THUMB_WIDTH = 320  # px; lecture pages are large, so 40 dpi alone is too wide
THUMB_COLUMNS = 4
LABEL_HEIGHT = 14


@dataclass
class PageInfo:
    """What the tool knows about one page."""

    number: int
    title: str
    image: np.ndarray
    title_group: int | None = None
    image_group: int | None = None
    flags: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    """Collapses runs of whitespace into one space and strips the ends."""
    return re.sub(r"\s+", " ", text).strip()


def page_title(page: pymupdf.Page, ignore: list[re.Pattern[str]]) -> str:
    """Returns the largest-font text line in the top part of the page.

    Args:
        page: The PDF page.
        ignore: Lines matching any of these patterns are skipped.

    Returns:
        The normalized title, or an empty string when no line qualifies.
        On a font-size tie, the line highest on the page wins.
    """
    limit = page.rect.y0 + page.rect.height * TITLE_ZONE
    best: tuple[float, float] | None = None
    best_text = ""
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = line["spans"]
            text = normalize("".join(s["text"] for s in spans))
            top = line["bbox"][1]
            if not text or top > limit:
                continue
            if any(p.search(text) for p in ignore):
                continue
            size = round(max(s["size"] for s in spans), 1)
            key = (-size, top)
            if best is None or key < best:
                best, best_text = key, text
    return best_text


def render_gray(page: pymupdf.Page, dpi: int,
                cut_footer: bool = True) -> np.ndarray:
    """Renders the page as a grayscale float array in the range 0 to 1.

    Args:
        page: The PDF page.
        dpi: Render resolution.
        cut_footer: Drop the bottom part of the page (it holds the page
            number, which differs on every page).
    """
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    raw = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = raw.reshape(pix.height, pix.stride)[:, : pix.width]
    arr = arr.astype(np.float32) / 255
    if cut_footer:
        arr = arr[: int(pix.height * (1 - FOOTER_CUT))]
    return arr


class UnionFind:
    """Minimal union-find over page indexes."""

    def __init__(self, n: int) -> None:
        """Starts with every index in its own set."""
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        """Returns the root of the set holding i."""
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        """Merges two sets; the smaller index becomes the root."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def image_groups(images: list[np.ndarray], threshold: float) -> list[list[int]]:
    """Groups pages whose image is close to an earlier page.

    Each page joins the group of the earlier page with the same shape and the
    smallest mean absolute difference, when that difference is below
    ``threshold``. Chains merge. Only groups of two or more are kept.

    Returns:
        Groups as sorted lists of 0-based page indexes, ordered by first page.
    """
    uf = UnionFind(len(images))
    for i, img in enumerate(images):
        best, best_j = None, None
        for j in range(i):
            if images[j].shape != img.shape:
                continue
            d = float(np.mean(np.abs(img - images[j])))
            if best is None or d < best:
                best, best_j = d, j
        if best_j is not None and best < threshold:
            uf.union(i, best_j)
    return _collect(uf, len(images))


def title_groups(titles: list[str]) -> list[list[int]]:
    """Groups pages whose title key (first 30 chars, casefolded) is equal.

    Empty titles never group. Only groups of two or more are kept.
    """
    by_key: dict[str, list[int]] = {}
    for i, t in enumerate(titles):
        key = t[:TITLE_KEY_LEN].casefold()
        if key:
            by_key.setdefault(key, []).append(i)
    groups = [g for g in by_key.values() if len(g) >= 2]
    return sorted(groups, key=lambda g: g[0])


def _collect(uf: UnionFind, n: int) -> list[list[int]]:
    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(uf.find(i), []).append(i)
    groups = [g for g in members.values() if len(g) >= 2]
    return sorted(groups, key=lambda g: g[0])


def assign(pages: list[PageInfo], tgroups: list[list[int]],
           igroups: list[list[int]]) -> None:
    """Sets group indexes and disagreement flags on each page."""
    for n, g in enumerate(tgroups):
        for i in g:
            pages[i].title_group = n
    for n, g in enumerate(igroups):
        for i in g:
            pages[i].image_group = n
    for page in pages:
        i = page.number - 1
        if page.title_group is not None:
            mates = [j for j in tgroups[page.title_group] if j != i]
            ig = page.image_group
            if ig is None or not any(pages[j].image_group == ig
                                     for j in mates):
                page.flags.append("same_title_diff_image")
        if page.image_group is not None:
            key = page.title[:TITLE_KEY_LEN].casefold()
            mates = [j for j in igroups[page.image_group] if j != i]
            if any(pages[j].title[:TITLE_KEY_LEN].casefold() != key
                   for j in mates):
                page.flags.append("same_image_diff_title")


Groups = list[list[int]]


def build_draft(pdf: Path, ignore: list[str], threshold: float,
                dpi: int) -> tuple[dict, Groups, Groups]:
    """Reads the PDF and returns the draft dict plus the data behind it."""
    patterns = [re.compile(p) for p in ignore]
    with pymupdf.open(pdf) as doc:
        pages = [PageInfo(number=k + 1, title=page_title(p, patterns),
                          image=render_gray(p, dpi))
                 for k, p in enumerate(doc)]
    tgroups = title_groups([p.title for p in pages])
    igroups = image_groups([p.image for p in pages], threshold)
    assign(pages, tgroups, igroups)
    draft = {
        "pdf": pdf.name,
        "pdf_pages": len(pages),
        "draft": True,
        "title_groups": [_group_entry(pages, g) for g in tgroups],
        "image_groups": [_group_entry(pages, g) for g in igroups],
        "pages": [{"p": p.number, "title": p.title,
                   "title_group": p.title_group,
                   "image_group": p.image_group,
                   "flags": p.flags, "type": None} for p in pages],
    }
    return draft, tgroups, igroups


def _group_entry(pages: list[PageInfo], group: list[int]) -> dict:
    return {"title": pages[group[0]].title,
            "pages": [i + 1 for i in group]}


def contact_sheet(doc: pymupdf.Document, group: list[int], out: Path) -> None:
    """Saves one PNG with a labeled thumbnail of every page in the group."""
    thumbs = []
    for i in group:
        arr = render_gray(doc[i], SHEET_DPI, cut_footer=False)
        thumb = Image.fromarray((arr * 255).astype(np.uint8), mode="L")
        thumb.thumbnail((THUMB_WIDTH, 10 * THUMB_WIDTH))
        thumbs.append(thumb)
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs) + LABEL_HEIGHT
    cols = min(THUMB_COLUMNS, len(thumbs))
    rows = -(-len(thumbs) // cols)
    sheet = Image.new("L", (cols * cell_w, rows * cell_h), 255)
    draw = ImageDraw.Draw(sheet)
    for k, (i, thumb) in enumerate(zip(group, thumbs, strict=True)):
        x, y = (k % cols) * cell_w, (k // cols) * cell_h
        draw.text((x + 2, y + 1), f"p{i + 1}", fill=0)
        sheet.paste(thumb, (x, y + LABEL_HEIGHT))
        draw.rectangle([x, y + LABEL_HEIGHT, x + thumb.width - 1,
                        y + LABEL_HEIGHT + thumb.height - 1], outline=128)
    sheet.save(out)


def write_sheets(pdf: Path, sheet_dir: Path, tgroups: list[list[int]],
                 igroups: list[list[int]]) -> None:
    """Writes title-<n>.png and image-<n>.png contact sheets."""
    sheet_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        for prefix, groups in (("title", tgroups), ("image", igroups)):
            for n, g in enumerate(groups):
                contact_sheet(doc, g, sheet_dir / f"{prefix}-{n}.png")


def cmd_draft(args: argparse.Namespace) -> int:
    """Runs the draft subcommand."""
    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"error: file not found: {pdf}", file=sys.stderr)
        return 2
    with pdf.open("rb") as f:
        if f.read(5) != b"%PDF-":
            print(f"error: not a PDF file: {pdf}", file=sys.stderr)
            return 2
    for p in args.ignore:
        try:
            re.compile(p)
        except re.error as e:
            print(f"error: bad --ignore regex {p!r}: {e}", file=sys.stderr)
            return 2
    draft, tgroups, igroups = build_draft(pdf, args.ignore, args.threshold,
                                          args.dpi)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    if args.sheet:
        write_sheets(pdf, Path(args.sheet), tgroups, igroups)
    print(f"{draft['pdf']}: {draft['pdf_pages']} pages -> {out}")
    for kind in ("title_groups", "image_groups"):
        print(f"{kind}: {len(draft[kind])}")
        for n, g in enumerate(draft[kind]):
            title = g["title"][:50]
            print(f"  [{n}] {len(g['pages']):2d} x {title!r}: {g['pages']}")
    flagged = sum(1 for p in draft["pages"] if p["flags"])
    print(f"flagged pages: {flagged}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parses arguments and runs the chosen subcommand."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draft", help="write a draft reference sequence YAML")
    d.add_argument("pdf")
    d.add_argument("--out", required=True)
    d.add_argument("--sheet", help="directory for contact-sheet PNGs")
    d.add_argument("--ignore", action="append", default=[],
                   help="regex; matching lines are never the title "
                        "(repeatable)")
    d.add_argument("--threshold", type=float, default=0.035,
                   help="mean abs pixel difference below which pages group")
    d.add_argument("--dpi", type=int, default=20)
    args = parser.parse_args(argv)
    return cmd_draft(args)


if __name__ == "__main__":
    sys.exit(main())
