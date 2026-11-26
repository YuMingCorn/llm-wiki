"""Repository paths and git safety lists shared by the tools."""

from pathlib import Path

import _limits  # noqa: F401  (4 threads, 8 GB; see _limits.py)

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / ".llm-wiki" / "extract"
WIKI = ROOT / "wiki"
LOG = WIKI / "log.md"
# Monthly archive of older log entries (wiki.py log-archive).
LOG_ARCHIVE = WIKI / "meta" / "log-archive"

# Repo-relative POSIX paths that must never be committed (CLAUDE.md, §7).
FORBIDDEN_FILES = frozenset({
    ".mcp.json",
    "mcp.json",
    ".claude/settings.local.json",
})
FORBIDDEN_PREFIXES = ("raw/", "hackmd_note/", ".llm-wiki/", ".venv/")
ALLOWED_EXCEPTIONS = frozenset({"hackmd_note/README.md"})


def is_forbidden(rel_path: str) -> bool:
    """Returns True if a repo-relative path must stay out of git.

    Args:
        rel_path: Path relative to the repo root, with ``/`` separators.
    """
    if rel_path in ALLOWED_EXCEPTIONS:
        return False
    return rel_path in FORBIDDEN_FILES or rel_path.startswith(
        FORBIDDEN_PREFIXES
    )
