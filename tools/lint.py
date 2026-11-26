"""Mechanical wiki checks (schema §5 Lint). Prints one JSON object; the LLM does the semantic pass."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Sequence
from urllib.parse import unquote


sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import ledger  # noqa: E402
import lifecycle  # noqa: E402
import lineage  # noqa: E402
import retention  # noqa: E402
import relations  # noqa: E402

INDEX, LOG, OVERVIEW = "wiki/index.md", "wiki/log.md", "wiki/overview.md"
SPECIAL_PAGES = (INDEX, LOG, OVERVIEW)
# Domains are the directories under wiki/ (_common.discover_domains), so they
# are read per wiki root; log.md is never link-checked because it is an
# append-only history that legitimately names pages that no longer exist.
ASSETS_PREFIX = "wiki/assets/"
# Shared files that are not pages: ledgers and the log archive (wiki.py log-archive).
META_PREFIX = "wiki/meta/"
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")

UPDATED_PAGES_SECTION = "本來源更新的頁面"
UNREAD_SECTION = "未讀章節"
CONTEXT_SECTION = "研究脈絡"
SOURCE_KINDS = frozenset({"paper", "report", "article", "docs", "book", "code"})
# source 頁從這天起，或已填 source_kind（重新 ingest 的舊頁），要有合法的 source_kind 與「研究脈絡」
SOURCE_KIND_SINCE = "2026-11-15"

COMMON_FIELDS: dict[str, frozenset[str] | None] = {
    "title": None,
    "type": frozenset({"entity", "concept", "source", "query", "synthesis", "comparison", "topic"}),
    "domain": None,  # checked against the wiki's domains in _frontmatter_problems
    "tags": None,
    "sources": None,
    "created": None,
    "updated": None,
    "status": _common.STATUS_VALUES | {_common.LEGACY_DISPUTED},
}
TYPE_FIELDS: dict[str, dict[str, frozenset[str] | None]] = {
    "source": {
        "tier": frozenset({"primary", "secondary", "tertiary"}),
        "coverage": frozenset({"full", "partial"}),
    },
}

PAGE_WARN_AT, PAGE_SPLIT_AT = 90, 100
LINT_REMIND_AT = 5
LINT_COMMIT_PATTERN = r"^docs\(wiki\): lint 第 [0-9]+ 次($|並)"
COUNTED_LOG_TYPES = frozenset({"ingest"})

LINK_RE = re.compile(r'(!?)\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+"[^"]*")?\s*\)')
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+).+?\1")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
ALIAS_TYPES = ("concept", "entity")
# `English（別名）`: the gloss must follow a Latin letter or digit (bold/underscore marks allowed).
GLOSS_RE = re.compile(r"(?<=[A-Za-z0-9*_]) ?[（(]([^（）()]*)[）)]")
GLOSS_SPLIT_RE = re.compile(r"[、，,；;]")
LOG_HEADING_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] (\S+) \| ", re.M)


@dataclass(frozen=True)
class Link:
    raw_target: str
    resolved: str
    is_image: bool
    line: int = 0


@dataclass(frozen=True)
class Anchor:
    target: str
    fragment: str
    raw_target: str
    line: int


@dataclass
class Page:
    path: PurePosixPath
    frontmatter: dict | None
    body: str
    links: list[Link]
    updated_pages: set[str] | None
    anchors: list[Anchor] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    body_offset: int = 0  # lines before the body, so body line N is file line N + offset

    @property
    def type(self) -> object:
        return (self.frontmatter or {}).get("type")

    @property
    def sources(self) -> list[str]:
        raw = (self.frontmatter or {}).get("sources")
        if not isinstance(raw, list):
            return []
        return [posixpath.normpath(str(s)) for s in raw if s]


@dataclass
class Wiki:
    root: Path
    pages: dict[str, Page]
    special: dict[str, Page]
    # Navigation only: their links must not count as incoming links or content pages.
    topics: dict[str, Page] = field(default_factory=dict)
    domains: tuple[str, ...] = ()

    def sub_indexes(self) -> tuple[str, ...]:
        """schema §8: after the split, each domain has its own index."""
        return tuple(f"wiki/{d}/index.md" for d in self.domains)

    def link_checked_special(self) -> list[Page]:
        return [self.special[k] for k in (INDEX, OVERVIEW, *self.sub_indexes()) if k in self.special]

    def source_pages(self) -> dict[str, Page]:
        return {k: p for k, p in self.pages.items() if p.type == "source"}

    def authored_pages(self) -> dict[str, Page]:
        return {**self.pages, **self.topics}


def _prose_lines(body: str):
    fence = None
    for number, line in enumerate(body.split("\n"), 1):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            yield number, line, INLINE_CODE_RE.sub("", line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
            fence = None


def _resolve(page_dir: str, target: str) -> str | None:
    if target.startswith(EXTERNAL_PREFIXES):
        return None
    path = unquote(target.split("#", 1)[0])
    if not path:
        return None
    return posixpath.normpath(posixpath.join(page_dir, path))


def parse_page(root: Path, rel: str) -> Page:
    text = (root / rel).read_text(encoding="utf-8-sig")
    frontmatter, body = _common.split_frontmatter(text)
    offset = text[:len(text) - len(body)].count("\n")
    page_dir = posixpath.dirname(rel)
    links: list[Link] = []
    anchors: list[Anchor] = []
    headings: list[str] = []
    updated: set[str] | None = None
    section = None
    for number, original, line in _prose_lines(body):
        heading = HEADING_RE.match(line)
        if heading:
            headings.append(HEADING_RE.match(original).group(2))
        if heading and len(heading.group(1)) <= 2:
            section = heading.group(2) if len(heading.group(1)) == 2 else None
            if section == UPDATED_PAGES_SECTION and updated is None:
                updated = set()
        for m in LINK_RE.finditer(line):
            raw = m.group(2).strip("<>")
            resolved = _resolve(page_dir, raw)
            if "#" in raw and not raw.startswith(("http://", "https://", "mailto:")):
                anchors.append(Anchor(
                    resolved or rel, unquote(raw.split("#", 1)[1]), raw, number + offset,
                ))
            if resolved is None:
                continue
            link = Link(raw, resolved, m.group(1) == "!", number + offset)
            links.append(link)
            if section == UPDATED_PAGES_SECTION and not link.is_image:
                updated.add(resolved)
    return Page(PurePosixPath(rel), frontmatter, body, links, updated, anchors, headings, offset)


def load(root: Path, exclude: Sequence[str] = ()) -> Wiki:
    pages: dict[str, Page] = {}
    special: dict[str, Page] = {}
    topics: dict[str, Page] = {}
    domains = _common.discover_domains(root)
    sub_indexes = {f"wiki/{d}/index.md" for d in domains}
    topics_re = re.compile(rf"^wiki/(?:(?:{'|'.join(map(re.escape, domains))})/)?topics/"
                           if domains else r"^wiki/topics/")
    for file in sorted((root / "wiki").rglob("*.md")):
        rel = file.relative_to(root).as_posix()
        if rel.startswith((ASSETS_PREFIX, META_PREFIX)) or any(fnmatch(rel, pattern) for pattern in exclude):
            continue
        is_special = rel in SPECIAL_PAGES or rel in sub_indexes
        group = special if is_special else topics if topics_re.match(rel) else pages
        group[rel] = parse_page(root, rel)
    return Wiki(root, pages, special, topics, domains)


def check_broken_links(wiki: Wiki) -> list[dict]:
    checked = [*wiki.authored_pages().values(), *wiki.link_checked_special()]
    out = []
    for page in checked:
        for link in page.links:
            if not (wiki.root / link.resolved).exists():
                out.append({"page": str(page.path), "line": link.line, "target": link.raw_target, "kind": "link"})
        for source in page.sources:
            if not (wiki.root / source).exists():
                out.append({"page": str(page.path), "target": source, "kind": "frontmatter_source"})
    return out


def _normalize_heading(text: str) -> str:
    return " ".join(text.split()).casefold()


def _github_slug(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def check_broken_anchors(wiki: Wiki) -> list[dict]:
    """Lists heading fragments that do not match a heading's text.

    `slug_only` targets match the GitHub slug but not the heading text, so they
    work on GitHub and may not jump to the heading in Obsidian.
    """
    every = {**wiki.authored_pages(), **wiki.special}
    out = []
    for key, page in every.items():
        for anchor in page.anchors:
            target = every.get(anchor.target)
            # `#top` is the HTML built-in for the page start; `^id` is an Obsidian block reference.
            if target is None or anchor.fragment.startswith("^") or anchor.fragment.lower() == "top":
                continue
            if _normalize_heading(anchor.fragment) in {_normalize_heading(h) for h in target.headings}:
                continue
            slug = anchor.fragment.lower() in {_github_slug(h) for h in target.headings}
            out.append({"page": key, "line": anchor.line, "target": anchor.raw_target,
                        "reason": "slug_only" if slug else "not_found"})
    return out


def check_duplicate_basenames(wiki: Wiki) -> list[dict]:
    groups: dict[str, list[str]] = {}
    for key in wiki.authored_pages():
        groups.setdefault(posixpath.basename(key).casefold(), []).append(key)
    return [{"basename": name, "pages": paths} for name, paths in groups.items() if len(paths) > 1]


def check_hidden_target_links(wiki: Wiki) -> list[dict]:
    """Lists links into dot directories such as the .llm-wiki scratch area."""
    return [
        {"page": key, "line": link.line, "target": link.raw_target}
        for key, page in wiki.authored_pages().items()
        for link in page.links
        if any(part.startswith(".") and part != ".." for part in link.resolved.split("/"))
    ]


def check_unlinked_raw_sources(wiki: Wiki) -> list[dict]:
    """Lists raw files in a source page's frontmatter that its body never links.

    Fix with `tools/wiki.py link-raw <page>` when the body names the file as code.
    """
    out = []
    for key, page in wiki.source_pages().items():
        linked = {link.resolved for link in page.links}
        out.extend({"page": key, "target": source} for source in page.sources
                   if source.startswith("raw/") and source not in linked)
    return out


def check_single_source_topics(wiki: Wiki) -> list[dict]:
    """Topics whose only content page is one source page.

    That source cannot have incoming content links yet, so it is reported
    here instead of as an orphan.
    """
    out = []
    for key, topic in sorted(wiki.topics.items()):
        members = {link.resolved for link in topic.links
                   if not link.is_image and link.resolved in wiki.pages}
        if len(members) == 1:
            (page,) = members
            if wiki.pages[page].type == "source":
                out.append({"topic": key, "page": page})
    return out


def check_orphans(wiki: Wiki) -> list[str]:
    """Content pages no other content page links to."""
    incoming = {
        link.resolved
        for key, page in wiki.pages.items()
        for link in page.links
        if not link.is_image and link.resolved != key
    }
    lone = {t["page"] for t in check_single_source_topics(wiki)}
    return [key for key in wiki.pages
            if key not in incoming and key not in lone]


def _frontmatter_problems(page: Page, domains: Sequence[str] = ()) -> list[str]:
    """Frontmatter rule violations (schema §3).

    Args:
        page: The parsed page.
        domains: The wiki's domain directories; empty for a flat wiki, where any
            kebab-case domain name is accepted.
    """
    fm = page.frontmatter
    if fm is None:
        return ["missing or unparseable frontmatter"]
    fields = COMMON_FIELDS | TYPE_FIELDS.get(page.type, {}) if isinstance(page.type, str) else COMMON_FIELDS
    if page.type == "topic":
        fields = {name: allowed for name, allowed in fields.items() if name != "sources"}
    problems = []
    for name, allowed in fields.items():
        value = fm.get(name)
        if value is None:
            problems.append(f"missing: {name}")
        elif allowed is not None and not (isinstance(value, str) and value in allowed):
            problems.append(f"invalid {name}: {value}")
    domain = fm.get("domain")
    if domain is not None and not (domain in domains if domains else _common.is_domain_name(domain)):
        problems.append(f"invalid domain: {domain}")
    if "sources" in fm and fm["sources"] is not None and not isinstance(fm["sources"], list):
        problems.append("sources is not a list")
    status = fm.get("status")
    if status == "superseded" and not fm.get("superseded_by"):
        problems.append("superseded without superseded_by")
    if status == "active" and page.type != "topic" and not page.sources:
        problems.append("active without sources")
    if "aliases" in fm:
        if page.type not in ALIAS_TYPES:
            problems.append(f"aliases only allowed on {'/'.join(ALIAS_TYPES)} pages")
        elif _aliases(fm) is None:
            problems.append("aliases is not a list of non-empty strings")
    if (
        page.type == "source"
        and fm.get("coverage") == "partial"
        and not re.search(rf"^##\s+{UNREAD_SECTION}\s*$", page.body, re.M)
    ):
        problems.append(f"coverage partial without ## {UNREAD_SECTION}")
    if page.type == "source" and (
        str(fm.get("created", "")) >= SOURCE_KIND_SINCE or "source_kind" in fm
    ):
        kind = fm.get("source_kind")
        if kind is None:
            problems.append("missing: source_kind")
        elif not isinstance(kind, str) or kind not in SOURCE_KINDS:
            problems.append(f"invalid source_kind: {kind}")
        if not re.search(rf"^##\s+{CONTEXT_SECTION}\s*$", page.body, re.M):
            problems.append(f"missing ## {CONTEXT_SECTION}")
    return problems


def _aliases(fm: dict) -> list[str] | None:
    """The page's `aliases`, or None when the field is malformed."""
    raw = fm.get("aliases")
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(a, str) and a.strip() for a in raw):
        return None
    return [a.strip() for a in raw]


