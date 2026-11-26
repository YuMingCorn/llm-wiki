"""Tests for tools/slide_refseq.py, using small PDFs made on the fly."""

import sys
from pathlib import Path

import pymupdf
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import slide_refseq  # noqa: E402

TITLE_SIZE = 28
BODY_SIZE = 12


def make_pdf(path: Path, pages: list[dict]) -> Path:
    """Writes a PDF from page specs.

    Keys: title, title_size, body, box, header.
    """
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=720, height=540)
        if "header" in spec:
            page.insert_text((20, 30), spec["header"], fontsize=40)
        if "title" in spec:
            page.insert_text((40, 80), spec["title"],
                             fontsize=spec.get("title_size", TITLE_SIZE))
        for k, line in enumerate(spec.get("body", [])):
            page.insert_text((40, 200 + 18 * k), line, fontsize=BODY_SIZE)
        if "box" in spec:
            page.draw_rect(pymupdf.Rect(*spec["box"]), color=(0, 0, 0),
                           fill=(0, 0, 0))
        page.insert_text((680, 530), str(len(doc)), fontsize=10)  # page number
    doc.save(path)
    doc.close()
    return path


def run(tmp_path: Path, pdf: Path, *extra: str) -> dict:
    out = tmp_path / "draft.yaml"
    code = slide_refseq.main(["draft", str(pdf), "--out", str(out), *extra])
    assert code == 0
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def test_repeated_title_identical_body_groups_both_ways(tmp_path):
    body = ["first point", "second point", "third point"]
    pdf = make_pdf(tmp_path / "a.pdf", [
        {"title": "Lecture Plan", "body": body},
        {"title": "Other Slide", "box": (40, 150, 680, 450)},
        {"title": "Lecture Plan", "body": body},
        {"title": "Lecture Plan", "body": body},
    ])
    draft = run(tmp_path, pdf)
    expected = [{"title": "Lecture Plan", "pages": [1, 3, 4]}]
    assert draft["title_groups"] == expected
    assert draft["image_groups"] == expected
    assert all(p["flags"] == [] for p in draft["pages"])
    assert draft["pages"][0]["title_group"] == 0
    assert draft["pages"][1]["title_group"] is None
    assert all(p["type"] is None for p in draft["pages"])


def test_same_title_different_body_is_flagged(tmp_path):
    pdf = make_pdf(tmp_path / "b.pdf", [
        {"title": "Results", "body": ["a small table"]},
        {"title": "Results", "box": (40, 150, 680, 450)},
    ])
    draft = run(tmp_path, pdf)
    assert draft["title_groups"] == [{"title": "Results", "pages": [1, 2]}]
    assert draft["image_groups"] == []
    for p in draft["pages"]:
        assert p["flags"] == ["same_title_diff_image"]


def test_same_image_different_title_is_flagged(tmp_path):
    box = (40, 150, 680, 450)
    pdf = make_pdf(tmp_path / "c.pdf", [
        {"title": "Part one", "title_size": 5, "box": box},
        {"title": "Part two", "title_size": 5, "box": box},
    ])
    draft = run(tmp_path, pdf)
    assert draft["title_groups"] == []
    assert draft["image_groups"] == [{"title": "Part one", "pages": [1, 2]}]
    for p in draft["pages"]:
        assert p["flags"] == ["same_image_diff_title"]


def test_ignore_skips_header_line(tmp_path):
    pdf = make_pdf(tmp_path / "d.pdf", [
        {"header": "MIT 6.5940 Course", "title": "Attention"},
    ])
    assert run(tmp_path, pdf)["pages"][0]["title"] == "MIT 6.5940 Course"
    draft = run(tmp_path, pdf, "--ignore", r"MIT 6\.5940")
    assert draft["pages"][0]["title"] == "Attention"


def test_title_ignores_text_below_top_zone(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((40, 80), "Small Title", fontsize=14)
    page.insert_text((40, 400), "Huge body", fontsize=60)
    doc.save(tmp_path / "e.pdf")
    doc.close()
    draft = run(tmp_path, tmp_path / "e.pdf")
    assert draft["pages"][0]["title"] == "Small Title"


def test_yaml_fields_and_sheets(tmp_path):
    body = ["x", "y"]
    pdf = make_pdf(tmp_path / "f.pdf", [
        {"title": "Roadmap", "body": body},
        {"title": "Roadmap", "body": body},
        {"title": "Detail", "box": (40, 150, 680, 450)},
    ])
    sheets = tmp_path / "sheets"
    draft = run(tmp_path, pdf, "--sheet", str(sheets))
    assert draft["pdf"] == "f.pdf"
    assert draft["pdf_pages"] == 3
    assert draft["draft"] is True
    assert list(draft) == ["pdf", "pdf_pages", "draft", "title_groups",
                           "image_groups", "pages"]
    assert set(draft["pages"][0]) == {"p", "title", "title_group",
                                      "image_group", "flags", "type"}
    assert (sheets / "title-0.png").is_file()
    assert (sheets / "image-0.png").is_file()


def test_unicode_title_round_trips(tmp_path):
    pdf = make_pdf(tmp_path / "g.pdf", [{"title": "Plan"}])
    out = tmp_path / "draft.yaml"
    slide_refseq.main(["draft", str(pdf), "--out", str(out)])
    assert "pdf_pages: 1" in out.read_text(encoding="utf-8")


def test_missing_file_exits_2(tmp_path):
    code = slide_refseq.main(["draft", str(tmp_path / "nope.pdf"),
                              "--out", str(tmp_path / "o.yaml")])
    assert code == 2


def test_non_pdf_exits_2(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_text("hello", encoding="utf-8")
    code = slide_refseq.main(["draft", str(fake),
                              "--out", str(tmp_path / "o.yaml")])
    assert code == 2


@pytest.mark.parametrize("titles,expected", [
    (["A", "a", "", "", "B"], [[0, 1]]),
    (["x" * 30 + "1", "x" * 30 + "2"], [[0, 1]]),
])
def test_title_groups_key(titles, expected):
    assert slide_refseq.title_groups(titles) == expected
