import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from lint import Page, Wiki, _empty_h2_sections, check_backlinks, check_index_missing, compare, has_findings, lint
from lint import load as load_wiki

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "repo"
LINT_SCRIPT = REPO / "tools" / "lint.py"

GOOD = "wiki/sources/2024-a-good.md"
NO_SECTION = "wiki/sources/2024-b-nosection.md"
PARTIAL = "wiki/sources/2024-c-partial.md"
NO_TIER = "wiki/sources/2024-d-notier.md"
CLEAN = "wiki/concepts/clean.md"


@pytest.fixture(scope="module")
def report():
    return lint(FIXTURE)


@pytest.mark.parametrize("page_type, flagged", [
    ("concept", True), ("entity", True), ("synthesis", False), ("comparison", False),
])
def test_backlinks_only_required_for_concepts_and_entities(page_type, flagged):
    src, other = "wiki/sources/a.md", "wiki/x/b.md"
    pages = {
        src: Page(PurePosixPath(src), {"type": "source"}, "", [], set()),
        other: Page(PurePosixPath(other), {"type": page_type, "sources": [src]}, "", [], None),
    }
    assert bool(check_backlinks(Wiki(FIXTURE, pages, {}))) is flagged


def test_broken_links_cover_content_pages_overview_and_frontmatter_sources(report):
    found = {(b["page"], b["target"], b["kind"]) for b in report["broken_links"]}
    assert found == {
        ("wiki/concepts/unlisted.md", "does-not-exist.md", "link"),
        ("wiki/overview.md", "concepts/removed.md", "link"),
        ("wiki/concepts/ghost-cite.md", "wiki/sources/2099-missing.md", "frontmatter_source"),
    }


def test_orphans_ignore_index_links_self_links_and_code(report):
    assert set(report["orphans"]) == {"wiki/concepts/orphan.md", "wiki/concepts/index-only.md"}


def test_frontmatter_errors_name_the_offending_field(report):
    errors = {e["page"]: e["problems"] for e in report["frontmatter_errors"]}
    assert set(errors) == {"wiki/concepts/no-domain.md", "wiki/concepts/bad-enum.md", PARTIAL, NO_TIER}
    expected_field = {
        "wiki/concepts/no-domain.md": "domain",
        "wiki/concepts/bad-enum.md": "status",
        PARTIAL: "未讀章節",
        NO_TIER: "tier",
    }
    for page, field in expected_field.items():
        assert len(errors[page]) == 1 and field in errors[page][0], (page, errors[page])


def test_backlink_mismatches_follow_schema_section_4(report):
    found = {(m["source"], m.get("page"), m["problem"]) for m in report["backlink_mismatches"]}
    assert found == {
        (GOOD, "wiki/concepts/uncited.md", "listed_but_not_cited"),
        (GOOD, "wiki/concepts/unlisted.md", "cited_but_not_listed"),
        (NO_SECTION, None, "missing_section"),
    }


def test_todo_lists(report):
    assert report["disputed"] == ["wiki/entities/review-me.md"]
    assert {"page": "wiki/entities/review-me.md", "status": "needs-review",
            "to": "disputed"} in report["legacy_status"]
    assert set(report["partial_sources"]) == {NO_SECTION, PARTIAL}


def test_page_count_excludes_special_pages_and_assets(report):
    assert report["page_count"] == {"count": 13, "warn_at": 90, "split_at": 100, "layout": "flat", "warn": False}


def test_ingests_counted_after_last_lint(report):
    assert report["ingests_since_last_lint"] == {"count": 1, "remind": False}


def test_ingests_counted_from_start_without_lint(tmp_path):
    (tmp_path / "wiki").mkdir()
    headings = "\n".join(f"## [2026-10-0{i}] {t} | x" for i, t in enumerate(["ingest"] * 5, 1))
    (tmp_path / "wiki" / "log.md").write_text(f"# Log\n\n{headings}\n", encoding="utf-8")
    assert lint(tmp_path)["ingests_since_last_lint"] == {"count": 5, "remind": True}


