r"""Renders a /wiki-study result as one local, self-contained HTML page.

The main conversation writes the study as JSON (format below); this tool
turns it into a quiet, readable page with a fixed layout, so every study
page looks the same. The page stays on this computer: it is written next to
the JSON under ``.llm-wiki/study/`` (not in git). Only the Mermaid drawing
library is fetched from a CDN; without network the page shows the plain
indented tree instead.

Usage (from the repo root):
  uv run python -I tools/study_html.py build <study.json> [--open]

``build`` validates the JSON, writes ``<same name>.html`` and prints three
lines: the page path, the dispute titles and the coverage. ``--open`` opens
the page in the default browser.

JSON format (fields marked * are required)::

  {
    "title"*: "AI agent 知識地圖",
    "topic"*: "wiki/music/topics/jazz-history.md",
    "date"*: "2026-11-20",
    "coverage"*: "answered" | "partial",
    "coverage_note": "缺什麼",
    "scope": {"read": [<path>], "unread": [<path>], "note": "…"},
    "map"*: {
      "summary": "…",
      "mermaid": "flowchart TD\\n  A[Scale] --> B[Chord loop]",
      "tree": "plain-text indented tree (fallback for Mermaid)",
      "layers"*: [{"name": "底層概念",
                   "items": [{"title": "…", "text": "…",
                              "tag": "推論" | "單一來源" | "解說",
                              "refs": [<ref>]}]}],
      "advice": "…"
    },
    "disputes"*: [{"title": "…", "question": "爭的是什麼",  # [] if none
                   "sides": [{"label": "說不可靠", "text": "…",
                              "refs": [<ref>]}],
                   "assumptions": [{"text": "…", "tag": "推論",
                                    "refs": [<ref>]}]}],
    "minor_disputes": [{"text": "…", "refs": [<ref>]}],
    "gaps": ["…"],
    "quiz"*: [{"category": "核心概念" | "因果關係" | "實際應用" | "主要爭議",
               "question": "…"}],
    "grading": [{"answer": "使用者的作答", "result": "right" | "partial" |
                 "wrong" | "skipped", "comment": "哪裡對、哪裡要修正",
                 "solution": "附出處的解答", "refs": [<ref>]}],
    "next": [{"text": "建議接著讀的頁與理由", "refs": [<ref>]}]
  }

A ``<ref>`` is ``{"path": "wiki/music/concepts/chord-loop.md", "loc": "p.9"}``
(``loc`` optional). Refs become ``obsidian://`` links into this vault, labeled
with the page's frontmatter title. ``grading`` is aligned with ``quiz`` by
position. Text fields accept ``**bold**`` and ``code`` in backticks; a blank
line starts a new paragraph.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _paths import ROOT  # noqa: E402  (also applies _limits)

ASSETS = Path(__file__).resolve().parent / "study_assets"

CATEGORIES = ("核心概念", "因果關係", "實際應用", "主要爭議")
TAGS = {"推論": "infer", "單一來源": "single", "解說": "explain"}
RESULTS = {"right": "全對", "partial": "部分對", "wrong": "錯",
           "skipped": "沒答"}
COVERAGE = {"answered": "三部分都有出處", "partial": "部分覆蓋"}
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.M)


class StudyError(ValueError):
    """The study JSON is missing a field or has a wrong value."""


def _need(obj: dict, key: str, where: str) -> object:
    if key not in obj or obj[key] in (None, "", []):
        raise StudyError(f"{where} 缺少 {key}")
    return obj[key]


def validate(data: dict) -> list[str]:
    """Checks the study JSON.

    Args:
        data: The parsed JSON.

    Returns:
        Warnings that do not stop the build (e.g. a missing quiz category).

    Raises:
        StudyError: A required field is missing or a value is not allowed.
    """
    for key in ("title", "topic", "date", "coverage", "map", "quiz"):
        _need(data, key, "study")
    if not isinstance(data.get("disputes"), list):
        raise StudyError("study 缺少 disputes（沒有爭議時寫空清單 []）")
    if data["coverage"] not in COVERAGE:
        raise StudyError(f"coverage 只能是 {'/'.join(COVERAGE)}")
    for i, layer in enumerate(_need(data["map"], "layers", "map")):
        _need(layer, "name", f"map.layers[{i}]")
        for j, item in enumerate(_need(layer, "items", f"map.layers[{i}]")):
            _need(item, "text", f"map.layers[{i}].items[{j}]")
            if item.get("tag") and item["tag"] not in TAGS:
                raise StudyError(f"tag 只能是 {'/'.join(TAGS)}")
    for i, dispute in enumerate(data["disputes"]):
        _need(dispute, "title", f"disputes[{i}]")
        for j, side in enumerate(_need(dispute, "sides", f"disputes[{i}]")):
            _need(side, "label", f"disputes[{i}].sides[{j}]")
            _need(side, "text", f"disputes[{i}].sides[{j}]")
    for i, q in enumerate(data["quiz"]):
        _need(q, "question", f"quiz[{i}]")
        if q.get("category") not in CATEGORIES:
            allowed = "/".join(CATEGORIES)
            raise StudyError(f"quiz[{i}].category 只能是 {allowed}")
    grading = data.get("grading") or []
    if grading and len(grading) != len(data["quiz"]):
        raise StudyError("grading 的題數要和 quiz 一樣")
    for i, g in enumerate(grading):
        if g.get("result") not in RESULTS:
            raise StudyError(f"grading[{i}].result 只能是 {'/'.join(RESULTS)}")
    for ref in _all_refs(data):
        if not isinstance(ref.get("path"), str) or not ref["path"]:
            raise StudyError(f"出處缺少 path：{ref}")
    for tag in _all_tags(data):
        if tag not in TAGS:
            raise StudyError(f"tag 只能是 {'/'.join(TAGS)}，不是 {tag}")
    warnings = []
    if data["map"].get("mermaid") and not data["map"].get("tree"):
        warnings.append("有 mermaid 但沒有 tree：沒網路時關係圖會是空的")
    missing = [c for c in CATEGORIES
               if sum(q["category"] == c for q in data["quiz"]) < 2]
    if missing:
        warnings.append(f"這些類別少於 2 題：{'、'.join(missing)}")
    paths = {ref["path"] for ref in _all_refs(data)} | {data["topic"]}
    scope = data.get("scope") or {}
    paths |= set(scope.get("read", [])) | set(scope.get("unread", []))
    for path in sorted(paths):
        if not (ROOT / path).is_file():
            warnings.append(f"出處找不到檔案：{path}")
    return warnings


def _all_refs(node: object) -> list[dict]:
    """Every ref dict (an item of a ``refs`` list) anywhere in the JSON."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "refs" and isinstance(value, list):
                found += [v if isinstance(v, dict) else {} for v in value]
            else:
                found += _all_refs(value)
    elif isinstance(node, list):
        for value in node:
            found += _all_refs(value)
    return found