def _page_domain(rel: str, page: Page, domains: Sequence[str]) -> str | None:
    domain = (page.frontmatter or {}).get("domain")
    if domain in domains or (not domains and _common.is_domain_name(domain)):
        return domain
    parts = rel.split("/")
    return parts[1] if len(parts) > 2 and parts[1] in domains else None


def _alias_owners(wiki: Wiki) -> dict[str, list[tuple[str, str | None]]]:
    """alias -> [(owner page, owner domain)] over concept/entity pages."""
    owners: dict[str, list[tuple[str, str | None]]] = {}
    for rel, page in wiki.pages.items():
        if page.type not in ALIAS_TYPES:
            continue
        for alias in _aliases(page.frontmatter or {}) or []:
            owners.setdefault(alias, []).append((rel, _page_domain(rel, page, wiki.domains)))
    return owners


def _domains_meet(reader: str | None, owner: str | None) -> bool:
    """schema §4: compare within a domain; cross pages and pages with no domain see every domain."""
    cross = _common.CROSS_DOMAIN
    return reader in (None, cross) or owner in (None, cross) or reader == owner


def check_alias_conflicts(wiki: Wiki) -> list[dict]:
    """One alias claimed by two pages whose domains meet (schema §3)."""
    out = []
    for alias, owners in sorted(_alias_owners(wiki).items()):
        clash = sorted({a for a, da in owners for b, db in owners
                        if a != b and _domains_meet(da, db)})
        if clash:
            out.append({"alias": alias, "pages": clash})
    return out


