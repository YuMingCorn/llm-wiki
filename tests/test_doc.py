"""Tests for the docsrc package and the doc.py CLI."""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import doc  # noqa: E402
import docsrc  # noqa: E402


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    """A three-page PDF whose pages each hold two known lines."""
    pdf = pymupdf.open()
    for n in range(1, 4):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Page {n} heading", fontsize=11)
        page.insert_text((72, 100), f"warm start value {n * 10}", fontsize=11)
    path = tmp_path / "paper.pdf"
    pdf.save(path)
    return path


@pytest.fixture
def md_path(tmp_path: Path) -> Path:
    """A small Markdown file."""
    path = tmp_path / "note.md"
    path.write_text(
        "# Title\nalpha line\nSecond Theme here\nomega line\n",
        encoding="utf-8",
    )
    return path


def test_open_document_picks_reader_by_extension(
    pdf_path: Path, md_path: Path
) -> None:
    assert isinstance(docsrc.open_document(pdf_path), docsrc.PdfDocument)
    assert isinstance(docsrc.open_document(md_path), docsrc.TextDocument)


def test_open_document_rejects_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "x.xyz"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(docsrc.UnsupportedFormatError):
        docsrc.open_document(path)


def test_register_adds_a_new_format(tmp_path: Path) -> None:
    @docsrc.register(".rev")
    class ReversedDocument(docsrc.TextDocument):
        def lines(self) -> list[docsrc.Line]:
            return [
                docsrc.Line(ln.label, ln.page, ln.text[::-1])
                for ln in super().lines()
            ]

    path = tmp_path / "x.rev"
    path.write_text("abc\n", encoding="utf-8")
    try:
        assert docsrc.open_document(path).lines()[0].text == "cba"
    finally:
        docsrc.unregister(".rev")


def test_pdf_lines_carry_page_labels(pdf_path: Path) -> None:
    lines = docsrc.open_document(pdf_path).lines()
    assert ("p.2", 2, "warm start value 20") in [
        (ln.label, ln.page, ln.text) for ln in lines
    ]