def _all_tags(node: object) -> list[str]:
    """Every non-empty ``tag`` value anywhere in the JSON."""
    found = []
    if isinstance(node, dict):
        if node.get("tag"):
            found.append(node["tag"])
        for value in node.values():
            found += _all_tags(value)
    elif isinstance(node, list):
        for value in node:
            found += _all_tags(value)
    return found



def _page_title(path: str) -> str:
    try:
        head = (ROOT / path).read_text(encoding="utf-8")[:2000]
    except OSError:
        return Path(path).stem
    match = _TITLE_RE.search(head)
    return match.group(1).strip("\"'") if match else Path(path).stem


def short_title(path: str, limit: int = 28) -> str:
    """Page title cut before a subtitle (colon or bracket), for ref labels."""
    title = _page_title(path)
    for sep in ("：", ": ", "（", " ("):
        head = title.split(sep, 1)[0].strip()
        if len(head) >= 3:
            title = head
    return title if len(title) <= limit else title[:limit - 1] + "…"


def obsidian_url(path: str) -> str:
    """Returns an obsidian:// link that opens a repo file in this vault."""
    query = urllib.parse.urlencode({"vault": ROOT.name, "file": path},
                                   quote_via=urllib.parse.quote)
    return f"obsidian://open?{query}"


def inline(text: str) -> str:
    """Escapes text and applies **bold** and `code`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def paragraphs(text: str, cls: str = "") -> str:
    """Turns text into <p> blocks; a blank line starts a new one."""
    attr = f' class="{cls}"' if cls else ""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f"<p{attr}>{inline(p).replace(chr(10), '<br>')}</p>"
                   for p in parts)


def refs_html(refs: list[dict] | None) -> str:
    """Renders refs as small obsidian:// links."""
    if not refs:
        return ""
    links = []
    for ref in refs:
        label = html.escape(short_title(ref["path"]))
        loc = f" {html.escape(ref['loc'])}" if ref.get("loc") else ""
        links.append(f'<a href="{html.escape(obsidian_url(ref["path"]))}">'
                     f"{label}</a>{loc}")
    return f'<span class="refs">{"；".join(links)}</span>'


