"""Learning state from /wiki-study: what the user knows and what to reread.

Each answered question becomes one line in
``.llm-wiki/memory/learning.jsonl`` (local, not in git)::

    {"date": "2026-11-16", "topic": "wiki/music/topics/jazz-history.md",
     "study": "wiki/music/queries/2026-11-16-study-jazz-history.md",
     "question": 3, "result": "partial", "pages": ["wiki/music/concepts/x.md"],
     "kind": "check", "note": "把 A 當成 B 的原因"}

``pages`` are the pages the answer is based on. ``kind`` says where the
question came from: ``quiz`` (the 10-question quiz; the default, not
written), ``probe`` (a short question on a prerequisite page before the
quiz) or ``check`` (the check question that ends one remedial step).
``note`` is optional: one line on the wrong idea the answer showed.

Results are ASCII words so that no shell encoding can garble them:
``right``, ``partial``, ``wrong`` and ``skipped`` (not answered). A miss
weighs 1 (wrong, skipped), 0.5 (partial) or 0 (right). A page's weakness is
the mean miss weight of its last RECENT_RESULTS results of any kind, so
answering later questions right clears it.

The file only grows. Grading the same question of the same study page again
replaces the earlier result. Each kind numbers its questions on its own, so
a probe or check question never replaces a quiz question; pass ``next`` as
the question number of a probe or check to take the next free number of that
topic, kind and day. Pages that were moved or deleted since are
reported as missing and never recommended.

This tool only prints. It does not edit topic pages or their 先讀 lists.

Usage:
  uv run python -I tools/learning.py record <topic page> <question|next>
      <right|partial|wrong|skipped> <page ...> [--study <study page>]
      [--kind quiz|probe|check] [--note "<the wrong idea, one line>"]
      (one command line; wrapped here)
  uv run python -I tools/learning.py status [--topic <topic page>] [--json]
  uv run python -I tools/learning.py next [--topic <topic page>] [-n 5]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths  # noqa: E402
import lint  # noqa: E402

LEARNING_LOG = ".llm-wiki/memory/learning.jsonl"
MISS_WEIGHT = {"right": 0.0, "partial": 0.5, "wrong": 1.0, "skipped": 1.0}
RESULTS = tuple(MISS_WEIGHT)
RECENT_RESULTS = 3
KINDS = ("quiz", "probe", "check")
KIND_LABEL = {"quiz": "", "probe": "探底 ", "check": "補弱 "}
NOTE_MAX = 200
NOTES_SHOWN = 2
DEFAULT_NEXT = 5
CONCEPT_LIKE = frozenset({"concept", "entity", "synthesis", "comparison"})
_FIRST_READ_RE = re.compile(r"^##\s+先讀\s*$(.*?)(?=^#{1,2}\s|\Z)", re.M | re.S)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _key(path: str) -> str:
    return Path(path).as_posix().removeprefix("./")


def _title(page: lint.Page | None, key: str) -> str:
    if page is None:
        return key
    return str((page.frontmatter or {}).get("title") or key)


def record(root: Path, topic: str, question: int | None, result: str,
           pages: Sequence[str], study: str | None = None,
           today: dt.date | None = None, kind: str = "quiz",
           note: str | None = None) -> dict:
    """Appends one answered question.

    Args:
        root: The repo root.
        topic: The topic page the question belongs to.
        question: The question number. None takes the next free number of
            this topic, kind and day; only probe and check questions may
            use it, because quiz questions have fixed numbers.
        result: One of RESULTS.
        pages: The pages the answer is based on.
        study: The saved study page of a quiz question, if any.
        today: The date to record; defaults to today.
        kind: One of KINDS.
        note: One line on the wrong idea the answer showed; whitespace is
            collapsed and an empty note is dropped.

    Raises:
        ValueError: The result or kind is unknown, the question number is
            not positive (or missing for a quiz question), a probe or check
            names a study page, the note is too long, the topic is not a
            topic page, or a page (or the study page) is not a content page.
    """
    if result not in RESULTS:
        raise ValueError(f"結果要是 {'、'.join(RESULTS)} 之一：{result}")
    if kind not in KINDS:
        raise ValueError(f"--kind 要是 {'、'.join(KINDS)} 之一：{kind}")
    if question is None and kind == "quiz":
        raise ValueError("測驗題要給題號，next 只給探底與補弱用")
    if question is not None and question < 1:
        raise ValueError("題號要是正整數")
    if kind != "quiz" and study is not None:
        raise ValueError("探底與補弱不屬於 study 頁，不加 --study")
    note = " ".join(note.split()) if note else None
    if note and len(note) > NOTE_MAX:
        raise ValueError(f"--note 最多 {NOTE_MAX} 字，現在 {len(note)} 字")
    wiki = lint.load(root)
    topic = _key(topic)
    if topic not in wiki.topics:
        raise ValueError(f"不是主題頁：{topic}")
    keys = list(dict.fromkeys(_key(p) for p in pages))
    if not keys:
        raise ValueError("至少要列一個答案依據頁")
    missing = [k for k in keys if k not in wiki.pages]
    if study is not None:
        study = _key(study)
        if study not in wiki.pages:
            missing.append(study)
    if missing:
        raise ValueError(f"不是內容頁：{'、'.join(missing)}")
    date = (today or dt.date.today()).isoformat()
    if question is None:
        question = 1 + max(
            (e["question"] for e in load_results(root)
             if (e["topic"], e["kind"], e["date"]) == (topic, kind, date)),
            default=0)
    entry = {"date": date, "topic": topic, "study": study,
             "question": question, "result": result, "pages": keys}
    if kind != "quiz":
        entry["kind"] = kind
    if note:
        entry["note"] = note
    path = root / LEARNING_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_results(root: Path) -> list[dict]:
    """Reads the log; a later grading of the same question wins.

    Lines that are not valid entries are skipped. The list keeps the order
    of first grading, which is also date order for an append-only file.
    Every entry gets a ``kind`` (``quiz`` when the line has none); a
    ``note`` that is not text is dropped.
    """
    path = root / LEARNING_LOG
    if not path.is_file():
        return []
    latest: dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("result") not in RESULTS:
            continue
        if not isinstance(entry.get("date"), str):
            continue
        if not isinstance(entry.get("topic"), str) or not isinstance(
                entry.get("pages"), list):
            continue
        question = entry.get("question")
        if not isinstance(question, int) or isinstance(question, bool):
            continue
        if not isinstance(
                entry.get("study"), str | None):
            continue
        kind = entry.get("kind", "quiz")
        if kind not in KINDS:
            continue
        entry["kind"] = kind
        if not isinstance(entry.get("note"), str):
            entry.pop("note", None)
        entry["pages"] = [p for p in entry["pages"] if isinstance(p, str)]
        same = (entry["topic"], entry.get("study"), entry.get("question"),
                kind)
        if entry.get("study") is None:
            same = (*same, entry.get("date"))
        latest[same] = entry
    return list(latest.values())


def weak_pages(root: Path, topic: str | None = None) -> list[dict]:
    """Ranks pages by weakness, weakest first.

    Ties go to the page behind more missed questions, then to concept-like
    pages before source pages (a concept page covers more ground), then to
    the most recent miss.

    Returns:
        Rows {page, title, type, weakness, last_miss, questions, studies,
        notes, exists}; only pages with weakness above 0. ``questions``
        lists the missed question numbers as "#N" (probe and check questions
        carry their label); ``studies`` names their study pages; ``notes``
        holds the page's latest NOTES_SHOWN notes, newest first.
    """
    wiki = lint.load(root)
    topic = _key(topic) if topic else None
    history: dict[str, list[dict]] = {}
    for entry in load_results(root):
        if topic and entry["topic"] != topic:
            continue
        for page in entry["pages"]:
            history.setdefault(page, []).append(entry)
    rows = []
    for page, entries in history.items():
        entries.sort(key=lambda e: str(e.get("date")))
        recent = entries[-RECENT_RESULTS:]
        weakness = sum(MISS_WEIGHT[e["result"]] for e in recent) / len(recent)
        if weakness <= 0:
            continue
        misses = [e for e in entries if MISS_WEIGHT[e["result"]] > 0]
        notes = list(dict.fromkeys(
            e["note"] for e in reversed(entries) if e.get("note")))
        info = wiki.pages.get(page)
        rows.append({
            "page": page, "title": _title(info, page),
            "type": info.type if info else None,
            "weakness": round(weakness, 2),
            "last_miss": max(str(e.get("date")) for e in misses),
            "questions": [f"{KIND_LABEL[e['kind']]}#{e.get('question')}"
                          for e in misses],
            "studies": sorted({Path(str(e["study"])).stem for e in misses
                               if e.get("study")}),
            "notes": notes[:NOTES_SHOWN],
            "exists": info is not None})
    rows.sort(key=lambda r: r["page"])
    rows.sort(key=lambda r: r["last_miss"], reverse=True)
    rows.sort(key=lambda r: (-r["weakness"], -len(r["questions"]),
                             r["type"] not in CONCEPT_LIKE))
    return rows


def status(root: Path) -> list[dict]:
    """Summarizes each topic page: when studied and how the answers went.

    Quiz, probe and check questions are counted together.

    Returns:
        One row per topic page, studied topics first:
        {topic, title, last, graded, counts: {result: n}}. ``last`` is None
        for topics never studied.
    """
    wiki = lint.load(root)
    rows: dict[str, dict] = {
        key: {"topic": key, "title": _title(page, key), "last": None,
              "graded": 0, "counts": dict.fromkeys(RESULTS, 0)}
        for key, page in wiki.topics.items()}
    for entry in load_results(root):
        row = rows.setdefault(entry["topic"], {
            "topic": entry["topic"], "title": entry["topic"] + "（已不存在）",
            "last": None, "graded": 0, "counts": dict.fromkeys(RESULTS, 0)})
        row["graded"] += 1
        row["counts"][entry["result"]] += 1
        row["last"] = max(filter(None, (row["last"], str(entry["date"]))))
    return sorted(rows.values(),
                  key=lambda r: (r["last"] is None, r["last"] or "",
                                 r["topic"]))


def first_read(root: Path, topic: str) -> list[dict]:
    """Returns the topic page's 先讀 list as [{page, title}]."""
    key = _key(topic)
    wiki = lint.load(root)
    page = wiki.topics.get(key)
    if page is None:
        raise ValueError(f"不是主題頁：{key}")
    match = _FIRST_READ_RE.search(page.body)
    if not match:
        return []
    base = key.rsplit("/", 1)[0]
    out = []
    for title, target in _LINK_RE.findall(match.group(1)):
        resolved = lint._resolve(base, target)
        if resolved:
            out.append({"page": resolved, "title": title})
    return out


