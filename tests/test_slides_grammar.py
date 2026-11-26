"""Tests for slides_grammar."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path

import _limits  # noqa: F401  (4 threads, 8 GB; before pymupdf)
import pymupdf
import pytest
import slides_grammar as sg
from slides_grammar import Chapter, Grammar, Item

GRAMMAR_YAML = """\
name: test
order: [problem, quantify, observation, insight, method, steps, result,
        limitation]
same_as:
  ablation: result
free: [spotlight, figure, prior, motivation, background]
opening: [roadmap, divider]
head: [cover, motivation, roadmap, background, figure]
tail: [summary, appendix]
no_source: [cover, roadmap, divider]
"""

ROADMAP_POINTS = ["Efficient Attention", "  AlphaCache", "H2O"]


@pytest.fixture
def grammar(tmp_path: Path) -> Grammar:
    """The fixture grammar, loaded from a file."""
    path = tmp_path / "test.yaml"
    path.write_text(GRAMMAR_YAML, encoding="utf-8")
    return sg.load_grammar(path)


def clean_deck() -> tuple[list[Item], list[Chapter]]:
    """A small deck that breaks no rule."""
    chapters = [
        Chapter("stream", "AlphaCache", "Efficient Attention", None),
        Chapter("h2o", "H2O", None, "[延續](../x.md) 上一章的問題"),
    ]
    items = [
        Item("p1", "cover"),
        Item("p2", "motivation"),
        Item("p3", "roadmap", points=list(ROADMAP_POINTS)),
        Item("p4", "roadmap", "stream",
             highlight=["Efficient Attention", "AlphaCache"],
             points=list(ROADMAP_POINTS)),
        Item("p5", "problem", "stream", build="b1"),
        Item("p6", "problem", "stream", build="b1"),
        Item("p7", "method", "stream"),
        Item("p8", "ablation", "stream"),
        Item("p9", "roadmap", "h2o", highlight=["H2O"],
             points=list(ROADMAP_POINTS)),
        Item("p10", "spotlight", "h2o", build="s1"),
        Item("p11", "insight", "h2o"),
        Item("p12", "result", "h2o"),
        Item("p13", "summary",
             points=["AlphaCache 留住開頭的 token", "H2O 留住重要 token"]),
    ]
    return items, chapters


def codes(messages: list[str]) -> set[str]:
    """The rule codes found in messages."""
    return {c for m in messages for c in re.findall(r"\[(S\d+)\]", m)}


def test_clean_deck(grammar: Grammar) -> None:
    """A well-formed deck gives no errors and no warnings."""
    items, chapters = clean_deck()
    assert sg.check_structure(items, chapters, grammar) == ([], [])


Mutation = Callable[[list[Item], list[Chapter]], None]


def _set(index: int, **values: object) -> Mutation:
    def mutate(items: list[Item], _: list[Chapter]) -> None:
        for key, value in values.items():
            setattr(items[index], key, value)
    return mutate


def _insert(index: int, item: Item) -> Mutation:
    def mutate(items: list[Item], _: list[Chapter]) -> None:
        items.insert(index, item)
    return mutate


def _bridge(text: str | None) -> Mutation:
    def mutate(_: list[Item], chapters: list[Chapter]) -> None:
        chapters[1].bridge = text
    return mutate


def _swap_types(a: int, b: int) -> Mutation:
    def mutate(items: list[Item], _: list[Chapter]) -> None:
        items[a].type, items[b].type = items[b].type, items[a].type
    return mutate


def _drop(index: int) -> Mutation:
    def mutate(items: list[Item], _: list[Chapter]) -> None:
        del items[index]
    return mutate


# (id, mutation, rule code, is the problem a warning)
CASES = [
    ("S4-unknown", _set(10, type="foo"), "S4", False),
    ("S4-empty", _set(10, type=""), "S4", False),
    ("S5-order", _swap_types(10, 11), "S5", False),
    ("S5-cover-in-chapter", _set(6, type="cover"), "S5", False),
    ("S6-no-opener", _set(8, type="observation", highlight=[]), "S6",
     False),
    ("S6-gap", _insert(8, Item("gap", "figure")), "S6", False),
    ("S6-split", _insert(12, Item("late", "limitation", "stream")), "S6",
     False),
    ("S6-unknown-chapter", _set(11, chapter="nope"), "S6", False),
    ("S7-no-link", _bridge("延續上一章"), "S7", False),
    ("S7-missing", _bridge(None), "S7", False),
    ("S8-single", _set(4, build=None), "S8", False),
    ("S8-apart", lambda i, c: (_set(5, build=None)(i, c),
                               _set(6, build="b1")(i, c)), "S8", False),
    ("S9-no-summary", _drop(12), "S9", False),
    ("S9-after-summary", _insert(13, Item("extra", "figure")), "S9",
     False),
    ("S9-two-summaries", _insert(13, Item("again", "summary")), "S9",
     False),
    ("S9-motivation", _set(1, type="background"), "S9", True),
    ("S10-order", _set(12, points=["H2O 先", "AlphaCache 後"]), "S10",
     False),
    ("S10-missing", _set(12, points=["AlphaCache 而已"]), "S10", True),
    ("S11-not-in-points",
     _set(3, points=["Efficient Attention", "H2O"]), "S11", False),
    ("S11-wrong", _set(8, highlight=["AlphaCache"]), "S11", False),
    ("S11-overview", _set(2, highlight=["H2O"]), "S11", False),
]


@pytest.mark.parametrize(("mutate", "code", "warning"),
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_rule_fires_alone(grammar: Grammar, mutate: Mutation, code: str,
                          warning: bool) -> None:
    """Each broken deck reports its own rule and nothing else."""
    items, chapters = copy.deepcopy(clean_deck())
    mutate(items, chapters)
    errors, warnings = sg.check_structure(items, chapters, grammar)
    assert codes(errors + warnings) == {code}, errors + warnings
    if warning:
        assert errors == []
        assert warnings
    else:
        assert errors
        assert all(m.startswith(("p", "gap", "late", "extra", "again", "章"))
                   for m in errors)


def test_chapter_order_and_empty(grammar: Grammar) -> None:
    """Chapters out of order or without pages are S6."""
    items, chapters = clean_deck()
    errors, _ = sg.check_structure(items, list(reversed(chapters)), grammar)
    assert any("[S6]" in e and "順序" in e for e in errors)
    chapters.append(Chapter("empty", "Empty"))
    errors, _ = sg.check_structure(items, chapters, grammar)
    assert any(e.startswith("章 empty") and "[S6]" in e for e in errors)


SUBS_POINTS = ["Beyond Transformers", "  State-Space Models (SSMs): Mamba",
               "  Hybrid Models: Jamba"]


def _subs_deck(highlight: list[str]) -> tuple[list[Item], list[Chapter]]:
    """The clean deck with chapter 2 covering a section and its subs."""
    items, chapters = clean_deck()
    chapters[1] = Chapter("h2o", "Beyond Transformers", None,
                          chapters[1].bridge,
                          ["State-Space Models (SSMs): Mamba",
                           "Hybrid Models: Jamba"])
    items[8].highlight = highlight
    items[8].points = list(SUBS_POINTS)
    items[12].points = ["AlphaCache 留住開頭", "Beyond Transformers 換架構"]
    return items, chapters


def test_roadmap_with_subs(grammar: Grammar) -> None:
    """A roadmap may highlight a section together with its subsections."""
    items, chapters = _subs_deck([s.strip() for s in SUBS_POINTS])
    assert sg.check_structure(items, chapters, grammar) == ([], [])


def test_roadmap_missing_sub(grammar: Grammar) -> None:
    """Leaving a subsection out of the highlight is S11."""
    items, chapters = _subs_deck([s.strip() for s in SUBS_POINTS[:2]])
    errors, warnings = sg.check_structure(items, chapters, grammar)
    assert codes(errors + warnings) == {"S11"}
    assert errors


def test_subs_parsed() -> None:
    """子節 in frontmatter and subs in a sequence become Chapter.subs."""
    chapters, problems = sg.parse_chapters(
        [{"id": "a", "名稱": "A", "子節": ["B", "C"]}])
    assert problems == [] and chapters[0].subs == ["B", "C"]
    assert sg.parse_chapters([{"id": "a", "名稱": "A", "子節": "B"}])[1]
    doc = copy.deepcopy(SEQUENCE)
    doc["chapters"][1]["subs"] = ["X"]
    assert sg.sequence_items(doc)[1][1].subs == ["X"]


def test_bridge_inference_and_links() -> None:
    """A 銜接 may cite a link or start with 推論：."""
    assert sg.bridge_has_link("見 [H2O](../a.md#方法) 與 [b](c.md)") == [
        "../a.md#方法", "c.md"]
    assert sg._check_bridge(Chapter("x", "X", None, "推論：兩者相同")) == []


def test_spotlight_build_group_is_free(grammar: Grammar) -> None:
    """A build group made only of spotlight pages needs no partner."""
    items, chapters = clean_deck()
    assert items[9].type == "spotlight"
    assert sg._check_builds(items) == []


# --- grammar files and chapters --------------------------------------------


def test_grammar_path(tmp_path: Path) -> None:
    """The grammar lives under the wiki-slides skill."""
    assert sg.grammar_path("research", tmp_path) == (
        tmp_path / ".claude/skills/wiki-slides/references/grammars/research.yaml")


@pytest.mark.parametrize("text", [
    "- a\n- b\n",
    "name: x\norder: [a]\nopening: [r]\nhead: []\n",
    "name: x\norder: a\nopening: [r]\nhead: []\ntail: []\n",
    "name: x\norder: [a]\nopening: [r]\nhead: []\ntail: []\n"
    "same_as: {b: c}\n",
    "name: [x\n",
])
def test_load_grammar_rejects(tmp_path: Path, text: str) -> None:
    """Bad grammar files raise ValueError."""
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        sg.load_grammar(path)


def test_load_grammar_missing(tmp_path: Path) -> None:
    """A missing file raises ValueError."""
    with pytest.raises(ValueError):
        sg.load_grammar(tmp_path / "none.yaml")


def test_parse_chapters() -> None:
    """Chapters are read; bad entries become problems."""
    chapters, problems = sg.parse_chapters([
        {"id": "a", "名稱": "A", "節": "Sec", "銜接": "推論：x"},
        {"id": "b", "名稱": "B"},
    ])
    assert problems == []
    assert chapters == [Chapter("a", "A", "Sec", "推論：x"),
                        Chapter("b", "B", None, None)]
    _, problems = sg.parse_chapters([{"id": "a", "名稱": "A"},
                                     {"id": "a", "名稱": "B"},
                                     {"id": "c"}, "x"])
    assert len(problems) == 3
    assert any("重複" in p for p in problems)
    assert any("名稱" in p for p in problems)
    assert sg.parse_chapters("x")[1]
    assert sg.parse_chapters(None) == ([], [])


# --- page references (S3) --------------------------------------------------


def test_page_refs() -> None:
    """Only page notes right after a link count."""
    text = ("[H2O](../../wiki/demo/sources/x.md#方法)（p.4）、"
            "[B](b.md) (p.2-5)，另見（Kwon 2023 p.4）[C](c.md)（p.7–9）")
    assert sg.page_refs(text) == [
        ("../../wiki/demo/sources/x.md#方法", 4, 4),
        ("b.md", 2, 5),
        ("c.md", 7, 9),
    ]
    assert sg.page_refs("[A](a.md) 說明（p.3）") == []
    # A label and a comma before the page, as SKILL.md shows.
    assert sg.page_refs("[A](a.md)（式 2，p.3）、[B](b.md)(Fig. 1, p.5)") == [
        ("a.md", 3, 3), ("b.md", 5, 5)]
    assert sg.page_refs("[A](a.md)（式 2）") == []


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fake repo with source pages and a 3-page PDF."""
    sources = tmp_path / "wiki/demo/sources"
    sources.mkdir(parents=True)
    (tmp_path / "wiki/demo/concepts").mkdir()
    (tmp_path / "raw/sources").mkdir(parents=True)
    pages = {
        "x.md": "sources: [raw/sources/x.pdf]",
        "gone.md": "sources: [raw/sources/gone.pdf]",
        "web.md": "sources: [raw/sources/x.html]",
        "two.md": "sources: [raw/sources/x.pdf, raw/sources/y.pdf]",
    }
    for name, meta in pages.items():
        (sources / name).write_text(f"---\n{meta}\n---\n# 方法\n",
                                    encoding="utf-8")
    (tmp_path / "wiki/demo/concepts/c.md").write_text("# C\n",
                                                    encoding="utf-8")
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page()
        doc.save(tmp_path / "raw/sources/x.pdf")
    (tmp_path / "slides/deck").mkdir(parents=True)
    return tmp_path


