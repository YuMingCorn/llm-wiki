import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pdf_tool  # noqa: E402


def make_pdf():
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for num, title in [("3", "Pre-training"), ("3.1", "Pre-training Data"), ("A", "Appendix")]:
        page.insert_text((72, y), num, fontname="hebo", fontsize=12)
        page.insert_text((100, y), title, fontname="hebo", fontsize=12)
        page.insert_text((72, y + 20), "Body text " * 8, fontname="helv", fontsize=10)
        y += 60
    page.insert_text((72, y), "98.0", fontname="hebo", fontsize=10)
    page.insert_text((120, y), "97.2", fontname="hebo", fontsize=10)
    return doc


def test_split_number_and_title_become_heading():
    heads = [(h.level, h.title) for h in pdf_tool.detect_headings(make_pdf())]
    assert (1, "3 Pre-training") in heads
    assert (2, "3.1 Pre-training Data") in heads
    assert (1, "A Appendix") in heads
    assert not any(t.startswith("98.0") for _, t in heads), "table numbers are not headings"


BODY = "This paragraph is ordinary body text that runs across the full column width here."


def figure_page():
    """上方一段內文，中間一張向量圖（含座標軸文字），下方圖說，再一段內文。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Introduction", fontname="hebo", fontsize=10)
    for i in range(4):
        page.insert_text((72, 80 + 14 * i), BODY, fontname="helv", fontsize=10)
    page.draw_rect(pymupdf.Rect(150, 160, 450, 330), color=(0, 0, 0))
    page.draw_line((160, 320), (440, 170), color=(1, 0, 0))
    page.insert_text((280, 345), "Training step", fontname="helv", fontsize=8)
    page.insert_text((72, 368), "Figure 1: A synthetic plot.", fontname="helv", fontsize=10)
    page.insert_text((72, 400), "Figure 2 shows something else entirely in running text.", fontname="helv", fontsize=10)
    for i in range(3):
        page.insert_text((72, 420 + 14 * i), BODY, fontname="helv", fontsize=10)
    return doc


def table_page():
    """圖說在上，下方是只有水平線的表格（列與列之間有分組間距）。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Table 1: Results.", fontname="helv", fontsize=10)
    page.draw_line((72, 90), (500, 90))
    y = 104
    for group in range(2):
        for row in range(3):
            page.insert_text((80, y), f"Model {group}{row}", fontname="helv", fontsize=9)
            page.insert_text((300, y), "12.3", fontname="helv", fontsize=9)
            y += 13
        y += 9  # 分組間距
    y -= 9 + 13 - 5  # 底線緊貼最後一列
    page.draw_line((72, y), (500, y))
    page.insert_text((72, y + 40), BODY, fontname="helv", fontsize=10)
    return doc, y


def test_caption_without_bold_is_found_but_running_text_is_not():
    caps = [(c["kind"], c["id"]) for c in pdf_tool.find_captions(figure_page())]
    assert caps == [("Figure", "1")]


def test_figure_crop_excludes_body_text():
    doc = figure_page()
    cap = pdf_tool.find_captions(doc)[0]
    r = pdf_tool.crop_rect(doc[0], cap, pdf_tool.body_size(doc))
    assert r is not None
    assert r.y0 > 120, "body paragraph above the figure must not be included"
    assert r.y1 < 390, "running text below the caption must not be included"
    assert r.contains(pymupdf.Rect(150, 160, 450, 330)), "whole plot area"
    assert r.y1 >= 368, "caption is included"


def test_table_crop_grows_across_row_groups():
    doc, bottom = table_page()
    cap = pdf_tool.find_captions(doc)[0]
    r = pdf_tool.crop_rect(doc[0], cap, pdf_tool.body_size(doc))
    assert r is not None and r.y1 >= bottom, "all row groups down to the bottom rule"
    assert r.y1 < bottom + 30, "body text below the table is excluded"


def test_merge_keeps_larger_rect_when_it_comes_later():
    small = pymupdf.Rect(10, 10, 20, 20)
    big = pymupdf.Rect(0, 0, 100, 100)
    assert pdf_tool._merge([small, big], 5) == [big]


def test_vertical_caption_crops_sideways_and_rotates():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 400, 600), color=(0, 0, 0))
    page.insert_text((430, 600), "Figure 3. A rotated chart.", fontname="helv", fontsize=10, rotate=90)
    cap = pdf_tool.find_captions(doc)[0]
    assert cap["rotate"] != 0
    r = pdf_tool.crop_rect(page, cap, 10)
    assert r is not None and r.contains(pymupdf.Rect(100, 100, 400, 600))


def test_split_marks_figure_position(tmp_path):
    pdf = tmp_path / "fig.pdf"
    figure_page().save(pdf)

    class A:
        pass
    args = A()
    args.pdf, args.out, args.level = str(pdf), str(tmp_path / "out"), 1
    pdf_tool.cmd_split(args)
    text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "out").glob("*.md"))
    assert text.count("<!-- figure: Figure 1 p.1 -> figures/figure-1.png -->") == 1


def test_zero_thickness_line_inside_figure_does_not_hang():
    doc = figure_page()
    doc[0].draw_line((200, 250), (400, 250))  # 圖內的水平格線
    cap = pdf_tool.find_captions(doc)[0]
    assert pdf_tool.crop_rect(doc[0], cap, pdf_tool.body_size(doc)) is not None


def test_pdf_without_headings_or_text_does_not_crash(tmp_path):
    import argparse
    plain = pymupdf.open()
    plain.new_page().insert_text((72, 72), "plain text without any heading", fontname="helv")
    plain.save(tmp_path / "plain.pdf")
    pdf_tool.cmd_split(argparse.Namespace(pdf=str(tmp_path / "plain.pdf"), out=str(tmp_path / "s"), level=2))
    assert "plain text" in (tmp_path / "s" / "00-plain.md").read_text(encoding="utf-8")
    blank = pymupdf.open()
    blank.new_page()
    blank.save(tmp_path / "blank.pdf")
    pdf_tool.cmd_figures(argparse.Namespace(pdf=str(tmp_path / "blank.pdf"), out=str(tmp_path / "f"), dpi=72))
    assert (tmp_path / "f" / "manifest.json").read_text(encoding="utf-8").strip() == "[]"


def test_shot_rejects_page_zero_and_out_of_range(tmp_path):
    import argparse

    import pytest
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "one.pdf")
    for pages in ("0", "2"):
        with pytest.raises(SystemExit):
            pdf_tool.cmd_shot(argparse.Namespace(pdf=str(tmp_path / "one.pdf"), pages=pages, clip=None,
                                                 dpi=72, out=str(tmp_path / "shots")))
