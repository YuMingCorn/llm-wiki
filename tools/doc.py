"""Search or print source text in any format docsrc can read.

Usage (from the repo root):
  uv run python -I tools/doc.py grep <regex> <file-or-dir>... [-i] [-C N]
      [--pages 3-8] [--max 40] [--width 200]
  uv run python -I tools/doc.py show <file> [--pages 12-13 | --lines 100-160]
  uv run python -I tools/doc.py quote --from <verify.md> [--source <file>]
  uv run python -I tools/doc.py check <pdf>...

PDF pages without a text layer (scans) are OCR'd in English with Tesseract
(TESSDATA_PREFIX, else C:/Program Files/Tesseract-OCR/tessdata) and cached
in .llm-wiki/ocr/<sha256>.json. OCR lines keep their ``p.N`` labels; show
marks such pages ``--- p.N (OCR) ---``. Without Tesseract data those pages
yield no lines, as before; check reports them.

check is the preflight before ingest. Per PDF it prints the page count, the
pages without a text layer and whether OCR read them, non-ASCII characters
found between letters (likely broken ligature fonts; letters, numbers,
dashes, quotes, accents and math symbols are ignored) with an example word
and font, and the
span fonts with counts. The last line is the verdict, first match wins:
NEEDS_OCR (textless pages OCR could not read), SUSPECT_GLYPHS, OCR_USED, OK.
Exit status is 0 when every file is OK or OCR_USED, 1 otherwise, 2 on bad
input.

quote checks every evidence line (``證據：[<path>] <p.N|LN>「quote」…``; wrap
a path that contains spaces in backticks) in
--from: the quote must appear verbatim (ignoring whitespace, ``*`` and
backticks, after NFKC) at the stated page, or within 2 lines of the stated
line. A line without a path uses --source. Exit status is 0 when every quote
checks out, 1 otherwise, 2 on bad input.

grep prints ``path:location: text`` (context lines use ``-``) and stops after
--max hits. Directories are searched recursively; unsupported files are
skipped. Exit status is 0 with hits, 1 without, 2 on bad input.
"""

from __future__ import annotations

import argparse
import bisect
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

# -I drops the script directory from sys.path; docsrc lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docsrc  # noqa: E402


def _iter_files(paths: Sequence[str]) -> Iterator[Path]:
    """Yields readable files, expanding directories recursively."""
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from (
                p for p in sorted(path.rglob("*"))
                if p.is_file() and docsrc.supported(p)
            )
        else:
            yield path


def _clip(text: str, width: int) -> str:
    """Truncates text to width characters, marking the cut."""
    return text if len(text) <= width else text[:width] + "…"


def _grep(args: argparse.Namespace) -> int:
    """Runs the grep subcommand and returns the exit status."""
    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        pattern = re.compile(args.pattern, flags)
        pages = docsrc.parse_ranges(args.pages) if args.pages else None
    except (re.error, ValueError) as err:
        print(f"參數錯誤：{err}", file=sys.stderr)
        return 2

    shown = total = 0
    bad_input = stopped_early = False
    for path in _iter_files(args.paths):
        if shown >= args.max:
            stopped_early = True
            break
        try:
            lines = docsrc.open_document(path).lines()
        except (OSError, RuntimeError, docsrc.UnsupportedFormatError) as err:
            print(f"略過 {path.as_posix()}：{err}", file=sys.stderr)
            bad_input = True
            continue
        if pages is not None:
            lines = [ln for ln in lines if ln.page is None or ln.page in pages]
        hits = [i for i, ln in enumerate(lines) if pattern.search(ln.text)]
        total += len(hits)
        last = -1
        for i in hits:
            if shown >= args.max:
                break
            shown += 1
            lo = max(i - args.context, last + 1)
            if args.context and last >= 0 and lo > last + 1:
                print("--")
            for j in range(lo, min(i + args.context, len(lines) - 1) + 1):
                if j <= last:
                    continue
                sep = ":" if j in hits else "-"
                ln = lines[j]
                print(f"{path.as_posix()}:{ln.label}{sep} "
                      f"{_clip(ln.text, args.width)}")
                last = j

    if total == 0:
        print("沒有符合的結果")
    elif total > shown:
        print(f"…另有 {total - shown} 筆，用 --max 調整")
    if stopped_early:
        print("…已達 --max 上限，其餘檔案未搜尋；縮小範圍或調高 --max")
    if bad_input:
        return 2
    return 0 if total else 1


