"""Tests for hybrid search (no vector model needed)."""

from pathlib import Path

import pytest

import search


def test_tokenize_keeps_latin_terms_and_cjk_bigrams() -> None:
    tokens = search.tokenize("Bach-BWV1 的十二平均律 [x](../a/b.md) losses")
    assert "bach-bwv1" in tokens and "bach" in tokens and "bwv1" in tokens
    assert {"十二", "二平", "平均", "均律"} <= set(tokens)
    assert "loss" in tokens
    assert not any("md" == t or "b" == t for t in tokens)


def test_tokenize_drops_question_words() -> None:
    assert "什麼" not in search.tokenize("什麼是朝代")


def _write(root: Path, rel: str, fm: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    _write(tmp_path, "wiki/music/concepts/canon.md", "title: Canon\ntype: concept\nsources: []",
           "# Canon\n\n輪唱曲。\n\n## 調音\n\n聲部音量不平均時加 extra voice。\n")
    _write(tmp_path, "wiki/music/concepts/round.md",
           "title: Round\ntype: concept\nsources: []\nrequires: [wiki/music/concepts/canon.md]",
           "# Round\n\n見 [Canon](canon.md)。round 接續主題。\n")
    _write(tmp_path, "wiki/music/concepts/fugue.md", "title: Fugue\ntype: concept\nsources: []",
           "# Fugue\n\n主題與答句。\n\n```\n## not a heading\n```\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/search_eval.tsv").write_text(
        "# comment\n聲部音量不平均\twiki/music/concepts/canon.md\n", encoding="utf-8")
    return tmp_path


def test_chunks_split_on_h2_outside_fences(wiki_root: Path) -> None:
    s = search.Searcher(wiki_root, use_vector=False)
    by_page = {}
    for c in s.chunks:
        by_page.setdefault(c.page, []).append(c.heading)
    assert by_page["wiki/music/concepts/canon.md"] == ["", "調音"]
    assert by_page["wiki/music/concepts/fugue.md"] == [""]
    assert all(c.text.startswith(("Canon", "Round", "Fugue")) for c in s.chunks)


def test_search_ranks_keyword_page_first_and_names_section(wiki_root: Path) -> None:
    hits = search.Searcher(wiki_root, use_vector=False).search("聲部音量不平均", 3)
    assert hits[0].page == "wiki/music/concepts/canon.md"
    assert hits[0].heading == "調音"
    assert "bm25" in hits[0].ranks


def test_graph_stream_reaches_related_pages(wiki_root: Path) -> None:
    s = search.Searcher(wiki_root, use_vector=False)
    graph = dict(s.streams("extra voice"))["graph"]
    assert ("wiki/music/concepts/round.md", "") in graph


def test_prior_reorders_ties(wiki_root: Path) -> None:
    s = search.Searcher(wiki_root, use_vector=False,
                        prior=lambda p: 0.0 if p.endswith("canon.md") else 1.0)
    plain = search.Searcher(wiki_root, use_vector=False).search("聲部音量不平均")
    nudged = s.search("聲部音量不平均")
    canon = next(h for h in nudged if h.page.endswith("canon.md"))
    assert canon.score < next(h for h in plain if h.page.endswith("canon.md")).score


def test_evaluate_reports_recall_without_vectors(wiki_root: Path) -> None:
    report = search.evaluate(wiki_root, wiki_root / "tools/search_eval.tsv", use_vector=False)
    assert report["questions"] == 1
    assert report["found"]["bm25"] == 1 and "vector" not in report["found"]


def test_chunks_index_aliases_with_the_title() -> None:
    from pathlib import PurePosixPath

    import lint
    fm = {"title": "Feudalism", "type": "concept", "aliases": ["封建制度"]}
    page = lint.Page(PurePosixPath("wiki/history/concepts/v.md"), fm, "## A\ntext\n", [], None)
    chunks = search.chunks_of("wiki/history/concepts/v.md", page)
    assert chunks and chunks[0].text.startswith("Feudalism 封建制度\n")


def test_chinese_alias_in_query_finds_english_page(tmp_path: Path) -> None:
    _write(tmp_path, "wiki/history/concepts/dynasty.md",
           "title: Dynasty\ntype: concept\naliases: [朝代]\nsources: []",
           "# Dynasty\n\n開頭。\n\n## Falls\n\ndynasty dynasty falls after long wars.\n")
    s = search.Searcher(tmp_path, use_vector=False)
    assert s.expand("朝代史觀") == "朝代史觀 Dynasty"
    # The English-only section is reachable from a Chinese query only through expansion.
    i = next(k for k, c in enumerate(s.chunks) if c.heading == "Falls")
    assert s.bm25.scores(search.tokenize("朝代"))[i] == 0
    assert s.bm25.scores(search.tokenize(s.expand("朝代")))[i] > 0
    # streams() searches the expanded query, not the raw one.
    seen = []
    s.expand = lambda q: seen.append(q) or "falls"
    assert s.streams("朝代")["bm25"][0] == ("wiki/history/concepts/dynasty.md", "Falls")
    assert seen == ["朝代"]


def test_expand_keeps_every_page_sharing_an_alias(tmp_path: Path) -> None:
    _write(tmp_path, "wiki/history/concepts/a.md",
           "title: Alpha\ntype: concept\naliases: [朝代]\nsources: []", "# A\n")
    _write(tmp_path, "wiki/music/concepts/b.md",
           "type: concept\naliases: [朝代, 朝代史觀]\nsources: []", "# B\n")
    s = search.Searcher(tmp_path, use_vector=False)
    assert s.expand("朝代史觀與朝代") == "朝代史觀與朝代 wiki/music/concepts/b.md Alpha wiki/music/concepts/b.md"