S3_CASES = [
    ("ok", "[X](../../wiki/demo/sources/x.md#方法)（p.1–3）", None, False),
    ("non-source", "[C](../../wiki/demo/concepts/c.md)（p.2）", "source 頁",
     False),
    ("url", "[W](https://example.com/a.pdf)（p.2）", "source 頁", False),
    ("out-of-range", "[X](../../wiki/demo/sources/x.md)(p.2-4)", "超出",
     False),
    ("page-zero", "[X](../../wiki/demo/sources/x.md)（p.0）", "超出", False),
    ("missing-pdf", "[G](../../wiki/demo/sources/gone.md)（p.1）", "不存在",
     False),
    ("non-pdf", "[W](../../wiki/demo/sources/web.md)（p.1）", "無法核對",
     True),
    ("two-sources", "[T](../../wiki/demo/sources/two.md)（p.1）", "無法核對",
     True),
]


@pytest.mark.parametrize(("text", "wanted", "warning"),
                         [c[1:] for c in S3_CASES],
                         ids=[c[0] for c in S3_CASES])
def test_page_ref_problems(repo: Path, text: str, wanted: str | None,
                           warning: bool) -> None:
    """Page notes are checked against the single source PDF."""
    errors, warnings = sg.page_ref_problems(repo / "slides/deck", repo, text,
                                            "第 5 行「T」")
    if wanted is None:
        assert (errors, warnings) == ([], [])
        return
    found, other = (warnings, errors) if warning else (errors, warnings)
    assert other == []
    assert len(found) == 1
    assert "[S3]" in found[0] and wanted in found[0]
    assert found[0].startswith("第 5 行「T」")
    assert codes(found) == {"S3"}


