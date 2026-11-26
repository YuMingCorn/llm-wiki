"""Append wiki/log.md entries and make guarded git commits.

Usage (from the repo root):
  uv run python -I tools/wiki.py log <type> "<title>" <<'EOF'
  - bullet lines for the entry body
  EOF
  uv run python -I tools/wiki.py commit "docs(wiki): lint 第 7 次" \
      [path ...] [--body "why"]
  uv run python -I tools/wiki.py link-raw [page ...]
  uv run python -I tools/wiki.py related <page> [--depth N] [--type T ...]
  uv run python -I tools/wiki.py lineage [topic ...]
  uv run python -I tools/wiki.py log-archive [--keep 60] [--apply]
  uv run python -I tools/wiki.py log-check [--json]
  uv run python -I tools/wiki.py index-check [--json]

log appends ``## [YYYY-MM-DD] <type> | <title>`` plus the stdin body and never
rewrites earlier entries. log-archive moves older entries verbatim into
``wiki/meta/log-archive/YYYY-MM.md`` (dry run unless ``--apply``). commit
stages the given paths, refuses anything on the private list (raw/,
hackmd_note/ notes, .mcp.json, settings.local.json, .llm-wiki/), checks
the Conventional Commits subject, adds the Co-Authored-By trailer, and never
pushes. link-raw turns inline-code mentions of existing
``raw/sources/`` files in source pages into relative Markdown links, so
Obsidian can open the original next to the summary.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

# -I drops the script directory from sys.path; _paths lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402
import _paths  # noqa: E402
import lineage  # noqa: E402
import lint  # noqa: E402
import redact  # noqa: E402
import relations  # noqa: E402

LOG_TYPES = ("ingest", "lint", "schema")
MAX_BODY_LINES = 1
COMMIT_TYPES = ("feat", "fix", "docs", "chore", "test", "refactor", "perf",
                "style", "build", "ci")
# schema §7: what each scope covers is listed there.
COMMIT_SCOPES = ("wiki", "slides", "schema", "docs", "skills", "agents",
                 "tools", "config", "repo")
SUBJECT_MAX = 50
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
_SUBJECT_RE = re.compile(
    rf"^(?:{'|'.join(COMMIT_TYPES)})\((?P<scope>[\w-]+)\)!?: (?P<subject>\S.*)$"
)
_LOG_HEADING_RE = re.compile(r"^ {0,3}## \[", re.M)
_ENTRY_START_RE = re.compile(r"^## \[", re.M)
_ENTRY_RE = re.compile(
    r"^## \[(?P<month>\d{4}-\d{2})-\d{2}\] (?P<type>[^\s|]+)")
# Newest entries left in log.md by log-archive; `log` suggests archiving
# once the log holds more than ARCHIVE_HINT_AT entries.
ARCHIVE_KEEP = 60
ARCHIVE_HINT_AT = 150
_STDIN_WAIT = 2.0
_RAW_CODE_RE = re.compile(r"`(raw/sources/[^`]+)`")
# Flat layout and the schema §8 per-domain layout (wiki/<domain>/sources/).
SOURCES_GLOBS = ("sources/*.md", "*/sources/*.md")


def _clean_body(body: str) -> str:
    """Normalizes newlines and drops the BOM PowerShell pipes prepend."""
    return body.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def append_log(log: Path, log_type: str, title: str, body: str,
               date: str) -> None:
    """Appends one entry to the log, leaving earlier entries untouched.

    Args:
        log: Path of wiki/log.md.
        log_type: One of LOG_TYPES.
        title: Single-line entry title.
        body: Entry body (usually ``-`` bullets); may be empty.
        date: ``YYYY-MM-DD``.

    Raises:
        ValueError: The type, title or date is invalid, or the body contains
            its own ``## [`` heading or exceeds MAX_BODY_LINES lines.
    """
    if log_type not in LOG_TYPES:
        raise ValueError(f"log 類型必須是 {'|'.join(LOG_TYPES)}：{log_type}")
    if not title.strip() or "\n" in title or "\r" in title:
        raise ValueError("標題必須是單行且不能空白")
    datetime.date.fromisoformat(date)
    body = _clean_body(body)
    if _LOG_HEADING_RE.search(body):
        raise ValueError("內文不能包含 '## [' 標題，一次只追加一筆")
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) > MAX_BODY_LINES:
        raise ValueError(f"內文最多 {MAX_BODY_LINES} 行")

    existing = log.read_text(encoding="utf-8")
    sep = "" if existing.endswith("\n\n") else (
        "\n" if existing.endswith("\n") else "\n\n")
    entry = f"## [{date}] {log_type} | {title.strip()}\n"
    if body.strip():
        entry += body.strip("\n") + "\n"
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(sep + entry)


def split_log(text: str) -> tuple[str, list[str]]:
    """Splits log text into the part before the first entry and the entries.

    Every ``## [`` heading starts an entry, whatever its type: early entries
    use types that are no longer allowed (``query``, ``sync``) and have
    multi-line bodies. Each entry keeps its text verbatim, trailing blank
    lines included, so ``head + "".join(entries) == text``.
    """
    starts = [m.start() for m in _ENTRY_START_RE.finditer(text)]
    if not starts:
        return text, []
    ends = [*starts[1:], len(text)]
    pairs = zip(starts, ends, strict=True)
    return text[:starts[0]], [text[s:e] for s, e in pairs]


def _entry_heading(entry: str) -> re.Match:
    match = _ENTRY_RE.match(entry)
    try:
        if match:
            datetime.date.fromisoformat(entry[4:14])
    except ValueError:
        match = None
    if not match:
        raise ValueError(f"無法辨識的紀錄標題：{entry.splitlines()[0]}")
    return match


def _archive_header(month: str) -> str:
    return (f"# Log 封存 {month}\n\n"
            "> 從 `wiki/log.md` 依月份搬來的舊紀錄，內容沒有改動。"
            "紀錄裡的路徑是當時的路徑，可能已經不存在。\n\n")


def archive_log(log: Path, archive_dir: Path, keep: int = ARCHIVE_KEEP,
                apply: bool = False) -> dict:
    """Moves older log entries into one archive file per month.

    The newest ``keep`` entries stay in the log, and so do the last ``lint``
    entry and everything after it, because lint counts the ingests since its
    last run from the log, and /wiki-lint reads its next number there. Moved
    entries are written verbatim. An entry already in its archive file is
    not written again (counted per copy), so a rerun after an interrupted
    move does not duplicate entries. The new files are checked in memory
    before anything is written.

    Args:
        log: Path of wiki/log.md.
        archive_dir: Directory of the monthly archive files.
        keep: Minimum number of newest entries left in the log.
        apply: Write the files; otherwise only report the plan.

    Returns:
        {"total", "moved", "kept", "months": {YYYY-MM: count}, "applied"}.

    Raises:
        ValueError: keep is negative, an entry heading cannot be read, or
            the planned files would lose an entry. Nothing is written then.
    """
    if keep < 0:
        raise ValueError("--keep 不能是負數")
    text = log.read_text(encoding="utf-8")
    head, entries = split_log(text)
    headings = [_entry_heading(e) for e in entries]
    lints = [i for i, m in enumerate(headings) if m["type"] == "lint"]
    cutoff = max(len(entries) - keep, 0)
    if lints:
        cutoff = min(cutoff, lints[-1])
    months: dict[str, list[str]] = {}
    for entry, match in zip(entries[:cutoff], headings[:cutoff],
                            strict=True):
        months.setdefault(match["month"], []).append(entry)
    result = {"total": len(entries), "moved": cutoff,
              "kept": len(entries) - cutoff,
              "months": {m: len(v) for m, v in months.items()},
              "applied": False}
    if not apply or not cutoff:
        return result
    files: dict[Path, str] = {}
    for month, items in months.items():
        path = archive_dir / f"{month}.md"
        existing = (path.read_text(encoding="utf-8") if path.is_file()
                    else _archive_header(month))
        _, present = split_log(existing)
        left = Counter(e.rstrip("\n") for e in present)
        new = []
        for entry in items:
            key = entry.rstrip("\n")
            if left[key]:
                left[key] -= 1
            else:
                new.append(key)
        if new:
            body = existing.rstrip("\n") + "\n\n" if present else existing
            files[path] = body + "\n\n".join(new) + "\n"
    files[log] = head + "".join(entries[cutoff:])
    _check_archive(entries, files, archive_dir, set(months))
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path, body in files.items():  # the log last, after the archive
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    result["applied"] = True
    return result


def _check_archive(entries: list[str], files: dict[Path, str],
                   archive_dir: Path, months: set[str]) -> None:
    """Checks that the planned files hold every original entry."""
    have: Counter = Counter()
    for month in months:
        path = archive_dir / f"{month}.md"
        text = files.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
        have.update(e.rstrip("\n") for e in split_log(text)[1])
    log_text = next(t for p, t in files.items() if p.parent != archive_dir)
    have.update(e.rstrip("\n") for e in split_log(log_text)[1])
    want = Counter(e.rstrip("\n") for e in entries)
    lost = [e for e, n in want.items() if have[e] < n]
    if lost:
        raise ValueError(
            f"封存計畫核對失敗：{len(lost)} 筆紀錄會遺失，沒有寫入")


def archive_hint(log: Path) -> str:
    """Suggests log-archive once the log holds many entries."""
    _, entries = split_log(log.read_text(encoding="utf-8"))
    if len(entries) <= ARCHIVE_HINT_AT:
        return ""
    return (f"提示：log.md 已有 {len(entries)} 筆紀錄，可以跑 "
            "`wiki.py log-archive` 把舊紀錄依月份封存")


def lint_reminder(log: Path, log_type: str) -> str:
    """Returns a reminder to run /wiki-lint if enough entries have piled up.

    Args:
        log: Path of wiki/log.md.
        log_type: Type of the entry just appended.

    Returns:
        The reminder, or "" if none is due.
    """
    if log_type != "ingest":
        return ""
    status = lint.ingests_since_last_lint(log.read_text(encoding="utf-8"))
    if not status["remind"]:
        return ""
    return (f"提醒：上次 lint 之後已累積 {status['count']} 次 ingest，"
            "請提醒使用者跑 /wiki-lint")


def check_message(message: str) -> None:
    """Validates a Conventional Commits subject line (schema §7).

    Args:
        message: The commit subject.

    Raises:
        ValueError: The subject does not follow the format.
    """
    match = _SUBJECT_RE.match(message)
    if not match:
        raise ValueError(
            "commit 訊息格式應為 <type>(<scope>): <subject>，type 是 "
            + "/".join(COMMIT_TYPES) + "，scope 是 " + "/".join(COMMIT_SCOPES)
        )
    if match["scope"] not in COMMIT_SCOPES:
        raise ValueError(f"scope 不在清單內：{match['scope']}（可用 "
                         + "/".join(COMMIT_SCOPES) + "，見 schema §7）")
    subject = match["subject"]
    if len(subject) > SUBJECT_MAX:
        raise ValueError(f"subject 超過 {SUBJECT_MAX} 字元：{len(subject)}")
    if subject.endswith(("。", ".")):
        raise ValueError("subject 結尾不加句號")


def _relative(root: Path, path: str) -> str:
    """Returns path relative to root with POSIX separators."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root.resolve())
        except ValueError:
            raise ValueError(f"路徑不在 repo 裡：{path}") from None
    return p.as_posix().removeprefix("./").rstrip("/")


