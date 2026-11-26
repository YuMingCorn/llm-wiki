"""Built-in readers: PDF (page-labelled) and plain text (line-labelled).

PDF pages without a text layer (scans) are OCR'd with Tesseract when its
English data is available; so is every page of a PDF whose whole text layer
is garbled. Results are cached per file hash in OCR_CACHE.
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pymupdf

from docsrc.base import Document, Line, register

# OCR results live in the repo's git-ignored scratch area; tests override it.
OCR_CACHE = Path(__file__).resolve().parents[2] / ".llm-wiki" / "ocr"
OCR_LANGUAGE = "eng"
OCR_DPI = 300
# Bumped when OCR settings change so old cache files are redone.
_OCR_VERSION = f"tesseract-{OCR_LANGUAGE}-{OCR_DPI}-v2"
_DEFAULT_TESSDATA = Path(r"C:\Program Files\Tesseract-OCR\tessdata")

# Elsevier "Advent" math fonts store symbols under Latin-1 or control codes,
# so the text layer shows ¼ for "=" and drops the minus sign (\x04).
# Each entry was read off equations and tables of a real paper (FF 2012 JFE).
_SYMBOL_FONTS: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (re.compile(r"^Adv\w*MthSy"), {
        "\x02": "]", "\x03": "×", "\x04": "−", "0": "′", "¼": "=",
        "½": "[", "Þ": ")", "ð": "(", "þ": "+",
    }),
    (re.compile(r"^AdvMPi"), {"2": "−"}),
    # Wiley's Advent math font: checked against the p.7 image of Cohen,
    # Polk & Vuolteenaho (2003), eq. 5 "log(exp(−δt) + 1)".
    (re.compile(r"^AdvP4C4E"),
     {"þ": "+", "ð": "(", "Þ": ")", "¼": "=", "½": "["}),
    # Advent text fonts (Wiley JF scans) put the f-ligatures under Latin-1
    # codes: "¢rms" is "firms". Read off Cohen, Polk & Vuolteenaho (2003).
    (re.compile(r"^AdvT"), {"¢": "fi", "¡": "ff", "£": "fl", "⁄": "ffi"}),
    # TeX Computer Modern / EC fonts re-embedded as TrueType map the
    # ligatures to WinAnsi punctuation: "di¤erent", "bene…ts", "‡exible".
    # Read off Christoffersen et al. (2011 working paper).
    # Text fonts only: cmsy/cmmi/cmex are math, where "‡" is a real \ddagger.
    (re.compile(r"^(?:TT)?(?:cm|dc)(?:r|bx|b|ti|sl|ss|csc|tt|bxti)\d"),
     {"…": "fi", "¤": "ff", "‡": "fl"}),
]
# Math fonts (e.g. TeX's cmex big brackets) can map glyphs to control codes.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def span_text(span: dict) -> str:
    """Returns a span's text with symbol-font codes decoded, controls dropped.

    Args:
        span: A span from ``page.get_text("dict")``.
    """
    text = span["text"]
    for pattern, table in _SYMBOL_FONTS:
        if pattern.match(span.get("font", "")):
            text = "".join(table.get(ch, ch) for ch in text)
            break
    return _CONTROL.sub("", text)


def _same_row(a: dict, b: dict) -> bool:
    """Returns True if two horizontal lines share a baseline."""
    if any(tuple(round(v) for v in ln["dir"]) != (1, 0) for ln in (a, b)):
        return False
    a0, a1, b0, b1 = a["bbox"][1], a["bbox"][3], b["bbox"][1], b["bbox"][3]
    return min(a1, b1) - max(a0, b0) > 0.5 * min(a1 - a0, b1 - b0)


def page_lines(
    page: pymupdf.Page, merge: bool = True
) -> Iterator[tuple[str, dict]]:
    """Yields (text, line) for each non-empty line in reading order.

    Follows the PDF's content-stream order: sorting a page by height
    interleaves the two columns of a two-column paper. Stream order splits
    a table row or an equation into pieces, so with merge, consecutive
    lines that share a baseline are joined again. Consecutive lines from
    different columns never share a baseline, so columns stay apart.

    Args:
        page: The page to read.
        merge: Join consecutive lines that share a baseline. Callers that
            need each line's own spans (e.g. heading detection) pass False.
    """
    row: list[tuple[str, dict]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span_text(s) for s in line["spans"]).strip()
            if not text:
                continue
            if not merge:
                yield text, line
            elif row and _same_row(row[-1][1], line):
                row.append((text, line))
            else:
                if row:
                    yield _join(row)
                row = [(text, line)]
    if row:
        yield _join(row)


def _join(row: list[tuple[str, dict]]) -> tuple[str, dict]:
    """Joins one row's pieces left to right; keeps the first piece's line."""
    ordered = sorted(row, key=lambda it: it[1]["bbox"][0])
    return "  ".join(text for text, _ in ordered), row[0][1]


def tessdata_dir() -> Path | None:
    """Returns the Tesseract data folder that holds English, or None.

    Looks at ``TESSDATA_PREFIX`` (the folder itself or its parent), then at
    the default Windows install path.
    """
    candidates: list[Path] = []
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        candidates += [Path(env), Path(env) / "tessdata"]
    candidates.append(_DEFAULT_TESSDATA)
    for folder in candidates:
        if (folder / f"{OCR_LANGUAGE}.traineddata").is_file():
            return folder
    return None


def _ocr_page(page: pymupdf.Page, tessdata: Path) -> list[str]:
    """OCRs a whole page and returns its lines in reading order.

    Reads the OCR result as its own one-page PDF in display orientation.
    ``Page.get_textpage_ocr`` would map the text back onto a page with
    /Rotate (common in scans), leaving vertical lines that page_lines
    cannot merge into rows. Tesseract splits a row at wide word gaps, so
    the row join's double space is folded back to one for plain greps.
    """
    pixmap = page.get_pixmap(dpi=OCR_DPI)
    data = pixmap.pdfocr_tobytes(language=OCR_LANGUAGE, tessdata=str(tessdata))
    with pymupdf.open("pdf", data) as ocr:
        return [re.sub(r" {2,}", " ", text)
                for page_ in ocr for text, _ in page_lines(page_)]


# Common English words. A clean English text layer has roughly one word in
# four from this list. A scan whose text layer maps glyphs to the wrong
# letters has almost none: one old working-paper scan reads "flIJTACL2IrA o irnuo" on every page.
_COMMON_WORDS = frozenset(
    "the of and to in is that for on are with as by be this we from at an"
    " it or which not".split())


def common_word_share(texts: list[str]) -> float | None:
    """Returns the share of common English words in a text layer.

    Returns None when the text is too short or not mostly Latin letters
    (e.g. a Chinese document), because the share says nothing there.

    Args:
        texts: The text-layer lines of a whole document.
    """
    joined = " ".join(texts)
    letters = sum(ch.isalpha() for ch in joined)
    latin = sum(ch.isascii() and ch.isalpha() for ch in joined)
    words = re.findall(r"[A-Za-z]+", joined)
    if len(words) < _GARBLED_MIN_WORDS or latin < 0.8 * letters:
        return None
    return sum(w.lower() in _COMMON_WORDS for w in words) / len(words)


_GARBLED_MIN_WORDS = 300
# Measured on a set of raw PDFs: the garbled scan has 0.008,
# the lowest clean paper 0.14. Judged per document, not per page: a table
# or formula page has few common words even in a clean text layer.
_GARBLED_MAX_SHARE = 0.05


class _OcrCache:
    """OCR lines per page, stored as JSON under the PDF's SHA-256."""

    def __init__(self, pdf: Path) -> None:
        digest = hashlib.sha256()
        with pdf.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        self.path = OCR_CACHE / f"{digest.hexdigest()}.json"
        self.pages: dict[str, list[str]] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not (isinstance(data, dict) and data.get("version") == _OCR_VERSION):
            return
        pages = data.get("pages")
        if isinstance(pages, dict):
            self.pages = {
                key: texts for key, texts in pages.items()
                if isinstance(texts, list)
                and all(isinstance(t, str) for t in texts)
            }

    def get(self, pno: int) -> list[str] | None:
        """Returns the cached lines of a page, or None if not cached."""
        return self.pages.get(str(pno))

    def put(self, pno: int, texts: list[str]) -> None:
        """Stores a page's lines and saves at once, so progress survives.

        Writes go through a unique temp file and an atomic replace, because
        parallel agents may read the same PDF. A failed write only costs a
        later re-OCR, so OSError is ignored.
        """
        self.pages[str(pno)] = texts
        payload = json.dumps({"version": _OCR_VERSION, "pages": self.pages},
                             ensure_ascii=False)
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except OSError:
            if tmp:
                with contextlib.suppress(OSError):
                    os.remove(tmp)