def tag_html(tag: str | None) -> str:
    """Renders a 推論／單一來源／解說 label."""
    if not tag:
        return ""
    return f'<span class="tag tag-{TAGS[tag]}">{html.escape(tag)}</span>'


def _text_with_refs(text: str, refs: list[dict] | None,
                    tag: str | None = None) -> str:
    body = inline(text).replace("\n", "<br>")
    return f"<p>{tag_html(tag)}{body} {refs_html(refs)}</p>"


def _map_section(m: dict) -> str:
    parts = ['<section id="map"><h2><span class="num">一</span>核心共識'
             '<small>知識地圖</small></h2>']
    if m.get("summary"):
        parts.append(f'<div class="lede">{paragraphs(m["summary"])}</div>')
    if m.get("mermaid") or m.get("tree"):
        parts.append('<figure class="diagram">')
        if m.get("mermaid"):
            parts.append(f'<pre class="mermaid" hidden>'
                         f'{html.escape(m["mermaid"])}</pre>')
        if m.get("tree"):
            parts.append(f'<pre class="tree">{html.escape(m["tree"])}</pre>')
        parts.append("</figure>")
    for layer in m["layers"]:
        parts.append(f'<h3>{html.escape(layer["name"])}</h3><ul class="items">')
        for item in layer["items"]:
            head = tag_html(item.get("tag"))
            if item.get("title"):
                head = (f'<div class="item-title"><strong>'
                        f'{inline(item["title"])}</strong>{head}</div>')
            body = inline(item["text"]).replace("\n", "<br>")
            parts.append(f"<li>{head}{body} "
                         f"{refs_html(item.get('refs'))}</li>")
        parts.append("</ul>")
    if m.get("advice"):
        parts.append(f'<aside class="note"><b>時間怎麼分配</b>'
                     f'{paragraphs(m["advice"])}</aside>')
    parts.append("</section>")
    return "".join(parts)


def _dispute_section(disputes: list[dict], minor: list[dict]) -> str:
    parts = ['<section id="disputes"><h2><span class="num">二</span>根本爭議'
             f"<small>{len(disputes)} 個</small></h2>"]
    if not disputes:
        parts.append('<p class="question">wiki 找不到有出處的爭議，'
                     "缺的來源見下方。</p>")
    for n, d in enumerate(disputes, 1):
        parts.append(f'<article class="dispute"><h3><span class="dn">{n}</span>'
                     f'{inline(d["title"])}</h3>')
        if d.get("question"):
            parts.append(paragraphs(d["question"], "question"))
        parts.append('<div class="sides">')
        for side in d["sides"]:
            parts.append(f'<div class="side"><div class="side-label">'
                         f'{inline(side["label"])}</div>'
                         f'{_text_with_refs(side["text"], side.get("refs"))}'
                         "</div>")
        parts.append("</div>")
        if d.get("assumptions"):
            parts.append('<div class="assume"><b>隱藏假設</b><ul>')
            for a in d["assumptions"]:
                parts.append(f"<li>{tag_html(a.get('tag'))}"
                             f"{inline(a['text'])} {refs_html(a.get('refs'))}"
                             "</li>")
            parts.append("</ul></div>")
        parts.append("</article>")
    if minor:
        parts.append('<h3 class="minor">其他較小的分歧</h3><ul class="items">')
        for m in minor:
            parts.append(f"<li>{inline(m['text'])} {refs_html(m.get('refs'))}"
                         "</li>")
        parts.append("</ul>")
    parts.append("</section>")
    return "".join(parts)