def _blocked_arg(rel: str) -> bool:
    """Returns True if a path argument names or contains private files."""
    dirs = {prefix.rstrip("/") for prefix in _paths.FORBIDDEN_PREFIXES}
    return _paths.is_forbidden(rel) or rel in dirs


def _read_stdin(timeout: float) -> str:
    """Reads stdin, giving up with "" if it is still open after timeout.

    A heredoc arrives and closes at once; an idle, never-closed stdin (as in
    some agent shells) must not hang the command.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    chunks: list[str] = []
    reader = threading.Thread(
        target=lambda: chunks.append(sys.stdin.read()), daemon=True)
    reader.start()
    reader.join(timeout)
    return chunks[0] if chunks else ""


def _staged_secrets(root: Path) -> list[redact.Finding]:
    """Scans the staged (index) version of every added or changed file.

    Every file is read as bytes and decoded leniently, so key files
    (``.pem``), scripts and suffix-less files are covered and a non-UTF-8
    file cannot crash the commit. Only known binary types are skipped.
    """
    names = _common.git(root, "diff", "--cached", "--name-only",
                        "--diff-filter=d").splitlines()
    out: list[redact.Finding] = []
    for name in names:
        if Path(name).suffix.lower() in redact.BINARY_SUFFIXES:
            continue
        shown = subprocess.run(
            ["git", "-C", str(root), "show", f":{name}"],
            capture_output=True, check=False)
        if shown.returncode == 0:
            text = shown.stdout.decode("utf-8", errors="replace")
            out += redact.blocking(redact.scan_text(text, name))
    return out


def commit(root: Path, message: str, paths: Sequence[str],
           body: str = "") -> str:
    """Stages paths and commits them after the safety checks.

    Args:
        root: Repository root.
        message: Conventional Commits subject.
        paths: Paths to ``git add`` first; may be empty if already staged.
        body: Optional commit body.

    Returns:
        The short hash of the new commit.

    Raises:
        ValueError: Bad message, a private path, or nothing staged.
    """
    check_message(message)
    rel = [_relative(root, p) for p in paths]
    blocked = [p for p in rel if _blocked_arg(p)]
    if blocked:
        raise ValueError(f"拒絕 commit 私人路徑：{', '.join(blocked)}")
    if rel:
        _common.git(root, "add", "--", *rel)

    staged = _common.git(root, "diff", "--cached", "--name-only").splitlines()
    blocked = [p for p in staged if _paths.is_forbidden(p)]
    if blocked:
        # Reset the index entry to HEAD; `rm --cached` would stage a deletion
        # of an already-tracked file. Without a HEAD there is nothing to keep.
        head = _common.run_git(root, "rev-parse", "-q", "--verify", "HEAD")
        if head.returncode == 0:
            _common.git(root, "reset", "-q", "--", *blocked)
        else:
            _common.git(root, "rm", "--cached", "-q", "--", *blocked)
        raise ValueError(
            f"已取消 stage 並拒絕 commit 私人路徑：{', '.join(blocked)}")
    if not staged:
        raise ValueError("沒有 staged 的變更")
    secrets = _staged_secrets(root)
    if secrets:
        # Unstage them, as for private paths, so a later plain `git commit`
        # cannot carry them in.
        files = sorted({f.path for f in secrets})
        head = _common.run_git(root, "rev-parse", "-q", "--verify", "HEAD")
        if head.returncode == 0:
            _common.git(root, "reset", "-q", "--", *files)
        else:
            _common.git(root, "rm", "--cached", "-q", "--", *files)
        raise ValueError("staged 的內容疑似含密鑰，已取消 stage 並拒絕 commit"
                         "（先移除，需要提到時寫 [REDACTED]）：\n"
                         + redact.format_findings(secrets))

    parts = [message, body.strip(), TRAILER]
    full = "\n\n".join(p for p in parts if p) + "\n"
    _common.git(root, "commit", "-q", "-F", "-", stdin=full)
    return _common.git(root, "rev-parse", "--short", "HEAD").strip()


def link_raw_text(text: str, page_dir: str, root: Path) -> tuple[str, int]:
    """Replaces `raw/sources/<file>` inline code with a relative link.

    Only paths that name an existing file are converted; directory mentions
    such as `raw/sources/` stay as code.

    Args:
        text: Page text.
        page_dir: Repo-relative POSIX directory of the page.
        root: Repository root used to check that the file exists.

    Returns:
        The new text and the number of replacements.
    """
    depth = len([part for part in page_dir.split("/") if part])

    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group(1)
        if not (root / target).is_file():
            return m.group(0)
        count += 1
        name = target.rsplit("/", 1)[-1]
        name = name.replace("[", r"\[").replace("]", r"\]")
        return f"[{name}]({'../' * depth}{quote(target)})"

    return _RAW_CODE_RE.sub(_sub, text), count


def link_raw(root: Path, pages: Sequence[str]) -> list[tuple[str, int]]:
    """Applies link_raw_text to source pages and reports changed pages.

    Args:
        root: Repository root.
        pages: Repo-relative page paths; empty means every source page.

    Returns:
        (page, replacements) for each changed page.
    """
    targets = list(pages) or sorted(
        p.relative_to(root).as_posix()
        for pattern in SOURCES_GLOBS
        for p in (root / "wiki").glob(pattern))
    changed = []
    for rel in targets:
        path = root / rel
        with path.open(encoding="utf-8", newline="") as f:
            text = f.read()
        new, count = link_raw_text(text, rel.rsplit("/", 1)[0], root)
        if count:
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed.append((rel, count))
    return changed


# --- log-check and index-check -------------------------------------------

_STRICT_HEADING_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<type>\S+) \| (?P<title>\S.*)$")
_LOOSE_HEADING_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<type>\S+) \| (?P<title>.*)$")
_LINT_NUMBER_RE = re.compile(r"第 (\d+) 次")
_REVIEW_PATH_RE = re.compile(r"\.llm-wiki/review/[^\s`()\[\]]+\.md")
_LOG_COMMIT_RE = re.compile(r"^docs\(wiki\): (?:ingest|lint)")
_COMMIT_EXEMPT = ("補正", "修正")
_LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>[^)\s]+)\)")
_MEMBER_RE = re.compile(r"^- \[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)\)")
_READ_FIRST_RE = re.compile(
    r"^\d+\. \[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)\)")
READ_FIRST_MIN_MEMBERS = 8
READ_FIRST_RANGE = (3, 6)


def _finding(check: str, severity: str, file: str, line: int | None,
             detail: str) -> dict:
    return {"check": check, "severity": severity, "file": file,
            "line": line, "detail": detail}


def _log_entries(path: Path) -> list[tuple[int, str]]:
    """Returns (first line number, text) for each entry of a log file."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    head, entries = split_log(text)
    line = head.count("\n") + 1
    result = []
    for entry in entries:
        result.append((line, entry))
        line += entry.count("\n")
    return result


