"""Document interface and the extension-based reader registry."""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import Callable
from pathlib import Path


class UnsupportedFormatError(ValueError):
    """Raised when no reader is registered for a file extension."""


@dataclasses.dataclass(frozen=True)
class Line:
    """One line of extracted text.

    Attributes:
        label: Human-readable location, e.g. ``p.12`` or ``L120``.
        page: 1-based page number, or None for formats without pages.
        text: The line's text, without the trailing newline.
    """

    label: str
    page: int | None
    text: str


class Document(abc.ABC):
    """A readable source file, exposed as a flat list of lines."""

    def __init__(self, path: Path) -> None:
        """Initializes the document.

        Args:
            path: The file to read.
        """
        self.path = path

    @abc.abstractmethod
    def lines(self) -> list[Line]:
        """Returns every non-empty line in reading order."""


_READERS: dict[str, type[Document]] = {}


def register(*extensions: str) -> Callable[[type[Document]], type[Document]]:
    """Class decorator that maps file extensions to a Document subclass.

    Args:
        *extensions: Lower-case extensions including the dot, e.g. ``.pdf``.

    Returns:
        A decorator that registers the class and returns it unchanged.
    """

    def decorator(cls: type[Document]) -> type[Document]:
        for ext in extensions:
            _READERS[ext.lower()] = cls
        return cls

    return decorator


def unregister(extension: str) -> None:
    """Removes a registered extension; mainly for tests."""
    _READERS.pop(extension.lower(), None)


def supported(path: Path) -> bool:
    """Returns True if a reader is registered for the file's extension."""
    return path.suffix.lower() in _READERS


def open_document(path: str | Path) -> Document:
    """Builds the Document reader that matches a file's extension.

    Args:
        path: The file to open.

    Returns:
        A Document for the file.

    Raises:
        UnsupportedFormatError: No reader handles the extension.
    """
    path = Path(path)
    cls = _READERS.get(path.suffix.lower())
    if cls is None:
        known = ", ".join(sorted(_READERS))
        raise UnsupportedFormatError(
            f"不支援 {path.suffix or '（無副檔名）'}；可用：{known}"
        )
    return cls(path)


def parse_ranges(spec: str) -> set[int]:
    """Parses a 1-based range list such as ``2-4,7``.

    Args:
        spec: Comma-separated numbers or inclusive ``a-b`` ranges.

    Returns:
        The set of numbers the spec covers.

    Raises:
        ValueError: A part is not a number or a range runs backwards.
    """
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        start, end = int(lo), int(hi or lo)
        if start < 1 or end < start:
            raise ValueError(f"範圍不正確：{part}")
        out.update(range(start, end + 1))
    return out