def test_unpushed_commits_reports_error_outside_a_git_repo(report):
    result = report["unpushed_commits"]
    assert result["count"] is None and result["error"]


def test_changed_since_last_lint_lists_wiki_files_after_lint_commit(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True, capture_output=True,
        )

    def commit(path, message):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(message, encoding="utf-8")
        git("add", ".")
        git("commit", "-m", message)

    git("init", "-b", "main")
    commit("wiki/old.md", "start")
    assert lint(tmp_path)["changed_since_last_lint"] == {"since": None, "files": []}

    commit("wiki/log.md", "docs(wiki): lint 第 1 次")
    commit("wiki/concepts/a.md", "docs(wiki): lint 第 1 次的修正")
    commit("wiki/log.md", "docs(wiki): log")
    commit("tools/x.py", "feat(tools): x")
    result = lint(tmp_path)["changed_since_last_lint"]
    assert result["since"]
    assert result["files"] == ["wiki/concepts/a.md"]

    commit("wiki/concepts/b.md", "docs(wiki): lint 第 2 次並全部修正")
    commit("wiki/concepts/c.md", "docs(wiki): edit c")
    assert lint(tmp_path)["changed_since_last_lint"]["files"] == ["wiki/concepts/c.md"]


def test_unpushed_commits_counts_main_ahead_of_origin_but_not_for_nested_roots(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "pushed")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    for message in ("one", "two"):
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", message)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "nested" / "wiki").mkdir(parents=True)

    assert lint(tmp_path)["unpushed_commits"] == {"count": 2}
    assert lint(tmp_path / "nested")["unpushed_commits"]["count"] is None


def test_clean_page_has_no_findings(report):
    serialized = json.dumps(report, ensure_ascii=False)
    assert CLEAN not in serialized


def test_cli_prints_the_same_json():
    proc = subprocess.run(
        [sys.executable, str(LINT_SCRIPT), "--root", str(FIXTURE)],
        capture_output=True, encoding="utf-8", check=True,
    )
    assert json.loads(proc.stdout) == json.loads(json.dumps(lint(FIXTURE)))


def test_cli_exits_2_without_wiki(tmp_path):
    proc = subprocess.run([sys.executable, str(LINT_SCRIPT), "--root", str(tmp_path)], capture_output=True)
    assert proc.returncode == 2


def test_frontmatter_table_one_line_per_page():
    from lint import frontmatter_table, load

    rows = frontmatter_table(load(FIXTURE))
    assert rows[0].startswith("path\ttype\t")
    assert len(rows) == len(load(FIXTURE).pages) + 1
    assert all(r.count("\t") == rows[0].count("\t") for r in rows)


def test_empty_sections_ignore_code_subheadings_and_h1() -> None:
    body = "# T\n\n## empty\n\n## code\n```\n## not a heading\n```\n## sub\n### child\n\n## last\n"
    assert _empty_h2_sections(body) == ["empty", "last"]


def test_index_missing_requires_a_topic_and_topics_in_the_index(tmp_path: Path) -> None:
    timeline = "---\ntype: synthesis\n---\n"
    _page(tmp_path, "wiki/index.md", "[t](topics/t.md) [s](synthesis/s.md) [direct](concepts/direct.md)\n")
    _page(tmp_path, "wiki/topics/t.md", "[a](../concepts/a.md)\n")
    _page(tmp_path, "wiki/topics/unlisted.md", "x\n")
    _page(tmp_path, "wiki/concepts/a.md", "x\n")
    _page(tmp_path, "wiki/concepts/direct.md", "x\n")
    _page(tmp_path, "wiki/concepts/b.md", "x\n")
    _page(tmp_path, "wiki/synthesis/s.md", timeline)
    _page(tmp_path, "wiki/synthesis/unlisted.md", timeline)
    assert check_index_missing(load_wiki(tmp_path)) == [
        "wiki/topics/unlisted.md",
        "wiki/concepts/b.md",
        "wiki/concepts/direct.md",
        "wiki/synthesis/unlisted.md",
    ]