def _quiz_section(quiz: list[dict]) -> str:
    parts = ['<section id="quiz"><h2><span class="num">三</span>測驗'
             f"<small>{len(quiz)} 題，不附答案</small></h2>"
             '<p class="hint">在每題下面作答。寫完按「複製全部答案」，'
             "貼回對話批改。作答會暫存在這個瀏覽器。</p>"
             '<ol class="quiz">']
    for n, q in enumerate(quiz, 1):
        cat = html.escape(q["category"])
        parts.append(f'<li data-n="{n}" data-cat="{cat}">'
                     f'<span class="cat">{cat}</span>'
                     f'<p class="q">{inline(q["question"])}</p>'
                     f'<textarea rows="3" aria-label="第 {n} 題作答" '
                     'placeholder="你的答案…"></textarea></li>')
    parts.append('</ol><div class="actions"><button id="copy" type="button">'
                 '複製全部答案</button><span id="copied" role="status">'
                 "</span></div></section>")
    return "".join(parts)


def _ask_section() -> str:
    return ('<section id="ask"><h2>提問'
            "<small>想問的問題</small></h2>"
            '<p class="hint">一行寫一題。寫完按「複製全部提問」，貼回對話，'
            "我會看 wiki 缺什麼，再補來源或頁面。提問會暫存在這個瀏覽器。</p>"
            '<textarea id="ask-box" rows="8" aria-label="提問" '
            'placeholder="每行一個問題…"></textarea>'
            '<div class="actions"><button id="copy-ask" type="button">'
            '複製全部提問</button><span id="copied-ask" role="status">'
            "</span></div></section>")


def _grading_section(quiz: list[dict], grading: list[dict]) -> str:
    counts = {k: sum(g["result"] == k for g in grading) for k in RESULTS}
    summary = "、".join(f"{RESULTS[k]} {v}" for k, v in counts.items() if v)
    parts = ['<section id="grading"><h2><span class="num">三</span>解答與批改'
             f"<small>{summary}</small></h2>"]
    for n, (q, g) in enumerate(zip(quiz, grading, strict=True), 1):
        parts.append(f'<article class="graded"><h3><span class="dn">{n}</span>'
                     f'<span class="result r-{g["result"]}">'
                     f'{RESULTS[g["result"]]}</span>'
                     f'<span class="gq">{inline(q["question"])}</span></h3>')
        if g.get("answer"):
            parts.append(f'<div class="answer"><b>你的作答</b>'
                         f'{paragraphs(g["answer"])}</div>')
        if g.get("comment"):
            parts.append(f'<div class="comment"><b>批改</b>'
                         f'{paragraphs(g["comment"])}</div>')
        if g.get("solution"):
            parts.append(f'<div class="solution"><b>解答</b>'
                         f'{paragraphs(g["solution"])}'
                         f'{refs_html(g.get("refs"))}</div>')
        parts.append("</article>")
    parts.append("</section>")
    return "".join(parts)