def _blank_gloss(m: re.Match, aliases: dict) -> str:
    """Blanks an allowed `English（別名、別名）` gloss, keeping offsets.

    Only when every item is a whole alias or holds no alias: `Table 3（朝代更替的人口）`
    is prose in parentheses, not a gloss, so it stays.
    """
    items = [s.strip() for s in GLOSS_SPLIT_RE.split(m.group(1))]
    if all(s in aliases or not any(a in s for a in aliases) for s in items):
        return " " * len(m.group(0))
    return m.group(0)


def check_alias_in_prose(wiki: Wiki) -> list[dict]:
    """Prose that uses another page's Chinese alias instead of its English name (schema §4).

    Skips code, link targets, the page that owns the alias, and the allowed
    `English（alias）` gloss (a bare `（alias）` after Chinese is still reported). Longest alias wins, so 朝代 inside 朝代循環 is one hit.
    """
    owners = _alias_owners(wiki)
    if not owners:
        return []
    pattern = re.compile("|".join(re.escape(a) for a in sorted(owners, key=len, reverse=True)))
    readers = {**wiki.authored_pages(), **{str(p.path): p for p in wiki.link_checked_special()}}
    hits: dict[tuple[str, str, str], list[int]] = {}
    for rel, page in readers.items():
        domain = _page_domain(rel, page, wiki.domains)
        for number, original, _ in _prose_lines(page.body):
            # A space, not "", so code between two halves of an alias does not join them.
            text = LINK_TARGET_RE.sub("]()", INLINE_CODE_RE.sub(" ", original))
            text = GLOSS_RE.sub(lambda g: _blank_gloss(g, owners), text)
            for m in pattern.finditer(text):
                alias = m.group(0)
                for owner, owner_domain in owners[alias]:
                    if owner != rel and _domains_meet(domain, owner_domain):
                        hits.setdefault((rel, alias, owner), []).append(number + page.body_offset)
    return [{"page": p, "alias": a, "owner": o, "line": lines}
            for (p, a, o), lines in sorted(hits.items())]


