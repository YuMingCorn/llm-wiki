"""Event-driven bookkeeping for Claude Code hooks (schema §10 自動化).

Hooks (configured in ``.claude/settings.json``):

- ``session-start``: prints a short brief that Claude Code adds to the
  session context: new or changed raw sources, secrets found in them, lint
  due, stale pages, ledger problems, claims held by others. With new sources
  the brief tells the agent to start /wiki-ingest steps 1-2 on its own and stop at
  the step-3 confirmation point.
- ``post-edit``: after Write/Edit of a ``wiki/`` page, checks that page
  (frontmatter, broken links, relations, superseded-claims section) and feeds
  problems back to the agent.
- ``pre-write``: refuses Write/Edit content with block-level secrets
  (tools/redact.py) in ``wiki/`` or ``hackmd_note/``.
- ``session-end``: writes a session digest (commits, uncommitted wiki files,
  log entries) to ``.llm-wiki/memory/sessions/``. It is working memory, not
  wiki content: knowledge still enters the wiki only through /wiki-ingest.

Each hook reads the hook JSON on stdin. ``brief`` and ``new-sources`` print
the same information for a person.

Usage:
  uv run python -I tools/autopilot.py brief
  uv run python -I tools/autopilot.py new-sources [--json]
  uv run python -I tools/autopilot.py defer <raw ...> [--undo]
  uv run python -I tools/autopilot.py hook <name>
      (name: session-start, post-edit, pre-write, session-end)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402
import _paths  # noqa: E402
import ledger  # noqa: E402
import lifecycle  # noqa: E402
import lint  # noqa: E402
import redact  # noqa: E402
import relations  # noqa: E402
import work  # noqa: E402

RAW = "raw/sources"
HASH_CACHE = ".llm-wiki/memory/raw-hashes.json"
DEFERRED = ".llm-wiki/memory/deferred-sources.json"
SESSIONS = ".llm-wiki/memory/sessions"
BRIEF_LIST_MAX = 10
SCAN_BUDGET_SECONDS = 20
GUARDED_PREFIXES = ("wiki/", "hackmd_note/")
# Links may point at pages written later in the same ingest: hint, not block.
LINK_HINT = "（提醒）"


def _raw_files(root: Path) -> list[Path]:
    base = root / RAW
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*")
        if p.is_file() and not p.name.startswith(".")
        and not any(part.endswith("_images") for part in p.parts))


def _hashes(root: Path, files: Sequence[Path]) -> dict[str, str]:
    """SHA-256 per file, reusing a cache keyed by size and mtime."""
    cache_path = root / HASH_CACHE
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    out: dict[str, str] = {}
    fresh: dict[str, dict] = {}
    for file in files:
        rel = file.relative_to(root).as_posix()
        stat = file.stat()
        key = f"{stat.st_size}:{stat.st_mtime_ns}"
        entry = cache.get(rel)
        digest = entry["sha256"] if entry and entry.get("key") == key else (
            ledger.sha256_file(file))
        out[rel] = digest
        fresh[rel] = {"key": key, "sha256": digest}
    if fresh != cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(fresh), encoding="utf-8")
    return out


def new_sources(root: Path) -> list[dict]:
    """Raw files the ledger does not know (``new``) or knows with other bytes.

    Returns:
        {path, status, page?} per file; ``status`` is ``new`` or
        ``changed`` (same path, different bytes: stop and report, as in
        /wiki-ingest step 1). ``personal`` marks files under raw/sources/personal/,
        which are private.
    """
    records = ledger._load(root, ledger.SOURCE_LEDGER)
    deferred = _load_deferred(root)
    by_hash = {r["sha256"] for r in records}
    by_path = {r["locator"]: r for r in records}
    out = []
    for rel, digest in _hashes(root, _raw_files(root)).items():
        if digest in by_hash:
            continue
        entry = {"path": rel, "personal": rel.startswith(f"{RAW}/personal/")}
        if rel in by_path:
            entry |= {"status": "changed", "page": by_path[rel]["page"]}
        else:
            entry["status"] = "new"
        # Read before but not confirmed: extraction.md is named after the
        # planned source page, which matches the raw file's stem by the
        # inbox naming rule (schema §2).
        extract = root / ".llm-wiki/extract" / Path(rel).stem / "extraction.md"
        entry["extracted"] = extract.is_file()
        entry["deferred"] = rel in deferred
        out.append(entry)
    return out


def _load_deferred(root: Path) -> dict[str, str]:
    try:
        data = json.loads((root / DEFERRED).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def defer(root: Path, paths: Sequence[str], undo: bool = False) -> int:
    """Adds raw files to (or removes them from) the deferred list.

    A deferred source is one the user declined at the confirmation point;
    the brief only counts it instead of asking to start /wiki-ingest again.
    """
    data = _load_deferred(root)
    keys = [Path(p).as_posix().removeprefix("./") for p in paths]
    for key in keys:
        if undo:
            data.pop(key, None)
        else:
            data[key] = dt.date.today().isoformat()
    path = root / DEFERRED
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return len(keys)


def _scan_new(root: Path, sources: Sequence[dict]) -> list[redact.Finding]:
    """Scans new sources for secrets within SCAN_BUDGET_SECONDS.

    The hook has a timeout; whatever is left is reported as unscanned and
    /wiki-ingest step 1 scans it (``redact.py scan``).
    """
    out: list[redact.Finding] = []
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
    for i, source in enumerate(sources):
        if time.monotonic() > deadline:
            out.append(redact.Finding(
                f"另外 {len(sources) - i} 份", 0, "not_scanned", "block",
                "時間不夠，ingest 第 1 步再跑 redact.py scan"))
            break
        out += redact.blocking(redact.scan_file(
            root / source["path"], source["path"]))
    return out


def brief(root: Path) -> str:
    """The session-start brief; empty when there is nothing to report."""
    lines: list[str] = []
    sources = new_sources(root)
    fresh = [s for s in sources if s["status"] == "new"]
    deferred = [s for s in fresh if s["deferred"]]
    waiting = [s for s in fresh if s["extracted"] and not s["deferred"]]
    new = [s for s in fresh if not s["extracted"] and not s["deferred"]]
    changed = [s for s in sources if s["status"] == "changed"]
    if waiting:
        lines.append(f"已讀過、還在等確認的來源 {len(waiting)} 份（不要重讀，"
                     "直接用 .llm-wiki/extract/<名稱>/extraction.md "
                     "列確認點）：")
        lines += [f"- {s['path']}" for s in waiting[:BRIEF_LIST_MAX]]
    if deferred:
        lines.append(f"使用者暫緩的來源 {len(deferred)} 份（不自動開始；"
                     "`autopilot.py defer --undo <檔>` 取消暫緩）。")
    if new:
        lines.append(f"新來源 {len(new)} 份（raw/sources/ 有、帳本沒有）：")
        lines += [f"- {s['path']}" + ("（personal）"
                                      if s["personal"] else "")
                  for s in new[:BRIEF_LIST_MAX]]
        if len(new) > BRIEF_LIST_MAX:
            lines.append(f"- 另外 {len(new) - BRIEF_LIST_MAX} 份")
        lines.append(
            "→ 自動開始 /wiki-ingest：照 wiki-ingest skill 做第 1–2 步（預檢、帳本檢查、"
            "閱讀、抽取單），停在第 3 步確認點等使用者確認，不寫 wiki 頁。"
            "3 份以上可以派閱讀 agent 並行（docs/dispatch.md），但確認點一樣要停下來等"
            "（使用者沒有說全自動）。使用者在確認點說先不要的來源，"
            "用 `autopilot.py defer <檔>` 記下，之後的 session "
            "就不再自動開始。")
    if changed:
        lines.append("內容被改過的原始檔（同路徑、雜湊不同；"
                     "停下來回報，不 ingest）：")
        lines += [f"- {s['path']}（帳本對應 {s['page']}）" for s in changed]
    scanned = _scan_new(root, sources)
    secrets = [f for f in scanned if f.kind != "not_scanned"]
    unscanned = [f for f in scanned if f.kind == "not_scanned"]
    if unscanned:
        lines.append(f"密鑰掃描沒跑完（{unscanned[0].path}）："
                     "ingest 第 1 步再跑 redact.py scan。")
    if secrets:
        lines.append("新來源裡疑似有密鑰（寫頁時不得轉錄）：")
        lines.append(redact.format_findings(secrets))
    wiki = lint.load(root)
    report = {name: check(wiki) for name, check in (
        ("ingests_since_last_lint", lint.check_ingests_since_last_lint),
        ("ledgers", lint.check_ledgers),
        ("stale_pages", lifecycle.stale_pages),
    )}
    since = report["ingests_since_last_lint"]
    count = since.get("count") if isinstance(since, dict) else None
    if count and count >= lint.LINT_REMIND_AT:
        lines.append(f"上次 lint 之後已 ingest {count} 次，建議跑 /wiki-lint。")
    violations = report["ledgers"].get("violations", [])
    if violations:
        lines.append(f"來源帳本有 {len(violations)} 個問題"
                     "（ledger.py validate）。")
    stale = report["stale_pages"]
    if stale:
        pages = sorted({s["page"] for s in stale})
        lines.append(f"過時頁 {len(pages)} 頁（引用的來源已被取代或過期），"
                     "review 時處理：" + "、".join(pages[:BRIEF_LIST_MAX]))
    claims = work.status(root)
    if claims:
        lines.append("其他 session 的認領（不要寫這些頁）：" + "、".join(
            f"{c['path']}（{c['by']}）" for c in claims[:BRIEF_LIST_MAX]))
    if not lines:
        return ""
    return "【wiki 開工提示（autopilot）】\n" + "\n".join(lines)


def _rel(root: Path, file_path: str) -> str | None:
    try:
        return Path(file_path).resolve().relative_to(
            root.resolve()).as_posix()
    except ValueError:
        return None


def page_problems(root: Path, rel: str) -> list[str]:
    """Checks one wiki page right after it was written."""
    wiki = lint.load(root)
    page = wiki.authored_pages().get(rel)
    if page is None:
        return []
    out: list[str] = []
    probs = lint._frontmatter_problems(page, wiki.domains)
    out += [f"frontmatter：{p}" for p in probs]
    for legacy in lint.check_legacy_status(wiki):
        if legacy["page"] == rel:
            out.append(f"{LINK_HINT}status：{legacy['status']} 是舊值，"
                       f"應為 {legacy['to']}（schema §3）")
    for b in lint.check_broken_links(wiki):
        if b["page"] == rel:
            out.append(f"{LINK_HINT}壞連結：{b['target']}")
    for a in lint.check_broken_anchors(wiki):
        if a["page"] == rel:
            out.append(f"{LINK_HINT}壞錨點（第 {a['line']} 行）：{a['target']}")
    for r in relations.check(wiki):
        if r["page"] == rel or r.get("target") == rel:
            out.append(f"relations：{r['problem']} {r.get('relation') or ''} "
                       f"{r.get('target') or ''}".rstrip())
    for s in lifecycle.check_supersession(wiki):
        if s["page"] == rel:
            out.append(f"已被取代的說法：{s['problem']}（{s['claim']}）")
    return out


def _hook_input() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _written_text(tool_input: dict) -> str:
    parts = [tool_input.get("content"), tool_input.get("new_string")]
    parts += [e.get("new_string") for e in tool_input.get("edits") or []
              if isinstance(e, dict)]
    return "\n".join(p for p in parts if isinstance(p, str))


def hook_pre_write(root: Path, data: dict) -> tuple[int, str]:
    """Returns (exit code, stderr); exit 2 blocks the tool call."""
    tool_input = data.get("tool_input") or {}
    rel = _rel(root, str(tool_input.get("file_path", "")))
    if not rel or not rel.startswith(GUARDED_PREFIXES):
        return 0, ""
    found = redact.blocking(redact.scan_text(_written_text(tool_input), rel))
    if not found:
        return 0, ""
    return 2, ("寫入內容疑似含密鑰，已擋下（tools/redact.py）。"
               "不要把密鑰轉錄進 wiki 或筆記；需要提到時寫成 [REDACTED]。\n"
               + redact.format_findings(found))


def hook_post_edit(root: Path, data: dict) -> dict | None:
    """Returns PostToolUse JSON feeding problems back, or None."""
    tool_input = data.get("tool_input") or {}
    rel = _rel(root, str(tool_input.get("file_path", "")))
    if not rel or not rel.startswith("wiki/") or not rel.endswith(".md"):
        return None
    if rel == lint.LOG:
        return None
    problems = page_problems(root, rel)
    hints = [p.removeprefix(LINK_HINT) for p in problems
             if p.startswith(LINK_HINT)]
    errors = [p for p in problems if not p.startswith(LINK_HINT)]
    if errors:
        return {"decision": "block",
                "reason": f"{rel} 寫入後的檢查發現問題，請修正：\n- "
                          + "\n- ".join(errors + hints)}
    if hints:
        return {"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"{rel} 有連結的目標還不存在；這次 ingest 稍後會寫的頁可以"
                "先不管，收尾前 lint 會再查：\n- " + "\n- ".join(hints))}}
    return None


def _session_name(data: dict) -> str:
    """The session id, reduced to characters safe in a file name."""
    raw = str(data.get("session_id") or "unknown")
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64] or "unknown"


def _start_marker(root: Path, session: str) -> Path:
    return root / SESSIONS / f".start-{session}.json"


def hook_session_start(root: Path, data: dict) -> str:
    """Records the session start and returns the brief."""
    session = _session_name(data)
    marker = _start_marker(root, session)
    if not marker.exists():
        head = _common.run_git(root, "rev-parse", "HEAD").stdout.strip()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({
            "head": head,
            "started": dt.datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8")
    return brief(root)


def session_digest(root: Path, session: str) -> str:
    """Markdown digest of what a session left behind."""
    marker = _start_marker(root, session)
    try:
        start = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        start = {}
    head = start.get("head")
    lines = [f"# Session {session[:8]}",
             "",
             f"- 開始：{start.get('started', '不明')}",
             f"- 結束：{dt.datetime.now().isoformat(timespec='seconds')}",
             ""]
    if head:
        log = _common.run_git(root, "log", "--oneline", f"{head}..HEAD")
        commits = log.stdout.strip().splitlines()
        lines += ["## Commits", ""] + [f"- {c}" for c in commits] + [""] \
            if commits else ["## Commits", "", "- 無", ""]
        diff = _common.run_git(root, "diff", "--unified=0", f"{head}..HEAD",
                               "--", lint.LOG)
        entries = [ln[1:] for ln in diff.stdout.splitlines()
                   if ln.startswith("+## [")]
        if entries:
            lines += ["## log.md 新增", ""] + [f"- {e}" for e in entries] + [""]
    status = _common.run_git(root, "status", "--short", "--", "wiki/")
    pending = status.stdout.rstrip().splitlines()
    lines += ["## 未 commit 的 wiki 檔案", ""]
    lines += [f"- `{p}`" for p in pending] if pending else ["- 無"]
    return "\n".join(lines) + "\n"


def hook_session_end(root: Path, data: dict) -> Path:
    """Writes the digest and removes the start marker."""
    session = _session_name(data)
    folder = root / SESSIONS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dt.date.today().isoformat()}-{session[:8]}.md"
    path.write_text(session_digest(root, session), encoding="utf-8")
    _start_marker(root, session).unlink(missing_ok=True)
    return path


def run_hook(root: Path, name: str) -> int:
    """Runs one hook; never fails the session for an internal error."""
    data = _hook_input()
    try:
        if name == "pre-write":
            code, message = hook_pre_write(root, data)
            if message:
                print(message, file=sys.stderr)
            return code
        if name == "post-edit":
            result = hook_post_edit(root, data)
            if result:
                print(json.dumps(result, ensure_ascii=False))
            return 0
        if name == "session-start":
            text = hook_session_start(root, data)
            if text:
                print(text)
            return 0
        hook_session_end(root, data)
        return 0
    except Exception as err:  # noqa: BLE001  (a hook must not break a session)
        print(f"autopilot {name} 失敗：{err}", file=sys.stderr)
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("brief")
    df = sub.add_parser("defer")
    df.add_argument("paths", nargs="+")
    df.add_argument("--undo", action="store_true")
    ns = sub.add_parser("new-sources")
    ns.add_argument("--json", action="store_true")
    hk = sub.add_parser("hook")
    hk.add_argument("name", choices=("session-start", "post-edit",
                                     "pre-write", "session-end"))
    args = ap.parse_args(argv)
    root = _paths.ROOT
    if args.command == "hook":
        return run_hook(root, args.name)
    if args.command == "defer":
        count = defer(root, args.paths, args.undo)
        print(f"已{'取消暫緩' if args.undo else '暫緩'} {count} 份")
        return 0
    if args.command == "new-sources":
        rows = new_sources(root)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print(f"{r['status']}\t{r['path']}")
            print(f"共 {len(rows)} 份")
        return 0
    print(brief(root) or "沒有要提醒的事")
    return 0


if __name__ == "__main__":
    sys.exit(main())