def next_pages(root: Path, topic: str | None = None,
               limit: int = DEFAULT_NEXT) -> dict:
    """Recommends what to read next.

    Returns:
        {"weak": rows from weak_pages that still exist, at most ``limit``,
         "first_read": the topic's 先讀 list when a topic is given and has
         no graded questions yet, otherwise [],
         "unstudied": titles of topic pages with no graded questions}.
    """
    if limit < 1:
        raise ValueError("-n 要是正整數")
    weak = [r for r in weak_pages(root, topic) if r["exists"]][:limit]
    rows = status(root)
    unstudied = [r["title"] for r in rows if r["last"] is None]
    first: list[dict] = []
    if topic:
        studied = any(r["topic"] == _key(topic) and r["graded"] for r in rows)
        if not studied:
            first = first_read(root, topic)
    return {"weak": weak, "first_read": first, "unstudied": unstudied}


def _questions(row: dict) -> str:
    where = f"（{'、'.join(row['studies'])}）" if row["studies"] else ""
    return f"題目 {'、'.join(row['questions'])}{where}"


def _print_status(root: Path, topic: str | None) -> None:
    rows = status(root)
    if topic:
        rows = [r for r in rows if r["topic"] == _key(topic)]
    studied = [r for r in rows if r["last"]]
    for r in studied:
        c = r["counts"]
        print(f"{r['last']}\t{r['graded']} 題\t對 {c['right']}、部分 "
              f"{c['partial']}、錯 {c['wrong']}、沒答 {c['skipped']}\t"
              f"{r['title']}（{r['topic']}）")
    never = [r["title"] for r in rows if not r["last"]]
    if never:
        print(f"還沒有作答紀錄：{'、'.join(never)}")
    weak = weak_pages(root, topic)
    if weak:
        print("\n弱點頁（弱點分數 0–1，越高越需要重讀）：")
        for r in weak:
            gone = "" if r["exists"] else "（頁面已不存在）"
            print(f"{r['weakness']:.2f}\t{r['title']}{gone}\t{r['page']}\t"
                  f"{_questions(r)}")
            for note in r["notes"]:
                print(f"\t誤解：{note}")