def test_topic_pages_are_navigation_only(tmp_path: Path) -> None:
    frontmatter = "title: t\ndomain: music\ntags: []\ncreated: x\nupdated: x\nstatus: draft"
    _page(tmp_path, "wiki/topics/t.md", f"---\ntype: topic\n{frontmatter}\n---\n\n[a](../concepts/a.md) [gone](gone.md)\n")
    _page(tmp_path, "wiki/concepts/a.md", "x\n")
    report = lint(tmp_path)
    assert "wiki/concepts/a.md" in report["orphans"]
    assert report["page_count"]["count"] == 1
    assert [b["page"] for b in report["broken_links"]] == ["wiki/topics/t.md"]
    assert "wiki/topics/t.md" not in {e["page"] for e in report["frontmatter_errors"]}


def test_compare_reports_resolved_new_and_remaining() -> None:
    before = {"orphans": ["a", "b"], "ledgers": {"violations": []}}
    after = {"orphans": ["b", "c"], "ledgers": {"violations": [{"id": "c-1"}]}}
    result = compare(before, after)
    assert result["orphans"] == {"resolved": ["a"], "new": ["c"], "remaining": 1}
    assert result["ledger_violations"]["new"] == [{"id": "c-1"}]


def _page(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_new_checks_report_anchors_duplicates_and_hidden_links(tmp_path: Path) -> None:
    _page(tmp_path, "wiki/concepts/a.md", (
        "---\ntitle: a\n---\n\n## Warm Start：x\n\n"
        "[ok](#warm%20start：x) [slug](#warm-startx) [bad](#nope) [block](#^id)\n"
        "[hidden](../../.llm-wiki/x.md)\n"
    ))
    _page(tmp_path, "wiki/sources/a.md", "x\n")
    report = lint(tmp_path)
    assert report["broken_anchors"] == [
        {"page": "wiki/concepts/a.md", "line": 7, "target": "#warm-startx", "reason": "slug_only"},
        {"page": "wiki/concepts/a.md", "line": 7, "target": "#nope", "reason": "not_found"},
    ]
    assert report["duplicate_basenames"][0]["basename"] == "a.md"
    assert report["hidden_target_links"][0]["line"] == 8
    assert lint(tmp_path, ("wiki/sources/*",))["duplicate_basenames"] == []


def test_compare_ignores_line_shifts_and_strict_detects_findings() -> None:
    finding = {"page": "p", "target": "x", "reason": "not_found"}
    empty = {"ledgers": {"violations": []}}
    before = {"broken_anchors": [{**finding, "line": 3}], **empty}
    after = {"broken_anchors": [{**finding, "line": 9}], **empty}
    assert compare(before, after)["broken_anchors"] == {"resolved": [], "new": [], "remaining": 1}
    assert has_findings(after) and not has_findings(empty)


def test_lone_source_of_a_topic_is_not_an_orphan(tmp_path: Path) -> None:
    fm = "title: t\ndomain: music\ntags: []\ncreated: x\nupdated: x\nstatus: draft"
    _page(tmp_path, "wiki/topics/t.md", f"---\ntype: topic\n{fm}\n---\n\n[s](../sources/s.md)\n")
    _page(tmp_path, "wiki/topics/u.md", f"---\ntype: topic\n{fm}\n---\n\n[a](../concepts/a.md)\n")
    _page(tmp_path, "wiki/sources/s.md", "---\ntype: source\n---\n\nx\n")
    _page(tmp_path, "wiki/concepts/a.md", "---\ntype: concept\n---\n\nx\n")
    report = lint(tmp_path)
    assert report["single_source_topics"] == [{"topic": "wiki/topics/t.md", "page": "wiki/sources/s.md"}]
    assert report["orphans"] == ["wiki/concepts/a.md"]


def test_unlinked_raw_sources_flags_frontmatter_files_missing_from_body():
    from lint import Link, check_unlinked_raw_sources
    key = "wiki/sources/a.md"
    linked = Link("../../raw/sources/x.pdf", "raw/sources/x.pdf", False, 3)
    page = Page(PurePosixPath(key), {"type": "source", "sources": ["raw/sources/x.pdf", "raw/sources/y.pdf"]},
                "", [linked], None)
    assert check_unlinked_raw_sources(Wiki(FIXTURE, {key: page}, {})) == [{"page": key, "target": "raw/sources/y.pdf"}]


def test_index_missing_with_domain_sub_indexes(tmp_path: Path) -> None:
    _page(tmp_path, "wiki/index.md", "[music](music/index.md)\n")
    _page(tmp_path, "wiki/music/index.md", "[t](topics/t.md)\n")
    _page(tmp_path, "wiki/history/index.md", "[f](topics/f.md)\n")
    _page(tmp_path, "wiki/music/topics/t.md", "[a](../concepts/a.md) [b](../../history/concepts/b.md)\n")
    _page(tmp_path, "wiki/history/topics/f.md", "x\n")
    _page(tmp_path, "wiki/music/concepts/a.md", "x\n")
    _page(tmp_path, "wiki/history/concepts/b.md", "x\n")
    wiki = load_wiki(tmp_path)
    assert set(wiki.topics) == {"wiki/music/topics/t.md", "wiki/history/topics/f.md"}
    assert "wiki/music/index.md" in wiki.special
    assert check_index_missing(wiki) == ["wiki/history/index.md"]


def test_page_count_stops_warning_after_domain_split(tmp_path: Path) -> None:
    for i in range(95):
        _page(tmp_path, f"wiki/music/concepts/c{i}.md", "x\n")
    count = lint(tmp_path)["page_count"]
    assert count["layout"] == "domain" and count["count"] == 95 and count["warn"] is False


def _source_problems(created, kind=None, body="## 研究脈絡\n\n承先。\n"):
    from lint import _frontmatter_problems
    fm = {"title": "t", "type": "source", "domain": "music", "tags": [], "sources": [],
          "created": created, "updated": created, "status": "draft",
          "tier": "primary", "coverage": "full"}
    if kind is not None:
        fm["source_kind"] = kind
    return _frontmatter_problems(Page(PurePosixPath("wiki/music/sources/x.md"), fm, body, [], None))


def test_new_source_pages_need_source_kind_and_research_context():
    assert _source_problems("2026-11-15", "paper") == []
    assert _source_problems("2026-11-15") == ["missing: source_kind"]
    assert _source_problems("2026-11-16", "blog") == ["invalid source_kind: blog"]
    assert _source_problems("2026-11-16", ["paper"]) == ["invalid source_kind: ['paper']"]
    assert _source_problems("2026-11-15", "docs", body="## 一句話\n") == ["missing ## 研究脈絡"]


def test_older_source_pages_are_not_held_to_source_kind():
    assert _source_problems("2026-11-14", body="") == []


def test_reingested_older_page_with_source_kind_is_checked():
    assert _source_problems("2026-11-08", "paper", body="## 一句話\n") == ["missing ## 研究脈絡"]
    assert _source_problems("2026-11-08", "blog") == ["invalid source_kind: blog"]


def _alias_page(root: Path, rel: str, domain: str, aliases: str, body: str,
                page_type: str = "concept") -> None:
    fm = f"---\ntitle: t\ntype: {page_type}\ndomain: {domain}\n"
    if aliases:
        fm += f"aliases: {aliases}\n"
    _page(root, rel, fm + "---\n" + body)


def _alias_wiki(tmp_path: Path) -> dict:
    _alias_page(tmp_path, "wiki/history/concepts/feudalism.md", "history",
                "[封建制度]", "# 封建制度（Feudalism）\n本頁自己用封建制度不算。\n")
    _alias_page(tmp_path, "wiki/history/concepts/dynasty.md", "history",
                "[朝代]", "x\n")
    _alias_page(tmp_path, "wiki/history/concepts/dynastic-cycle.md", "history",
                "[朝代循環]", "x\n")
    _alias_page(tmp_path, "wiki/music/concepts/harmony.md", "music", "[和聲學]", "x\n")
    _alias_page(tmp_path, "wiki/cross/concepts/dating.md", "cross", "[定年]", "x\n")
    _alias_page(tmp_path, "wiki/history/sources/2024-a.md", "history", "", (
        "封建制度很強。\n"                               # 3: hit
        "## 名詞\n- dynasty（朝代）：解釋\n"             # 7-8: allowed form
        "看 [連結](https://example.com/封建制度) 與 `封建制度`\n"  # 9: target and code skipped
        "```\n封建制度\n```\n"                           # 10-12: fence skipped
        "朝代循環與和聲學，定年也有。\n"                   # 13: 朝代循環 once, 和聲學 is music
    ), page_type="source")
    _alias_page(tmp_path, "wiki/cross/concepts/c.md", "cross", "", (
        "和聲學與封建制度\n"
        "人口（朝代）\n"                                  # 7: a bare gloss after Chinese is a hit
        "`動`能，dynasty（朝代、朝代循環）\n"             # 8: code split and English gloss skipped
        "Table 3（朝代更替的人口）\n"                       # 9: prose in parentheses is a hit
        "AMP 2013（全球朝代循環研究）\n"))                  # 10: same
    return lint(tmp_path)


def test_alias_in_prose_reports_aliases_outside_the_owner(tmp_path: Path) -> None:
    found = {(h["page"], h["alias"], tuple(h["line"])) for h in _alias_wiki(tmp_path)["alias_in_prose"]}
    src, cross = "wiki/history/sources/2024-a.md", "wiki/cross/concepts/c.md"
    assert found == {
        (src, "封建制度", (6,)),
        (src, "朝代循環", (13,)),
        (src, "定年", (13,)),
        (cross, "和聲學", (6,)),
        (cross, "封建制度", (6,)),
        (cross, "朝代", (7, 9)),
        (cross, "朝代循環", (10,)),
    }


def test_alias_in_prose_is_not_strict_but_conflicts_are(tmp_path: Path) -> None:
    from lint import COMPARED_FINDINGS
    empty = {**{n: [] for n in COMPARED_FINDINGS}, "ledgers": {"violations": []}}
    assert not has_findings({**empty, "alias_in_prose": [{"page": "p"}]})
    assert has_findings({**empty, "alias_conflicts": [{"alias": "a"}]})
    assert _alias_wiki(tmp_path)["alias_conflicts"] == []
    _alias_page(tmp_path, "wiki/history/concepts/dup.md", "history", "[朝代]", "x\n")
    _alias_page(tmp_path, "wiki/music/concepts/dup.md", "music", "[朝代循環]", "x\n")
    assert lint(tmp_path)["alias_conflicts"] == [{"alias": "朝代", "pages": [
        "wiki/history/concepts/dup.md", "wiki/history/concepts/dynasty.md"]}]


def test_aliases_must_be_a_string_list_on_concept_or_entity_pages(tmp_path: Path) -> None:
    _alias_page(tmp_path, "wiki/music/concepts/a.md", "music", "價值", "x\n")
    _alias_page(tmp_path, "wiki/music/concepts/b.md", "music", '[""]', "x\n")
    _alias_page(tmp_path, "wiki/music/sources/2024-s.md", "music", "[x]", "x\n", page_type="source")
    problems = {e["page"]: e["problems"] for e in lint(tmp_path)["frontmatter_errors"]}
    assert "aliases is not a list of non-empty strings" in problems["wiki/music/concepts/a.md"]
    assert "aliases is not a list of non-empty strings" in problems["wiki/music/concepts/b.md"]
    assert "aliases only allowed on concept/entity pages" in problems["wiki/music/sources/2024-s.md"]


def test_sources_missing_and_unused_compare_body_links_with_frontmatter(
        tmp_path: Path) -> None:
    src = "---\ntype: source\nsources: [raw/sources/x.pdf]\n---\n\n[other](b.md)\n"
    _page(tmp_path, "wiki/sources/a.md", src)
    _page(tmp_path, "wiki/sources/b.md", src)
    _page(tmp_path, "wiki/sources/c.md", src)
    _page(tmp_path, "wiki/concepts/k.md", (
        "---\ntype: concept\nsources: [wiki/sources/a.md, wiki/sources/c.md]\n---\n\n"
        "[a](../sources/a.md) ![fig](../sources/c.md)\n\n"
        "[b](../sources/b.md#x) [topic](../topics/t.md)\n"
    ))
    report = lint(tmp_path)
    assert report["sources_missing"] == [
        {"page": "wiki/concepts/k.md", "source": "wiki/sources/b.md", "line": [8]},
    ]
    assert report["sources_unused"] == [
        {"page": "wiki/concepts/k.md", "source": "wiki/sources/c.md"},
    ]
    empty = {"ledgers": {"violations": []}}
    assert not has_findings({**empty, "sources_unused": report["sources_unused"]})
    assert has_findings({**empty, "sources_missing": report["sources_missing"]})


def test_domains_are_the_wiki_directories_not_a_fixed_list(tmp_path: Path) -> None:
    import _common
    for d in ("cooking", "home-garden", "assets", "meta", "canvases", "concepts", "Bad_Name"):
        (tmp_path / "wiki" / d).mkdir(parents=True)
    assert _common.discover_domains(tmp_path) == ("cooking", "home-garden")
    assert _common.discover_domains(tmp_path / "missing") == ()


def test_any_domain_name_works_end_to_end(tmp_path: Path) -> None:
    fm = ("---\ntitle: t\ntype: concept\ndomain: {d}\ntags: []\nsources: []\n"
          "created: 2026-01-01\nupdated: 2026-01-01\nstatus: draft\n---\n")
    _page(tmp_path, "wiki/index.md", "[c](cooking/index.md)\n")
    _page(tmp_path, "wiki/cooking/index.md", "[t](topics/t.md)\n")
    _page(tmp_path, "wiki/cooking/topics/t.md", "[a](../concepts/a.md) [b](../concepts/b.md)\n")
    _page(tmp_path, "wiki/cooking/concepts/a.md", fm.format(d="cooking") + "x\n")
    _page(tmp_path, "wiki/cooking/concepts/b.md", fm.format(d="music") + "x\n")
    wiki = load_wiki(tmp_path)
    assert wiki.domains == ("cooking",)
    assert set(wiki.topics) == {"wiki/cooking/topics/t.md"}
    assert "wiki/cooking/index.md" in wiki.special
    errors = {e["page"]: e["problems"] for e in lint(tmp_path)["frontmatter_errors"]}
    assert errors["wiki/cooking/concepts/b.md"] == ["invalid domain: music"]
    assert "wiki/cooking/concepts/a.md" not in errors


def test_flat_wiki_accepts_any_kebab_case_domain():
    from lint import _frontmatter_problems
    fm = {"title": "t", "type": "concept", "tags": [], "sources": [], "created": "2026-01-01",
          "updated": "2026-01-01", "status": "draft"}
    page = lambda d: Page(PurePosixPath("wiki/concepts/x.md"), {**fm, "domain": d}, "", [], None)  # noqa: E731
    assert _frontmatter_problems(page("home-cooking")) == []
    assert _frontmatter_problems(page("Home Cooking")) == ["invalid domain: Home Cooking"]