def check_frontmatter(wiki: Wiki) -> list[dict]:
    return [
        {"page": key, "problems": problems}
        for key, page in wiki.authored_pages().items()
        if (problems := _frontmatter_problems(page, wiki.domains))
    ]


def check_backlinks(wiki: Wiki) -> list[dict]:
    out = []
    for s, source in wiki.source_pages().items():
        listed = source.updated_pages
        if listed is None:
            out.append({"source": s, "problem": "missing_section"})
            continue
        for p in sorted(listed):
            if p in wiki.pages and p != s and s not in wiki.pages[p].sources:
                out.append({"source": s, "page": p, "problem": "listed_but_not_cited"})
        for p, page in wiki.pages.items():
            # schema §4 只要求 source ↔ concept／entity 雙向；synthesis、comparison 是事後的整合頁
            if page.type in ("concept", "entity") and s in page.sources and p not in listed:
                out.append({"source": s, "page": p, "problem": "cited_but_not_listed"})
    return out


def _source_links(wiki: Wiki, key: str, page: Page) -> dict[str, list[int]]:
    """Maps each source page linked in the body (not images) to its lines."""
    source_pages = wiki.source_pages()
    lines: dict[str, list[int]] = {}
    for link in page.links:
        target = link.resolved
        if not link.is_image and target in source_pages and target != key:
            lines.setdefault(target, []).append(link.line)
    return lines