# --- reference sequences ---------------------------------------------------


SEQUENCE = {
    "lecture": "Lec",
    "pdf_pages": 30,
    "grammar": "test",
    "head": [{"p": 1, "type": "cover"}, {"p": 2, "type": "motivation"},
             {"p": 3, "type": "roadmap"}],
    "chapters": [
        {"id": "s", "name": "AlphaCache", "section": "Efficient Attention",
         "slides": [
             {"p": 5, "type": "roadmap",
              "highlight": ["Efficient Attention", "AlphaCache"]},
             {"p": 6, "type": "problem", "build": "c"},
             {"p": 7, "type": "problem", "build": "c"},
             {"p": 9, "type": "method"}]},
        {"id": "h", "name": "H2O",
         "slides": [{"p": 12, "type": "roadmap", "highlight": ["H2O"]},
                    {"p": 13, "type": "result"}]},
    ],
    "tail": [{"p": 30, "type": "summary"}],
}


def test_sequence_valid(grammar: Grammar) -> None:
    """A valid sequence converts and passes without bridges."""
    items, chapters, problems = sg.sequence_items(SEQUENCE)
    assert problems == []
    assert [i.where for i in items][:3] == ["p.1", "p.2", "p.3"]
    assert items[3].points is None
    assert chapters[0] == Chapter("s", "AlphaCache", "Efficient Attention")
    result = sg.check_structure(items, chapters, grammar, check_bridges=False)
    assert result == ([], [])
    errors, _ = sg.check_structure(items, chapters, grammar)
    assert codes(errors) == {"S7"}