def test_pdf_two_columns_stay_separate(tmp_path: Path) -> None:
    """Lines at the same height in two columns must not be merged."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    for col, x in (("left", 72), ("right", 320)):
        for row in range(3):
            page.insert_text((x, 72 + row * 14), f"{col} column line {row}",
                             fontsize=10)
    path = tmp_path / "two_col.pdf"
    pdf.save(path)
    texts = [ln.text for ln in docsrc.open_document(path).lines()]
    assert texts == [f"left column line {r}" for r in range(3)] + [
        f"right column line {r}" for r in range(3)
    ]


def test_span_text_decodes_elsevier_symbol_font() -> None:
    """Symbol-font codes become real symbols; other fonts are untouched."""
    span = {"font": "AdvMacMthSyN", "text": "ð\x04Þ ¼ \x03 þ½\x02a0"}
    assert docsrc.span_text(span) == "(−) = × +[]a′"
    assert docsrc.span_text({"font": "AdvMPi-One", "text": "2"}) == "−"
    assert docsrc.span_text({"font": "Times-Roman", "text": "¼ 2"}) == "¼ 2"


def test_span_text_decodes_ligature_fonts() -> None:
    """Advent and re-embedded TeX fonts get their f-ligatures back."""
    span = {"font": "AdvT019", "text": "¢rms di¡er £ow coe⁄cient"}
    assert docsrc.span_text(span) == "firms differ flow coefficient"
    span = {"font": "TTdcr10", "text": "bene…ts di¤er ‡exible"}
    assert docsrc.span_text(span) == "benefits differ flexible"
    assert docsrc.span_text({"font": "cmr10", "text": "…"}) == "fi"
    assert docsrc.span_text({"font": "Helvetica", "text": "… ¢"}) == "… ¢"
    assert docsrc.span_text({"font": "cmsy10", "text": "‡"}) == "‡"
    span = {"font": "AdvP4C4E74", "text": "logðexpðxÞ þ 1Þ ¼ N½0"}
    assert docsrc.span_text(span) == "log(exp(x) + 1) = N[0"


def test_text_lines_carry_line_numbers(md_path: Path) -> None:
    lines = docsrc.open_document(md_path).lines()
    assert (lines[2].label, lines[2].text) == ("L3", "Second Theme here")
    assert lines[2].page is None


def test_parse_ranges() -> None:
    assert docsrc.parse_ranges("2-3,5") == {2, 3, 5}
    with pytest.raises(ValueError):
        docsrc.parse_ranges("3-1")


def test_grep_finds_hits_across_formats(
    pdf_path: Path, md_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = doc.main(["grep", "-i", "warm start|second theme", str(pdf_path),
                     str(md_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert f"{pdf_path.as_posix()}:p.1: warm start value 10" in out
    assert f"{md_path.as_posix()}:L3: Second Theme here" in out


def test_grep_page_filter_and_context(
    pdf_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc.main(["grep", "value", "--pages", "3", "-C", "1", str(pdf_path)])
    out = capsys.readouterr().out
    assert "value 30" in out
    assert "value 10" not in out
    assert "p.3- Page 3 heading" in out


def test_grep_caps_hits_and_width(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "many.txt"
    path.write_text("".join(f"hit {'x' * 300}\n" for _ in range(5)),
                    encoding="utf-8")
    doc.main(["grep", "hit", "--max", "2", "--width", "20", str(path)])
    out = capsys.readouterr().out.splitlines()
    assert len([ln for ln in out if ":L" in ln]) == 2
    assert all(len(ln.split(": ", 1)[1]) <= 21 for ln in out if ":L" in ln)
    assert "另有 3 筆" in out[-1]


def test_grep_directory_skips_unsupported_files(
    md_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (md_path.parent / "junk.bin").write_bytes(b"alpha")
    code = doc.main(["grep", "alpha", str(md_path.parent)])
    out = capsys.readouterr().out
    assert code == 0
    assert "junk.bin" not in out
    assert "note.md:L2: alpha line" in out


def test_grep_no_hits_returns_one(
    md_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert doc.main(["grep", "nothing-here", str(md_path)]) == 1
    assert "沒有符合" in capsys.readouterr().out


def test_show_pages(pdf_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc.main(["show", str(pdf_path), "--pages", "2"])
    out = capsys.readouterr().out
    assert "--- p.2 ---" in out
    assert "warm start value 20" in out
    assert "value 10" not in out


def test_show_lines(md_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc.main(["show", str(md_path), "--lines", "2-3"])
    out = capsys.readouterr().out
    assert out.splitlines() == ["L2: alpha line", "L3: Second Theme here"]


def test_grep_missing_file_is_bad_input(
    md_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = doc.main(["grep", "alpha", str(md_path), str(md_path) + ".nope"])
    assert code == 2
    assert "note.md:L2: alpha line" in capsys.readouterr().out


def test_grep_stops_scanning_files_after_max(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("hit\n", encoding="utf-8")
    assert doc.main(["grep", "hit", "--max", "1", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "b.txt" not in out
    assert "其餘檔案未搜尋" in out


def test_grep_rejects_negative_numbers(md_path: Path) -> None:
    for flag in (["-C", "-1"], ["--max", "0"]):
        with pytest.raises(SystemExit):
            doc.main(["grep", "x", *flag, str(md_path)])


def test_show_range_mismatch(pdf_path: Path, md_path: Path) -> None:
    assert doc.main(["show", str(md_path), "--pages", "1"]) == 2
    assert doc.main(["show", str(pdf_path), "--lines", "1-3"]) == 2
    assert doc.main(["show", str(pdf_path), "--pages", "99"]) == 1


def _verify(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "verify.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_quote_accepts_verbatim_quotes_at_the_stated_place(
    pdf_path: Path, md_path: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verify = _verify(tmp_path, (
        f"- 證據：`{pdf_path.as_posix()}` p.2「warm  start value 20」\n"
        "- 證據：L4「Second Theme here」L4「omega line」\n"
    ))
    code = doc.main(["quote", "--from", str(verify), "--source", str(md_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "引句 3 筆，不合格 0 筆" in out


def test_quote_quote_may_span_lines(
    md_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verify = _verify(tmp_path, "- 證據：L2「alpha line Second」\n")
    assert doc.main(["quote", "--from", str(verify), "--source",
                     str(md_path)]) == 0


def test_quote_flags_missing_wrong_place_and_no_location(
    pdf_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = f"`{pdf_path.as_posix()}`"
    verify = _verify(tmp_path, (
        f"- 證據：{src} p.1「warm start value 99」\n"
        f"- 證據：{src} p.1「warm start value 30」\n"
        f"- 證據：{src} 「Page 2 heading」\n"
        "- 證據：沒有引句\n"
    ))
    code = doc.main(["quote", "--from", str(verify)])
    out = capsys.readouterr().out
    assert code == 1
    assert "MISS" in out
    assert "寫 p.1，實際在 p.3" in out
    assert "NOLOC" in out
    assert "NOQUOTE" in out
    assert "不合格 4 筆" in out


def test_quote_without_evidence_or_source_is_bad_input(
    md_path: Path, tmp_path: Path
) -> None:
    assert doc.main(["quote", "--from",
                     str(_verify(tmp_path, "nothing\n"))]) == 2
    assert doc.main(["quote", "--from",
                     str(_verify(tmp_path, "- 證據：L2「alpha」\n"))]) == 2


# --- OCR fallback and check -------------------------------------------------

from docsrc import readers  # noqa: E402


@pytest.fixture(autouse=True)
def ocr_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keeps every test's OCR cache out of the repo's .llm-wiki/ocr/."""
    cache = tmp_path / "ocr-cache"
    monkeypatch.setattr(readers, "OCR_CACHE", cache)
    return cache


