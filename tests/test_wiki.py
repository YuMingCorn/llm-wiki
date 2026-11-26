"""Tests for wiki.py (log append and guarded commit)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import _paths  # noqa: E402
import wiki  # noqa: E402


def test_is_forbidden() -> None:
    assert _paths.is_forbidden("raw/sources/a.pdf")
    assert _paths.is_forbidden("hackmd_note/Areas/x.md")
    assert _paths.is_forbidden(".claude/settings.local.json")
    assert not _paths.is_forbidden("hackmd_note/README.md")
    assert not _paths.is_forbidden("wiki/raw-notes.md")


def test_append_log_adds_dated_heading(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text("# Log\n\n## [2026-11-01] ingest | Old\n- a", encoding="utf-8")
    wiki.append_log(log, "lint", "第七次", "- item 1\n", "2026-11-08")
    assert log.read_text(encoding="utf-8") == (
        "# Log\n\n## [2026-11-01] ingest | Old\n- a\n\n"
        "## [2026-11-08] lint | 第七次\n- item 1\n"
    )


def test_append_log_rejects_bad_input(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text("# Log\n", encoding="utf-8")
    with pytest.raises(ValueError):
        wiki.append_log(log, "deploy", "x", "", "2026-11-08")
    with pytest.raises(ValueError):
        wiki.append_log(log, "lint", "a | b\nc", "", "2026-11-08")
    with pytest.raises(ValueError):
        wiki.append_log(log, "lint", "x", "## [2026-11-08] lint | forged",
                        "2026-11-08")
    assert log.read_text(encoding="utf-8") == "# Log\n"


@pytest.mark.parametrize("log_type", ["edit", "query", "refine", "review"])
def test_append_log_rejects_minor_types(log_type: str, tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text("# Log\n", encoding="utf-8")
    with pytest.raises(ValueError):
        wiki.append_log(log, log_type, "x", "", "2026-11-09")


def test_append_log_strips_bom(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text("# Log\n", encoding="utf-8")
    wiki.append_log(log, "schema", "修正", "﻿- a\n", "2026-11-09")
    assert log.read_text(encoding="utf-8") == (
        "# Log\n\n## [2026-11-09] schema | 修正\n- a\n")


def test_append_log_limits_body_to_one_line(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text("# Log\n", encoding="utf-8")
    with pytest.raises(ValueError, match="最多 1 行"):
        wiki.append_log(log, "lint", "x", "- 1\n- 2\n", "2026-11-09")
    wiki.append_log(log, "lint", "x", "\n- 1\n\n", "2026-11-09")


def _log_with(types: list[str], tmp_path: Path) -> Path:
    log = tmp_path / "log.md"
    log.write_text("# Log\n\n" + "".join(
        f"## [2026-11-01] {t} | x\n" for t in types), encoding="utf-8")
    return log


@pytest.mark.parametrize("types, log_type, due", [
    (["lint"] + ["ingest"] * 5, "ingest", True),
    (["lint"] + ["ingest"] * 4 + ["merge"], "merge", False),
    (["ingest"] * 5 + ["lint", "ingest"], "ingest", False),
    (["lint"] + ["ingest"] * 4, "ingest", False),
    (["ingest"] * 5, "review", False),
])
def test_lint_reminder(types: list[str], log_type: str, due: bool,
                       tmp_path: Path) -> None:
    reminder = wiki.lint_reminder(_log_with(types, tmp_path), log_type)
    assert bool(reminder) is due


@pytest.mark.parametrize("msg, ok", [
    ("docs(wiki): ingest Bach-1722", True),
    ("feat(tools): doc.py grep", True),
    ("chore(repo): bump", True),
    ("docs(agents): source-verifier 回合數", True),
    ("chore: bump", False),
    ("docs(dispatch): 精簡", False),
    ("update stuff", False),
    ("docs(wiki): " + "x" * 51, False),
    ("docs(wiki): 結尾有句號。", False),
])
def test_check_message(msg: str, ok: bool) -> None:
    if ok:
        wiki.check_message(msg)
    else:
        with pytest.raises(ValueError):
            wiki.check_message(msg)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True,
                          encoding="utf-8").stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "secret.pdf").write_text("x", encoding="utf-8")
    return tmp_path


def test_commit_adds_paths_and_trailer(repo: Path) -> None:
    wiki.commit(repo, "docs(wiki): lint 第 7 次", ["wiki/a.md"], body="原因")
    log = _git(repo, "log", "-1", "--format=%B")
    assert log.startswith("docs(wiki): lint 第 7 次\n\n原因\n\n")
    assert wiki.TRAILER in log


def test_commit_refuses_forbidden_and_unstages(repo: Path) -> None:
    with pytest.raises(ValueError, match="raw/secret.pdf"):
        wiki.commit(repo, "docs(wiki): x", ["wiki/a.md", "raw/secret.pdf"])
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    with pytest.raises(ValueError, match="raw"):
        wiki.commit(repo, "docs(wiki): x", ["raw"])
    assert _git(repo, "rev-list", "--all") == ""


def test_commit_refuses_forbidden_already_staged(repo: Path) -> None:
    _git(repo, "add", "-f", "raw/secret.pdf")
    with pytest.raises(ValueError, match="raw/secret.pdf"):
        wiki.commit(repo, "docs(wiki): x", ["wiki/a.md"])
    assert "raw/secret.pdf" not in _git(repo, "diff", "--cached", "--name-only")


def test_commit_refuses_empty(repo: Path) -> None:
    with pytest.raises(ValueError, match="沒有"):
        wiki.commit(repo, "docs(wiki): x", [])


def test_append_log_rejects_indented_heading_and_cr(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_bytes(b"# Log\n")
    with pytest.raises(ValueError):
        wiki.append_log(log, "lint", "x", "   ## [2026-01-01] lint | forged",
                        "2026-11-08")
    with pytest.raises(ValueError):
        wiki.append_log(log, "lint", "a\r## [2026-01-01] lint | f", "",
                        "2026-11-08")
    wiki.append_log(log, "lint", "x", "- a\r\n", "2026-11-08")
    assert "\r" not in log.read_bytes().decode("utf-8")


def test_read_stdin_does_not_hang_on_open_pipe(monkeypatch) -> None:
    import io
    import os
    import threading
    import time

    r, w = os.pipe()
    stdin = io.TextIOWrapper(io.FileIO(r))
    monkeypatch.setattr(sys, "stdin", stdin)
    before = set(threading.enumerate())
    start = time.monotonic()
    assert wiki._read_stdin(0.3) == ""
    assert time.monotonic() - start < 2
    readers = set(threading.enumerate()) - before
    os.close(w)
    # The reader is still blocked in read(); let EOF finish it before closing.
    for reader in readers:
        reader.join(5)
    stdin.close()


def test_commit_allows_hackmd_readme(repo: Path) -> None:
    (repo / "hackmd_note").mkdir()
    (repo / "hackmd_note" / "README.md").write_text("r\n", encoding="utf-8")
    wiki.commit(repo, "docs(schema): readme", ["hackmd_note/README.md"])
    assert "hackmd_note/README.md" in _git(repo, "show", "--name-only")


def test_commit_refusal_keeps_tracked_forbidden_file(repo: Path) -> None:
    _git(repo, "add", "-f", "raw/secret.pdf", "wiki/a.md")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "raw" / "secret.pdf").write_text("changed", encoding="utf-8")
    (repo / "wiki" / "a.md").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "-f", "raw/secret.pdf")
    with pytest.raises(ValueError):
        wiki.commit(repo, "docs(wiki): x", ["wiki/a.md"])
    status = _git(repo, "status", "--short")
    assert "D  raw/secret.pdf" not in status
    assert " M raw/secret.pdf" in status


def test_commit_rejects_path_outside_repo(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不在 repo"):
        wiki.commit(repo, "docs(wiki): x", [str(tmp_path.parent)])


def test_utf8_streams_decodes_chinese_stdin(monkeypatch) -> None:
    import io
    raw = io.BytesIO("- 中文內文\n".encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw, encoding="cp950"))
    wiki._utf8_streams()
    assert sys.stdin.read() == "- 中文內文\n"


def test_commit_refuses_staged_private_path_with_chinese_name(repo: Path) -> None:
    note = repo / "hackmd_note" / "中文筆記.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-f", "hackmd_note/中文筆記.md")
    with pytest.raises(ValueError, match="私人路徑"):
        wiki.commit(repo, "docs(wiki): x", [])


def test_link_raw_text_links_existing_files_only(tmp_path: Path) -> None:
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    (tmp_path / "raw" / "sources" / "a b.pdf").write_text("x", encoding="utf-8")
    text = ("- **原始檔**：`raw/sources/a b.pdf`（3 頁）\n"
            "見 `raw/sources/`；`raw/sources/missing.pdf`\n")
    new, count = wiki.link_raw_text(text, "wiki/sources", tmp_path)
    assert count == 1
    assert "[a b.pdf](../../raw/sources/a%20b.pdf)（3 頁）" in new
    assert "`raw/sources/`" in new and "`raw/sources/missing.pdf`" in new


def test_link_raw_keeps_line_endings_and_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    (tmp_path / "raw" / "sources" / "x.pdf").write_text("x", encoding="utf-8")
    page = tmp_path / "wiki" / "sources" / "p.md"
    page.parent.mkdir(parents=True)
    page.write_bytes("a `raw/sources/x.pdf`\r\n".encode())
    assert wiki.link_raw(tmp_path, []) == [("wiki/sources/p.md", 1)]
    assert page.read_bytes() == b"a [x.pdf](../../raw/sources/x.pdf)\r\n"
    assert wiki.link_raw(tmp_path, []) == []


def _log_text(entries: list[tuple[str, str]]) -> str:
    """Builds a log with one multi-line entry per (date, type)."""
    body = "".join(f"## [{d}] {t} | e{i}\n- line\n- more\n\n"
                   for i, (d, t) in enumerate(entries))
    return "---\ntitle: Log\n---\n\n# Log\n\n" + body


def test_archive_log_moves_old_entries_by_month(tmp_path: Path) -> None:
    import lint
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    entries = ([("2026-10-30", "sync")] * 2 + [("2026-11-01", "ingest")] * 3
               + [("2026-11-02", "lint")] + [("2026-11-03", "ingest")] * 2)
    original = _log_text(entries)
    log.write_text(original, encoding="utf-8")
    before = lint.ingests_since_last_lint(original)

    plan = wiki.archive_log(log, arc, keep=2)
    assert plan["moved"] == 5 and not plan["applied"]
    assert log.read_text(encoding="utf-8") == original  # dry run

    res = wiki.archive_log(log, arc, keep=2, apply=True)
    assert res["months"] == {"2026-10": 2, "2026-11": 3}
    kept = log.read_text(encoding="utf-8")
    assert kept.startswith("---\ntitle: Log\n---\n\n# Log\n\n## [2026-11-02] lint")
    assert lint.ingests_since_last_lint(kept) == before
    oct_text = (arc / "2026-10.md").read_text(encoding="utf-8")
    assert oct_text.startswith("# Log 封存 2026-10")
    assert oct_text.count("## [") == 2 and "- more" in oct_text

    again = wiki.archive_log(log, arc, keep=2, apply=True)
    assert again["moved"] == 0
    assert (arc / "2026-11.md").read_text(encoding="utf-8").count("## [") == 3


def test_archive_log_never_moves_last_lint(tmp_path: Path) -> None:
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    log.write_text(_log_text([("2026-11-01", "lint")]
                             + [("2026-11-02", "ingest")] * 5),
                   encoding="utf-8")
    res = wiki.archive_log(log, arc, keep=1, apply=True)
    assert res["moved"] == 0 and res["kept"] == 6


def test_archive_log_skips_entries_already_archived(tmp_path: Path) -> None:
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    text = _log_text([("2026-11-01", "ingest")] * 2 + [("2026-11-05", "lint")])
    log.write_text(text, encoding="utf-8")
    _, entries = wiki.split_log(text)
    arc.mkdir()
    (arc / "2026-11.md").write_text("# Log 封存 2026-11\n\n" + entries[0],
                                    encoding="utf-8")
    wiki.archive_log(log, arc, keep=0, apply=True)
    archived = (arc / "2026-11.md").read_text(encoding="utf-8")
    assert archived.count("## [") == 2


def test_lint_load_skips_meta(tmp_path: Path) -> None:
    import lint
    path = tmp_path / "wiki" / "meta" / "log-archive" / "2026-11.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Log 封存\n", encoding="utf-8")
    assert not lint.load(tmp_path).pages


def test_archive_log_refuses_malformed_heading(tmp_path: Path) -> None:
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    text = ("# Log\n\n## [2026-11-01] ingest | a\n\n## [2026-11-2] ingest | bad\n\n"
            "## [2026-11-03] lint|x\n")
    log.write_text(text, encoding="utf-8")
    assert len(wiki.split_log(text)[1]) == 3
    with pytest.raises(ValueError, match="無法辨識"):
        wiki.archive_log(log, arc, keep=0, apply=True)
    assert log.read_text(encoding="utf-8") == text and not arc.exists()


def test_archive_log_keeps_identical_entries(tmp_path: Path) -> None:
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    same = "## [2026-11-01] ingest | a\n"
    arc.mkdir()
    (arc / "2026-11.md").write_text("# Log 封存 2026-11\n\n" + same * 2,
                                    encoding="utf-8")
    log.write_text("# Log\n\n" + same + "\n## [2026-11-02] lint | n\n",
                   encoding="utf-8")
    res = wiki.archive_log(log, arc, keep=0, apply=True)
    assert res["moved"] == 1
    assert "lint | n" in log.read_text(encoding="utf-8")


def test_archive_log_refuses_impossible_date(tmp_path: Path) -> None:
    log, arc = tmp_path / "log.md", tmp_path / "arc"
    text = "# Log\n\n## [2026-13-45] ingest | x\n\n## [2026-11-02] lint | n\n"
    log.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="無法辨識"):
        wiki.archive_log(log, arc, keep=0, apply=True)
    assert not arc.exists()


# --- log-check and index-check ---------------------------------------------

def _log_root(tmp_path: Path, log: str, archive: str | None = None) -> Path:
    (tmp_path / "wiki").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "log.md").write_text(
        "# Log\n\n" + log, encoding="utf-8")
    if archive is not None:
        arch = tmp_path / "wiki" / "meta" / "log-archive"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "2026-11.md").write_text("# A\n\n" + archive,
                                         encoding="utf-8")
    return tmp_path


def _codes(found: list[dict]) -> list[str]:
    return [f["check"] for f in found]


def _kinds(found: list[dict]) -> list[tuple[str, str]]:
    return [(f["check"], f["severity"]) for f in found]


GOOD_LOG = ("## [2026-11-12] ingest | A\nbody\n\n"
            "## [2026-11-13] lint | 第 18 次\n.llm-wiki/x\n")


def test_log_check_passes_clean_log(tmp_path: Path) -> None:
    root = _log_root(tmp_path, GOOD_LOG.replace(".llm-wiki/x", "x"),
                     "## [2026-11-08] edit | 舊\na\nb\n")
    assert wiki.log_check(root) == []
    assert wiki.format_findings([]).startswith("通過")
    assert wiki.findings_status([]) == 0


def test_log_check_l1_bad_heading_type_and_body(tmp_path: Path) -> None:
    root = _log_root(tmp_path, (
        "## [2026-11-12] query | T\n\n"
        "## [2026-11-12] ingest bad\n\n"
        "## [2026-11-12] ingest | T\na\nb\n"))
    found = wiki.log_check(root)
    assert _codes(found).count("L1") == 3
    assert wiki.findings_status(found) == 1


def test_log_check_l2_date_goes_back(tmp_path: Path) -> None:
    root = _log_root(tmp_path, "## [2026-11-13] ingest | A\n\n"
                               "## [2026-11-12] ingest | B\n")
    assert _codes(wiki.log_check(root)) == ["L2"]


def test_log_check_l3_duplicates_and_archive_order(tmp_path: Path) -> None:
    dup = "## [2026-11-12] ingest | A\nx\n"
    root = _log_root(tmp_path, dup + "\n" + dup)
    assert _codes(wiki.log_check(root)) == ["L3"]
    root2 = _log_root(tmp_path / "b", dup, dup)
    assert "L3" in _codes(wiki.log_check(root2))
    root3 = _log_root(tmp_path / "c", dup, "## [2026-11-20] ingest | Z\n")
    assert "L3" in _codes(wiki.log_check(root3))


def test_log_check_l4_latest_lint_and_size(tmp_path: Path) -> None:
    root = _log_root(tmp_path, "## [2026-11-12] ingest | A\n",
                     "## [2026-11-08] lint | 第 17 次\n")
    assert _kinds(wiki.log_check(root)) == [("L4", "違規")]
    many = "".join(f"## [2026-11-12] ingest | N{i}\n\n" for i in range(151))
    root2 = _log_root(tmp_path / "b", many + "## [2026-11-12] lint | x\n")
    found2 = wiki.log_check(root2)
    assert _kinds(found2) == [("L4", "提醒")]
    assert wiki.findings_status(found2) == 0


def test_log_check_l5_lint_numbers(tmp_path: Path) -> None:
    archive = ("## [2026-11-08] lint | 第八次\n\n"
               "## [2026-11-08] lint | 第 9 次\n")
    ok = ("## [2026-11-12] lint | 第 10 次\n\n"
          "## [2026-11-12] lint | 第 10 次 的修正\n\n"
          "## [2026-11-13] lint | 第 11 次\n")
    assert wiki.log_check(_log_root(tmp_path, ok, archive)) == []
    gap = "## [2026-11-12] lint | 第 11 次\n"
    found = wiki.log_check(_log_root(tmp_path / "b", gap, archive))
    assert _codes(found) == ["L5"]
    dup = "## [2026-11-12] lint | 第 9 次\n"
    found = wiki.log_check(_log_root(tmp_path / "c", dup, archive))
    assert _codes(found) == ["L5"]


def test_log_check_l6_review_path(tmp_path: Path) -> None:
    root = _log_root(tmp_path, "## [2026-11-12] lint | 第 1 次\n"
                               ".llm-wiki/review/r.md\n")
    assert _kinds(wiki.log_check(root)) == [("L6", "提醒")]
    (root / ".llm-wiki" / "review").mkdir(parents=True)
    (root / ".llm-wiki" / "review" / "r.md").write_text("x", encoding="utf-8")
    assert wiki.log_check(root) == []


def test_log_check_l7_commits_touch_log(repo: Path) -> None:
    _log_root(repo, "## [2000-01-01] ingest | A\n")
    _git(repo, "add", "wiki")
    _git(repo, "commit", "-q", "-m", "docs(wiki): ingest A")
    assert wiki.log_check(repo) == []
    (repo / "wiki" / "a.md").write_text("b\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "docs(wiki): lint 第 2 次")
    (repo / "wiki" / "a.md").write_text("c\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "docs(wiki): lint 第 2 次 補正")
    found = wiki.log_check(repo)
    assert _kinds(found) == [("L7", "提醒")]
    assert "lint 第 2 次" in found[0]["detail"]
    assert wiki.findings_status(found) == 0


def _index_root(tmp_path: Path, top_extra: str = "", sub_extra: str = "",
                members: int = 2, read_first: str = "") -> Path:
    wk = tmp_path / "wiki"
    (wk / "music" / "topics").mkdir(parents=True)
    (wk / "music" / "synthesis").mkdir()
    (wk / "overview.md").write_text("o\n", encoding="utf-8")
    (wk / "index.md").write_text(
        "# Index\n- [Overview](overview.md)\n"
        "- [Music](music/index.md) — x（1 個主題與 1 篇跨主題整合）\n" + top_extra,
        encoding="utf-8")
    (wk / "music" / "index.md").write_text(
        "# Music\n- [主題 A](topics/a.md) — x\n"
        "- [回總目錄](../index.md)\n## 整合\n"
        "- [S](synthesis/s.md) — x\n" + sub_extra, encoding="utf-8")
    rows = "".join(f"- [M{i}](../concepts/m{i}.md) — x\n"
                   for i in range(members))
    (wk / "music" / "topics" / "a.md").write_text(
        "---\ntitle: 主題 A\n---\n# A\n" + read_first + "## 成員\n" + rows,
        encoding="utf-8")
    return tmp_path


def test_index_check_passes(tmp_path: Path) -> None:
    assert wiki.index_check(_index_root(tmp_path)) == []
    root = _index_root(
        tmp_path / "b", members=8,
        read_first="## 先讀\n1. [M0](../concepts/m0.md) — x\n"
                   "2. [M1](../concepts/m1.md) — x\n"
                   "3. [M2](../concepts/m2.md) — x\n\n")
    assert wiki.index_check(root) == []


def test_index_check_i1_top_links(tmp_path: Path) -> None:
    root = _index_root(tmp_path, top_extra="- [A](music/topics/a.md)\n")
    assert _codes(wiki.index_check(root)) == ["I1"]


def test_index_check_i2_sub_links(tmp_path: Path) -> None:
    root = _index_root(tmp_path, sub_extra="- [C](concepts/c.md)\n")
    assert _codes(wiki.index_check(root)) == ["I2"]


def test_index_check_i3_counts(tmp_path: Path) -> None:
    root = _index_root(tmp_path)
    top = root / "wiki" / "index.md"
    top.write_text(top.read_text(encoding="utf-8").replace(
        "1 個主題", "3 個主題"), encoding="utf-8")
    found = wiki.index_check(root)
    assert _codes(found) == ["I3"] and "3 個主題" in found[0]["detail"]
    top.write_text(top.read_text(encoding="utf-8").replace(
        "3 個主題與 1 篇", "1 個主題與 2 篇"), encoding="utf-8")
    assert _codes(wiki.index_check(root)) == ["I3"]
    top.write_text("- [Music](music/index.md) — 沒有數字\n", encoding="utf-8")
    assert wiki.index_check(root) == []


def test_index_check_i4_title(tmp_path: Path) -> None:
    root = _index_root(tmp_path)
    sub = root / "wiki" / "music" / "index.md"
    sub.write_text(sub.read_text(encoding="utf-8").replace(
        "[主題 A]", "[別名]"), encoding="utf-8")
    assert _codes(wiki.index_check(root)) == ["I4"]


def test_index_check_i5_read_first(tmp_path: Path) -> None:
    root = _index_root(tmp_path, members=8)
    assert _codes(wiki.index_check(root)) == ["I5"]  # missing section
    topic = root / "wiki" / "music" / "topics" / "a.md"
    base = topic.read_text(encoding="utf-8")
    topic.write_text(base.replace("## 成員", (
        "## 先讀\n1. [M0](../concepts/m0.md) — x\n"
        "2. [M1](../concepts/m1.md) — x\n\n## 成員")), encoding="utf-8")
    assert _codes(wiki.index_check(root)) == ["I5"]  # only 2 items
    topic.write_text(base.replace("## 成員", (
        "## 先讀\n1. [M0](../concepts/m0.md) — x\n"
        "2. [M1](../concepts/m1.md) — x\n"
        "3. [X](../concepts/zz.md) — x\n\n## 成員")), encoding="utf-8")
    found = wiki.index_check(root)
    assert _codes(found) == ["I5"] and "不是成員" in found[0]["detail"]


def test_check_commands_exit_status(tmp_path: Path, monkeypatch,
                                    capsys) -> None:
    root = _log_root(tmp_path, "## [2026-11-12] query | T\n")
    monkeypatch.setattr(_paths, "ROOT", root)
    assert wiki.main(["log-check"]) == 1
    assert capsys.readouterr().out.startswith("L1\t違規\twiki/log.md:3\t")
    assert wiki.main(["log-check", "--json"]) == 1
    assert '"check": "L1"' in capsys.readouterr().out
    assert wiki.main(["index-check"]) == 1  # no wiki/index.md
    assert "I1" in capsys.readouterr().out


def test_log_check_missing_log_and_git(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "wiki").mkdir()
    assert _kinds(wiki.log_check(tmp_path)) == [("L1", "違規")]

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")
    monkeypatch.setattr(wiki._common, "run_git", no_git)
    root = _log_root(tmp_path / "b", "## [2026-11-12] ingest | A\n")
    assert _kinds(wiki.log_check(root)) == [("L7", "提醒")]


def test_log_check_l5_labels_going_back(tmp_path: Path) -> None:
    log = ("## [2026-11-12] lint | 第 18 次\n\n"
           "## [2026-11-13] lint | 第 17 次\n")
    found = wiki.log_check(_log_root(tmp_path, log))
    assert _codes(found) == ["L5"] and "倒退" in found[0]["detail"]


def test_index_check_counts_distinct_internal_members(tmp_path: Path) -> None:
    root = _index_root(tmp_path, members=5)
    topic = root / "wiki" / "music" / "topics" / "a.md"
    rows = "".join(f"- [M{i}](../concepts/m{i}.md) — x\n" for i in range(5))
    urls = "".join(f"- [U{i}](https://e.com/{i}) — x\n" for i in range(5))
    topic.write_text(topic.read_text(encoding="utf-8") + "## 相關\n"
                     + rows + urls, encoding="utf-8")
    assert wiki.index_check(root) == []
