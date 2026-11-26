"""Frontmatter parsing and git calls shared by the tools."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import _limits  # noqa: F401  (4 threads, 8 GB; see _limits.py)
import yaml

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class FrontmatterError(ValueError):
    """The text looks like it has frontmatter, but it cannot be read."""


def split_frontmatter(
    text: str, *, strict: bool = False,
) -> tuple[dict | None, str]:
    """Splits text into its frontmatter mapping and body.

    Args:
        text: The page text; a leading BOM is ignored.
        strict: Raise instead of returning None for unreadable frontmatter.
            Callers making a privacy decision must set this, so that a broken
            page is refused rather than treated as not personal.

    Returns:
        The mapping (None if absent, unreadable or not a mapping) and the body.

    Raises:
        FrontmatterError: strict is set and the frontmatter cannot be read.
    """
    text = text.lstrip("﻿")
    match = FRONTMATTER_RE.match(text)
    if not match:
        if strict and text.startswith("---"):
            raise FrontmatterError("frontmatter 格式無法辨識")
        return None, text
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        if strict:
            raise FrontmatterError(f"frontmatter 無法解析（{e}）") from None
        data = None
    return (data if isinstance(data, dict) else None), text[match.end():]


def run_git(root: Path, *args: str,
            stdin: str | None = None) -> subprocess.CompletedProcess:
    """Runs git in root, leaving the exit status to the caller.

    Paths are printed unquoted, so non-ASCII names can be compared as text.
    The ceiling stops git from reporting on a parent repo of a nested root.
    """
    env = {**os.environ,
           "GIT_CEILING_DIRECTORIES": str(Path(root).resolve().parent)}
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotepath=off", *args],
        input=stdin, capture_output=True, text=True, encoding="utf-8",
        env=env, check=False,
    )


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    """Runs git in root and returns stdout.

    Raises:
        RuntimeError: git exited with a non-zero status.
    """
    proc = run_git(root, *args, stdin=stdin)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"git {args[0]} 失敗")
    return proc.stdout


# Page `status` values (schema §3). `needs-review` is the pre-migration
# name of `disputed`; it stays valid until tools/status_migrate.py has run.
STATUS_VALUES = frozenset(
    {"active", "draft", "disputed", "superseded", "archived"})
LEGACY_DISPUTED = "needs-review"
DISPUTED_STATUSES = frozenset({"disputed", LEGACY_DISPUTED})

# Domains (schema §8) are not a fixed list: each directory under wiki/ that is
# not a page-type or shared directory is one. `cross` is the one name with a
# meaning: its pages are compared with every domain (schema §4).
CROSS_DOMAIN = "cross"
PAGE_TYPE_DIRS = ("concepts", "entities", "sources", "synthesis", "comparisons",
                  "queries", "topics")
SHARED_WIKI_DIRS = ("assets", "meta", "canvases")
_DOMAIN_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_domain_name(name: object) -> bool:
    """True for a kebab-case name that is not a page-type or shared dir."""
    return (isinstance(name, str) and bool(_DOMAIN_NAME_RE.match(name))
            and name not in PAGE_TYPE_DIRS and name not in SHARED_WIKI_DIRS)


def discover_domains(root: Path) -> tuple[str, ...]:
    """The domain directories under ``root/wiki``, sorted; empty when flat."""
    wiki = root / "wiki"
    if not wiki.is_dir():
        return ()
    return tuple(sorted(d.name for d in wiki.iterdir()
                        if d.is_dir() and is_domain_name(d.name)))
