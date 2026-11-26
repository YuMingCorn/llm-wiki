import posixpath
from pathlib import Path, PurePosixPath

import lineage
import lint
from lint import Page, Wiki

OLD = "wiki/music/sources/2020-a-old.md"
MID = "wiki/music/sources/2022-b-mid.md"
NEW = "wiki/music/sources/2024-c-new.md"
OUT = "wiki/music/sources/2023-d-out.md"
CON = "wiki/music/concepts/x.md"
TOPIC = "wiki/music/topics/t.md"


def src(key, prior=None, title=None, created="2026-11-01"):
    body = f"# {title or key}\n\n"
    if prior is not None:
        body += (f"## 研究脈絡\n\n**承先**：{prior}\n\n"
                 "**這份改了什麼**：x [A](2020-a-old.md)。\n\n"
                 "**啟後**：wiki 尚無。\n\n## 機制與方法\n\n[A](2020-a-old.md)\n")
    fm = {"type": "source", "title": title or PurePosixPath(key).stem,
          "created": created}
    return lint.Page(PurePosixPath(key), fm, body, [], None)


def topic(members):
    lines = "\n".join(f"- [{m}](../sources/{PurePosixPath(m).name})"
                      for m in members)
    body = f"# T\n\n## 先讀\n\n- [X](../concepts/x.md)\n\n## 來源\n\n{lines}\n"
    return Page(PurePosixPath(TOPIC), {"type": "topic", "title": "T"}, body,
                [], None)


def wiki(*pages, members=(), root=Path(".")):
    concept = Page(PurePosixPath(CON), {"type": "concept", "title": "X"}, "",
                   [], None)
    return Wiki(root, {p.path.as_posix(): p for p in (*pages, concept)}, {},
                {TOPIC: topic(members)})


FIXED = ("\n- 接續 [A](2020-a-old.md)：卡在 X（p.1）→ 本文改為 Y（p.2）"
         "\n- 沿用 [D](2023-d-out.md)：本文沿用它的 Z（p.3）")


def test_fixed_lines_give_described_edges():
    w = wiki(src(OLD), src(OUT), src(MID, FIXED))
    edges = lineage.all_edges(w)
    assert [(e.target, e.kind, e.note) for e in edges] == [
        (OLD, "接續", "卡在 X（p.1）→ 本文改為 Y（p.2）"),
        (OUT, "沿用", "本文沿用它的 Z（p.3）"),
    ]


def test_paragraph_links_are_undescribed_and_skip_non_sources():
    prior = ("前人 [A](2020-a-old.md) 卡在 X。比較對象是 [D](2023-d-out.md)；"
             "概念見 [X](../concepts/x.md)，再提 [A](2020-a-old.md)。")
    w = wiki(src(OLD), src(OUT), src(MID, prior))
    edges = lineage.all_edges(w)
    assert [(e.target, e.kind) for e in edges] == [
        (OLD, "無說明"), (OUT, "無說明")]
    assert edges[0].note == "前人 A 卡在 X。"  # link text only, no path
    assert edges[1].note == "比較對象是 D；"


def test_block_stops_at_next_label_and_missing_section():
    w = wiki(src(OLD), src(MID, "無"), src(NEW))
    assert lineage.all_edges(w) == []  # 這份改了什麼 links are not 承先
    assert lineage.prior_block(w.pages[NEW]) is None


def test_format_problems():
    good = src(MID, FIXED)
    assert lineage.format_problems(good) == []
    assert lineage.format_problems(src(MID, "wiki 尚無")) == []
    bad = src(MID, "前人 [A](2020-a-old.md)。\n- 接續 [A](2020-a-old.md)：卡在 X")
    assert len(lineage.format_problems(bad)) == 2
    assert lineage.format_problems(src(NEW)) == []


def test_check_format_only_after_the_date(monkeypatch):
    w = wiki(src(MID, "前人 [A](2020-a-old.md)。", created="2026-12-01"),
             src(NEW, "前人 [A](2020-a-old.md)。", created="2026-11-22"),
             src(OLD))
    assert [p["page"] for p in lineage.check_format(w)] == [MID]
    monkeypatch.setattr(lineage, "FORMAT_SINCE", None)
    assert lineage.check_format(w) == []


def test_newer_targets():
    w = wiki(src(OLD, "後來的 [C](2024-c-new.md)。"), src(NEW))
    assert lineage.check_newer_targets(w) == [
        {"page": OLD, "target": NEW, "kind": "無說明"}]