def _show(args: argparse.Namespace) -> int:
    """Runs the show subcommand and returns the exit status."""
    try:
        document = docsrc.open_document(args.path)
        pages = docsrc.parse_ranges(args.pages) if args.pages else None
        wanted = docsrc.parse_ranges(args.lines) if args.lines else None
        lines = document.lines()
    except (OSError, RuntimeError, ValueError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 2

    has_pages = any(ln.page is not None for ln in lines)
    if pages is not None and lines and not has_pages:
        print("錯誤：這個格式沒有頁碼，改用 --lines", file=sys.stderr)
        return 2
    if wanted is not None and has_pages:
        print("錯誤：有頁碼的格式（PDF）改用 --pages", file=sys.stderr)
        return 2

    printed = 0
    ocr_pages = getattr(document, "ocr_pages", set())
    if pages is not None:
        current = None
        for ln in lines:
            if ln.page in pages:
                if ln.page != current:
                    mark = " (OCR)" if ln.page in ocr_pages else ""
                    print(f"--- p.{ln.page}{mark} ---")
                    current = ln.page
                print(ln.text)
                printed += 1
    else:
        for ln in lines:
            if wanted is None or int(ln.label[1:]) in wanted:
                print(f"{ln.label}: {ln.text}")
                printed += 1
    if not printed:
        unread = getattr(document, "unread_pages", set())
        if pages is not None and pages & unread:
            print("指定範圍內沒有文字：這些頁沒有文字層，且無法 OCR"
                  "（找不到 Tesseract 英文資料）；跑 doc.py check 看詳情")
        else:
            print("指定範圍內沒有文字（超出頁數或行數？）")
        return 1
    return 0


def _pages_text(pages: set[int]) -> str:
    """Formats page numbers compactly, e.g. ``1-3,7``."""
    out: list[str] = []
    for n in sorted(pages):
        if out and out[-1][1] == n - 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


_CHECK_TOP = 5
_CHECK_FONTS = 12


def _check(args: argparse.Namespace) -> int:
    """Runs the check subcommand and returns the exit status."""
    status = 0
    for raw in args.paths:
        path = Path(raw)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            print(f"錯誤：{raw} 不是存在的 PDF", file=sys.stderr)
            status = 2
            continue
        try:
            report = docsrc.inspect_pdf(path)
        except (OSError, RuntimeError, ValueError) as err:
            print(f"錯誤：讀不到 {raw}：{err}", file=sys.stderr)
            status = 2
            continue
        print(f"== {path.as_posix()}：{report.pages} 頁")
        if report.garbled:
            ocr = {True: "可用", False: "不可用", None: "未檢查"}[
                report.ocr_available]
            line = f"  文字層亂碼，整份改用 OCR（OCR {ocr}"
            if report.unread_pages:
                line += f"；讀不到 p.{_pages_text(report.unread_pages)}"
            print(line + "）")
        elif report.blank_pages:
            ocr = {True: "可用", False: "不可用", None: "未檢查"}[
                report.ocr_available]
            line = (f"  無文字層：p.{_pages_text(report.blank_pages)}"
                    f"（OCR {ocr}")
            if report.ocr_pages:
                line += f"；已 OCR p.{_pages_text(report.ocr_pages)}"
            if report.unread_pages:
                line += f"；讀不到 p.{_pages_text(report.unread_pages)}"
            empty = (report.blank_pages - report.ocr_pages
                     - report.unread_pages)
            if empty:
                line += f"；OCR 無文字 p.{_pages_text(empty)}"
            print(line + "）")
        total = sum(s.count for s in report.suspects.values())
        print(f"  可疑字中字元：{total}")
        ranked = sorted(report.suspects.items(), key=lambda kv: -kv[1].count)
        for ch, hit in ranked[:_CHECK_TOP]:
            font = hit.fonts.most_common(1)[0][0]
            print(f"    {ch!r} U+{ord(ch):04X} ×{hit.count}  "
                  f"例：{hit.example}  字型：{font}")
        fonts = report.fonts.most_common()
        shown = ", ".join(f"{name or '?'}×{n}" for name, n in
                          fonts[:_CHECK_FONTS])
        more = (f", …另 {len(fonts) - _CHECK_FONTS} 種"
                if len(fonts) > _CHECK_FONTS else "")
        print(f"  字型：{shown or '（無）'}{more}")
        verdict = report.verdict
        print(f"  結果：{verdict}")
        if verdict in ("NEEDS_OCR", "SUSPECT_GLYPHS"):
            status = max(status, 1)
    return status


_EVIDENCE = re.compile(r"證據[:：]\s*(?P<rest>.*)")
_QUOTE = re.compile(r"(?:(?P<label>p\.\d+|L\d+)\s*)?「(?P<quote>[^」]+)」")
_SOURCE = re.compile(r"^\s*(?:`(?P<tick>[^`]+)`|(?P<bare>[^\s「」`]+))")
_LABEL = re.compile(r"^(?:p\.\d+|L\d+)$")
_LINE_SLACK = 2


def _normalize(text: str) -> str:
    """Folds width, drops whitespace and Markdown emphasis for matching."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s*`]+", "", text)


class _Index:
    """A document's normalized text, mapping offsets back to line labels."""

    def __init__(self, lines: list[docsrc.Line]) -> None:
        self.lines = lines
        self.starts: list[int] = []
        parts: list[str] = []
        offset = 0
        for ln in lines:
            self.starts.append(offset)
            norm = _normalize(ln.text)
            parts.append(norm)
            offset += len(norm)
        self.text = "".join(parts)

    def find(self, quote: str) -> list[docsrc.Line]:
        """Returns the line where each occurrence of quote starts."""
        needle = _normalize(quote)
        found: list[docsrc.Line] = []
        start = self.text.find(needle) if needle else -1
        while start >= 0:
            pos = bisect.bisect_right(self.starts, start) - 1
            found.append(self.lines[pos])
            start = self.text.find(needle, start + 1)
        return found


def _located(claimed: str, found: list[docsrc.Line]) -> bool:
    """Returns True if any occurrence matches the claimed location."""
    if claimed.startswith("p."):
        return any(ln.label == claimed for ln in found)
    want = int(claimed[1:])
    return any(
        ln.page is None and abs(int(ln.label[1:]) - want) <= _LINE_SLACK
        for ln in found
    )


def _quote(args: argparse.Namespace) -> int:
    """Runs the quote subcommand and returns the exit status."""
    try:
        evidence = docsrc.open_document(args.from_).lines()
    except (OSError, RuntimeError, docsrc.UnsupportedFormatError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 2

    indexes: dict[str, _Index | None] = {}

    def index_for(path: str) -> _Index | None:
        if path not in indexes:
            try:
                indexes[path] = _Index(docsrc.open_document(path).lines())
            except (OSError, RuntimeError,
                    docsrc.UnsupportedFormatError) as err:
                print(f"錯誤：讀不到 {path}：{err}", file=sys.stderr)
                indexes[path] = None
        return indexes[path]

    checked = failed = 0
    bad_input = False
    for ev in evidence:
        m = _EVIDENCE.search(ev.text)
        if not m:
            continue
        rest = m.group("rest")
        src = _SOURCE.match(rest.split("「", 1)[0])
        path = src and (src.group("tick") or src.group("bare"))
        if path and _LABEL.match(path):
            path = None
        path = path or args.source
        quotes = list(_QUOTE.finditer(rest))
        where = f"{args.from_}:{ev.label}"
        if not quotes:
            print(f"NOQUOTE {where}：證據行沒有「引句」")
            failed += 1
            continue
        if path is None:
            print(f"NOPATH  {where}：沒寫來源檔，也沒給 --source")
            failed += len(quotes)
            bad_input = True
            continue
        index = index_for(path)
        if index is None:
            failed += len(quotes)
            bad_input = True
            continue
        for q in quotes:
            checked += 1
            label, text = q.group("label"), q.group("quote")
            found = index.find(text)
            short = _clip(text, 30)
            if not found:
                status, note = "MISS", "原文找不到"
            elif label is None:
                status = "NOLOC"
                places = ", ".join(ln.label for ln in found[:5])
                note = f"沒寫位置；出現在 {places}"
            elif not _located(label, found):
                status = "LOC"
                note = (f"寫 {label}，實際在 "
                        f"{', '.join(ln.label for ln in found[:5])}")
            else:
                continue
            failed += 1
            print(f"{status:<7} {where} {path}「{short}」：{note}")

    if checked == 0 and failed == 0:
        print(f"錯誤：{args.from_} 沒有「證據：」行", file=sys.stderr)
        return 2
    print(f"引句 {checked} 筆，不合格 {failed} 筆")
    if bad_input:
        return 2
    return 0 if failed == 0 else 1


def _count(minimum: int) -> Callable[[str], int]:
    """Returns an argparse type that accepts integers >= minimum."""

    def parse(value: str) -> int:
        number = int(value)
        if number < minimum:
            raise argparse.ArgumentTypeError(f"必須 ≥ {minimum}：{value}")
        return number

    return parse


def _parser() -> argparse.ArgumentParser:
    """Builds the command-line parser."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    grep = sub.add_parser("grep", help="regex search across files")
    grep.add_argument("pattern")
    grep.add_argument("paths", nargs="+")
    grep.add_argument("-i", "--ignore-case", action="store_true")
    grep.add_argument("-C", "--context", type=_count(0), default=0)
    grep.add_argument("--pages", help="only these PDF pages, e.g. 3-8,12")
    grep.add_argument("--max", type=_count(1), default=40)
    grep.add_argument("--width", type=_count(1), default=200)
    grep.set_defaults(func=_grep)

    show = sub.add_parser("show", help="print a page or line range")
    show.add_argument("path")
    group = show.add_mutually_exclusive_group(required=True)
    group.add_argument("--pages")
    group.add_argument("--lines")
    show.set_defaults(func=_show)

    quote = sub.add_parser("quote", help="verify evidence quotes in a file")
    quote.add_argument("--from", dest="from_", required=True,
                       help="file with 證據： lines, e.g. verify.md")
    quote.add_argument("--source",
                       help="default source for lines without a path")
    quote.set_defaults(func=_quote)

    check = sub.add_parser("check", help="preflight PDFs before ingest")
    check.add_argument("paths", nargs="+")
    check.set_defaults(func=_check)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name; defaults to sys.argv.

    Returns:
        The process exit status.
    """
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
