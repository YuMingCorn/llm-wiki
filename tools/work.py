"""Work coordination between parallel sessions and agents.

Two pieces, both stored under ``.llm-wiki/work/`` (local, not in git):

- **claims**: before a session writes a shared page (a topic page, the
  index, a page three or more pages link to), it claims it. Each claim is one
  file created exclusively, so two sessions cannot both win; a claim expires
  after its TTL, so a crashed session does not block forever.
- **snapshots**: before dispatching agents, the main conversation saves the
  size and mtime of every repo file except caches; ``changed`` later lists
  what changed since, minus the files the caller says it edited itself and
  the folders the agent was allowed to write (CLAUDE.md 分工原則).

Usage:
  uv run python -I tools/work.py claim <path ...> --by <name> [--ttl 120]
                                       [--note "..."]
  uv run python -I tools/work.py release <path ...> --by <name>
  uv run python -I tools/work.py status
  uv run python -I tools/work.py snapshot <name>
  uv run python -I tools/work.py changed <name> [--mine <path ...>]
                                       [--allow <folder ...>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths  # noqa: E402

WORK_DIR = ".llm-wiki/work"
CLAIMS = f"{WORK_DIR}/claims"
SNAPSHOTS = f"{WORK_DIR}/snapshots"
DEFAULT_TTL_MINUTES = 120
_NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def _key(path: str) -> str:
    """Normalises a repo-relative path (``./``, ``..``, backslashes)."""
    return posixpath.normpath(Path(path).as_posix())


def _claim_file(root: Path, path: str) -> Path:
    # Windows paths are case-insensitive: Wiki/INDEX.md is wiki/index.md.
    name = _key(path).casefold() if sys.platform == "win32" else _key(path)
    digest = hashlib.sha1(name.encode()).hexdigest()[:16]
    return root / CLAIMS / f"{digest}.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _read(file: Path) -> dict | None:
    """Reads a claim; a missing, unreadable or malformed file gives None."""
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        dt.datetime.fromisoformat(data["expires"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data.get("by"), str) else None


def _expired(claim: dict, now: dt.datetime) -> bool:
    return dt.datetime.fromisoformat(claim["expires"]) <= now


def _remove(file: Path) -> bool:
    """Deletes a claim file; False when another process holds it open."""
    try:
        file.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def claim(root: Path, paths: Sequence[str], by: str,
          ttl_minutes: int = DEFAULT_TTL_MINUTES, note: str = "",
          now: dt.datetime | None = None) -> list[dict]:
    """Claims every path for ``by``, or none of them.

    Re-claiming one's own path renews it; an expired claim of someone else is
    taken over.

    Returns:
        The claims written.

    Raises:
        ValueError: A path is held by someone else (nothing is claimed).
    """
    if not by.strip():
        raise ValueError("--by 要寫認領者名稱")
    if ttl_minutes < 1:
        raise ValueError("--ttl 至少 1 分鐘")
    now = now or _now()
    blocked = []
    for path in paths:
        held = _read(_claim_file(root, path))
        if held and held["by"] != by and not _expired(held, now):
            blocked.append(
                f"{_key(path)}（{held['by']}，到 {held['expires']}）")
    if blocked:
        raise ValueError("已被認領：" + "、".join(blocked))
    (root / CLAIMS).mkdir(parents=True, exist_ok=True)
    written = []
    for path in paths:
        record = {"path": _key(path), "by": by, "note": note,
                  "claimed": now.isoformat(timespec="seconds"),
                  "expires": (now + dt.timedelta(minutes=ttl_minutes))
                  .isoformat(timespec="seconds")}
        file = _claim_file(root, path)
        # A file here is ours, expired or malformed (checked above): replace
        # it. Losing a race to another claimer undoes this call's claims.
        try:
            if file.exists() and not _remove(file):
                raise FileExistsError(file)
            fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            # Undo this call's own fresh files directly; nobody else has
            # touched them yet.
            for done in written:
                _claim_file(root, done["path"]).unlink(missing_ok=True)
            raise ValueError(
                f"已被認領：{_key(path)}（同時有人認領）") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        written.append(record)
    return written


def release(root: Path, paths: Sequence[str], by: str) -> int:
    """Releases the caller's own claims; others' claims are left alone."""
    count = 0
    for path in paths:
        file = _claim_file(root, path)
        held = _read(file)
        if held and held["by"] == by and _remove(file):
            count += 1
    return count


def status(root: Path, now: dt.datetime | None = None) -> list[dict]:
    """Lists live claims; expired claim files are removed."""
    now = now or _now()
    out = []
    folder = root / CLAIMS
    if not folder.is_dir():
        return out
    for file in sorted(folder.glob("*.json")):
        held = _read(file)
        if held is None or _expired(held, now):
            _remove(file)
            continue
        out.append(held)
    return sorted(out, key=lambda c: c["path"])


# Skipped when scanning: version control, environments, and caches that tools
# rewrite on their own (OCR, search index, models, the snapshots themselves).
SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache",
                       ".ruff_cache", "node_modules", ".obsidian",
                       ".vault-meta"})
SKIP_PREFIXES = (".llm-wiki/work/", ".llm-wiki/ocr/", ".llm-wiki/search/",
                 ".llm-wiki/models/")


def _signatures(root: Path) -> dict[str, str]:
    """Maps every repo file to ``size:mtime_ns``.

    Size and modification time instead of a hash: the repo holds over 1 GB
    under ``raw/`` and ``.llm-wiki/``, and a rewrite always moves mtime.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            file = Path(dirpath) / filename
            rel = file.relative_to(root).as_posix()
            if rel.startswith(SKIP_PREFIXES):
                continue
            try:
                st = file.stat()
            except OSError:
                continue
            out[rel] = f"{st.st_size}:{st.st_mtime_ns}"
    return out