@register(".pdf")
class PdfDocument(Document):
    """Reads a PDF page by page; each line is labelled ``p.<page>``.

    A page whose text layer has no lines is OCR'd (English) when Tesseract
    data is available. When the whole text layer is garbled (almost no
    common English words, see ``common_word_share``), every page is OCR'd.
    After ``lines()``, these attributes describe it:

    Attributes:
        blank_pages: Pages with no text layer.
        garbled: Whether the text layer was judged garbled and replaced.
        ocr_pages: Pages whose lines come from OCR.
        unread_pages: Pages that needed OCR (no text layer, or a garbled
            one) but OCR could not read (no Tesseract data, or OCR
            failed); they yield no lines.
        ocr_available: Whether OCR could run; None if no page needed it.
    """

    def __init__(self, path: Path) -> None:
        """Initializes the document.

        Args:
            path: The PDF to read.
        """
        super().__init__(path)
        self.blank_pages: set[int] = set()
        self.garbled = False
        self.ocr_pages: set[int] = set()
        self.unread_pages: set[int] = set()
        self.ocr_available: bool | None = None

    def lines(self) -> list[Line]:
        """Returns the PDF's non-empty text lines in page order."""
        self.blank_pages, self.ocr_pages = set(), set()
        self.unread_pages = set()
        self.ocr_available = None
        out: list[Line] = []
        cache: _OcrCache | None = None
        tessdata: Path | None = None
        with pymupdf.open(self.path) as pdf:
            layer = [[text for text, _ in page_lines(page)] for page in pdf]
            share = common_word_share([t for texts in layer for t in texts])
            self.garbled = share is not None and share < _GARBLED_MAX_SHARE
            pages = zip(pdf, layer, strict=True)
            for pno, (page, texts) in enumerate(pages, start=1):
                if not texts:
                    self.blank_pages.add(pno)
                if self.garbled or not texts:
                    if cache is None:
                        cache = _OcrCache(self.path)
                        tessdata = tessdata_dir()
                        self.ocr_available = tessdata is not None
                    texts = self._ocr(page, pno, cache, tessdata)
                out.extend(Line(f"p.{pno}", pno, text) for text in texts)
        return out

    def _ocr(self, page: pymupdf.Page, pno: int, cache: _OcrCache,
             tessdata: Path | None) -> list[str]:
        """Returns a page's OCR lines from the cache or Tesseract."""
        texts = cache.get(pno)
        if texts is None and self.ocr_available and tessdata is not None:
            try:
                texts = _ocr_page(page, tessdata)
            # MuPDF's Tesseract binding raises its own error types; any
            # failure means "no OCR", which callers detect via unread_pages.
            except Exception:  # noqa: BLE001
                self.ocr_available = False
            else:
                cache.put(pno, texts)
        if texts is None:
            self.unread_pages.add(pno)
            return []
        if texts:
            self.ocr_pages.add(pno)
        return texts