def _entry_date(entry: str) -> str | None:
    match = _STRICT_HEADING_RE.match(entry.split("\n", 1)[0])
    if not match:
        return None
    try:
        datetime.date.fromisoformat(match["date"])
    except ValueError:
        return None
    return match["date"]


def _log_git_findings(root: Path, first_date: str) -> list[dict]:
    """L7: log-type commits since first_date that did not touch the log."""
    try:
        proc = _common.run_git(
            root, "log", "--no-merges", "--name-only",
            "--format=%x01%h%x09%as%x09%s")
    except OSError as err:
        return [_finding("L7", "提醒", "wiki/log.md", None,
                         f"無法執行 git，略過 L7：{err}")]
    if proc.returncode:
        return []
    found = []
    for block in proc.stdout.split("\x01")[1:]:
        head, *names = block.strip("\n").split("\n")
        short, date, subject = head.split("\t", 2)
        if (not _LOG_COMMIT_RE.match(subject) or date < first_date
                or any(w in subject for w in _COMMIT_EXEMPT)
                or "wiki/log.md" in names):
            continue
        found.append(_finding(
            "L7", "提醒", "wiki/log.md", None,
            f"commit {short}「{subject}」沒有改到 wiki/log.md"))
    return found


def _lint_entries(items: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    """Returns (line, entry, title) for the lint entries of a log file."""
    result = []
    for line, entry in items:
        match = _LOOSE_HEADING_RE.match(entry.split("\n", 1)[0])
        if match and match["type"] == "lint":
            result.append((line, entry, match["title"].strip()))
    return result


def log_check(root: Path) -> list[dict]:
    """Checks wiki/log.md and its monthly archives (checks L1 to L7).

    Args:
        root: Repository root.

    Returns:
        Findings ``{check, severity, file, line, detail}``; severity is
        ``違規`` or ``提醒``. Archives only take part in L3, L4 and L5,
        because they hold old entries written before the rules existed.
    """
    rel_log = "wiki/log.md"
    found: list[dict] = []
    if not (root / rel_log).is_file():
        return [_finding("L1", "違規", rel_log, None, "log.md 不存在")]
    entries = _log_entries(root / rel_log)
    archives = sorted((root / "wiki" / "meta" / "log-archive").glob("*.md"))
    arch_entries = [(a.relative_to(root).as_posix(), _log_entries(a))
                    for a in archives]

    prev = ""
    for line, entry in entries:
        first = entry.split("\n", 1)[0]
        match = _STRICT_HEADING_RE.match(first)
        date = _entry_date(entry)
        if not match or date is None:
            found.append(_finding("L1", "違規", rel_log, line,
                                  f"標題格式不符：{first}"))
            continue
        if match["type"] not in LOG_TYPES:
            found.append(_finding(
                "L1", "違規", rel_log, line,
                f"類型不在 {'|'.join(LOG_TYPES)}：{match['type']}"))
        body = [x for x in entry.split("\n")[1:] if x.strip()]
        if len(body) > MAX_BODY_LINES:
            found.append(_finding(
                "L1", "違規", rel_log, line,
                f"內文 {len(body)} 行，最多 {MAX_BODY_LINES} 行"))
        if date < prev:
            found.append(_finding("L2", "違規", rel_log, line,
                                  f"日期 {date} 早於前一筆 {prev}"))
        prev = max(prev, date)

    seen: dict[str, tuple[str, int]] = {}
    for name, items in [*arch_entries, (rel_log, entries)]:
        for line, entry in items:
            key = entry.rstrip("\n")
            if key in seen:
                where = f"{seen[key][0]}:{seen[key][1]}"
                found.append(_finding("L3", "違規", name, line,
                                      f"與 {where} 的紀錄完全相同"))
            else:
                seen[key] = (name, line)
    log_dates = [d for _, e in entries if (d := _entry_date(e))]
    arch_dates = [d for _, items in arch_entries for _, e in items
                  if (d := _entry_date(e))]
    if log_dates and arch_dates and max(arch_dates) > min(log_dates):
        found.append(_finding(
            "L3", "違規", rel_log, entries[0][0],
            f"封存檔有 {max(arch_dates)} 的紀錄，晚於 log.md 最早一筆 "
            f"{min(log_dates)}"))

    if not _lint_entries(entries) and any(
            _lint_entries(items) for _, items in arch_entries):
        found.append(_finding("L4", "違規", rel_log, None,
                              "最新的一筆 lint 不在 log.md，只在封存檔"))
    if len(entries) > ARCHIVE_HINT_AT:
        found.append(_finding(
            "L4", "提醒", rel_log, None,
            f"log.md 有 {len(entries)} 筆，超過 {ARCHIVE_HINT_AT}，"
            "可以跑 wiki.py log-archive"))

    last = None
    for name, items in [*arch_entries, (rel_log, entries)]:
        for line, _, title in _lint_entries(items):
            match = _LINT_NUMBER_RE.fullmatch(title)
            if not match:
                continue  # Chinese numerals and "第 N 次的修正" are skipped
            num = int(match[1])
            if last is not None and num != last + 1:
                kind = ("重複" if num == last
                        else "倒退" if num < last else "不連續")
                found.append(_finding(
                    "L5", "違規", name, line,
                    f"lint 編號{kind}：第 {last} 次之後是第 {num} 次"))
            last = num if last is None else max(last, num)

    for line, entry in entries:
        for path in dict.fromkeys(_REVIEW_PATH_RE.findall(entry)):
            if not (root / path).is_file():
                found.append(_finding("L6", "提醒", rel_log, line,
                                      f"報告檔不存在：{path}"))
    if log_dates:
        found += _log_git_findings(root, min(log_dates))
    return found


def _resolve(root: Path, page: str, href: str) -> str:
    """Resolves a relative link to a repo-relative POSIX path."""
    base = (root / page).parent
    target = Path(os.path.normpath(base / href.replace("%20", " ")))
    try:
        return target.relative_to(os.path.normpath(root)).as_posix()
    except ValueError:
        return target.as_posix()


def _page_links(text: str) -> list[tuple[int, str, str]]:
    """Returns (line, text, target without anchor) for non-URL links."""
    result = []
    for number, line in enumerate(text.split("\n"), 1):
        for m in _LINK_RE.finditer(line):
            href = m["href"].split("#", 1)[0]
            if href and "://" not in href and not href.startswith("mailto:"):
                result.append((number, m["text"], href))
    return result


def _title(path: Path) -> str | None:
    if not path.is_file():
        return None
    meta, _ = _common.split_frontmatter(path.read_text(encoding="utf-8"))
    title = (meta or {}).get("title")
    return title if isinstance(title, str) else None


def _topic_findings(root: Path, page: str) -> list[dict]:
    """I5 for one topic page."""
    lines = (root / page).read_text(encoding="utf-8").split("\n")
    start = next((i for i, x in enumerate(lines)
                  if x.strip() == "## 先讀"), None)
    end = len(lines)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
    paths = set()
    for i, text in enumerate(lines):
        if start is not None and start <= i < end:
            continue
        m = _MEMBER_RE.match(text)
        href = m["href"].split("#", 1)[0] if m else ""
        if href and "://" not in href and not href.startswith("mailto:"):
            paths.add(_resolve(root, page, href))
    count = len(paths)  # distinct internal members, not bullets
    if count < READ_FIRST_MIN_MEMBERS:
        return []
    if start is None:
        return [_finding("I5", "違規", page, None,
                         f"成員 {count} 個，需要「## 先讀」小節")]
    items = [(i + 1, m) for i in range(start + 1, end)
             if (m := _READ_FIRST_RE.match(lines[i]))]
    found = []
    lo, hi = READ_FIRST_RANGE
    if not lo <= len(items) <= hi:
        found.append(_finding("I5", "違規", page, start + 1,
                              f"先讀有 {len(items)} 項，應為 {lo} 到 {hi} 項"))
    for number, m in items:
        target = _resolve(root, page, m["href"].split("#", 1)[0])
        if target not in paths:
            found.append(_finding("I5", "違規", page, number,
                                  f"先讀項目不是成員：{m['href']}"))
    return found


def index_check(root: Path) -> list[dict]:
    """Checks wiki/index.md, the domain indexes and topic pages (I1 to I5).

    Args:
        root: Repository root.

    Returns:
        Findings ``{check, severity, file, line, detail}``; all are ``違規``.
    """
    found: list[dict] = []
    top = "wiki/index.md"
    if not (root / top).is_file():
        return [_finding("I1", "違規", top, None, "wiki/index.md 不存在")]
    top_text = (root / top).read_text(encoding="utf-8")
    domains = _common.discover_domains(root)
    allowed = {"wiki/overview.md",
               *(f"wiki/{d}/index.md" for d in domains)}
    for line, _, href in _page_links(top_text):
        if _resolve(root, top, href) not in allowed:
            found.append(_finding(
                "I1", "違規", top, line,
                f"總 index 只能連 overview 與子 index：{href}"))

    counts: dict[str, tuple[int, int]] = {}
    for domain in domains:
        sub = f"wiki/{domain}/index.md"
        if not (root / sub).is_file():
            if (root / "wiki" / domain).is_dir():
                found.append(_finding("I2", "違規", sub, None,
                                      "領域目錄存在，但子 index 不存在"))
            continue
        synthesis = set()
        sub_text = (root / sub).read_text(encoding="utf-8")
        for number, text in enumerate(sub_text.split("\n"), 1):
            for m in _LINK_RE.finditer(text):
                href = m["href"].split("#", 1)[0]
                if not href or "://" in href:
                    continue
                target = _resolve(root, sub, href)
                if target.startswith(f"wiki/{domain}/synthesis/"):
                    synthesis.add(target)
                elif target.startswith(f"wiki/{domain}/topics/"):
                    title = _title(root / target)
                    if title is not None and title != m["text"]:
                        found.append(_finding(
                            "I4", "違規", sub, number,
                            f"連結文字「{m['text']}」與主題頁 title"
                            f"「{title}」不同"))
                elif target != top:
                    found.append(_finding(
                        "I2", "違規", sub, number,
                        "子 index 只能連 topics/、synthesis/ 與總 index："
                        f"{href}"))
        topics = sorted((root / "wiki" / domain / "topics").glob("*.md"))
        counts[domain] = (len(topics), len(synthesis))
        for topic in topics:
            found += _topic_findings(root, topic.relative_to(root).as_posix())

    for number, text in enumerate(top_text.split("\n"), 1):
        for domain, (n_topics, n_synth) in counts.items():
            if f"({domain}/index.md)" not in text:
                continue
            for label, actual in (("個主題", n_topics),
                                  ("篇跨主題整合", n_synth)):
                m = re.search(rf"(\d+) {label}", text)
                if m and int(m[1]) != actual:
                    found.append(_finding(
                        "I3", "違規", top, number,
                        f"寫「{m[0]}」，{domain} 實際是 {actual}"))
    return found


def format_findings(found: list[dict]) -> str:
    """Formats findings as tab-separated lines, or one pass line."""
    if not found:
        return "通過：沒有問題"
    rows = []
    for f in found:
        where = f["file"] + (f":{f['line']}" if f["line"] else "")
        rows.append(f"{f['check']}\t{f['severity']}\t{where}\t{f['detail']}")
    return "\n".join(rows)


def findings_status(found: list[dict]) -> int:
    """Returns 1 if any finding is a violation, else 0."""
    return int(any(f["severity"] == "違規" for f in found))


def _parser() -> argparse.ArgumentParser:
    """Builds the command-line parser."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", required=True)
    log = sub.add_parser("log", help="append a wiki/log.md entry")
    log.add_argument("type", choices=LOG_TYPES)
    log.add_argument("title")
    log.add_argument("--date", default=datetime.date.today().isoformat())
    com = sub.add_parser("commit", help="guarded git commit (no push)")
    com.add_argument("message")
    com.add_argument("paths", nargs="*")
    com.add_argument("--body", default="")
    lr = sub.add_parser("link-raw", help="link raw files in source pages")
    lr.add_argument("pages", nargs="*")
    arc = sub.add_parser("log-archive",
                         help="move older log entries to monthly files")
    arc.add_argument("--keep", type=int, default=ARCHIVE_KEEP)
    arc.add_argument("--apply", action="store_true")
    for name, text in (("log-check", "check wiki/log.md and its archives"),
                       ("index-check", "check the index files and topics")):
        chk = sub.add_parser(name, help=text)
        chk.add_argument("--json", action="store_true")
    rel = sub.add_parser("related", help="walk typed relations from a page")
    rel.add_argument("page")
    rel.add_argument("--depth", type=int, default=1)
    rel.add_argument("--type", dest="types", action="append",
                     choices=relations.RELATION_TYPES,
                     help="relation to follow (repeatable; default all)")
    lin = sub.add_parser("lineage",
                         help="write wiki/meta/lineage/<topic>.md files")
    lin.add_argument("topics", nargs="*",
                     help="topic name or path (default: every topic)")
    return ap


def related(root: Path, page: str, depth: int,
            types: Sequence[str] | None) -> list[str]:
    """Formats the pages reached from page through typed relations.

    Returns:
        Tab-separated lines: depth, relation, page, title, via.

    Raises:
        ValueError: page is not a content page.
    """
    key = Path(page).as_posix().removeprefix("./")
    wiki = lint.load(root)
    if key not in wiki.pages:
        raise ValueError(f"不是內容頁：{key}")
    found = relations.neighbors(
        wiki, key, depth, tuple(types) if types else relations.RELATION_TYPES)
    return ["\t".join(str(r[k]) for k in ("depth", "relation", "page",
                                          "title", "via"))
            for r in found]


def _utf8_streams() -> None:
    """Switches stdin, stdout and stderr to UTF-8.

    ``-I`` ignores PYTHONIOENCODING/PYTHONUTF8, so on Windows stdin would
    otherwise be decoded as cp950 and a Chinese heredoc body would fail.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name; defaults to sys.argv.

    Returns:
        The process exit status.
    """
    _utf8_streams()
    args = _parser().parse_args(argv)
    try:
        if args.command == "log":
            body = _read_stdin(_STDIN_WAIT)
            append_log(_paths.LOG, args.type, args.title, body, args.date)
            print(f"已追加：## [{args.date}] {args.type} | {args.title}")
            reminder = lint_reminder(_paths.LOG, args.type)
            if reminder:
                print(reminder)
            hint = archive_hint(_paths.LOG)
            if hint:
                print(hint)
        elif args.command == "log-archive":
            res = archive_log(_paths.LOG, _paths.LOG_ARCHIVE, args.keep,
                              args.apply)
            months = "、".join(f"{m} {n} 筆" for m, n in res["months"].items())
            verb = ("已搬移" if res["applied"]
                    else "預計搬移（加 --apply 才寫入）")
            print(f"共 {res['total']} 筆；{verb} {res['moved']} 筆"
                  f"{'：' + months if months else ''}；留下 {res['kept']} 筆")
        elif args.command in ("log-check", "index-check"):
            check = log_check if args.command == "log-check" else index_check
            found = check(_paths.ROOT)
            print(json.dumps(found, ensure_ascii=False, indent=2)
                  if args.json else format_findings(found))
            return findings_status(found)
        elif args.command == "link-raw":
            changed = link_raw(_paths.ROOT, args.pages)
            for rel, count in changed:
                print(f"{rel}	{count}")
            print(f"已改 {len(changed)} 頁")
        elif args.command == "lineage":
            wiki = lint.load(_paths.ROOT)
            keys = lineage.resolve_topics(wiki, args.topics)
            for rel, changed in lineage.write(wiki, keys):
                print(f"{rel}	{'已更新' if changed else '沒有變動'}")
        elif args.command == "related":
            lines = related(_paths.ROOT, args.page, args.depth, args.types)
            print("\n".join(lines) if lines else "沒有關係")
        else:
            short = commit(_paths.ROOT, args.message, args.paths, args.body)
            print(f"已 commit {short}（未 push）")
    except (ValueError, RuntimeError) as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