def _cited_pages(wiki: Wiki) -> Iterator[tuple[str, Page, dict, set]]:
    # Source pages are skipped: their `sources` list raw files.
    for key, page in wiki.pages.items():
        if page.type != "source" and page.frontmatter is not None:
            yield key, page, _source_links(wiki, key, page), set(page.sources)


def check_sources_missing(wiki: Wiki) -> list[dict]:
    """Source pages linked in a page's body but missing from `sources`."""
    return [
        {"page": key, "source": s, "line": lines[s]}
        for key, _, lines, listed in _cited_pages(wiki)
        for s in sorted(lines.keys() - listed)
    ]


def check_sources_unused(wiki: Wiki) -> list[dict]:
    """Source pages listed in `sources` but never linked in the body.

    Candidates only: a source that changes no claim may be listed without a
    body link (ingest concept-writing rules).
    """
    source_pages = wiki.source_pages()
    return [
        {"page": key, "source": s}
        for key, _, lines, listed in _cited_pages(wiki)
        for s in sorted(listed & source_pages.keys() - lines.keys())
    ]


def check_disputed(wiki: Wiki) -> list[str]:
    """Pages with an unresolved contradiction (``disputed``; legacy ``needs-review`` too)."""
    return [k for k, p in wiki.pages.items()
            if (p.frontmatter or {}).get("status") in _common.DISPUTED_STATUSES]


def check_legacy_status(wiki: Wiki) -> list[dict]:
    """Pages still on a pre-migration status (run tools/status_migrate.py).

    ``needs-review`` is now ``disputed``. A ``draft`` is now a page without
    sources, so a ``draft`` with sources (or a topic page, which needs none)
    should be ``active``.
    """
    out = []
    for key, page in wiki.authored_pages().items():
        status = (page.frontmatter or {}).get("status")
        if status == _common.LEGACY_DISPUTED:
            out.append({"page": key, "status": status, "to": "disputed"})
        elif status == "draft" and (page.type == "topic" or page.sources):
            out.append({"page": key, "status": status, "to": "active"})
    return out


def check_partial_sources(wiki: Wiki) -> list[str]:
    return [k for k, p in wiki.source_pages().items() if p.frontmatter.get("coverage") == "partial"]


def check_index_missing(wiki: Wiki) -> list[str]:
    """Lists sub-indexes and topic pages missing from the index, and content pages with no topic.

    After the schema §8 split, topics may be listed in a domain sub-index, and
    each sub-index must be linked from wiki/index.md.

    Only a synthesis page may be listed directly in the index instead of a topic,
    because it can span several topics.
    """
    index = wiki.special.get(INDEX)
    root_links = {link.resolved for link in index.links} if index else set()
    subs = [k for k in wiki.sub_indexes() if k in wiki.special]
    in_index = root_links | {link.resolved for k in subs for link in wiki.special[k].links}
    in_topic = {link.resolved for topic in wiki.topics.values() for link in topic.links}
    missing_topics = [key for key in wiki.topics if key not in in_index]
    missing_pages = [
        key for key, page in wiki.pages.items()
        if key not in in_topic and not (page.type == "synthesis" and key in in_index)
    ]
    missing_subs = [k for k in subs if k not in root_links]
    return missing_subs + missing_topics + missing_pages


