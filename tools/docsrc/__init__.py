"""Format-independent text access to source files.

Example:
    doc = docsrc.open_document("raw/sources/paper.pdf")
    for line in doc.lines():
        print(line.label, line.text)

New formats subclass Document and use ``@register(".ext")``.
"""

import _limits  # noqa: F401  (4 threads, 8 GB; see _limits.py)

from docsrc.base import (
    Document,
    Line,
    UnsupportedFormatError,
    open_document,
    parse_ranges,
    register,
    supported,
    unregister,
)
from docsrc.readers import (
    PdfDocument,
    PdfReport,
    TextDocument,
    inspect_pdf,
    page_lines,
    span_text,
    tessdata_dir,
)

__all__ = [
    "Document",
    "Line",
    "PdfDocument",
    "PdfReport",
    "TextDocument",
    "UnsupportedFormatError",
    "inspect_pdf",
    "open_document",
    "page_lines",
    "parse_ranges",
    "register",
    "span_text",
    "supported",
    "tessdata_dir",
    "unregister",
]
