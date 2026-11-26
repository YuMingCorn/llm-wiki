"""Typed relations between wiki pages (schema §3 ``relations``).

A page may declare, in its frontmatter, how it relates to other content pages.
Each relation type is its own top-level list, because Obsidian's properties
panel cannot show nested properties:

    requires: [wiki/music/concepts/scales.md]      # read the target first
    extends: [wiki/music/concepts/scales.md]       # builds on the target
    alternative_to: [wiki/music/concepts/x.md]  # another way, same problem
    contradicts: [wiki/music/concepts/y.md]     # the two pages disagree

The old nested ``relations:`` mapping is reported as ``nested_relations``.

Edges point from the declaring page to the target. Reverse directions (who
requires this page) are never written; ``neighbors`` derives them. The
type hints name ``lint.Wiki`` and ``lint.Page`` only under TYPE_CHECKING, so
lint can import this module without a cycle.
"""

from __future__ import annotations

import posixpath
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import _common

if TYPE_CHECKING:  # type hints only; lint imports this module
    from lint import Page, Wiki

RELATION_TYPES = ("requires", "extends", "alternative_to", "contradicts")
SYMMETRIC = frozenset({"alternative_to", "contradicts"})
# Pages that hold claims about methods or things. Source pages already link
# through ``sources``; topic pages are navigation only.
RELATION_PAGE_TYPES = frozenset(
    {"concept", "entity", "synthesis", "comparison"})
REVERSE_LABEL = {
    "requires": "required_by",
    "extends": "extended_by",
    "alternative_to": "alternative_to",
    "contradicts": "contradicts",
}


@dataclass(frozen=True)
class Edge:
    """One declared relation: ``page`` --relation--> ``target``."""

    page: str
    relation: str
    target: str


def declared(page_key: str, page: Page) -> tuple[list[Edge], list[dict]]:
    """Reads one page's top-level relation fields.

    Args:
        page_key: Repo-relative path of the page.
        page: A ``lint.Page``.

    Returns:
        The well-formed edges, and the shape problems as
        {page, relation, problem} dicts.
    """
    fm = page.frontmatter or {}
    edges: list[Edge] = []
    problems: list[dict] = []
    if "relations" in fm:
        problems.append({"page": page_key, "relation": None,
                         "problem": "nested_relations"})
    for relation in RELATION_TYPES:
        if relation not in fm:
            continue
        targets = fm[relation]
        if targets is None:
            continue
        if not isinstance(targets, list):
            problems.append({"page": page_key, "relation": relation,
                             "problem": "not_a_list"})
            continue
        edges.extend(Edge(page_key, relation, posixpath.normpath(str(t)))
                     for t in targets if t)
    return edges, problems


def all_edges(wiki: Wiki) -> list[Edge]:
    """Returns every well-formed edge declared by a content page."""
    out: list[Edge] = []
    for key, page in wiki.pages.items():
        out.extend(declared(key, page)[0])
    return out


def _find_cycle(edges: list[Edge]) -> list[str] | None:
    """Returns one ``requires`` cycle as a list of pages, or None."""
    graph: dict[str, list[str]] = {}
    for e in edges:
        if e.relation == "requires":
            graph.setdefault(e.page, []).append(e.target)
    state: dict[str, int] = {}  # 1 = on the stack, 2 = finished
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def _edge_problems(wiki: Wiki, e: Edge, declared_set: set) -> list[str]:
    """Returns the problem names for one edge whose target may exist."""
    if e.target == e.page:
        return ["self"]
    target = wiki.authored_pages().get(e.target)
    if target is None:
        return ["missing_target"]
    out = []
    if target.type not in RELATION_PAGE_TYPES:
        out.append("bad_target_type")
    if not any(link.resolved == e.target and not link.is_image
               for link in wiki.authored_pages()[e.page].links):
        out.append("not_linked_in_body")
    if (e.relation in SYMMETRIC
            and (e.target, e.relation, e.page) not in declared_set):
        out.append("missing_reverse")
    return out


def check(wiki: Wiki) -> list[dict]:
    """Lists relation problems for lint (schema §3 ``relations``).

    Problems: nested_relations (the old ``relations:`` mapping), not_a_list,
    bad_page_type,
    self, missing_target, bad_target_type, not_linked_in_body,
    missing_reverse (symmetric relations must be declared on both pages),
    not_disputed (both pages of ``contradicts`` must be disputed;
    the legacy ``needs-review`` is accepted until the status migration),
    requires_cycle.
    """
    problems: list[dict] = []
    edges: list[Edge] = []
    for key, page in wiki.authored_pages().items():
        page_edges, shape = declared(key, page)
        problems.extend(shape)
        if (page_edges or shape) and page.type not in RELATION_PAGE_TYPES:
            problems.append({"page": key, "relation": None,
                             "problem": "bad_page_type"})
            continue
        edges.extend(page_edges)
    declared_set = {(e.page, e.relation, e.target) for e in edges}
    for e in edges:
        base = {"page": e.page, "relation": e.relation, "target": e.target}
        names = _edge_problems(wiki, e, declared_set)
        problems.extend({**base, "problem": n} for n in names)
        if e.relation == "contradicts" and e.target in wiki.pages:
            for side in (e.page, e.target):
                status = (wiki.pages[side].frontmatter or {}).get("status")
                if status not in _common.DISPUTED_STATUSES:
                    problems.append({**base, "problem": "not_disputed",
                                     "side": side})
    cycle = _find_cycle([e for e in edges
                         if e.target in wiki.pages and e.target != e.page])
    if cycle:
        problems.append({"page": cycle[0], "relation": "requires",
                         "cycle": cycle, "problem": "requires_cycle"})
    return problems


def neighbors(wiki: Wiki, start: str, depth: int = 1,
              relations: tuple[str, ...] = RELATION_TYPES) -> list[dict]:
    """Walks relations outward from ``start`` in both directions.

    Args:
        wiki: A loaded ``lint.Wiki``.
        start: Repo-relative path of the starting page.
        depth: How many hops to walk.
        relations: The relation types to follow.

    Returns:
        One dict per reached page, in breadth-first order:
        {depth, via, relation, page, title}. ``relation`` lists every
        relation between ``via`` and ``page``, comma-separated: the declared
        type when the edge points away from ``via``, and the reverse label
        (for example ``required_by``) otherwise.
    """
    # node -> {neighbor: {labels}}; a symmetric label equals its reverse, so
    # a pair declared on both pages collapses into one label.
    adjacent: dict[str, dict[str, set[str]]] = {}
    for e in all_edges(wiki):
        if e.relation not in relations or e.target not in wiki.pages:
            continue
        adjacent.setdefault(e.page, {}).setdefault(
            e.target, set()).add(e.relation)
        adjacent.setdefault(e.target, {}).setdefault(
            e.page, set()).add(REVERSE_LABEL[e.relation])
    seen = {start}
    result: list[dict] = []
    queue = deque([(start, 0)])
    while queue:
        node, level = queue.popleft()
        if level >= depth:
            continue
        for nxt, labels in sorted(adjacent.get(node, {}).items()):
            if nxt in seen:
                continue
            seen.add(nxt)
            title = (wiki.pages[nxt].frontmatter or {}).get("title", "")
            result.append({"depth": level + 1, "via": node,
                           "relation": ",".join(sorted(labels)),
                           "page": nxt, "title": title})
            queue.append((nxt, level + 1))
    return result
