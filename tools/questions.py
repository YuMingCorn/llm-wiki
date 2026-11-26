"""Question log: what the user asked and whether the wiki could answer it.

Each /wiki-query and /wiki-study run ends with one line in
``.llm-wiki/memory/questions.jsonl`` (local, not in git)::

    {"date": "2026-11-17", "kind": "query", "question": "對位法怎麼學",
     "result": "partial", "gap": "missing-source",
     "pages": ["wiki/music/concepts/harmony.md"], "note": "缺現場錄音的資料"}

``result`` says how well the wiki answered: ``answered``, ``partial`` or
``unanswered``. Anything short of ``answered`` names one ``gap``, the main
reason; an ``answered`` question may still name ``search-miss`` when the
answer was found only by browsing or grep. Each kind of gap leads to a
different fix:

- ``missing-source``: no wiki page has the evidence. Fix: find a source
  (``MISSING_SOURCES.md``) and /wiki-ingest it.
- ``unclear``: the page has the evidence, but the user could not follow it,
  or the answer needed explanations the page lacks. Fix: rewrite the page.
- ``search-miss``: the page has the answer, but ``search.py`` did not rank
  it in the top 5. ``pages`` names the answer pages, and the question
  becomes a ``tools/search_eval.tsv`` candidate.

``result`` and ``gap`` are ASCII words so that no shell encoding can garble
them. The file only grows; this tool only appends and prints.

Usage:
  uv run python -I tools/questions.py record "<question>"
      <answered|partial|unanswered> [--kind query|study]
      [--gap missing-source|unclear|search-miss] [--pages <page ...>]
      [--note "<what is missing>"]   (one command line; wrapped here)
  uv run python -I tools/questions.py report [--since YYYY-MM-DD] [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths  # noqa: E402
import lint  # noqa: E402

QUESTION_LOG = ".llm-wiki/memory/questions.jsonl"
SEARCH_EVAL = "tools/search_eval.tsv"
KINDS = ("query", "study")
RESULTS = ("answered", "partial", "unanswered")
GAPS = ("missing-source", "unclear", "search-miss")


def _key(path: str) -> str:
    return Path(path).as_posix().removeprefix("./")


def _one_line(text: str) -> str:
    """Collapses tabs and newlines so the text fits one TSV field."""
    return " ".join(text.split())


def record(root: Path, question: str, result: str, kind: str = "query",
           gap: str | None = None, pages: Sequence[str] = (),
           note: str = "", today: dt.date | None = None) -> dict:
    """Appends one question.

    Raises:
        ValueError: The question is empty; the kind, result or gap is
            unknown; an answered question has a gap or a partial or
            unanswered one has none; a search-miss names no page; or a page
            is not a content page.
    """
    question = _one_line(question)
    if not question:
        raise ValueError("問題不能是空的")
    if kind not in KINDS:
        raise ValueError(f"種類要是 {'、'.join(KINDS)} 之一：{kind}")
    if result not in RESULTS:
        raise ValueError(f"結果要是 {'、'.join(RESULTS)} 之一：{result}")
    if gap is not None and gap not in GAPS:
        raise ValueError(f"缺口要是 {'、'.join(GAPS)} 之一：{gap}")
    if result == "answered" and gap not in (None, "search-miss"):
        raise ValueError("answered 只能帶 search-miss 缺口")
    if result != "answered" and gap is None:
        raise ValueError(f"{result} 要用 --gap 寫出主要缺口")
    keys = [_key(p) for p in pages]
    if gap == "search-miss" and not keys:
        raise ValueError("search-miss 要用 --pages 列出答案所在的頁")
    if keys:
        wiki = lint.load(root)
        missing = [k for k in keys if k not in wiki.pages]
        if missing:
            raise ValueError(f"不是內容頁：{'、'.join(missing)}")
    entry = {"date": (today or dt.date.today()).isoformat(), "kind": kind,
             "question": question, "result": result, "gap": gap,
             "pages": keys, "note": _one_line(note)}
    path = root / QUESTION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load(root: Path, since: dt.date | None = None) -> list[dict]:
    """Reads the log in file order; invalid lines are skipped."""
    path = root / QUESTION_LOG
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("result") not in RESULTS:
            continue
        if not entry.get("question") or not isinstance(entry.get("date"),
                                                       str):
            continue
        if not isinstance(entry.get("pages"), list):
            entry["pages"] = []
        if since and entry["date"] < since.isoformat():
            continue
        out.append(entry)
    return out


def _eval_questions(root: Path) -> set[str]:
    path = root / SEARCH_EVAL
    if not path.is_file():
        return set()
    return {_one_line(line.split("\t", 1)[0])
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.startswith("#")}


def report(root: Path, since: dt.date | None = None) -> dict:
    """Summarizes the log.

    Returns:
        {"total": n, "results": {result: n}, "gaps": {gap: n},
         "kinds": {kind: n},
         "missing_source": [entries], "unclear": [entries],
         "eval_candidates": ["question<TAB>page ..."]}.
        ``eval_candidates`` are search misses whose question is not yet in
        ``tools/search_eval.tsv``, each question once.
    """
    entries = load(root, since)
    in_eval = _eval_questions(root)
    candidates: dict[str, list[str]] = {}
    for e in entries:
        if e.get("gap") == "search-miss" and e["question"] not in in_eval:
            pages = candidates.setdefault(e["question"], [])
            pages.extend(p for p in e["pages"] if p not in pages)
    return {
        "total": len(entries),
        "results": {r: sum(e["result"] == r for e in entries)
                    for r in RESULTS},
        "gaps": {g: sum(e.get("gap") == g for e in entries) for g in GAPS},
        "kinds": dict(Counter(e.get("kind") for e in entries)),
        "missing_source": [e for e in entries
                           if e.get("gap") == "missing-source"],
        "unclear": [e for e in entries if e.get("gap") == "unclear"],
        "eval_candidates": [f"{q}\t{' '.join(pages)}"
                            for q, pages in candidates.items() if pages],
    }


def _line(e: dict) -> str:
    note = f"｜{e['note']}" if e.get("note") else ""
    pages = f"｜{' '.join(e['pages'])}" if e.get("pages") else ""
    return f"{e['date']}\t{e.get('kind')}\t{e['question']}{note}{pages}"


def _print_report(rep: dict) -> None:
    if not rep["total"]:
        print("沒有問題紀錄")
        return
    r, g = rep["results"], rep["gaps"]
    print(f"共 {rep['total']} 題：答得出 {r['answered']}、部分 "
          f"{r['partial']}、答不出 {r['unanswered']}")
    print(f"缺口：缺來源 {g['missing-source']}、頁面難懂 {g['unclear']}、"
          f"搜尋沒找到 {g['search-miss']}")
    if rep["missing_source"]:
        print("\n缺來源（考慮寫進 MISSING_SOURCES.md）：")
        for e in rep["missing_source"]:
            print(_line(e))
    if rep["unclear"]:
        print("\n頁面難懂（考慮改寫）：")
        for e in rep["unclear"]:
            print(_line(e))
    if rep["eval_candidates"]:
        print(f"\n搜尋沒找到，可以加進 {SEARCH_EVAL}：")
        for row in rep["eval_candidates"]:
            print(row)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="save one question and its outcome")
    rec.add_argument("question")
    rec.add_argument("result", choices=RESULTS)
    rec.add_argument("--kind", choices=KINDS, default="query")
    rec.add_argument("--gap", choices=GAPS)
    rec.add_argument("--pages", nargs="+", default=[])
    rec.add_argument("--note", default="")
    rec.add_argument("--date", type=dt.date.fromisoformat)
    rep = sub.add_parser("report", help="summarize questions and gaps")
    rep.add_argument("--since", type=dt.date.fromisoformat)
    rep.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = _paths.ROOT
    try:
        if args.command == "record":
            entry = record(root, args.question, args.result, args.kind,
                           args.gap, args.pages, args.note, args.date)
            gap = f"，缺口 {entry['gap']}" if entry["gap"] else ""
            print(f"已記錄：{entry['result']}{gap}")
        else:
            result = report(root, args.since)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                _print_report(result)
    except ValueError as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