def _empty_h2_sections(body: str) -> list[str]:
    empty: list[str] = []
    current: str | None = None
    has_content = False
    fence = None
    for line in body.split("\n"):
        marker = FENCE_RE.match(line)
        if fence is not None:
            has_content = True
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            has_content = True
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) <= 2:
            if current is not None and not has_content:
                empty.append(current)
            current = heading.group(2) if len(heading.group(1)) == 2 else None
            has_content = False
        elif line.strip():
            has_content = True
    if current is not None and not has_content:
        empty.append(current)
    return empty


def check_empty_sections(wiki: Wiki) -> list[dict]:
    return [
        {"page": key, "section": section}
        for key, page in wiki.authored_pages().items()
        for section in _empty_h2_sections(page.body)
    ]


def check_ledgers(wiki: Wiki) -> dict:
    return ledger.validate(wiki.root)


def check_page_count(wiki: Wiki) -> dict:
    count = len(wiki.pages)
    split = bool(wiki.domains)
    return {
        "count": count, "warn_at": PAGE_WARN_AT, "split_at": PAGE_SPLIT_AT,
        "layout": "domain" if split else "flat",
        # The warning is about moving to the per-domain layout, so it stops once moved.
        "warn": count > PAGE_WARN_AT and not split,
    }


def check_ingests_since_last_lint(wiki: Wiki) -> dict:
    log = wiki.special.get(LOG)
    return ingests_since_last_lint(log.body if log else "")


def ingests_since_last_lint(log_body: str) -> dict:
    types = LOG_HEADING_RE.findall(log_body)
    last_lint = max((i for i, t in enumerate(types) if t == "lint"), default=-1)
    count = sum(t in COUNTED_LOG_TYPES for t in types[last_lint + 1:])
    return {"count": count, "remind": count >= LINT_REMIND_AT}


def check_changed_since_last_lint(wiki: Wiki) -> dict:
    """Lists wiki files changed since the last `docs(wiki): lint 第 N 次` commit."""
    try:
        last = _common.run_git(wiki.root, "log", "-1", "--format=%h", "-E", f"--grep={LINT_COMMIT_PATTERN}")
        if last.returncode != 0:
            return {"since": None, "files": [], "error": last.stderr.strip()}
        since = last.stdout.strip()
        if not since:
            return {"since": None, "files": []}
        diff = _common.run_git(wiki.root, "diff", "--name-only", since, "HEAD", "--", "wiki")
    except OSError as e:
        return {"since": None, "files": [], "error": str(e)}
    if diff.returncode != 0:
        return {"since": since, "files": [], "error": diff.stderr.strip()}
    return {"since": since, "files": [p for p in diff.stdout.splitlines() if p != LOG]}


def check_unpushed_commits(wiki: Wiki) -> dict:
    try:
        result = _common.run_git(wiki.root, "log", "origin/main..main", "--oneline")
    except OSError as e:
        return {"count": None, "error": str(e)}
    if result.returncode != 0:
        return {"count": None, "error": result.stderr.strip()}
    return {"count": len(result.stdout.splitlines())}


