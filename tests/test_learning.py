"""Tests for learning.py (quiz results and reading suggestions)."""

import datetime as dt
import json
from pathlib import Path

import pytest

import learning

TOPIC = "wiki/music/topics/t.md"
OTHER = "wiki/music/topics/u.md"
A, B = "wiki/music/concepts/a.md", "wiki/music/concepts/b.md"
STUDY = "wiki/music/queries/2026-11-16-study-t.md"
DAY = dt.date(2026, 11, 16)


def _write(root: Path, rel: str, type_: str, title: str, body: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {title}\ntype: {type_}\n---\n# {title}\n{body}",
                    encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _write(tmp_path, TOPIC, "topic", "主題 T",
           "\n## 先讀\n1. [A 頁](../concepts/a.md) — 先讀\n\n## 概念\n- [B](../concepts/b.md)\n")
    _write(tmp_path, OTHER, "topic", "主題 U")
    _write(tmp_path, A, "concept", "A 頁")
    _write(tmp_path, B, "concept", "B 頁")
    _write(tmp_path, STUDY, "query", "study T")
    return tmp_path


def test_record_validates(root: Path) -> None:
    with pytest.raises(ValueError, match="結果"):
        learning.record(root, TOPIC, 1, "ok", [A])
    with pytest.raises(ValueError, match="主題頁"):
        learning.record(root, A, 1, "right", [A])
    with pytest.raises(ValueError, match="內容頁"):
        learning.record(root, TOPIC, 1, "right", ["wiki/music/concepts/none.md"])
    with pytest.raises(ValueError, match="題號"):
        learning.record(root, TOPIC, 0, "right", [A])
    assert not (root / learning.LEARNING_LOG).exists()


def test_weak_pages_rank_and_later_right_answers_clear(root: Path) -> None:
    learning.record(root, TOPIC, 1, "wrong", [A], STUDY, DAY)
    learning.record(root, TOPIC, 2, "partial", [B], STUDY, DAY)
    learning.record(root, TOPIC, 3, "right", [B], STUDY, DAY)
    weak = learning.weak_pages(root)
    assert [(r["page"], r["weakness"]) for r in weak] == [(A, 1.0), (B, 0.25)]
    assert weak[0]["questions"] == ["#1"]
    assert weak[0]["studies"] == ["2026-11-16-study-t"]
    # Regrading the same question replaces the earlier result.
    learning.record(root, TOPIC, 1, "right", [A], STUDY, DAY)
    assert [r["page"] for r in learning.weak_pages(root)] == [B]


def test_status_and_next(root: Path) -> None:
    first = learning.next_pages(root, TOPIC)
    assert first["first_read"] == [{"page": A, "title": "A 頁"}]
    assert set(first["unstudied"]) == {"主題 T", "主題 U"}
    learning.record(root, TOPIC, 1, "skipped", [A], STUDY, DAY)
    rows = {r["topic"]: r for r in learning.status(root)}
    assert rows[TOPIC]["last"] == "2026-11-16"
    assert rows[TOPIC]["counts"]["skipped"] == 1 and rows[OTHER]["last"] is None
    nxt = learning.next_pages(root, TOPIC)
    assert nxt["first_read"] == [] and nxt["weak"][0]["page"] == A
    assert nxt["unstudied"] == ["主題 U"]
    assert learning.next_pages(root, OTHER)["weak"] == []


def test_missing_pages_and_bad_lines_are_tolerated(root: Path) -> None:
    log = root / learning.LEARNING_LOG
    log.parent.mkdir(parents=True)
    gone = {"date": "2026-11-01", "topic": TOPIC, "study": None,
            "question": 1, "result": "wrong", "pages": ["wiki/music/concepts/old.md"]}
    bad = {**gone, "question": [1]}
    log.write_text("not json\n" + json.dumps(gone) + "\n" + '{"result": "x"}\n'
                   + json.dumps(bad) + "\n", encoding="utf-8")
    weak = learning.weak_pages(root)
    assert weak and not weak[0]["exists"]
    assert learning.next_pages(root)["weak"] == []


def test_cli_record_and_next(root: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(learning._paths, "ROOT", root)
    assert learning.main(["record", TOPIC, "4", "wrong", B, "--study", STUDY]) == 0
    assert learning.main(["next", "--topic", TOPIC]) == 0
    out = capsys.readouterr().out
    assert "B 頁" in out and "主題 U" in out
    with pytest.raises(SystemExit):
        learning.main(["record", TOPIC, "4", "bad", B])


def test_record_dedupes_pages_and_next_rejects_bad_limit(root: Path) -> None:
    entry = learning.record(root, TOPIC, 1, "wrong", [A, A, "./" + A], STUDY)
    assert entry["pages"] == [A]
    with pytest.raises(ValueError):
        learning.next_pages(root, limit=0)


def test_kinds_number_apart_and_next_takes_free_number(root: Path) -> None:
    learning.record(root, TOPIC, 1, "right", [A], today=DAY)
    first = learning.record(root, TOPIC, None, "wrong", [A], today=DAY, kind="probe")
    second = learning.record(root, TOPIC, None, "right", [B], today=DAY, kind="probe")
    check = learning.record(root, TOPIC, None, "partial", [B], today=DAY, kind="check")
    assert (first["question"], second["question"], check["question"]) == (1, 2, 1)
    assert first["kind"] == "probe" and "kind" not in learning.record(
        root, TOPIC, 2, "right", [B], today=DAY)
    # Probe #1 did not replace quiz #1: both count, and A is half missed.
    assert len(learning.load_results(root)) == 5
    weak = {r["page"]: r for r in learning.weak_pages(root)}
    assert weak[A]["weakness"] == 0.5 and weak[A]["questions"] == ["探底 #1"]
    assert weak[B]["questions"] == ["補弱 #1"]
    # The next day starts again at 1; the same number on the same day replaces.
    later = DAY + dt.timedelta(days=1)
    assert learning.record(root, TOPIC, None, "wrong", [A], today=later,
                           kind="probe")["question"] == 1
    learning.record(root, TOPIC, 1, "right", [B], today=DAY, kind="check")
    assert B not in {r["page"] for r in learning.weak_pages(root)}


def test_record_rejects_bad_kind_number_study_and_note(root: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        learning.record(root, TOPIC, 1, "right", [A], kind="exam")
    with pytest.raises(ValueError, match="next"):
        learning.record(root, TOPIC, None, "right", [A])
    with pytest.raises(ValueError, match="study"):
        learning.record(root, TOPIC, None, "right", [A], STUDY, kind="check")
    with pytest.raises(ValueError, match="note"):
        learning.record(root, TOPIC, 1, "wrong", [A], note="字" * 201)
    assert not (root / learning.LEARNING_LOG).exists()


def test_notes_show_newest_first_on_weak_pages(root: Path) -> None:
    learning.record(root, TOPIC, 1, "wrong", [A], STUDY, DAY, note="  舊的\n誤解 ")
    learning.record(root, TOPIC, 2, "wrong", [A], STUDY, DAY, note="   ")
    learning.record(root, TOPIC, 3, "partial", [A], STUDY, DAY, note="中間的誤解")
    learning.record(root, TOPIC, None, "right", [A], today=DAY, kind="check",
                    note="預測時的誤解")
    learning.record(root, TOPIC, 4, "right", [B], STUDY, DAY, note="B 沒有弱點")
    lines = (root / learning.LEARNING_LOG).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["note"] == "舊的 誤解" and "note" not in json.loads(lines[1])
    weak = learning.weak_pages(root)
    assert [r["page"] for r in weak] == [A]
    assert weak[0]["notes"] == ["預測時的誤解", "中間的誤解"]


def test_old_lines_and_bad_kind_or_note_are_tolerated(root: Path) -> None:
    log = root / learning.LEARNING_LOG
    log.parent.mkdir(parents=True)
    old = {"date": "2026-11-01", "topic": TOPIC, "study": None, "question": 1,
           "result": "wrong", "pages": [A]}
    log.write_text("\n".join(json.dumps(e) for e in (
        old, {**old, "question": 2, "kind": "exam"},
        {**old, "question": 3, "note": 7})) + "\n", encoding="utf-8")
    entries = learning.load_results(root)
    assert [(e["question"], e["kind"]) for e in entries] == [(1, "quiz"), (3, "quiz")]
    assert learning.weak_pages(root)[0]["notes"] == []


def test_cli_probe_check_and_note(root: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(learning._paths, "ROOT", root)
    assert learning.main(["record", TOPIC, "next", "wrong", A, "--kind", "probe",
                          "--note", "把 A 當成 B"]) == 0
    assert learning.main(["record", TOPIC, "next", "partial", A, "--kind", "check"]) == 0
    assert "已記錄探底第 1 題" in capsys.readouterr().out
    assert learning.main(["status", "--topic", TOPIC]) == 0
    assert learning.main(["next", "--topic", TOPIC]) == 0
    out = capsys.readouterr().out
    assert out.count("誤解：把 A 當成 B") == 2 and "探底 #1、補弱 #1" in out
    assert learning.main(["record", TOPIC, "next", "wrong", A]) == 1
    with pytest.raises(SystemExit):
        learning.main(["record", TOPIC, "soon", "wrong", A, "--kind", "probe"])
