"""Tests for questions.py (question log and gap report)."""

import datetime as dt
import json
from pathlib import Path

import pytest

import questions

A = "wiki/music/concepts/a.md"
DAY = dt.date(2026, 11, 17)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    page = tmp_path / A
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: A 頁\ntype: concept\n---\n# A 頁\n",
                    encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "search_eval.tsv").write_text(
        "# 註解\n已經在評估裡\twiki/music/concepts/a.md\n", encoding="utf-8")
    return tmp_path


def test_record_validates(root: Path) -> None:
    with pytest.raises(ValueError, match="空"):
        questions.record(root, "  \t", "answered")
    with pytest.raises(ValueError, match="結果"):
        questions.record(root, "q", "ok")
    with pytest.raises(ValueError, match="種類"):
        questions.record(root, "q", "answered", kind="chat")
    with pytest.raises(ValueError, match="缺口"):
        questions.record(root, "q", "partial", gap="other")
    with pytest.raises(ValueError, match="只能帶"):
        questions.record(root, "q", "answered", gap="unclear")
    with pytest.raises(ValueError, match="--gap"):
        questions.record(root, "q", "unanswered")
    with pytest.raises(ValueError, match="--pages"):
        questions.record(root, "q", "partial", gap="search-miss")
    with pytest.raises(ValueError, match="內容頁"):
        questions.record(root, "q", "partial", gap="unclear",
                         pages=["wiki/music/concepts/none.md"])
    assert not (root / questions.QUESTION_LOG).exists()


def test_record_writes_one_line(root: Path) -> None:
    entry = questions.record(root, "第一行\n第二行\t尾", "partial",
                             gap="unclear", pages=["./" + A],
                             note="少了\n定義", today=DAY)
    assert entry["question"] == "第一行 第二行 尾"
    assert entry["pages"] == [A]
    assert entry["note"] == "少了 定義"
    lines = (root / questions.QUESTION_LOG).read_text(
        encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == entry


def test_report_groups_gaps_and_eval_candidates(root: Path) -> None:
    questions.record(root, "答得出", "answered", today=DAY)
    questions.record(root, "缺資料", "unanswered", gap="missing-source",
                     note="樂譜", today=DAY)
    questions.record(root, "難懂", "partial", gap="unclear", pages=[A],
                     today=DAY)
    questions.record(root, "沒找到", "answered", gap="search-miss",
                     pages=[A], today=DAY)
    questions.record(root, "沒找到", "partial", gap="search-miss",
                     pages=[A], today=DAY)
    questions.record(root, "沒找到", "partial", gap="search-miss",
                     pages=[A], today=DAY)
    questions.record(root, "已經在評估裡", "partial", gap="search-miss",
                     pages=[A], today=DAY)
    with (root / questions.QUESTION_LOG).open("a", encoding="utf-8") as f:
        f.write("not json\n{\"result\": \"bad\"}\n")
    rep = questions.report(root)
    assert rep["total"] == 7
    assert rep["results"] == {"answered": 2, "partial": 4, "unanswered": 1}
    assert rep["gaps"] == {"missing-source": 1, "unclear": 1,
                           "search-miss": 4}
    assert [e["question"] for e in rep["missing_source"]] == ["缺資料"]
    assert [e["question"] for e in rep["unclear"]] == ["難懂"]
    assert rep["eval_candidates"] == [f"沒找到\t{A}"]


def test_report_merges_pages_and_tolerates_bad_pages(root: Path) -> None:
    b = "wiki/music/concepts/b.md"
    (root / b).write_text("---\ntitle: B\ntype: concept\n---\n# B\n",
                          encoding="utf-8")
    questions.record(root, "q", "partial", gap="search-miss", pages=[A],
                     today=DAY)
    questions.record(root, "q", "partial", gap="search-miss",
                     pages=[b, A], today=DAY)
    with (root / questions.QUESTION_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": "2026-11-17", "question": "壞",
                            "result": "partial", "gap": "search-miss",
                            "pages": None}, ensure_ascii=False) + "\n")
    rep = questions.report(root)
    assert rep["total"] == 3
    assert rep["eval_candidates"] == [f"q\t{A} {b}"]


def test_eval_file_with_bom(root: Path) -> None:
    (root / questions.SEARCH_EVAL).write_text(
        "﻿# 註解\nq\tx\n", encoding="utf-8")
    assert questions._eval_questions(root) == {"q"}


def test_report_since(root: Path) -> None:
    questions.record(root, "舊", "answered", today=dt.date(2026, 11, 1))
    questions.record(root, "新", "answered", today=DAY)
    rep = questions.report(root, since=dt.date(2026, 11, 10))
    assert rep["total"] == 1
    assert questions.report(root / "none")["total"] == 0


def test_main_record_and_report(root: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(questions._paths, "ROOT", root)
    assert questions.main(["record", "q", "unanswered",
                           "--gap", "missing-source", "--date",
                           "2026-11-17"]) == 0
    assert questions.main(["record", "q", "answered", "--gap",
                           "unclear"]) == 1
    assert questions.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "共 1 題" in out and "缺來源 1" in out
