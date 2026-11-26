"""Tests for tools/slides.py (outline parsing, checks, deck building)."""

import zipfile
from pathlib import Path

import _limits  # noqa: F401  (4 threads, 8 GB; before pymupdf)
import pymupdf
import pytest
from PIL import Image

import slides

needs_office = pytest.mark.skipif(slides.find_xsl() is None,
                                  reason="需要 Office 的 MML2OMML.XSL")


def _page(root: Path) -> Path:
    """A source page backed by a 3-page PDF, so （p.3） is valid."""
    pdf = root / "raw" / "sources" / "concept.pdf"
    pdf.parent.mkdir(parents=True)
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.save(pdf)
    doc.close()
    page = root / "wiki" / "demo" / "sources" / "concept.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nsources:\n  - raw/sources/concept.pdf\n---\n"
                    "# Concept\n\n## 機制 與方法\n\n```\n# 不是標題\n```\n",
                    encoding="utf-8")
    return page


def _outline(root: Path, body: str) -> Path:
    deck = root / "slides" / "demo"
    deck.mkdir(parents=True)
    assets = root / "wiki" / "assets" / "src"
    assets.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 200), "white").save(assets / "fig.png")
    (assets / "notimg.png").write_text("text", encoding="utf-8")
    Image.new("RGB", (40, 20), "white").save(deck / "local.png")
    drawn = deck / "diagrams"
    drawn.mkdir()
    Image.new("RGB", (40, 20), "white").save(drawn / "flow.png")
    (drawn / "flow.json").write_text("{}", encoding="utf-8")
    Image.new("RGB", (40, 20), "white").save(drawn / "nospec.png")
    moving = deck / "animations"
    moving.mkdir()
    for stem in ("anim", "nosrc"):
        Image.new("RGB", (40, 20), "white").save(moving / f"{stem}.png")
        (moving / f"{stem}.mp4").write_bytes(b"not a real video")
    (moving / "anim.py").write_text("", encoding="utf-8")
    nested = deck / "up" / "wiki" / "assets" / "src"
    nested.mkdir(parents=True)
    Image.new("RGB", (40, 20), "white").save(nested / "fig.png")
    path = deck / "outline.md"
    path.write_text("---\ntitle: Demo\n---\n\n# 說明\n不是投影片\n\n" + body,
                    encoding="utf-8")
    return path


SRC = "[Concept](../../wiki/demo/sources/concept.md#機制%20與方法)（p.3）"

GOOD = f"""## 1. 封面標題
版型: 封面
副標: 副標文字

## 2. 圖頁
版型: 圖片
圖: ../../wiki/assets/src/fig.png
圖說: Figure 1
要點:
- 第一點
- 第二點
  接續上一點
出處:
- {SRC}
備忘稿: 第一行
第二行

## 3. 表格頁
版型: 比較表
表格:
| 做法 | 特點 |
|---|---|
| A | 甲 |
| B | 乙 |
出處:
- {SRC}

## 4. 條列頁
版型: 條列
要點:
- 一
出處:
- {SRC}
"""


def test_parse_outline(tmp_path: Path) -> None:
    _page(tmp_path)
    outline = slides.parse_outline(_outline(tmp_path, GOOD))
    assert outline.title == "Demo"
    assert [s.title for s in outline.slides] == [
        "封面標題", "圖頁", "表格頁", "條列頁"]
    image = outline.slides[1]
    assert image.items("要點") == ["第一點", "第二點 接續上一點"]
    assert image.text("備忘稿") == "第一行\n第二行"
    # Frontmatter (3 lines) and the preamble (4 lines) come before GOOD.
    assert image.line == GOOD.splitlines().index("## 2. 圖頁") + 8


def test_table_rows_drops_rule() -> None:
    rows = slides.table_rows("| a | b |\n|:--|--:|\n| 1 | 2 |")
    assert rows == [["a", "b"], ["1", "2"]]


def test_table_rows_keeps_dash_data_row() -> None:
    rows = slides.table_rows("| a | b |\n|---|---|\n| - | - |\n| x | y |")
    assert rows == [["a", "b"], ["-", "-"], ["x", "y"]]


