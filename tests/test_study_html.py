"""Tests for study_html.py (study JSON → local HTML page)."""

import copy
import json
from pathlib import Path

import pytest
import study_html

PAGE = "wiki/music/concepts/chord-loop.md"
CATS = ["核心概念", "因果關係", "實際應用", "主要爭議"]
BASE = {
    "title": "測試 <study>",
    "topic": "wiki/music/topics/jazz-history.md",
    "date": "2026-11-20",
    "coverage": "partial",
    "map": {"layers": [{"name": "底層概念", "items": [
        {"title": "Loop", "text": "a **b** `c` <x>", "tag": "推論",
         "refs": [{"path": PAGE, "loc": "p.1"}]}]}],
        "mermaid": "flowchart TD\n A-->B", "tree": "A\n└─ B"},
    "disputes": [{"title": "爭議一", "sides": [
        {"label": "甲", "text": "說法甲", "refs": [{"path": PAGE}]},
        {"label": "乙", "text": "說法乙"}]}],
    "quiz": [{"category": c, "question": f"{c} 題 {i}"}
             for c in CATS for i in (1, 2)],
}


@pytest.fixture(autouse=True)
def wiki_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway wiki with the pages BASE refers to, so tests never read the real one."""
    root = tmp_path / "vault"
    pages = {
        PAGE: "Chord loop",
        BASE["topic"]: "Harmony basics",
        "wiki/music/sources/2023-lee-chord-theory.md": "ChordTheory：Notes on Chords",
    }
    for rel, title in pages.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {title}\n---\n", encoding="utf-8")
    monkeypatch.setattr(study_html, "ROOT", root)
    return root


def make(**changes: object) -> dict:
    data = copy.deepcopy(BASE)
    data.update(changes)
    return data


def test_valid_has_no_warnings() -> None:
    assert study_html.validate(make()) == []


@pytest.mark.parametrize("change, message", [
    ({"title": ""}, "title"),
    ({"coverage": "done"}, "coverage"),
    ({"quiz": [{"category": "別的", "question": "q"}]}, "category"),
    ({"disputes": [{"title": "t", "sides": []}]}, "sides"),
    ({"grading": [{"result": "right"}]}, "題數"),
])
def test_validate_rejects(change: dict, message: str) -> None:
    with pytest.raises(study_html.StudyError, match=message):
        study_html.validate(make(**change))


def test_bad_result_rejected() -> None:
    grading = [{"result": "right"}] * 7 + [{"result": "maybe"}]
    with pytest.raises(study_html.StudyError, match="result"):
        study_html.validate(make(grading=grading))


def test_warnings_for_thin_category_and_missing_ref() -> None:
    data = make(quiz=[{"category": c, "question": "q"} for c in CATS])
    data["disputes"][0]["sides"][0]["refs"] = [{"path": "wiki/nope.md"}]
    warnings = study_html.validate(data)
    assert any("少於 2 題" in w for w in warnings)
    assert any("wiki/nope.md" in w for w in warnings)


def test_render_escapes_and_formats() -> None:
    page = study_html.render(make())
    assert "&lt;x&gt;" in page and "<x>" not in page
    assert "<strong>b</strong>" in page and "<code>c</code>" in page
    assert "<title>測試 &lt;study&gt;</title>" in page
    assert "obsidian://open?vault=" in page
    assert "file=wiki%2Fmusic%2Fconcepts%2Fchord-loop.md" in page
    assert page.count("<textarea") == 9  # 8 answer boxes + 1 question box
    assert 'id="grading"' not in page
    assert "mermaid@" in page


def test_render_without_mermaid_skips_cdn() -> None:
    data = make()
    del data["map"]["mermaid"]
    assert "cdn.jsdelivr" not in study_html.render(data)


def test_graded_page_shows_results_not_textareas() -> None:
    grading = [{"result": "right", "answer": "我的答案", "comment": "對",
                "solution": "解答", "refs": [{"path": PAGE}]}] * 8
    page = study_html.render(make(grading=grading))
    assert 'id="grading"' in page and "我的答案" in page
    assert 'aria-label="第 1 題作答"' not in page
    assert "全對 8" in page
    page = study_html.render(make(next=[{"text": "讀 loop", "refs": []}]))
    assert "建議接著讀" in page and "讀 loop" in page


def test_question_box_shown_on_every_page() -> None:
    for data in (make(), make(grading=[{"result": "right"}] * 8)):
        page = study_html.render(data)
        assert 'id="ask-box"' in page and 'id="copy-ask"' in page
        assert page.index('id="ask"') > page.index("<main>")
        assert "wiki-study-ask:" in page


def test_title_cannot_break_out_of_script() -> None:
    page = study_html.render(make(title="</script><script>alert(1)</script>"))
    assert "<script>alert(1)" not in page
    assert 'data-title="&lt;/script&gt;' in page


@pytest.mark.parametrize("where", ["side", "assumption"])
def test_bad_refs_and_tags_give_readable_errors(where: str) -> None:
    data = make()
    if where == "side":
        data["disputes"][0]["sides"][0]["refs"] = [{"loc": "p.1"}]
        message = "path"
    else:
        data["disputes"][0]["assumptions"] = [{"text": "t", "tag": "bogus"}]
        message = "tag"
    with pytest.raises(study_html.StudyError, match=message):
        study_html.validate(data)


def test_zero_disputes_allowed(tmp_path: Path,
                               capsys: pytest.CaptureFixture) -> None:
    data = make(disputes=[])
    assert study_html.validate(data) == []
    assert "找不到有出處的爭議" in study_html.render(data)
    src = tmp_path / "s.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert study_html.main(["build", str(src)]) == 0
    assert "爭議：（無）" in capsys.readouterr().out
    with pytest.raises(study_html.StudyError, match="disputes"):
        study_html.validate({k: v for k, v in make().items()
                             if k != "disputes"})


def test_mermaid_without_tree_warns() -> None:
    data = make()
    del data["map"]["tree"]
    assert any("tree" in w for w in study_html.validate(data))


def test_graded_heading_drops_no_answers_note() -> None:
    grading = [{"result": "skipped"}] * 8
    assert "不附答案" not in study_html.render(make(grading=grading))
    assert "不附答案" in study_html.render(make())


def test_short_title_cuts_subtitle() -> None:
    title = study_html.short_title("wiki/music/sources/2023-lee-chord-theory.md")
    assert title == "ChordTheory"
    assert study_html.short_title("wiki/missing-page.md") == "missing-page"


def test_build_writes_html_next_to_json(tmp_path: Path) -> None:
    src = tmp_path / "s.json"
    src.write_text(json.dumps(make(), ensure_ascii=False), encoding="utf-8")
    out, data, warnings = study_html.build(src)
    assert out == tmp_path / "s.html" and out.is_file()
    assert data["title"] == BASE["title"] and warnings == []


def test_main_prints_three_lines(tmp_path: Path,
                                 capsys: pytest.CaptureFixture) -> None:
    src = tmp_path / "s.json"
    src.write_text(json.dumps(make(), ensure_ascii=False), encoding="utf-8")
    assert study_html.main(["build", str(src)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line[:3] for line in lines] == ["網頁：", "爭議：", "覆蓋："]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert study_html.main(["build", str(bad)]) == 1