def _snapshot_path(root: Path, name: str) -> Path:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"快照名稱只能用英數字、-、_、.：{name}")
    return root / SNAPSHOTS / f"{name}.json"


def snapshot(root: Path, name: str) -> int:
    """Saves the signature of every repo file (see ``SKIP_DIRS``); returns
    the count.

    Raises:
        ValueError: The name has characters other than letters, digits,
            ``-``, ``_`` and ``.``.
    """
    path = _snapshot_path(root, name)
    signatures = _signatures(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signatures, indent=0), encoding="utf-8")
    return len(signatures)


def changed(root: Path, name: str, mine: Sequence[str] = (),
            allow: Sequence[str] = ()) -> dict:
    """Lists repo files added, modified or deleted since a snapshot.

    Args:
        root: Repository root.
        name: The snapshot name.
        mine: Files the caller edited itself in the meantime; they are
            reported separately, not as others' changes.
        allow: Folders the agent was allowed to write, such as
            ``.llm-wiki/extract/<名稱>/``; changes inside are reported under
            ``allowed``.

    Raises:
        ValueError: The snapshot does not exist.
    """
    path = _snapshot_path(root, name)
    if not path.is_file():
        raise ValueError(f"沒有這個快照：{name}")
    before = json.loads(path.read_text(encoding="utf-8"))
    after = _signatures(root)
    diff = {
        "added": sorted(after.keys() - before.keys()),
        "deleted": sorted(before.keys() - after.keys()),
        "modified": sorted(k for k in before.keys() & after.keys()
                           if before[k] != after[k]),
    }
    own = {_key(m) for m in mine}
    prefixes = tuple(_key(a).rstrip("/") + "/" for a in allow)
    changes = [p for v in diff.values() for p in v]
    return {
        "others": {k: [p for p in v if p not in own
                       and not p.startswith(prefixes)]
                   for k, v in diff.items()},
        "mine": sorted(p for p in changes if p in own),
        "allowed": sorted(p for p in changes
                          if p not in own and p.startswith(prefixes)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; exit 1 when a claim is refused or a snapshot is missing."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("claim")
    c.add_argument("paths", nargs="+")
    c.add_argument("--by", required=True)
    c.add_argument("--ttl", type=int, default=DEFAULT_TTL_MINUTES,
                   help="minutes")
    c.add_argument("--note", default="")
    r = sub.add_parser("release")
    r.add_argument("paths", nargs="+")
    r.add_argument("--by", required=True)
    sub.add_parser("status")
    s = sub.add_parser("snapshot")
    s.add_argument("name")
    d = sub.add_parser("changed")
    d.add_argument("name")
    d.add_argument("--mine", nargs="*", default=[])
    d.add_argument("--allow", nargs="*", default=[],
                   help="folders the agent may write, e.g. .llm-wiki/extract/<名稱>/")
    args = ap.parse_args(argv)
    root = _paths.ROOT
    try:
        if args.command == "claim":
            done = claim(root, args.paths, args.by, args.ttl, args.note)
            print(f"已認領 {len(done)} 個，到 {done[-1]['expires']}")
        elif args.command == "release":
            print(f"已釋放 {release(root, args.paths, args.by)} 個")
        elif args.command == "status":
            rows = status(root)
            for row in rows:
                print(f"{row['path']}\t{row['by']}\t{row['expires']}\t"
                      f"{row['note']}")
            print(f"共 {len(rows)} 個認領")
        elif args.command == "snapshot":
            print(f"已存快照 {args.name}：{snapshot(root, args.name)} 個檔案")
        else:
            print(json.dumps(changed(root, args.name, args.mine, args.allow),
                             ensure_ascii=False, indent=1))
    except ValueError as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