def test_title_number_only_strips_list_prefix(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## 3.5 版本\n版型: 條列\n要點:\n- x\n出處:\n- {SRC}\n"
    outline = slides.parse_outline(_outline(tmp_path, body))
    assert outline.slides[0].title == "3.5 版本"


@pytest.mark.parametrize("body", [
    f"## A\n版型: 條列\n要點:\n- x\n白話: 舊欄位\n出處:\n- {SRC}\n",
    f"## A\n版型: 條列\n要點:\n- x\n出處:\n- {SRC}\n備忘稿: 第一行\n重点: 簡體\n",
])
def test_unknown_field_reported(tmp_path: Path, body: str) -> None:
    _page(tmp_path)
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    assert any("不是欄位名稱" in p for p in problems), problems


def test_notes_may_contain_colon_lines(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 條列\n要點:\n- x\n出處:\n- {SRC}\n備忘稿: 一\n推論：二\n"
    outline = slides.parse_outline(_outline(tmp_path, body))
    assert slides.check(outline) == []
    assert outline.slides[0].text("備忘稿") == "一\n推論：二"


@pytest.mark.parametrize("body, expected", [
    (f"## 1. $\\foo$ 標題\n版型: 條列\n要點:\n- x\n出處:\n- {SRC}\n", "不認得"),
    (f"## A\n版型: 公式\n公式: x\n符號:\n- g_i: 說明：附註\n出處:\n- {SRC}\n",
     None),
    (f"## A\n版型: 公式\n公式: x\n符號:\n- f_i 沒冒號\n出處:\n- {SRC}\n",
     "沒有冒號"),
    (f"## A\n版型: 公式\n公式: $x^2$\n出處:\n- {SRC}\n", "不加 `$`"),
    ("## 這是一個非常非常長而且一定會在投影片上換行的標題文字喔\n版型: 條列\n"
     f"要點:\n- x\n出處:\n- {SRC}\n", "標題太長"),
    ("## A\n版型: 條列\n要點:\n" + "- 一二三四五六七八九十\n" * 12
     + f"出處:\n- {SRC}\n", "內容太多"),
    ("## A\n版型: 公式\n公式: x\n符號:\n" + "- x：說明\n" * 9
     + f"出處:\n- {SRC}\n", "內容太多"),
    ("## A\n版式: 條列\n要點:\n- x\n" + f"出處:\n- {SRC}\n", "「版式」不是欄位"),
    (f"## A\n版型: 圖片\n圖: diagrams/flow.png\n出處:\n- {SRC}\n", None),
    (f"## A\n版型: 圖片\n圖: diagrams/nospec.png\n出處:\n- {SRC}\n",
     "同名的 .json"),
])
def test_check_more(tmp_path: Path, body: str, expected: str | None) -> None:
    _page(tmp_path)
    outline = slides.parse_outline(_outline(tmp_path, body))
    problems = slides.check(outline)
    if expected is None:
        assert problems == []
        assert slides.split_symbol("g_i: 說明：附註") == ("g_i", "說明：附註")
    else:
        assert any(expected in p for p in problems), problems


def test_tab_indent_is_sub_level(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 條列\n要點:\n- a\n\t- b\n出處:\n- {SRC}\n"
    outline = slides.parse_outline(_outline(tmp_path, body))
    assert outline.slides[0].items("要點") == ["a", "  b"]


@pytest.mark.parametrize("text, spans", [
    ("聲部縮成 $1/m$ 大小", ["1/m"]),
    ("係數 $10^{-2}$", ["10^{-2}"]),
    ("價格 $100 與 $5", []),
    ("乘 $N$ 與 $x_i$", ["N", "x_i"]),
])
def test_inline_math_spans(text: str, spans: list[str]) -> None:
    assert slides.INLINE_MATH_RE.findall(text) == spans


def test_price_dollar_is_plain_text(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 條列\n要點:\n- 價格 $100 的例子\n出處:\n- {SRC}\n"
    assert slides.check(slides.parse_outline(_outline(tmp_path, body))) == []


def test_source_label() -> None:
    assert slides.source_label(SRC) == "Concept（p.3）"


def test_good_outline_passes(tmp_path: Path) -> None:
    _page(tmp_path)
    assert slides.check(slides.parse_outline(_outline(tmp_path, GOOD))) == []


@pytest.mark.parametrize("body, expected", [
    ("## A\n版型: 投影片\n", "版型「投影片」"),
    ("## A\n版型: 條列\n要點:\n- x\n", "缺少出處"),
    (f"## A\n版型: 圖片\n圖: nope.png\n出處:\n- {SRC}\n", "找不到圖檔"),
    ("## A\n版型: 條列\n要點:\n- x\n出處:\n"
     "- [C](../../wiki/demo/sources/concept.md#不是標題)\n", "不是 concept.md 的標題"),
    ("## A\n版型: 條列\n要點:\n- x\n出處:\n- [C](../../wiki/none.md)\n",
     "檔案不存在"),
    (f"## A\n版型: 條列\n要點:\n- 權重 10^{{-2}}\n出處:\n- {SRC}\n",
     "寫在 `$…$` 外面"),
    (f"## A\n版型: 條列\n要點:\n- x\n出處:\n- {SRC}\n備忘稿: 看 $x$\n",
     "備忘稿"),
    (f"## A\n版型: 條列\n要點:\n- 看 $\\foo$\n出處:\n- {SRC}\n", "不認得"),
    (f"## A\n版型: 比較表\n表格:\n| a | b |\n|---|---|\n| 1 |\n出處:\n- {SRC}\n",
     "欄數不同"),
    (f"## A\n版型: 公式\n公式: \\foo{{x}}\n出處:\n- {SRC}\n", "不認得的指令"),
    (f"## A\n版型: 公式\n公式: \\frac{{a}}{{\n出處:\n- {SRC}\n", "無法轉換"),
    ("## A\n版型: 條列\n要點:\n- x\n出處:\n- 只有文字\n", "沒有連結"),
    (f"## A\n版型: 圖片\n圖: ../../wiki/assets/src/notimg.png\n出處:\n- {SRC}\n",
     "無法開啟"),
    (f"## A\n版型: 圖片\n圖: local.png\n出處:\n- {SRC}\n", "圖檔要放在"),
    (f"## A\n版型: 圖片\n圖: up/wiki/assets/src/fig.png\n出處:\n- {SRC}\n",
     "圖檔要放在"),
    (f"## A\n版型: 公式\n公式: a & b\n出處:\n- {SRC}\n", "無法轉換"),
    (f"## A\n版型: 公式\n公式: \\text{{a<b}}\n出處:\n- {SRC}\n", "無法轉換"),
    (f"## A\n版型: 公式\n公式: a}}\n出處:\n- {SRC}\n", "不成對"),
    (f"## A\n版型: 公式\n公式: \\color{{red}}{{x}}\n出處:\n- {SRC}\n",
     "color"),
])
def test_check_reports(tmp_path: Path, body: str, expected: str) -> None:
    _page(tmp_path)
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    assert any(expected in p for p in problems), problems


def test_unknown_command_rejected_before_xsl() -> None:
    with pytest.raises(slides.FormulaError, match="不認得"):
        slides.to_omml(r"\notacommand + x")


@pytest.mark.parametrize("latex", [
    r"\begin{cases}a & b\\ c & d\end{cases}",
    r"\begin{cases}a & b\\0 & d\end{cases}",
])
def test_line_break_rejected(latex: str) -> None:
    with pytest.raises(slides.FormulaError, match="換行"):
        slides.to_omml(latex)


def _xml(pptx: Path) -> str:
    with zipfile.ZipFile(pptx) as z:
        return "".join(z.read(n).decode("utf-8") for n in z.namelist()
                       if n.endswith(".xml"))


def test_build_sets_both_fonts(tmp_path: Path) -> None:
    _page(tmp_path)
    outline = slides.parse_outline(_outline(tmp_path, GOOD))
    out = tmp_path / "demo.pptx"
    slides.DeckBuilder(outline).build(out)
    xml = _xml(out)
    assert '<a:latin typeface="Helvetica"/>' in xml
    assert '<a:ea typeface="微軟正黑體"/>' in xml
    with zipfile.ZipFile(out) as z:
        notes = [n for n in z.namelist() if "notesSlide" in n
                 and n.endswith(".xml")]
        table = z.read("ppt/slides/slide3.xml").decode("utf-8")
        assert "微軟正黑體" in z.read(notes[0]).decode("utf-8")
    assert '<a:ea typeface="微軟正黑體"/>' in table
    assert "出處：Concept（p.3）" in xml


@needs_office
def test_sum_body_is_filled() -> None:
    from lxml import etree

    omml = slides.to_omml(r"L=\sum_{i=1}^{N} f_i P_i + c")
    m = "{%s}" % slides.NS["m"]
    body = omml.find(f".//{m}nary/{m}e")
    text = "".join(body.itertext())
    assert text == "fiPi"
    rest = etree.tostring(omml, encoding="unicode")
    assert rest.index("+") > rest.index("</m:nary>")


@needs_office
@pytest.mark.parametrize("latex, body", [
    (r"\sum_{i} (x_i) = 0", "(xi)"),
    (r"\sum_i f_i c = x", "fic"),
    (r"L=\sum_x P(x,i) = y", "P(x,i)"),
    (r"\sum_i g(a-b) = c", "g(a−b)"),
    (r"\sum_i (a_i)^2 = b", "(ai)2"),
    (r"\sum_i (x_i-\mu)^2 = V", "(xi−μ)2"),
])
def test_sum_body_stops_at_merged_relation(latex: str, body: str) -> None:
    omml = slides.to_omml(latex)
    m = "{%s}" % slides.NS["m"]
    inside = "".join(omml.find(f".//{m}nary/{m}e").itertext())
    assert inside.replace(" ", "") == body
    assert "=" in "".join(omml.itertext()).split(inside)[-1]


@needs_office
def test_outer_closing_bracket_ends_sum_body() -> None:
    m = "{%s}" % slides.NS["m"]
    omml = slides.to_omml(r"(\sum_i a_i) + b")
    assert "".join(omml.find(f".//{m}nary/{m}e").itertext()) == "ai"


@needs_office
def test_astral_character_gets_own_run() -> None:
    m = "{%s}" % slides.NS["m"]
    omml = slides.to_omml(r"y=\sum_{i\in\mathcal{T}} p_i")
    texts = [t.text for t in omml.find(f".//{m}nary/{m}sub").iter(m + "t")]
    assert texts == ["i∈", "𝒯"]


@needs_office
def test_overline_becomes_top_bar() -> None:
    from lxml import etree

    xml = etree.tostring(slides.to_omml(r"e_i=\overline{c_i}-c_i"),
                         encoding="unicode")
    assert "<m:acc>" not in xml
    assert 'm:val="top"' in xml


@needs_office
def test_build_formula_slide(tmp_path: Path) -> None:
    _page(tmp_path)
    body = (f"## F\n版型: 公式\n公式: \\alpha N\\sum_i f_i P_i\n符號:\n"
            f"- f_i：比例\n- 1：常數\n重點: 越平均 $L$ 越小\n要點:\n- 第一點\n"
            f"  - 第二層 $x_i$\n出處:\n- {SRC}\n")
    outline = slides.parse_outline(_outline(tmp_path, body))
    assert slides.check(outline) == []
    assert outline.slides[0].items("要點") == ["第一點", "  第二層 $x_i$"]
    out = tmp_path / "f.pptx"
    slides.DeckBuilder(outline).build(out)
    xml = _xml(out)
    # Display formula, two symbol cells, takeaway and sub-bullet math.
    assert xml.count("<a14:m>") == 5
    assert "$1$" not in xml
    assert 'marL="742950"' in xml  # second-level bullet
    assert 'Requires="a14"' in xml
    assert "Cambria Math" in xml


def _formula_slide(symbols: list[str], points: list[str]) -> slides.Slide:
    return slides.Slide("T", 1, {"版型": "公式", "重點": "x", "公式": "y=x",
                                 "圖": "f.png", "要點": points,
                                 "符號": symbols})


def test_long_symbol_formula_overflows() -> None:
    # A long formula in the narrow symbol column wraps to many lines; this
    # slide ran into the source line when rendered.
    long = (r"P(t)_i=(t\cdot W_{s})_i+\mathrm{EnvelopeNormal}()\cdot"
            r"\mathrm{VibratoDepth}((t\cdot W_{tremolo})_i)"
            r"+\mathrm{Swell}(t)：音高")
    slide = _formula_slide([long, "k：聲部數"], ["a", "b", "  c"])
    slide.fields.pop("圖")
    assert slides._check_fit(slide, "t")


def test_fraction_symbol_fits() -> None:
    # Rendered fine with about 0.6 inch to spare.
    frac = r"p_i(x)=\frac{e^{h(x)_i}}{\sum_{j}^{N} e^{h(x)_j}}：音量比例"
    points = ["前段：只彈被選的聲部", "後段：聲部只收到分給它的音符",
              "指揮：沒乘 $p_i(x)$ 就聽不到"]
    slide = _formula_slide([frac, r"\mathcal{T}：選中的聲部集合"], points)
    assert slides._check_fit(slide, "t") == []


@pytest.mark.parametrize("latex, width", [
    (r"x^\alpha", 1.6),
    (r"W_\theta", 1.6),
    (r"x^{2}", 1.6),
])
def test_script_command_width(latex: str, width: float) -> None:
    assert slides._math_chars(latex) == pytest.approx(width)


def test_deep_nesting_does_not_crash() -> None:
    assert slides._units("$" + "x^{" * 5000 + "$") > 0


def test_parse_flow_columns_and_edges() -> None:
    flow = slides.parse_flow([
        "A[Chorale] -> C[Cantata]!",
        "B[Fugue] -(bias)-> C ~(distill)~> D[Mass]",
        "M[Math] -> D",
    ])
    assert [sorted(c) for c in flow.columns] == [["A", "B", "M"],
                                                ["C", "~3.1"], ["D"]]
    assert flow.nodes["C"].strong and flow.nodes["C"].label == "Cantata"
    assert flow.nodes["D"].label == "Mass"
    edges = {(e.src, e.dst): e for e in flow.edges}
    assert edges["B", "C"].label == "bias" and not edges["B", "C"].dashed
    assert edges["C", "D"].dashed and edges["C", "D"].label == "distill"
    assert flow.columns[0][-1] == "M"  # stays near its target D, below C's


def test_parse_flow_longest_path_column() -> None:
    flow = slides.parse_flow(["A -> B -> C", "A -> C"])
    assert flow.columns == [["A"], ["B", "~2.1"], ["C"]]
    assert flow.edges[2].via == ["~2.1"]  # A -> C bends around B


@pytest.mark.parametrize("items, expected", [
    (["A -> B", "B -> A"], "循環"),
    (["A -> A"], "指回自己"),
    (["A[x] -> B", "A[y] -> C"], "兩種"),
    (["A b -> C"], "看不懂節點"),
])
def test_parse_flow_errors(items: list[str], expected: str) -> None:
    with pytest.raises(slides.FlowError, match=expected):
        slides.parse_flow(items)


FLOW = f"""## 5. 流程頁
版型: 流程圖
重點: 兩條線匯合
流程:
- A[Chorale<br>四聲部] -> C[Cantata]!
- B[$c_t^{{KV}}$] -(壓縮)-> C ~> D[Mass]
出處:
- {SRC}
"""


def test_flow_slide_passes_and_builds(tmp_path: Path) -> None:
    _page(tmp_path)
    outline = slides.parse_outline(_outline(tmp_path, GOOD + "\n" + FLOW))
    assert slides.check(outline) == []
    out = tmp_path / "demo.pptx"
    slides.DeckBuilder(outline).build(out)
    with zipfile.ZipFile(out) as z:
        xml = z.read("ppt/slides/slide5.xml").decode("utf-8")
    assert xml.count('prst="roundRect"') == 4
    assert xml.count('<a:tailEnd type="triangle"') == 3
    assert 'prstDash val="dash"' in xml
    assert "四聲部" in xml and "壓縮" in xml


@pytest.mark.parametrize("flow, expected", [
    ("- A -> B -> C -> D -> E -> F -> G -> H", "最多 7 欄"),
    ("- R -> A\n- R -> B\n- R -> C\n- R -> D\n- R -> E\n- R -> F\n- R -> G",
     "同一欄最多"),
    ("- A[" + "很長的節點文字" * 40 + "] -> B", "文字太長"),
    ("- A -(" + "很長的箭頭文字" * 4 + ")-> B -> C -> D -> E", "最多兩行"),
    (r"- A[$\foo$] -> B", "不認得"),
    ("- A[權重 10^{-2}] -> B", "寫在 `$…$` 外面"),
    ("- A -> B\n- B -> A", "循環"),
])
def test_flow_check_reports(tmp_path: Path, flow: str, expected: str) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 流程圖\n流程:\n{flow}\n出處:\n- {SRC}\n"
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    assert any(expected in p for p in problems), problems


def test_parse_flow_label_spaces_and_parens() -> None:
    flow = slides.parse_flow(["A[ x ] -> B", "A[ x ] -(f(x))-> C"])
    assert flow.nodes["A"].label == "x"
    assert flow.edges[1].label == "f(x)"


def _term_outline(tmp_path: Path, terms: str) -> slides.Outline:
    path = tmp_path / "outline.md"
    path.write_text(
        f"---\ntitle: T\n{terms}---\n\n"
        "## 1. 開場\n版型: 條列\n要點:\n- 先提到 Foo buffer 與 small 模型\n"
        "備忘稿: 這裡提到 MLA 不算\n\n"
        "## 2. 名詞\n版型: 條列\n要點:\n- Foo buffer：存過去的 key 與 value\n"
        "- 注意力是加權平均\n\n"
        "## 3. 機制\n版型: 條列\n重點: MLA 壓縮 foo buffer\n要點:\n- 用到 SM\n",
        encoding="utf-8")
    return slides.parse_outline(path)


def test_terms_used_before_definition(tmp_path):
    outline = _term_outline(
        tmp_path, "名詞:\n  2: [Foo buffer, 注意力]\n  3: [MLA, SM]\n")
    problems = slides.term_problems(outline)
    assert len(problems) == 1
    assert "Foo buffer" in problems[0] and "第 1 頁" in problems[0]


def test_terms_whole_word_and_notes_ignored(tmp_path):
    # "SM" must not match "small"; 備忘稿 on page 1 does not count for MLA.
    outline = _term_outline(tmp_path, "名詞:\n  3: [MLA, SM]\n")
    assert slides.term_problems(outline) == []


def test_terms_bad_spec(tmp_path):
    outline = _term_outline(
        tmp_path, "名詞:\n  9: [MLA]\n  2: [不在這頁]\n  x: [y]\n")
    problems = slides.term_problems(outline)
    assert any("第 9 頁" in p for p in problems)
    assert any("不在這頁" in p for p in problems)
    assert any("x" in p for p in problems)


def test_terms_absent_is_fine(tmp_path):
    assert slides.term_problems(_term_outline(tmp_path, "")) == []


def test_terms_bool_key_and_empty_name(tmp_path):
    outline = _term_outline(tmp_path, "名詞:\n  true: [MLA]\n  2: ['']\n")
    problems = slides.term_problems(outline)
    assert len(problems) == 2
    assert all("要寫成" in p for p in problems)


VIDEO = ("## A\n版型: {layout}\n圖: {image}\n影片: {video}\n出處:\n- "
         + SRC + "\n")


@pytest.mark.parametrize("layout, image, video, expected", [
    ("圖片", "animations/anim.png", "animations/anim.mp4", None),
    ("條列", "animations/anim.png", "animations/anim.mp4", "只能用在"),
    ("圖片", "diagrams/flow.png", "animations/anim.mp4", "同名的封面"),
    ("圖片", "animations/anim.png", "animations/gone.mp4", "找不到影片"),
    ("圖片", "animations/anim.png", "anim.mp4", "要放在 animations/"),
    ("圖片", "animations/anim.png", "animations/anim.py", "要是 .mp4"),
    ("圖片", "animations/nosrc.png", "animations/nosrc.mp4", "同名的 .py"),
])
def test_video_checks(tmp_path: Path, layout: str, image: str, video: str,
                      expected: str | None) -> None:
    _page(tmp_path)
    body = VIDEO.format(layout=layout, image=image, video=video)
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


def test_poster_needs_source(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 圖片\n圖: animations/nosrc.png\n出處:\n- {SRC}\n"
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    assert any("同名的 .py 原始檔" in p for p in problems), problems


def test_render_rejects_outline(tmp_path: Path) -> None:
    outline = tmp_path / "outline.md"
    outline.write_text("# x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"\.pptx"):
        slides.render(outline, tmp_path / "render")


def test_build_embeds_video(tmp_path: Path) -> None:
    _page(tmp_path)
    body = VIDEO.format(layout="圖片", image="animations/anim.png",
                        video="animations/anim.mp4")
    out = tmp_path / "v.pptx"
    outline = slides.parse_outline(_outline(tmp_path, body))
    slides.DeckBuilder(outline).build(out)
    with zipfile.ZipFile(out) as z:
        media = [n for n in z.namelist() if n.startswith("ppt/media/")]
    assert any(n.endswith(".mp4") for n in media), media
    assert any(n.endswith(".png") for n in media), media


FOCUS = ("## A\n版型: {layout}\n圖: {image}\n{extra}焦點: {focus}\n出處:\n- "
         + SRC + "\n")


@pytest.mark.parametrize("image, extra, focus, expected", [
    ("../../wiki/assets/src/fig.png", "", "0.1, 0.2, 0.6, 0.9", None),
    ("../../wiki/assets/src/fig.png", "", "0.1，0.2，0.6，0.9", None),
    ("../../wiki/assets/src/fig.png", "", "0, 0, 1, 1", None),
    ("../../wiki/assets/src/fig.png", "", "0.6, 0.2, 0.1, 0.9", "x0 < x1"),
    ("../../wiki/assets/src/fig.png", "", "0.1, 0.2, 1.2, 0.9", "x0 < x1"),
    ("../../wiki/assets/src/fig.png", "", "0.1, 0.2, 0.6", "x0 < x1"),
    ("../../wiki/assets/src/fig.png", "", "a, b, c, d", "x0 < x1"),
    ("animations/anim.png", "影片: animations/anim.mp4\n", "0, 0, 1, 1",
     "只能用在"),
])
def test_focus_checks(tmp_path: Path, image: str, extra: str, focus: str,
                      expected: str | None) -> None:
    _page(tmp_path)
    body = FOCUS.format(layout="圖片", image=image, extra=extra, focus=focus)
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


def test_focus_needs_image(tmp_path: Path) -> None:
    _page(tmp_path)
    body = f"## A\n版型: 條列\n要點:\n- x\n焦點: 0, 0, 1, 1\n出處:\n- {SRC}\n"
    problems = slides.check(slides.parse_outline(_outline(tmp_path, body)))
    assert any("只能用在" in p for p in problems), problems


@pytest.mark.parametrize("focus, covers", [
    ("0.25, 0.25, 0.75, 0.75", 4),
    ("0, 0, 0.5, 1", 1),
])
def test_build_focus_dims_outside(tmp_path: Path, focus: str,
                                  covers: int) -> None:
    from pptx import Presentation

    _page(tmp_path)
    body = FOCUS.format(layout="圖片", image="../../wiki/assets/src/fig.png",
                        extra="", focus=focus)
    out = tmp_path / "f.pptx"
    slides.DeckBuilder(slides.parse_outline(_outline(tmp_path, body))).build(
        out)
    page = Presentation(str(out)).slides[0]
    pic = next(s for s in page.shapes if s.shape_type == 13)
    rects = [s for s in page.shapes if s.shape_type == 1
             and pic.left <= s.left and s.left + s.width <= pic.left
             + pic.width + 1 and pic.top <= s.top
             and s.top + s.height <= pic.top + pic.height + 1]
    alpha = [r for r in rects if b"alpha" in r._element.xml.encode()]
    assert len(alpha) == covers
    outlined = [r for r in rects if r not in alpha]
    assert len(outlined) == 1
    assert outlined[0].width == pytest.approx(
        pic.width * (slides.parse_focus(focus)[2]
                     - slides.parse_focus(focus)[0]), abs=2)


ROADMAP = f"""## 1. 封面標題
版型: 封面
副標: 副標文字

## 2. 今天的大綱
版型: 條列
要點:
- Efficient Attention
- AlphaCache
- BetaCache
反白: Efficient Attention, AlphaCache
出處:
- {SRC}

## 3. 沒有反白
版型: 條列
要點:
- 第一點
- 第二點
出處:
- {SRC}
"""


def test_highlights_round_trip(tmp_path: Path) -> None:
    _page(tmp_path)
    path = _outline(tmp_path, ROADMAP)
    outline = slides.parse_outline(path)
    out = tmp_path / "demo.pptx"
    slides.DeckBuilder(outline).build(out)
    read = slides.read_highlights(out)
    assert read[1][0] == ["Efficient Attention", "AlphaCache"]
    assert read[1][1] == ["Efficient Attention", "AlphaCache", "BetaCache"]
    assert read[2][0] == []
    assert slides.highlight_problems(outline, out) == []


def test_highlights_reports_mismatch(tmp_path: Path) -> None:
    _page(tmp_path)
    good = slides.parse_outline(_outline(tmp_path, ROADMAP))
    out = tmp_path / "demo.pptx"
    slides.DeckBuilder(good).build(out)
    changed = (tmp_path / "slides" / "demo" / "outline.md")
    changed.write_text(changed.read_text(encoding="utf-8").replace(
        "反白: Efficient Attention, AlphaCache", "反白: BetaCache"),
        encoding="utf-8")
    problems = slides.highlight_problems(slides.parse_outline(changed), out)
    assert problems and "betacache" in " ".join(problems)