CHECKS: dict[str, Callable[[Wiki], object]] = {
    "broken_links": check_broken_links,
    "broken_anchors": check_broken_anchors,
    "duplicate_basenames": check_duplicate_basenames,
    "hidden_target_links": check_hidden_target_links,
    "orphans": check_orphans,
    "single_source_topics": check_single_source_topics,
    "unlinked_raw_sources": check_unlinked_raw_sources,
    "frontmatter_errors": check_frontmatter,
    "backlink_mismatches": check_backlinks,
    "sources_missing": check_sources_missing,
    "sources_unused": check_sources_unused,
    "relation_problems": relations.check,
    "lineage_stale": lineage.check_stale,
    "lineage_newer_targets": lineage.check_newer_targets,
    "lineage_format": lineage.check_format,
    "supersession_problems": lifecycle.check_supersession,
    "stale_pages": lifecycle.stale_pages,
    "faded_pages": lambda wiki: retention.Retention(wiki.root).report(),
    "index_missing": check_index_missing,
    "empty_sections": check_empty_sections,
    "alias_conflicts": check_alias_conflicts,
    "alias_in_prose": check_alias_in_prose,
    "ledgers": check_ledgers,
    "disputed": check_disputed,
    "legacy_status": check_legacy_status,
    "partial_sources": check_partial_sources,
    "page_count": check_page_count,
    "ingests_since_last_lint": check_ingests_since_last_lint,
    "changed_since_last_lint": check_changed_since_last_lint,
    "unpushed_commits": check_unpushed_commits,
}


def lint(root: Path, exclude: Sequence[str] = ()) -> dict:
    wiki = load(root, exclude)
    return {name: check(wiki) for name, check in CHECKS.items()}


COMPARED_FINDINGS = ("broken_links", "broken_anchors", "duplicate_basenames", "hidden_target_links", "unlinked_raw_sources", "orphans", "frontmatter_errors", "backlink_mismatches", "sources_missing", "sources_unused", "relation_problems",
                     "supersession_problems", "index_missing", "empty_sections", "alias_conflicts",
                     "alias_in_prose", "disputed", "stale_pages", "faded_pages")


def _findings(report: dict, name: str) -> list:
    return report["ledgers"]["violations"] if name == "ledger_violations" else report.get(name, [])


def _finding_key(finding: object) -> str:
    # A repair above a finding shifts its line number without changing the finding.
    if isinstance(finding, dict):
        finding = {k: v for k, v in finding.items() if k != "line"}
    return json.dumps(finding, ensure_ascii=False, sort_keys=True)


def has_findings(report: dict) -> bool:
    """Returns whether any actionable check found something (for --strict)."""
    # Status lists, not defects to fix. alias_in_prose lists candidates a human judges (schema §4).
    names = [n for n in COMPARED_FINDINGS if n not in ("disputed", "stale_pages",
                                                      "faded_pages", "alias_in_prose",
                                                      "sources_unused")]
    return any(_findings(report, n) for n in (*names, "ledger_violations"))


def compare(before: dict, after: dict) -> dict:
    """Diffs two lint reports so a repair can show what it actually changed.

    Args:
        before: The report saved before the repair.
        after: The current report.

    Returns:
        For each check, the resolved and new findings and the remaining count.
    """
    out = {}
    for name in (*COMPARED_FINDINGS, "ledger_violations"):
        old = {_finding_key(f): f for f in _findings(before, name)}
        new = {_finding_key(f): f for f in _findings(after, name)}
        out[name] = {
            "resolved": [old[k] for k in old.keys() - new.keys()],
            "new": [new[k] for k in new.keys() - old.keys()],
            "remaining": len(old.keys() & new.keys()),
        }
    return out


FRONTMATTER_COLUMNS = ("type", "domain", "status", "tier", "coverage", "updated", "title")


def frontmatter_table(wiki: Wiki) -> list[str]:
    """One tab-separated line per content page, header first (replaces sed loops)."""
    rows = ["\t".join(("path", *FRONTMATTER_COLUMNS))]
    for rel, page in wiki.pages.items():
        fm = page.frontmatter or {}
        cells = [str(fm.get(c, "")).replace("\t", " ").replace("\n", " ") for c in FRONTMATTER_COLUMNS]
        rows.append("\t".join((rel, *cells)))
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--frontmatter", action="store_true",
                        help="print a TSV of every page's frontmatter instead of the checks")
    parser.add_argument("--compare", type=Path, metavar="BEFORE.json",
                        help="after a repair, diff the current findings against a saved report")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="leave matching wiki paths out of every check (repeatable)")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any actionable check has findings")
    args = parser.parse_args()
    root = args.root
    if not (root / "wiki").is_dir():
        print(f"wiki/ not found under {root}", file=sys.stderr)
        return 2
    if args.frontmatter:
        print("\n".join(frontmatter_table(load(root))))
        return 0
    report = lint(root, args.exclude)
    failed = args.strict and has_findings(report)
    if args.compare:
        report = compare(json.loads(args.compare.read_text(encoding="utf-8")), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