def _header(data: dict) -> str:
    cov = data["coverage"]
    note = (f'<span class="cov-note">{inline(data["coverage_note"])}</span>'
            if data.get("coverage_note") else "")
    topic = (f'<a href="{html.escape(obsidian_url(data["topic"]))}">'
             f'{html.escape(_page_title(data["topic"]))}</a>')
    out = [f'<header><p class="kicker">wiki study · {html.escape(data["date"])}'
           f'</p><h1>{inline(data["title"])}</h1>'
           f'<p class="meta">主題頁：{topic}'
           f'<span class="cov cov-{cov}">{COVERAGE[cov]}</span>{note}</p>']
    scope = data.get("scope") or {}
    if scope:
        out.append("<details class=\"scope\"><summary>這次讀了哪些頁</summary>")
        if scope.get("note"):
            out.append(paragraphs(scope["note"]))
        for key, label in (("read", "讀過"), ("unread", "沒讀")):
            if scope.get(key):
                links = "、".join(
                    f'<a href="{html.escape(obsidian_url(p))}">'
                    f"{html.escape(_page_title(p))}</a>" for p in scope[key])
                out.append(f"<p><b>{label}</b>：{links}</p>")
        out.append("</details>")
    last = ('<a href="#grading">批改</a>' if data.get("grading")
            else '<a href="#quiz">測驗</a>')
    out.append('<nav><a href="#map">知識地圖</a><a href="#disputes">爭議</a>'
               f"{last}</nav></header>")
    return "".join(out)


def render(data: dict) -> str:
    """Builds the full HTML page from validated study data."""
    graded = bool(data.get("grading"))
    body = [_header(data), "<main>", _map_section(data["map"]),
            _dispute_section(data["disputes"],
                             data.get("minor_disputes") or [])]
    if data.get("gaps"):
        items = "".join(f"<li>{inline(g)}</li>" for g in data["gaps"])
        body.append(f'<aside class="note gaps"><b>wiki 還缺的來源</b>'
                    f"<ul>{items}</ul></aside>")
    if graded:  # questions are repeated in the grading, so no quiz section
        body.append(_grading_section(data["quiz"], data["grading"]))
    else:
        body.append(_quiz_section(data["quiz"]))
    if data.get("next"):
        items = "".join(f"<li>{inline(n['text'])} {refs_html(n.get('refs'))}"
                        "</li>" for n in data["next"])
        body.append(f'<aside class="note"><b>建議接著讀</b>'
                    f"<ul>{items}</ul></aside>")
    body.append(_ask_section())
    body.append("</main>")
    css = (ASSETS / "study.css").read_text(encoding="utf-8")
    script = (ASSETS / "study.js").read_text(encoding="utf-8")
    mermaid = (f'<script src="{MERMAID_URL}" defer></script>'
               if data["map"].get("mermaid") else "")
    return ('<!doctype html><html lang="zh-Hant-TW"><head>'
            '<meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(data['title'])}</title><style>{css}</style>"
            f"{mermaid}</head>"
            f'<body data-title="{html.escape(data["title"])}">'
            f"{''.join(body)}"
            f"<script>{script}</script></body></html>")


def build(json_path: Path) -> tuple[Path, dict, list[str]]:
    """Validates a study JSON and writes the HTML page next to it."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    warnings = validate(data)
    out = json_path.with_suffix(".html")
    out.write_text(render(data), encoding="utf-8")
    return out, data, warnings


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="JSON → HTML")
    b.add_argument("json", type=Path)
    b.add_argument("--open", action="store_true", help="用瀏覽器打開")
    args = parser.parse_args(argv)
    try:
        out, data, warnings = build(args.json)
    except (StudyError, ValueError, OSError, KeyError, TypeError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"注意：{w}", file=sys.stderr)
    titles = "、".join(d["title"] for d in data["disputes"]) or "（無）"
    print(f"網頁：{out.resolve()}")
    print(f"爭議：{titles}")
    note = f"（{data['coverage_note']}）" if data.get("coverage_note") else ""
    print(f"覆蓋：{COVERAGE[data['coverage']]}{note}")
    if args.open:
        if hasattr(os, "startfile"):
            os.startfile(out.resolve())  # noqa: S606  (local file only)
        else:
            webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
