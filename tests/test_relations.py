from pathlib import Path, PurePosixPath

import pytest

import relations
from lint import Link, Page, Wiki

ROOT = Path(".")
A, B, C, S = "wiki/c/a.md", "wiki/c/b.md", "wiki/c/c.md", "wiki/s/src.md"


def page(key, rel=None, links=(), type_="concept", status="draft", title=None):
    fm = {"type": type_, "status": status, "title": title or key}
    if rel is not None:
        fm.update(rel)
    return Page(PurePosixPath(key), fm, "",
                [Link(t, t, False) for t in links], None)


def problems(*pages):
    wiki = Wiki(ROOT, {p.path.as_posix(): p for p in pages}, {})
    return {(p["page"], p.get("relation"), p.get("target"), p["problem"])
            for p in relations.check(wiki)}


def test_clean_requires_and_symmetric_pair_pass():
    assert problems(
        page(A, {"requires": [B], "alternative_to": [C]}, links=[B, C]),
        page(B),
        page(C, {"alternative_to": [A]}, links=[A]),
    ) == set()


def test_shape_problems():
    found = problems(page(A, {"relations": {"requires": [B]}}),
                     page(B, {"uses": [A], "requires": A}))
    assert found == {
        (A, None, None, "nested_relations"),
        (B, "requires", None, "not_a_list"),
    }


def test_nested_relations_are_not_read_as_edges():
    edges, _ = relations.declared(A, page(A, {"relations": {"requires": [B]}}))
    assert edges == []


@pytest.mark.parametrize("rel, others, expected", [
    ({"requires": [A]}, [], "self"),
    ({"requires": ["wiki/c/zzz.md"]}, [], "missing_target"),
    ({"requires": [S]}, [page(S, type_="source")], "bad_target_type"),
    ({"requires": [B]}, [page(B)], "not_linked_in_body"),
    ({"alternative_to": [B]}, [page(B)], "missing_reverse"),
])
def test_edge_problems(rel, others, expected):
    targets = [t for ts in rel.values() for t in ts]
    links = [] if expected == "not_linked_in_body" else targets
    found = problems(page(A, rel, links=links), *others)
    assert expected in {p[3] for p in found}


def test_relation_on_source_page_is_rejected():
    found = problems(page(S, {"requires": [A]}, links=[A], type_="source"), page(A))
    assert found == {(S, None, None, "bad_page_type")}


def test_contradicts_needs_both_sides_disputed():
    found = problems(
        page(A, {"contradicts": [B]}, links=[B], status="disputed"),
        page(B, {"contradicts": [A]}, links=[A]),
    )
    assert {p for p in found if p[3] == "not_disputed"} == {
        (A, "contradicts", B, "not_disputed"),
        (B, "contradicts", A, "not_disputed"),
    }


def test_contradicts_accepts_legacy_needs_review_until_migration():
    found = problems(
        page(A, {"contradicts": [B]}, links=[B], status="needs-review"),
        page(B, {"contradicts": [A]}, links=[A], status="disputed"),
    )
    assert not {p for p in found if p[3] == "not_disputed"}


def test_requires_cycle_is_reported():
    found = problems(
        page(A, {"requires": [B]}, links=[B]),
        page(B, {"requires": [A]}, links=[A]),
    )
    assert any(p[3] == "requires_cycle" for p in found)


def test_neighbors_walk_both_directions_and_depth():
    wiki = Wiki(ROOT, {
        A: page(A, {"requires": [B]}, links=[B]),
        B: page(B, {"requires": [C]}, links=[C]),
        C: page(C),
    }, {})
    one = relations.neighbors(wiki, B, depth=1)
    assert {(r["relation"], r["page"]) for r in one} == {("requires", C), ("required_by", A)}
    from_c = relations.neighbors(wiki, C, depth=2)
    assert [(r["depth"], r["relation"], r["page"]) for r in from_c] == [
        (1, "required_by", B), (2, "required_by", A)]
    assert relations.neighbors(wiki, C, depth=2, relations=("extends",)) == []


def test_two_relations_to_one_page_are_both_listed():
    wiki = Wiki(ROOT, {
        A: page(A, {"requires": [B], "extends": [B]}, links=[B]),
        B: page(B),
    }, {})
    assert [r["relation"] for r in relations.neighbors(wiki, B)] == [
        "extended_by,required_by"]


def test_symmetric_pair_is_listed_once():
    wiki = Wiki(ROOT, {
        A: page(A, {"alternative_to": [B]}, links=[B]),
        B: page(B, {"alternative_to": [A]}, links=[A]),
    }, {})
    assert [(r["relation"], r["page"]) for r in relations.neighbors(wiki, A)] == [
        ("alternative_to", B)]


def test_topic_pages_are_checked_and_rejected():
    topic = "wiki/t/topics/x.md"
    wiki = Wiki(ROOT, {A: page(A, {"requires": [topic]}, links=[topic])}, {},
                {topic: page(topic, {"requires": [A]}, links=[A], type_="topic")})
    found = {(p["page"], p["problem"]) for p in relations.check(wiki)}
    assert found == {(A, "bad_target_type"), (topic, "bad_page_type")}


def test_self_relation_is_not_also_a_cycle():
    assert {p[3] for p in problems(page(A, {"requires": [A]}, links=[A]))} == {"self"}
