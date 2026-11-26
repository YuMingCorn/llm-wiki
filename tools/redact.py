"""Finds credentials and other secrets before text reaches the wiki.

``raw/`` is read-only, so nothing there is rewritten. Instead every path out
of ``raw/`` is checked: session start (new sources), writes to
``wiki/`` and ``hackmd_note/`` (PreToolUse hook) and ``wiki.py commit``.

Two levels:

- ``block``: high-confidence secrets (private keys, provider API tokens,
  JWTs). Writes and commits that contain one are refused.
- ``warn``: ``password = "…"``-style assignments. Docs and papers often show
  placeholders, so these are only reported.

Findings never print the secret itself, only a masked preview.

Usage:
  uv run python -I tools/redact.py scan <path ...> [--json]
  uv run python -I tools/redact.py mask <file> --out <file>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _limits  # noqa: E402, F401

BLOCK_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"
        r"(?: BLOCK)?-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}"),
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
# Provider keys must mix digits and letters; kebab-case prose such as
# "sk-learn-compatible-estimator-..." does not.
_MIXED = {"openai_key", "anthropic_key"}
WARN_PATTERNS: dict[str, re.Pattern[str]] = {
    "credential_assignment": re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+_)?(?:password|passwd|pwd"
        r"|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']?(?P<value>[^\s\"'`]{6,})"),
}
# Placeholder values that are not secrets.
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:<.*>|\$\{?\w+\}?|x{4,}|\*{4,}|\.{3,}|your[_-]|example|changeme"
    r"|placeholder|dummy|redacted|none|null|true|false)")
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif",
    ".tiff", ".svgz", ".zip", ".gz", ".7z", ".tar", ".whl", ".exe", ".dll",
    ".so", ".pyc", ".npz", ".npy", ".onnx", ".bin", ".pt", ".pptx", ".docx",
    ".xlsx", ".mp3", ".mp4", ".wav", ".woff", ".woff2", ".ttf", ".otf"})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".html", ".htm", ".json", ".tsv",
                           ".csv", ".yaml", ".yml", ".py", ".js", ".ts",
                           ".toml", ".ini", ".cfg", ".env", ".tex", ".xml"})


@dataclass(frozen=True)
class Finding:
    """One suspected secret."""

    path: str
    line: int
    kind: str
    level: str
    preview: str


def _mask(secret: str) -> str:
    return secret[:4] + "…" + f"（{len(secret)} 字元）"


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    """Scans text line by line."""
    out: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in BLOCK_PATTERNS.items():
            for m in pattern.finditer(line):
                secret = m.group(0)
                if kind == "openai_key" and secret.startswith("sk-ant-"):
                    continue
                if kind in _MIXED and not (
                        re.search(r"\d", secret) and re.search(r"[A-Z]", secret)
                        and re.search(r"[a-z]", secret)):
                    continue
                out.append(Finding(path, number, kind, "block",
                                   _mask(m.group(0))))
        for kind, pattern in WARN_PATTERNS.items():
            for m in pattern.finditer(line):
                value = m.group("value")
                if _PLACEHOLDER_RE.match(value):
                    continue
                out.append(Finding(path, number, kind, "warn", _mask(value)))
    return out


def _pdf_text(path: Path) -> str:
    import docsrc  # noqa: PLC0415  (pulls in the PDF reader only when needed)

    doc = docsrc.open_document(str(path))
    return "\n".join(line.text for line in doc.lines())


def scan_file(path: Path, label: str | None = None) -> list[Finding]:
    """Scans one file; PDFs through docsrc, other binaries are skipped."""
    label = label or path.as_posix()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            text = _pdf_text(path)
        except Exception:  # noqa: BLE001  (unreadable PDF: nothing to scan)
            return []
    elif suffix in BINARY_SUFFIXES:
        return []
    else:
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return []
    return scan_text(text, label)


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    """Scans files and directories (recursively)."""
    out: list[Finding] = []
    for path in paths:
        files = sorted(p for p in path.rglob("*") if p.is_file()) \
            if path.is_dir() else [path]
        for file in files:
            out.extend(scan_file(file))
    return out


def blocking(findings: Iterable[Finding]) -> list[Finding]:
    """Keeps only the findings that must stop a write or upload."""
    return [f for f in findings if f.level == "block"]


def mask_text(text: str) -> str:
    """Replaces every block-level secret with a ``[REDACTED:kind]`` marker."""
    for kind, pattern in BLOCK_PATTERNS.items():
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


def format_findings(findings: Sequence[Finding]) -> str:
    """One line per finding, secret masked."""
    return "\n".join(f"{f.level}\t{f.kind}\t{f.path}:{f.line}\t{f.preview}"
                     for f in findings)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; scan exits 1 when a block-level secret is found."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan")
    s.add_argument("paths", nargs="+")
    s.add_argument("--json", action="store_true")
    m = sub.add_parser("mask")
    m.add_argument("file")
    m.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.command == "mask":
        out = Path(args.out)
        if out.resolve().as_posix().find("/raw/") >= 0:
            print("錯誤：raw/ 是唯讀，不能寫入", file=sys.stderr)
            return 1
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        out.write_text(mask_text(text), encoding="utf-8")
        print(f"已寫入 {out}")
        return 0
    findings = scan_paths(Path(p) for p in args.paths)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False,
                         indent=1))
    else:
        print(format_findings(findings) or "沒有發現")
    return 1 if blocking(findings) else 0


if __name__ == "__main__":
    sys.exit(main())