# Inside a word, or at the start of one ("¢rms", "…rst" are fi-words).
_MID_WORD = re.compile(
    r"(?<=[A-Za-z])[^\x00-\x7f](?=[A-Za-z])"
    r"|(?<![A-Za-z])[^\x00-\x7f](?=[a-z]{2})")
# TeX math-italic/symbol fonts put glyphs in the private-use area; they
# only occur in formulas, so they do not make a source unreadable.
_MATH_FONT = re.compile(r"^(?:TT)?[Cc]m(?:mi|sy|ex)\d")
# Unicode categories that occur inside words in clean papers: letters of any
# script (é, ﬁ, Greek in formulas), numbers, dashes ("Smith–Jones"), opening
# and closing quotes (’ “ ”), detached TeX accents (´ ¨ ˆ), combining marks,
# soft hyphens, and math symbols ("N−J"). Seen in real inbox PDFs. Broken
# ligatures land elsewhere: ¢ £ ¤ (Sc), … ‡ ¡ (Po), private-use glyphs (Co).
_WORD_SAFE_CATEGORIES = ("L", "N", "Pd", "Pi", "Pf", "Sk", "Mn", "Cf", "Sm")
# Exceptions inside the safe categories: codes that _SYMBOL_FONTS already
# knows as broken (Advent "ffi"; Elsevier math = [ ( ) +), so a new font of
# the same family is still flagged.
_LIGATURE_CODES = frozenset("⁄¼½Þðþ")