def test_sequence_wrong_highlight(grammar: Grammar) -> None:
    """Without points, the expected highlight is still checked."""
    doc = copy.deepcopy(SEQUENCE)
    doc["chapters"][1]["slides"][0]["highlight"] = []
    items, chapters, _ = sg.sequence_items(doc)
    errors, _ = sg.check_structure(items, chapters, grammar,
                                   check_bridges=False)
    assert codes(errors) == {"S11"}


def test_sequence_page_problems() -> None:
    """Decreasing or out-of-range pages are problems."""
    doc = copy.deepcopy(SEQUENCE)
    doc["chapters"][0]["slides"][1]["p"] = 4
    doc["tail"][0]["p"] = 31
    _, _, problems = sg.sequence_items(doc)
    assert any(p.startswith("p.4") and "遞增" in p for p in problems)
    assert any(p.startswith("p.31") and "超出" in p for p in problems)
    assert sg.sequence_items([])[2]


def _with_repeat(grammar: Grammar) -> Grammar:
    grammar.repeat = ["method", "result"]
    return grammar


@pytest.mark.parametrize(("second", "ok"), [
    (["method", "result"], True),        # a second round inside the span
    (["method", "steps", "result"], True),
    (["insight"], False),                # before the span: still backward
    (["result", "steps"], False),        # back inside the span, not to start
])
def test_repeat_span(grammar: Grammar, second: list[str], ok: bool) -> None:
    """With `repeat`, a chapter may go back to its start only."""
    items, chapters = clean_deck()
    extra = [Item(f"x{n}", kind, "h2o") for n, kind in enumerate(second)]
    items[12:12] = [Item("p11b", "method", "h2o")] + extra
    errors, _ = sg.check_structure(items, chapters, _with_repeat(grammar))
    assert (codes(errors) == set()) is ok, errors


def test_repeat_not_after_limitation(grammar: Grammar) -> None:
    """Going back to method after limitation is still an error."""
    items, chapters = clean_deck()
    items[12:12] = [Item("lim", "limitation", "h2o"),
                    Item("again", "method", "h2o")]
    errors, _ = sg.check_structure(items, chapters, _with_repeat(grammar))
    assert codes(errors) == {"S5"}


def test_repeat_must_be_a_forward_span(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(GRAMMAR_YAML + "repeat: [result, method]\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="repeat"):
        sg.load_grammar(path)