def _print_next(result: dict) -> None:
    if result["weak"]:
        print("先重讀答錯或沒答的題目依據的頁：")
        for i, r in enumerate(result["weak"], 1):
            print(f"{i}. {r['title']}（{r['page']}）— "
                  f"弱點 {r['weakness']:.2f}，{_questions(r)}")
            for note in r["notes"]:
                print(f"   誤解：{note}")
    if result["first_read"]:
        print("這個主題還沒有作答紀錄，照主題頁的「先讀」：")
        for i, r in enumerate(result["first_read"], 1):
            print(f"{i}. {r['title']}（{r['page']}）")
    if not result["weak"] and not result["first_read"]:
        print("沒有弱點頁")
    if result["unstudied"]:
        print(f"還沒有作答紀錄的主題：{'、'.join(result['unstudied'])}")


def _question(text: str) -> int | None:
    """Parses the question argument: a number, or "next" for the next one."""
    return None if text == "next" else int(text)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="save one answered question")
    rec.add_argument("topic")
    rec.add_argument("question", type=_question, metavar="question|next")
    rec.add_argument("result", choices=RESULTS)
    rec.add_argument("pages", nargs="+")
    rec.add_argument("--study")
    rec.add_argument("--date", type=dt.date.fromisoformat)
    rec.add_argument("--kind", choices=KINDS, default="quiz")
    rec.add_argument("--note")
    st = sub.add_parser("status", help="answers per topic and weak pages")
    st.add_argument("--topic")
    st.add_argument("--json", action="store_true")
    nx = sub.add_parser("next", help="what to read next")
    nx.add_argument("--topic")
    nx.add_argument("-n", type=int, default=DEFAULT_NEXT)
    nx.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = _paths.ROOT
    try:
        if args.command == "record":
            entry = record(root, args.topic, args.question, args.result,
                           args.pages, args.study, args.date, args.kind,
                           args.note)
            print(f"已記錄{KIND_LABEL[args.kind].strip()}第 "
                  f"{entry['question']} 題：{entry['result']}，"
                  f"{len(entry['pages'])} 頁")
        elif args.command == "status":
            if args.json:
                print(json.dumps({"topics": status(root),
                                  "weak": weak_pages(root, args.topic)},
                                 ensure_ascii=False, indent=1))
            else:
                _print_status(root, args.topic)
        else:
            result = next_pages(root, args.topic, args.n)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                _print_next(result)
    except ValueError as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
