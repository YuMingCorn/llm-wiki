"""Builds the blank public template of this repo from one commit.

The public repo is a template for anyone to start their own wiki. It holds the
rules, skills, agents, hooks, tools and tests, but none of this wiki's pages,
domains, sources or personal settings. From the commit it is built from:

- only paths in ``ALLOWED`` are copied; paths in ``EXCLUDED`` are left out;
  any other tracked path stops the export, so a new file is never published
  by accident;
- the files under ``template/`` are copied over the result (``template/x``
  becomes ``x``): the blank README, PURPOSE.md, index, log, ledger and so on;
- a leak check scans every written file for strings taken from this repo:
  page names and titles, the words of source page names (without year and
  author), domain names, source file names, aliases, the e-mail address and
  local paths, and dated history notes such as "（<date> 起）" or a link to
  a decision record. Any hit stops the export with exit 1.

Only committed files are read (``git archive``), so ignored files such as
``raw/`` and ``.mcp.json`` are never copied. The target must be empty or hold
only ``.git``; ``--replace`` first clears everything in it except ``.git``.

``--commit MSG`` then commits the result in the target (``git add -A``). It
needs the target to be a git repo whose ``user.email`` is a GitHub noreply
address. The tool never pushes.

Usage:
  uv run python -I tools/public_export.py <target> [--ref HEAD] [--replace]
      [--commit MSG]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402  (also applies _limits)

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = "template/"
NOREPLY_SUFFIX = "@users.noreply.github.com"

# Copied as they are (a trailing "/" means a whole directory).
ALLOWED: tuple[str, ...] = (
    ".claude/agents/", ".claude/hooks/", ".claude/skills/",
    ".claude/settings.json", ".gitignore", "CLAUDE.md", "GLOSSARY.md",
    "docs/", "pyproject.toml", "SCHEMA.md", "tests/", "tools/", "uv.lock",
)
# Left out: this wiki's content and personal files. Wins over ALLOWED.
EXCLUDED: tuple[str, ...] = (
    "wiki/", "slides/", "hackmd_note/", OVERLAY,
    "README.md", "PURPOSE.md", "LICENSE", "LICENSE-CONTENT",
    "MISSING_SOURCES.md", "PLAN.md",
    "tools/search_eval.tsv",  # queries about this wiki's pages
    "StudyVault/", "skills-lock.json",
    "docs/adr/",  # decision records: this wiki's dated history
    ".claude/skills/tutor/", ".claude/skills/tutor-setup/",
    # reference sequences built from lectures this user read
    ".claude/skills/wiki-slides/references/sequences/",
)
# Page names that every wiki has; they are not a leak.
GENERIC_STEMS = frozenset({"index", "overview", "log", "readme"})
# Page names and aliases that are plain words of the rules themselves.
GENERIC_WORDS = frozenset({"subagent", "子代理"})
# Page-name phrases that name the tooling itself, not this wiki's topics.
GENERIC_PHRASES = frozenset({"claude code hooks"})
_EMAIL_RE = re.compile(r"[\w.+-]+@gmail\.com", re.IGNORECASE)
_DATE = r"20\d\d-[01]\d-[0-3]\d"
# Dated history notes such as "（<date> 起）" or "decided <date>": they
# record when this wiki changed its rules, not what the rules are. Bare
# dates are allowed, because test data and examples need them. The last
# pattern catches links to files in the left-out decision-record folder.
_HISTORY_RES = (
    # A date or month followed by Chinese text: "<date> 起", "<date> 的評估".
    re.compile(r"20\d\d-[01]\d(?:-[0-3]\d)?\s*[一-鿿]"),
    re.compile(rf"\b(?:since|on|as of)\s+{_DATE}", re.IGNORECASE),
    re.compile(rf"(?:決定|指定|decided|使用者)[^\n）)]{{0,12}}{_DATE}",
               re.IGNORECASE),
    re.compile(rf"[（(]\s*{_DATE}\s*[）)]"),
    re.compile(r"docs/adr/\d"),
)


class ExportError(RuntimeError):
    """The export cannot go on safely."""


def _matches(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p))
               for p in prefixes)


def classify(path: str) -> str:
    """Returns ``"excluded"``, ``"allowed"`` or ``"unknown"`` for a path."""
    if _matches(path, EXCLUDED):
        return "excluded"
    if _matches(path, ALLOWED):
        return "allowed"
    return "unknown"


@dataclass
class Needles:
    """Strings from this repo that must not appear in the template."""

    words: set[str] = field(default_factory=set)  # whole-word matches
    texts: set[str] = field(default_factory=set)  # plain substrings
    patterns: list[re.Pattern[str]] = field(default_factory=list)

    def find(self, text: str) -> list[str]:
        """Lists the needles found in text, sorted."""
        hits = {t for t in self.texts if t in text}
        hits |= {m.group(0) for p in self.patterns for m in p.finditer(text)}
        if self.words:
            hits |= set(self._word_re().findall(text))
        return sorted(hits)

    def _word_re(self) -> re.Pattern[str]:
        if not hasattr(self, "_cached"):
            alts = "|".join(re.escape(w)
                            for w in sorted(self.words, key=len, reverse=True))
            self._cached = re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])")
        return self._cached


def build_needles(files: dict[str, bytes], repo: Path) -> Needles:
    """Collects the leak-check strings from the exported commit.

    Args:
        files: Every tracked file of the commit, path to content.
        repo: The repository, for its folder name and e-mail address.
    """
    needles = Needles()
    domains: set[str] = set()
    phrases: set[str] = set()
    for path, data in files.items():
        parts = PurePosixPath(path).parts
        if parts[0] != "wiki":
            continue
        if (len(parts) > 2 and _common.is_domain_name(parts[1])
                and parts[1] != _common.CROSS_DOMAIN):
            domains.add(parts[1])
        stem = PurePosixPath(path).stem
        if (path.endswith(".md") and not path.startswith("wiki/meta/")
                and stem.lower() not in GENERIC_STEMS):
            needles.words.add(stem)
        if path.endswith(".md") and not path.startswith("wiki/meta/"):
            phrase = _short_name(stem)
            if phrase:
                phrases.add(phrase)
            for name in (*_aliases(data), *_title(data)):
                # Chinese text has no word breaks, so match it anywhere.
                (needles.words if name.isascii() else needles.texts).add(
                    name)
        if path.endswith("source-ledger.json"):
            needles.texts.update(_ledger_names(data))
    for d in sorted(domains):
        # Any whole-word use: wiki/music, domain: music, "music", Music.
        needles.patterns.append(re.compile(rf"(?<![\w-]){d}(?![\w-])",
                                           re.IGNORECASE))
    phrases -= GENERIC_PHRASES
    if phrases:  # "rose pruning" from 2024-kim-rose-pruning
        alts = "|".join(re.escape(p).replace(r"\ ", r"[\s-]+")
                        for p in sorted(phrases, key=len, reverse=True))
        needles.patterns.append(re.compile(
            rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE))
    needles.patterns.append(_EMAIL_RE)
    needles.patterns.extend(_HISTORY_RES)
    needles.texts.update({repo.resolve().name,
                          repo.resolve().name.replace("_", "-"),
                          f"Users/{Path.home().name}",
                          f"Users\\{Path.home().name}"})
    url = _common.run_git(repo, "remote", "get-url", "origin").stdout.strip()
    if url:  # owner/name of the private repo
        needles.texts.add("/".join(
            url.removesuffix(".git").replace(":", "/").split("/")[-2:]))
    email = _common.run_git(repo, "config", "user.email").stdout.strip()
    if email and not email.endswith(NOREPLY_SUFFIX):
        needles.texts.add(email)
    needles.words = {w for w in needles.words
                     if w.lower() not in GENERIC_WORDS | GENERIC_STEMS}
    needles.texts -= GENERIC_WORDS
    return needles


def _short_name(stem: str) -> str:
    """The words of a page name without year and author.

    ``2024-kim-rose-pruning`` gives ``rose pruning`` and
    ``2024-kim-rosebush`` gives ``rosebush``. A one-word concept name gives
    ``""`` (the stem is already matched as a whole word), and so does a
    one-word source name under 6 letters, which is too common to scan for.
    """
    match = re.fullmatch(r"\d{4}-[a-z0-9]+-(.+)", stem)
    words = (match.group(1) if match else stem).split("-")
    if len(words) >= 2:
        return " ".join(words)
    return words[0] if match and len(words[0]) >= 6 else ""


def _title(data: bytes) -> list[str]:
    fm, _ = _common.split_frontmatter(data.decode("utf-8", "replace"))
    title = (fm or {}).get("title")
    return [title.strip()] if isinstance(title, str) and \
        len(title.strip()) >= 3 else []


def _aliases(data: bytes) -> list[str]:
    fm, _ = _common.split_frontmatter(data.decode("utf-8", "replace"))
    aliases = (fm or {}).get("aliases")
    if not isinstance(aliases, list):
        return []
    return [a for a in aliases if isinstance(a, str) and len(a.strip()) >= 2]


def _ledger_names(data: bytes) -> set[str]:
    try:
        entries = json.loads(data.decode("utf-8"))
    except ValueError:
        return set()
    names = set()
    for entry in entries if isinstance(entries, list) else []:
        locator = entry.get("locator") if isinstance(entry, dict) else None
        if isinstance(locator, str) and locator.startswith("raw/"):
            name = PurePosixPath(locator).stem
            if len(name) >= 6:
                names.add(name)
    return names


def _read_commit(repo: Path, ref: str) -> dict[str, bytes]:
    archive = subprocess.run(["git", "-C", str(repo), "archive", ref],
                             check=True, capture_output=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if member.isfile():
                files[member.name] = tar.extractfile(member).read()
    return files


def plan(files: dict[str, bytes]) -> dict[str, bytes]:
    """Returns the template's files: allowed paths plus the overlay.

    Raises:
        ExportError: A tracked path is neither allowed nor excluded, or the
            overlay is empty.
    """
    unknown = sorted(p for p in files if classify(p) == "unknown")
    if unknown:
        raise ExportError("沒有歸類的檔案（加進 public_export.py 的 ALLOWED 或"
                          " EXCLUDED）：" + "、".join(unknown))
    out = {p: d for p, d in files.items() if classify(p) == "allowed"}
    overlay = {p[len(OVERLAY):]: d for p, d in files.items()
               if p.startswith(OVERLAY)}
    if not overlay:
        raise ExportError(f"找不到 {OVERLAY} 的模板檔案")
    out.update(overlay)
    return out


def leaks(out: dict[str, bytes], needles: Needles) -> dict[str, list[str]]:
    """Maps each template file to the needles found in it."""
    found = {}
    for path, data in sorted(out.items()):
        hits = needles.find(path + "\n" + data.decode("utf-8", "replace"))
        if hits:
            found[path] = hits
    return found


def _prepare_target(target: Path, replace: bool) -> None:
    """Makes sure the target is empty apart from ``.git``."""
    target.mkdir(parents=True, exist_ok=True)
    others = [p for p in target.iterdir() if p.name != ".git"]
    if others and not replace:
        raise ExportError(
            f"目標資料夾不是空的：{target}（要清空請加 --replace）")
    for item in others:
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def export(ref: str, target: Path, replace: bool = False,
           repo: Path = ROOT) -> dict[str, int]:
    """Writes the blank template of ``ref`` into ``target``.

    Nothing is written when a path is unclassified or the leak check finds
    something.

    Returns:
        Counts of written and skipped files.

    Raises:
        ExportError: The target is unsafe, a path is unclassified, or the
            leak check found strings from this repo.
    """
    target = target.resolve()
    here = repo.resolve()
    if target == here or here in target.parents or target in here.parents:
        raise ExportError("目標資料夾不能是這個 repo、在它裡面或包含它")
    files = _read_commit(repo, ref)
    out = plan(files)
    found = leaks(out, build_needles(files, repo))
    if found:
        lines = [f"- {p}：{'、'.join(h[:8])}" for p, h in found.items()]
        raise ExportError("模板裡有這個 wiki 的內容，沒有寫入：\n"
                          + "\n".join(lines))
    _prepare_target(target, replace)
    for path, data in out.items():
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return {"written": len(out), "skipped": len(files) - len(out)}


def commit(target: Path, message: str) -> str:
    """Commits everything in the target; never pushes.

    Returns:
        The diffstat summary, or an empty string when nothing changed.

    Raises:
        ExportError: The target is not a git repo or its e-mail is not a
            GitHub noreply address.
    """
    if not (target / ".git").exists():
        raise ExportError(f"{target} 不是 git repo（先 git init 並設定"
                          " noreply 信箱）")
    email = _common.run_git(target, "config", "user.email").stdout.strip()
    if not email.endswith(NOREPLY_SUFFIX):
        raise ExportError(f"目標 repo 的 user.email 不是 GitHub noreply 信箱："
                          f"{email or '（未設定）'}")
    _common.git(target, "add", "-A")
    stat = _common.git(target, "diff", "--cached", "--shortstat").strip()
    if stat:
        _common.git(target, "commit", "-q", "-m", message)
    return stat


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--commit", metavar="MSG")
    args = parser.parse_args(argv)
    try:
        stats = export(args.ref, args.target, args.replace)
        print(f"寫入 {stats['written']} 個檔案，略過 {stats['skipped']} 個")
        if args.commit:
            stat = commit(args.target.resolve(), args.commit)
            print(f"已 commit（未 push）：{stat}" if stat
                  else "沒有變更，未 commit")
    except ExportError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
