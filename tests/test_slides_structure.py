"""Tests for the lecture-structure wiring in tools/slides.py."""

from pathlib import Path

import _limits  # noqa: F401  (4 threads, 8 GB; before pymupdf)
import pymupdf
import pytest

import slides

SEQ = Path(__file__).resolve().parent.parent / (
    ".claude/skills/wiki-slides/references/sequences/mit-6.5940-lec15.yaml")


def _repo(root: Path, pages: int = 3) -> None:
    """A fake repo: one source page backed by a PDF with `pages` pages."""
    pdf = root / "raw" / "sources" / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(pdf)
    doc.close()
    src = root / "wiki" / "demo" / "sources" / "2024-a-paper.md"
    src.parent.mkdir(parents=True)
    src.write_text("---\nsources:\n  - raw/sources/paper.pdf\n---\n"
                   "# Paper\n\n## 方法\n", encoding="utf-8")


def _deck(root: Path, body: str, grammar: bool = True) -> Path:
    deck = root / "slides" / "demo"
    deck.mkdir(parents=True)
    head = "---\ntitle: Demo\n"
    if grammar:
        head += ("文法: research\n章:\n  - id: a\n    名稱: AlphaCache\n"
                 "    節: Efficient Attention\n")
    path = deck / "outline.md"
    path.write_text(head + "---\n\n" + body, encoding="utf-8")
    return path


SRC = "[Paper](../../wiki/demo/sources/2024-a-paper.md#方法)"


def _body(page: int = 2) -> str:
    return f"""## 1. 封面
版型: 封面
類型: cover
副標: 副標

## 2. 動機
版型: 條列
類型: motivation
要點:
- 長文很貴
出處:
- {SRC}（p.1）

## 3. 大綱
版型: 條列
類型: roadmap
章: a
要點:
- Efficient Attention
- AlphaCache
- 其他
反白: Efficient Attention, AlphaCache

## 4. 問題
版型: 條列
類型: problem
章: a
要點:
- 視窗注意力會崩
出處:
- {SRC}（p.{page}）

## 5. 總結
版型: 條列
類型: summary
要點:
- AlphaCache 留住開頭的 token
出處:
- {SRC}（p.3）
"""


def test_grammar_deck_is_clean(tmp_path: Path) -> None:
    """A roadmap needs no 出處 under 文法; the rest passes."""
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body()))
    assert slides.check(outline) == []
    assert slides.structure(outline) == ([], [])


def test_roadmap_needs_source_without_grammar(tmp_path: Path) -> None:
    """Without 文法 the old rule stays: only the cover is exempt."""
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body(), grammar=False))
    problems = slides.check(outline)
    assert any("大綱" in p and "缺少出處" in p for p in problems)
    assert slides.structure(outline) == ([], [])


def test_page_out_of_range(tmp_path: Path) -> None:
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body(page=9)))
    problems = slides.check(outline)
    assert any("[S3]" in p and "共 3 頁" in p for p in problems)


def test_structure_error_reaches_check(tmp_path: Path) -> None:
    _repo(tmp_path)
    body = _body().replace("類型: problem", "類型: nonsense")
    problems = slides.check(slides.parse_outline(_deck(tmp_path, body)))
    assert any("[S4]" in p for p in problems)


def test_highlight_must_be_a_point_without_grammar(tmp_path: Path) -> None:
    _repo(tmp_path)
    body = _body().replace("反白: Efficient Attention, AlphaCache",
                           "反白: BetaCache")
    problems = slides.check(slides.parse_outline(
        _deck(tmp_path, body, grammar=False)))
    assert any("反白「BetaCache」不在這頁的要點裡" in p for p in problems)


def test_unknown_grammar_name(tmp_path: Path) -> None:
    _repo(tmp_path)
    path = _deck(tmp_path, _body())
    path.write_text(path.read_text(encoding="utf-8").replace(
        "文法: research", "文法: nosuch"), encoding="utf-8")
    problems = slides.check(slides.parse_outline(path))
    assert any("nosuch" in p for p in problems)