def _suspicious(ch: str) -> bool:
    """Returns True if a non-ASCII character inside a word looks broken."""
    if ch in _LIGATURE_CODES:
        return True
    if ch in "′″‴":  # primes in formulas ("β′x"), category Po
        return False
    return not unicodedata.category(ch).startswith(_WORD_SAFE_CATEGORIES)


@dataclasses.dataclass
class GlyphSuspect:
    """One suspicious character found inside words.

    Attributes:
        count: How many times it appears between letters.
        example: One word that contains it.
        fonts: Span font names it came from, with counts.
    """

    count: int = 0
    example: str = ""
    fonts: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter)


@dataclasses.dataclass
class PdfReport:
    """Preflight facts about one PDF, from ``inspect_pdf``.

    Attributes:
        pages: Page count.
        blank_pages: Pages with no text layer.
        garbled: Whether the text layer was garbled and every page OCR'd.
        ocr_pages: Pages read by OCR.
        unread_pages: Pages that needed OCR but OCR could not read.
        ocr_available: Whether OCR could run; None if no page needed it.
        fonts: Span font names on text-layer pages, with span counts.
        suspects: Suspicious mid-word characters on text-layer pages
            (none when the text layer is garbled, since it is not read).
    """

    pages: int
    blank_pages: set[int]
    garbled: bool
    ocr_pages: set[int]
    unread_pages: set[int]
    ocr_available: bool | None
    fonts: collections.Counter[str]
    suspects: dict[str, GlyphSuspect]

    @property
    def verdict(self) -> str:
        """Returns NEEDS_OCR, SUSPECT_GLYPHS, OCR_USED or OK, in that order."""
        if self.unread_pages:
            return "NEEDS_OCR"
        if self.suspects:
            return "SUSPECT_GLYPHS"
        if self.ocr_pages:
            return "OCR_USED"
        return "OK"


def inspect_pdf(path: str | Path) -> PdfReport:
    """Checks a PDF before ingest: text layer, OCR, fonts, broken glyphs.

    Reads the document once through PdfDocument (which OCRs and caches
    blank pages, or every page of a garbled text layer), then scans the
    decoded spans of the text-layer pages it kept.

    Args:
        path: The PDF to inspect.

    Returns:
        The report; ``verdict`` sums it up.
    """
    document = PdfDocument(Path(path))
    document.lines()
    fonts: collections.Counter[str] = collections.Counter()
    suspects: dict[str, GlyphSuspect] = {}
    with pymupdf.open(document.path) as pdf:
        pages = pdf.page_count
        for pno, page in enumerate(pdf, start=1):
            if document.garbled or pno in document.blank_pages:
                continue
            for _, line in page_lines(page, merge=False):
                text, owners = "", []
                for span in line["spans"]:
                    fonts[span.get("font", "")] += 1
                    piece = span_text(span)
                    text += piece
                    owners += [span.get("font", "")] * len(piece)
                for m in _MID_WORD.finditer(text):
                    ch = m.group()
                    font = owners[m.start()]
                    if not _suspicious(ch) or (
                            unicodedata.category(ch) == "Co"
                            and _MATH_FONT.match(font)):
                        continue
                    hit = suspects.setdefault(ch, GlyphSuspect())
                    hit.count += 1
                    hit.fonts[font] += 1
                    if not hit.example:
                        lo = text.rfind(" ", 0, m.start()) + 1
                        hi = text.find(" ", m.end())
                        hit.example = text[lo:hi if hi >= 0 else None][:40]
    return PdfReport(
        pages=pages,
        blank_pages=document.blank_pages,
        garbled=document.garbled,
        ocr_pages=document.ocr_pages,
        unread_pages=document.unread_pages,
        ocr_available=document.ocr_available,
        fonts=fonts,
        suspects=suspects,
    )


@register(".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml")
class TextDocument(Document):
    """Reads a UTF-8 text file; each line is labelled ``L<line number>``."""

    def lines(self) -> list[Line]:
        """Returns the file's non-empty lines with 1-based line numbers."""
        text = self.path.read_text(encoding="utf-8", errors="replace")
        return [
            Line(f"L{n}", None, raw.rstrip())
            for n, raw in enumerate(text.splitlines(), start=1)
            if raw.strip()
        ]