@pytest.fixture
def scan_path(tmp_path: Path) -> Path:
    """A PDF whose p.1 has text and p.2 is only an image of text."""
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 120), "Investor Diversification", fontsize=28)
    page.insert_text((72, 170), "Equity Markets 1991", fontsize=28)
    pixmap = page.get_pixmap(dpi=200)
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Typed first page", fontsize=11)
    scanned = pdf.new_page()
    scanned.insert_image(scanned.rect, pixmap=pixmap)
    path = tmp_path / "scan.pdf"
    pdf.save(path)
    return path


@pytest.fixture
def fake_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[int]:
    """Replaces Tesseract with a stub; returns the list of OCR'd pages."""
    calls: list[int] = []

    def stub(page: pymupdf.Page, tessdata: Path) -> list[str]:
        calls.append(page.number + 1)
        return [f"fake ocr line {page.number + 1}"]

    monkeypatch.setattr(readers, "tessdata_dir", lambda: tmp_path)
    monkeypatch.setattr(readers, "_ocr_page", stub)
    return calls


def test_real_ocr_reads_image_only_page(
    scan_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if readers.tessdata_dir() is None:
        pytest.skip("Tesseract English data not installed")
    document = docsrc.open_document(scan_path)
    text = " ".join(ln.text for ln in document.lines() if ln.page == 2)
    assert "Diversification" in text
    assert document.ocr_pages == {2}
    assert doc.main(["show", str(scan_path), "--pages", "1-2"]) == 0
    out = capsys.readouterr().out
    assert "--- p.1 ---" in out
    assert "--- p.2 (OCR) ---" in out


def test_ocr_is_cached_by_file_hash(
    scan_path: Path, fake_ocr: list[int], ocr_cache: Path
) -> None:
    for _ in range(2):
        document = docsrc.open_document(scan_path)
        lines = document.lines()
    assert fake_ocr == [2]
    assert ("p.2", "fake ocr line 2") in [(ln.label, ln.text) for ln in lines]
    assert document.ocr_pages == {2}
    assert len(list(ocr_cache.glob("*.json"))) == 1

    with pymupdf.open(scan_path) as pdf:
        pdf.set_metadata({"title": "changed"})
        pdf.save(scan_path.with_name("changed.pdf"))
    scan_path.with_name("changed.pdf").replace(scan_path)
    docsrc.open_document(scan_path).lines()
    assert fake_ocr == [2, 2]


def test_ocr_unavailable_gives_no_lines_but_is_detectable(
    scan_path: Path, monkeypatch: pytest.MonkeyPatch, ocr_cache: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(readers, "tessdata_dir", lambda: None)
    document = docsrc.open_document(scan_path)
    assert [ln.page for ln in document.lines()] == [1]
    assert document.unread_pages == {2}
    assert document.ocr_available is False
    assert not ocr_cache.exists()
    assert doc.main(["show", str(scan_path), "--pages", "2"]) == 1
    assert "無法 OCR" in capsys.readouterr().out
    assert doc.main(["check", str(scan_path)]) == 1
    out = capsys.readouterr().out
    assert "讀不到 p.2" in out
    assert "結果：NEEDS_OCR" in out


def test_ocr_failure_is_not_cached(
    scan_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ocr_cache: Path,
) -> None:
    def broken(page: pymupdf.Page, tessdata: Path) -> list[str]:
        raise RuntimeError("tesseract failed")

    monkeypatch.setattr(readers, "tessdata_dir", lambda: tmp_path)
    monkeypatch.setattr(readers, "_ocr_page", broken)
    document = docsrc.open_document(scan_path)
    document.lines()
    assert document.unread_pages == {2}
    assert document.ocr_available is False
    assert not list(ocr_cache.glob("*.json"))


def test_tessdata_dir_follows_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "tessdata"
    data.mkdir()
    (data / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setattr(readers, "_DEFAULT_TESSDATA", tmp_path / "none")
    for prefix in (data, tmp_path):
        monkeypatch.setenv("TESSDATA_PREFIX", str(prefix))
        assert readers.tessdata_dir() == data
    monkeypatch.delenv("TESSDATA_PREFIX")
    assert readers.tessdata_dir() is None


def test_check_reports_ok_and_ocr_used(
    pdf_path: Path, scan_path: Path, fake_ocr: list[int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert doc.main(["check", str(pdf_path), str(scan_path)]) == 0
    out = capsys.readouterr().out
    assert "3 頁" in out
    assert "結果：OK" in out
    assert "已 OCR p.2" in out
    assert "結果：OCR_USED" in out
    assert "Helvetica×" in out


def test_check_counts_mid_word_glyphs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Broken ligatures count; quotes, dashes, accents and math do not."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    writer = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("cjk")  # a built-in font with full Unicode
    writer.append((72, 72), "bene…ts and pro…t …rst", font=font,
                  fontsize=11)
    writer.append((72, 90), "world’s Smith–Jones café into“quoted”and",
                  font=font, fontsize=11)
    writer.append((72, 108), "N−J βiδw P´astor coe⁄cient β′xi", font=font,
                  fontsize=11)
    writer.write_text(page)
    path = tmp_path / "glyphs.pdf"
    pdf.save(path)
    report = docsrc.inspect_pdf(path)
    assert set(report.suspects) == {"…", "⁄"}
    assert report.suspects["…"].count == 3  # word-initial "…rst" counts
    assert report.suspects["…"].example == "bene…ts"
    assert report.verdict == "SUSPECT_GLYPHS"
    assert doc.main(["check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "可疑字中字元：4" in out
    assert "例：bene…ts" in out
    assert "結果：SUSPECT_GLYPHS" in out


_GARBLED = "flIJTACL2IrA o irnuo uq poojc-ro-wqcci cc qo uo cxbpqu gic"
_CLEAN = "the momentum of stock returns is a puzzle that is hard to explain"


def _text_pdf(tmp_path: Path, name: str, line: str, pages: int = 2) -> Path:
    """A PDF whose pages repeat one line of text 30 times."""
    pdf = pymupdf.open()
    for _ in range(pages):
        page = pdf.new_page()
        for row in range(30):
            page.insert_text((40, 40 + 20 * row), line, fontsize=9)
    path = tmp_path / name
    pdf.save(path)
    return path


def test_garbled_text_layer_is_replaced_by_ocr(
    tmp_path: Path, fake_ocr: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    path = _text_pdf(tmp_path, "garbled.pdf", _GARBLED)
    document = docsrc.open_document(path)
    lines = document.lines()
    assert document.garbled is True
    assert document.blank_pages == set()
    assert document.ocr_pages == {1, 2}
    assert [ln.text for ln in lines] == ["fake ocr line 1", "fake ocr line 2"]
    assert doc.main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "文字層亂碼，整份改用 OCR" in out
    assert "可疑字中字元：0" in out
    assert "結果：OCR_USED" in out


def test_garbled_text_layer_without_ocr_needs_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(readers, "tessdata_dir", lambda: None)
    path = _text_pdf(tmp_path, "garbled.pdf", _GARBLED)
    document = docsrc.open_document(path)
    assert document.lines() == []
    assert document.unread_pages == {1, 2}
    assert doc.main(["check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "讀不到 p.1-2" in out
    assert "結果：NEEDS_OCR" in out


def test_clean_text_layer_is_kept(
    tmp_path: Path, fake_ocr: list[int]
) -> None:
    path = _text_pdf(tmp_path, "clean.pdf", _CLEAN)
    document = docsrc.open_document(path)
    document.lines()
    assert document.garbled is False
    assert fake_ocr == []


def test_common_word_share_needs_enough_latin_text() -> None:
    assert readers.common_word_share([_GARBLED] * 5) is None  # too short
    assert readers.common_word_share(["中文段落的內容 is"] * 400) is None
    share = readers.common_word_share([_CLEAN] * 40)
    assert share is not None and share > 0.3
    assert readers.common_word_share([_GARBLED] * 40) == 0


def test_check_bad_input(md_path: Path, tmp_path: Path) -> None:
    assert doc.main(["check", str(md_path)]) == 2
    assert doc.main(["check", str(tmp_path / "missing.pdf")]) == 2


def test_corrupt_tessdata_counts_as_unavailable(
    scan_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ocr_cache: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Real Tesseract with junk data must fail quietly, not crash."""
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "eng.traineddata").write_bytes(b"not a model")
    monkeypatch.setattr(readers, "tessdata_dir", lambda: junk)
    document = docsrc.open_document(scan_path)
    assert [ln.page for ln in document.lines()] == [1]
    assert document.unread_pages == {2}
    assert not list(ocr_cache.glob("*.json"))
    assert capsys.readouterr().out == ""


def test_malformed_cache_is_ignored(
    scan_path: Path, fake_ocr: list[int], ocr_cache: Path
) -> None:
    docsrc.open_document(scan_path).lines()
    (cache_file,) = ocr_cache.glob("*.json")
    for pages in ("null", "[1]", '{"2": "text"}'):
        cache_file.write_text(
            f'{{"version": "{readers._OCR_VERSION}", "pages": {pages}}}',
            encoding="utf-8")
        lines = docsrc.open_document(scan_path).lines()
        assert "fake ocr line 2" in [ln.text for ln in lines]
    assert fake_ocr == [2, 2, 2, 2]