@pytest.mark.skipif(not SEQ.is_file(), reason="沒有 Lec15 參考序列")
def test_lec15_sequence_passes() -> None:
    assert slides.main(["check", "--sequence", str(SEQ)]) == 0


def _sequence(root: Path, chapters: str) -> Path:
    path = root / "seq.yaml"
    path.write_text("pdf_pages: 9\ngrammar: research\nhead:\n  - {p: 1, type: cover}\n"
                    "chapters:\n" + chapters
                    + "tail:\n  - {p: 9, type: summary}\n", encoding="utf-8")
    return path


REF = """  - id: a
    name: AlphaCache
    section: Efficient Attention
    slides:
      - {p: 2, type: roadmap}
      - {p: 3, type: problem, build: g}
      - {p: 4, type: problem, build: g}
      - {p: 5, type: figure}
      - {p: 6, type: method}
      - {p: 7, type: ablation}
"""


def test_compare_passes_on_same_shape(tmp_path: Path, capsys) -> None:
    """Builds merge, free types drop, ablation counts as result."""
    _repo(tmp_path)
    body = _body().replace("## 5. 總結", f"""## 4b. 方法
版型: 條列
類型: method
章: a
要點:
- 留住開頭
出處:
- {SRC}

## 4c. 結果
版型: 條列
類型: result
章: a
要點:
- 比較好
出處:
- {SRC}

## 5. 總結""")
    outline = slides.parse_outline(_deck(tmp_path, body))
    assert slides.check(outline) == []
    assert slides.compare(outline, _sequence(tmp_path, REF), None) == 0
    assert "通過" in capsys.readouterr().out


def test_compare_reports_shape_and_missing(tmp_path: Path, capsys) -> None:
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body()))
    more = REF + "  - id: b\n    name: GammaCache\n    slides:\n" \
        "      - {p: 8, type: roadmap}\n"
    assert slides.compare(outline, _sequence(tmp_path, more), None) == 1
    out = capsys.readouterr().out
    assert "缺章：b（GammaCache）" in out
    assert "章 a：不同" in out
    assert slides.compare(outline, _sequence(tmp_path, more), "a") == 1


def test_pages_and_sample(tmp_path: Path, capsys) -> None:
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body()))
    out = tmp_path / "pages"
    assert slides.main(["pages", str(outline.path), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "paper-p001.png", "paper-p002.png", "paper-p003.png"]
    capsys.readouterr()
    assert slides.main(["sample", str(outline.path), "--n", "2", "--seed",
                        "1", "--pages", str(out)]) == 0
    text = capsys.readouterr().out
    assert text.count("主張：") == 2
    assert "原頁：" in text


def test_page_refs_checked_without_grammar(tmp_path: Path) -> None:
    """（p.N） is checked even when the outline has no 文法."""
    _repo(tmp_path)
    body = _body(page=9).replace(
        "- 長文很貴\n出處:\n", "- 長文很貴\n出處:\n- [Blog](https://example.com)（p.2）\n")
    problems = slides.check(slides.parse_outline(
        _deck(tmp_path, body, grammar=False)))
    assert any("[S3]" in p and "共 3 頁" in p for p in problems)
    assert sum("[S3]" in p for p in problems) == 2


def test_compare_bad_sequence_file(tmp_path: Path, capsys) -> None:
    _repo(tmp_path)
    outline = slides.parse_outline(_deck(tmp_path, _body()))
    assert slides.compare(outline, tmp_path / "missing.yaml", None) == 1
    bad = tmp_path / "bad.yaml"
    bad.write_text("chapters: [\n", encoding="utf-8")
    assert slides.compare(outline, bad, None) == 1
    assert slides.main(["check", "--sequence", str(bad)]) == 1
    assert capsys.readouterr().out.count("錯誤：") == 3