def test_render_tree_review_and_lists():
    w = wiki(src(OLD), src(OUT, "[A](2020-a-old.md)。"),
             src(MID, "[A](2020-a-old.md) 是比較的 baseline。"),
             src(NEW, "\n- 接續 [B](2022-b-mid.md)：卡在 X（p.1）→ 本文改為 Y（p.2）"
                      "\n- 沿用 [A](2020-a-old.md)：本文沿用它的 Z（p.3）"),
             members=[NEW, OLD, MID])
    text = lineage.render(w, TOPIC)
    tree = text.split("## 系譜\n\n")[1].split("\n\n")[0].splitlines()
    assert tree == [
        "- 2020 [2020-a-old](../../music/sources/2020-a-old.md)",
        "  - 2022 [2022-b-mid](../../music/sources/2022-b-mid.md)｜無說明",
        "    - 2024 [2024-c-new](../../music/sources/2024-c-new.md)"
        "｜接續：卡在 X（p.1）→ 本文改為 Y（p.2）",
        "  - 2023 [2023-d-out](../../music/sources/2023-d-out.md)（其他主題）｜無說明",
        "  - 2024 [2024-c-new](../../music/sources/2024-c-new.md)"
        "｜沿用：本文沿用它的 Z（p.3）（見上）",
    ]
    assert "句子像比較對象" in text
    no_ctx = text.split("## 尚無研究脈絡的來源\n\n")[1].split("\n\n")[0]
    assert no_ctx == "- [2020-a-old](../../music/sources/2020-a-old.md)"
    assert "[2022-b-mid](../../music/sources/2022-b-mid.md)：1 條" in text
    assert lineage.render(w, TOPIC) == text  # deterministic


def test_render_links_resolve_from_lineage_dir():
    w = wiki(src(OLD), src(MID, "[A](2020-a-old.md)。"), members=[OLD, MID])
    text = lineage.render(w, TOPIC)
    targets = [m.group(2) for m in lint.LINK_RE.finditer(text)]
    assert targets
    for t in targets:
        key = posixpath.normpath(posixpath.join(lineage.LINEAGE_DIR, t))
        assert key in w.pages or key in w.topics, t


def test_outside_page_repeat_and_outside_root_after_being_shown():
    # OUT (outside) builds on OLD and MID; NEW (member) builds on OUT.
    w = wiki(src(OLD), src(MID, "[A](2020-a-old.md)。"),
             src(OUT, "[A](2020-a-old.md)、[B](2022-b-mid.md)。"),
             src(NEW, "[D](2023-d-out.md)。"), members=[OLD, MID, NEW])
    tree = lineage.render(w, TOPIC).split("## 系譜\n\n")[1].split("\n\n")[0]
    out_lines = [ln for ln in tree.splitlines() if "2023-d-out" in ln]
    assert out_lines[1].endswith("（見上）")
    assert "- 2023 [2023-d-out](../../music/sources/2023-d-out.md)（其他主題）" \
        in tree.splitlines()  # still a root, so NEW shows under it
    assert "2024-c-new" in tree


def test_cycle_does_not_loop():
    a = src(OLD, "[B](2022-b-mid.md)。")
    b = src(MID, "[A](2020-a-old.md)。")
    text = lineage.render(wiki(a, b, members=[OLD, MID]), TOPIC)
    assert "（循環）" in text


def test_write_and_check_stale(tmp_path):
    w = wiki(src(OLD), members=[OLD], root=tmp_path)
    assert lineage.check_stale(w) == []  # no folder yet: nothing to report
    assert lineage.write(w, [TOPIC]) == [("wiki/meta/lineage/t.md", True)]
    assert lineage.write(w, [TOPIC]) == [("wiki/meta/lineage/t.md", False)]
    assert lineage.check_stale(w) == []
    (tmp_path / "wiki/meta/lineage/t.md").write_text("old", encoding="utf-8")
    (tmp_path / "wiki/meta/lineage/gone.md").write_text("", encoding="utf-8")
    assert {p["problem"] for p in lineage.check_stale(w)} == {
        "stale", "no_topic"}


def test_resolve_topics():
    w = wiki(src(OLD))
    assert lineage.resolve_topics(w, []) == [TOPIC]
    assert lineage.resolve_topics(w, ["t", TOPIC]) == [TOPIC, TOPIC]
    try:
        lineage.resolve_topics(w, ["nope"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
