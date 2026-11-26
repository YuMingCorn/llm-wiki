"""Runs one antigravity CLI (agy) task through ACP with a whitelist policy.

``agy -p`` cannot ask for permission, so it denies every shell command. This
tool starts ``agy acp`` (Agent Client Protocol over stdio), sends one prompt,
and answers each permission request itself:

- execute: the command line may use only letters, digits, spaces and
  ``_ - . / : = ,`` (no quotes, brackets, ``$ % ^ @``, ``..`` or control
  characters), must start with an allowed prefix, and must run in the
  project root. ``pdf_tool.py`` needs ``--out``; ``--out`` / ``-o`` values and
  ``slides.py build/render`` paths must lie in a write directory; any other
  option starting with ``-o`` or ``--o`` is denied.
- edit: every path-like value in the request must lie in a write directory.
- read/search: every path-like value must lie in the project and must not be
  a secrets file.
- anything else (fetch, delete, unknown kinds): denied.

Usage (project root):
  uv run python -I tools/agy_acp.py --prompt-file P --write DIR [--write DIR]
      [--allow-cmd PREFIX] [--model ID] [--out FILE] [--log FILE]
      [--timeout SEC]

The agent's messages go to stdout (or --out). The log records every
permission decision. Exit 0 when the turn ends normally with no unresolved
denial; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _limits  # noqa: E402, F401  (resource limits for this process tree)

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COMMANDS = (
    "uv run python -I tools/doc.py ",
    "uv run python -I tools/pdf_tool.py ",
    "uv run python -I tools/slides.py ",
)
MAX_RESUMES = 5
RESUME_PROMPT = (
    "下面的動作被權限規則拒絕了：\n{denied}\n"
    "不要再試同樣的動作。改用允許的方式（讀檔、在允許的資料夾寫檔、"
    "白名單內的 tools/ 指令；指令只能用文字、數字、空白與 _ - . / : = , "
    "這些字元，不能有引號、括號、管線或 ..，路徑要指到具體的檔案或"
    "不含設定檔的資料夾），"
    "繼續完成原本的任務；做不到的部分在最後回報裡說明。")
# ASCII letters and digits, CJK ideographs (for Chinese grep patterns),
# space and a few separators. Other Unicode letters are out: Windows
# best-fit code-page conversion can turn some into " | ^ ' (sonnet-checker).
SAFE_COMMAND = re.compile(
    r"[A-Za-z0-9_ \-./:=,㐀-䶿一-鿿豈-﫿]+")
READ_KINDS = {"read", "search"}
PATH_KEY = re.compile(r"path|file|dir|target|dest|uri|location|folder|out",
                      re.I)
# Exact key names (lower case) whose values are text, not paths.
CONTENT_KEYS = {
    "content", "codecontent", "target", "replacement", "targetcontent",
    "replacementcontent", "toolsummary", "description", "instruction",
    "codemarkdownlanguage", "query", "explanation", "title", "toolaction",
    "targetlinterrorids", "complexity"}
# Never read and never scanned: git internals and third-party trees.
OPAQUE_DIRS = {".git", ".venv", "node_modules"}
SECRET_NAMES = {".env", ".mcp.json", "settings.local.json", "credentials.json"}
# --write may only point inside these project folders.
WRITE_ROOTS = (".llm-wiki", "slides")


def _bad_path_text(text: str) -> bool:
    """Path spellings the policy refuses outright (expansion, odd URIs)."""
    return (text.startswith("~") or "%" in text or "$" in text
            or re.match(r"^[A-Za-z]:(?![/\\])", text) is not None
            or (text.startswith("file:") and not text.startswith("file:///")))


def _to_path(text: str, cwd: Path) -> Path:
    """Turns an agy path (/D:/x, a Windows path, relative) into a Path."""
    if text.startswith("file:///"):
        text = text[len("file:///"):]
    if re.match(r"^/[A-Za-z]:[/\\]", text):
        text = text[1:]
    path = Path(text)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _inside(path: Path, dirs: list[Path]) -> bool:
    return any(path == d or path.is_relative_to(d) for d in dirs)


def _secret_spots(cwd: Path) -> tuple[Path, ...]:
    """Secrets files under the project (fixed names plus a fresh scan).

    Rescanned on every call (~30 ms) so a file created mid-run is caught.
    """
    spots = {cwd / ".git", cwd / ".env", cwd / ".mcp.json",
             cwd / ".claude" / "settings.local.json"}
    for top, dirs, files in os.walk(cwd):
        # An opaque folder at any depth is itself a spot, so every folder
        # above it is protected too; its contents are not scanned.
        spots.update(Path(top) / d for d in dirs
                     if d.lower() in OPAQUE_DIRS)
        dirs[:] = [d for d in dirs if d.lower() not in OPAQUE_DIRS]
        spots.update(Path(top) / f for f in files
                     if f.lower() in SECRET_NAMES)
    return tuple(spots)


def _protected(path: Path, cwd: Path) -> bool:
    """True for secrets, git internals, and folders that contain either.

    A folder counts because doc.py grep and agy's search recurse into it.
    """
    if path.name.lower() in SECRET_NAMES or OPAQUE_DIRS & {
            part.lower() for part in path.parts}:
        return True
    return any(spot == path or spot.is_relative_to(path)
               for spot in _secret_spots(cwd))


def _looks_like_path(text: str) -> bool:
    return bool(re.search(r"[/\\:]", text) or text.startswith((".", "~")))


def _strings(value: object, key: str = "") -> Iterator[tuple[str, str]]:
    """Yields (key, string) for nested strings, skipping text-valued keys."""
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, item in value.items():
            if str(k).lower() not in CONTENT_KEYS:
                yield from _strings(item, str(k))
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, key)


def _request_paths(tool_call: dict, *, every_string: bool) -> list[str]:
    """Collects the path strings a request names.

    With ``every_string`` (edits) every non-text string counts, so an
    unknown field fails closed. Otherwise a string counts when its key names
    a path or the string looks like one.
    """
    texts = [v for _, v in _strings(tool_call.get("locations"))]
    for key, value in _strings(tool_call.get("rawInput")):
        if not value.strip():
            continue
        if every_string or PATH_KEY.search(key) or _looks_like_path(value):
            texts.append(value)
    return texts


def _check_read(texts: list[str], cwd: Path) -> tuple[bool, str]:
    for text in texts:
        if _bad_path_text(text):
            return False, f"path form not allowed: {text}"
        path = _to_path(text, cwd)
        if not _inside(path, [cwd]):
            return False, f"read outside project: {path}"
        if _protected(path, cwd):
            return False, f"protected path (secrets or .git inside): {path}"
    return True, "read inside project"


def _check_command(line: str, cwd: Path, write_dirs: list[Path],
                   commands: tuple[str, ...]) -> tuple[bool, str]:
    """Checks one command line against the whitelist."""
    if not SAFE_COMMAND.fullmatch(line):
        return False, "command has characters outside the safe set"
    if ".." in line:
        return False, "command contains '..'"
    if not any(line.startswith(p) for p in commands):
        return False, "command not in whitelist"
    argv = line.split()
    tool = argv[4] if len(argv) > 4 else ""
    sub = argv[5] if len(argv) > 5 else ""
    targets: list[str] = []
    reads: list[str] = []
    has_out = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--out="):
            targets.append(arg[len("--out="):])
            has_out = True
        elif arg in ("--out", "-o"):
            if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
                return False, f"{arg} without a value"
            targets.append(argv[i + 1])
            has_out = True
            i += 1
        elif arg.startswith(("-o", "--o")):
            return False, f"ambiguous output option {arg}"
        elif "=" in arg and arg.startswith("-"):
            reads.append(arg.split("=", 1)[1])
        elif i >= 5 and not arg.startswith("-"):
            # Every argument after the tool is read-checked, bare names too.
            reads.append(arg)
        i += 1
    if tool.endswith("pdf_tool.py") and sub in ("shot", "figures", "split") \
            and not has_out:
        return False, "pdf_tool.py needs --out inside the write dirs"
    if tool.endswith("slides.py") and sub in ("build", "render"):
        if len(argv) < 7:
            return False, "slides.py without a path"
        if not argv[6].lower().endswith((".md", ".pptx")):
            return False, "slides.py target must be an .md or .pptx file"
        targets.append(argv[6])
    for target in targets:
        if not target or _bad_path_text(target) or not _inside(
                _to_path(target, cwd), write_dirs):
            return False, f"command writes outside write dirs: {target}"
    ok, reason = _check_read([r for r in reads if r], cwd)
    if not ok:
        return False, f"command {reason}"
    return True, "whitelisted command"


def decide(tool_call: dict, *, cwd: Path, write_dirs: list[Path],
           commands: tuple[str, ...]) -> tuple[bool, str]:
    """Returns (allowed, reason) for one permission request."""
    if not isinstance(tool_call, dict):
        return False, "malformed request"
    kind = tool_call.get("kind", "")
    raw = tool_call.get("rawInput")
    if not isinstance(raw, dict):
        raw = {}
    if kind == "execute":
        cwd_arg = raw.get("Cwd") or raw.get("cwd")
        if cwd_arg and _to_path(str(cwd_arg), cwd) != cwd:
            return False, "command must run in the project root"
        line = raw.get("CommandLine")
        if not isinstance(line, str):
            return False, "command line missing"
        return _check_command(line, cwd, write_dirs, commands)
    if kind == "edit":
        if not isinstance(tool_call.get("rawInput"), dict):
            return False, "edit without raw input"
        texts = _request_paths(tool_call, every_string=True)
        if not texts:
            return False, "edit without a path"
        for text in texts:
            if _bad_path_text(text):
                return False, f"path form not allowed: {text}"
            path = _to_path(text, cwd)
            if not _inside(path, write_dirs):
                return False, f"write outside write dirs: {path}"
        return True, "write inside write dirs"
    if kind in READ_KINDS:
        texts = _request_paths(tool_call, every_string=False)
        if not texts:
            # A search without a path covers the whole project, secrets too.
            return False, f"{kind} needs an explicit path"
        return _check_read(texts, cwd)
    return False, f"kind {kind or '?'} not allowed"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stops agy and every process it started."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        proc.kill()


class Client:
    """Minimal JSON-RPC client for ``agy acp`` over stdio."""

    def __init__(self, argv: list[str], cwd: Path, log: TextIO,
                 policy: Callable[[dict], tuple[bool, str]]) -> None:
        """Starts agy and the reader thread."""
        self.proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.log, self.policy = log, policy
        self.next_id, self.replies = 0, {}
        self.lock = threading.Lock()
        self.closed = False
        self.text: list[str] = []
        self.denied: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self.lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except OSError:
                self.closed = True

    def call(self, method: str, params: dict, timeout: float) -> dict:
        """Sends a request and waits for its reply."""
        self.next_id += 1
        box: queue.Queue = queue.Queue()
        self.replies[self.next_id] = box
        if self.closed:
            return {"error": {"message": "agy exited"}}
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                    "params": params})
        try:
            return box.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"{method} timed out after {timeout}s") from exc

    def note(self, line: str) -> None:
        """Writes one timestamped log line."""
        self.log.write(time.strftime("%H:%M:%S ") + line + "\n")
        self.log.flush()

    def _read(self) -> None:
        try:
            for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    try:
                        self._handle(msg)
                    except Exception as exc:  # noqa: BLE001
                        self.note(f"handler error: {exc!r}")
                        if "id" in msg:
                            self._answer(msg, False)
                elif msg.get("id") in self.replies:
                    self.replies[msg["id"]].put(msg)
        finally:
            self.closed = True
            for box in list(self.replies.values()):
                box.put({"error": {"message": "agy exited"}})

    def _answer(self, msg: dict, ok: bool) -> None:
        params = msg.get("params") if isinstance(msg.get("params"), dict) \
            else {}
        options = params.get("options") or []
        want = "allow_once" if ok else "reject_once"
        option = next((o for o in options if isinstance(o, dict)
                       and o.get("kind") == want), None)
        if option is None:
            result = {"outcome": {"outcome": "cancelled"}}
        else:
            result = {"outcome": {"outcome": "selected",
                                  "optionId": option.get("optionId")}}
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _handle(self, msg: dict) -> None:
        method, params = msg["method"], msg.get("params") or {}
        if method == "session/update":
            update = params.get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                self.text.append((update.get("content") or {}).get("text", ""))
            return
        if "id" not in msg:
            return
        if method == "session/request_permission":
            tool_call = params.get("toolCall") or {}
            ok, reason = self.policy(tool_call)
            title = tool_call.get("title") if isinstance(tool_call, dict) \
                else "?"
            if not ok:
                self.denied.append(f"{title}（{reason}）")
            kind = tool_call.get("kind") if isinstance(tool_call, dict) \
                else "?"
            self.note(f"{'ALLOW' if ok else 'DENY '} [{kind}] {title} — "
                      f"{reason}")
            if not ok and isinstance(tool_call, dict):
                raw = json.dumps(tool_call.get("rawInput"), ensure_ascii=False)
                self.note(f"  rawInput {raw[:300]}")
            self._answer(msg, ok)
            return
        self._send({"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": "not supported"}})

    def close(self) -> None:
        """Stops agy and its child processes."""
        _kill_tree(self.proc)


def main(argv: list[str] | None = None) -> int:
    """Runs one prompt and returns the exit code."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--write", action="append", required=True,
                        help="directory agy may write to (repeatable)")
    parser.add_argument("--allow-cmd", action="append", default=[],
                        help="extra allowed command prefix (repeatable)")
    parser.add_argument("--model", default="gemini-4-neon-high")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--timeout", type=float, default=3 * 3600)
    args = parser.parse_args(argv)

    exe = shutil.which("agy")
    if not exe:
        print("找不到 agy", file=sys.stderr)
        return 2
    try:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"讀不到說明檔：{exc}", file=sys.stderr)
        return 2
    cwd = ROOT
    write_dirs = [_to_path(d, cwd) for d in args.write]
    allowed_roots = [cwd / r for r in WRITE_ROOTS]
    for d in write_dirs:
        if not _inside(d, allowed_roots) or d in allowed_roots:
            print(f"寫入目錄要在 {'、'.join(WRITE_ROOTS)} 底下的子資料夾：{d}",
                  file=sys.stderr)
            return 2
    commands = DEFAULT_COMMANDS + tuple(
        p if p.endswith(" ") else p + " " for p in args.allow_cmd)
    log = (args.log.open("a", encoding="utf-8") if args.log
           else open(sys.stderr.fileno(), "w", encoding="utf-8", closefd=False))

    def policy(tool_call: dict) -> tuple[bool, str]:
        return decide(tool_call, cwd=cwd, write_dirs=write_dirs,
                      commands=commands)

    client = Client([exe, "acp"], cwd, log, policy)
    turns: list[str] = []
    code = 1
    try:
        client.call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False,
                                          "writeTextFile": False},
                                   "terminal": False}}, 120)
        new = client.call("session/new", {"cwd": str(cwd), "mcpServers": []},
                          120)
        if "error" in new:
            print(f"session/new 失敗：{new['error']}", file=sys.stderr)
            return 1
        sid = new["result"]["sessionId"]
        chosen = client.call("session/set_model",
                             {"sessionId": sid, "modelId": args.model}, 60)
        status = f"error {chosen['error']}" if "error" in chosen else "ok"
        client.note(f"model {args.model}: {status}")
        deadline = time.monotonic() + args.timeout
        for attempt in range(MAX_RESUMES + 1):
            client.denied.clear()
            client.text.clear()
            reply = client.call("session/prompt", {
                "sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
                max(deadline - time.monotonic(), 1))
            turns.append("".join(client.text))
            if "error" in reply:
                print(f"session/prompt 失敗：{reply['error']}", file=sys.stderr)
                break
            stop = reply["result"].get("stopReason")
            client.note(f"stop {stop}; usage {reply['result'].get('usage')}")
            if not client.denied:
                code = 0 if stop == "end_turn" else 1
                break
            if attempt == MAX_RESUMES:
                client.note("denials still pending after the last resume")
                break
            # agy ends the turn after any denial; resume with the reason.
            client.note(f"resume {attempt + 1}/{MAX_RESUMES}")
            prompt = RESUME_PROMPT.format(denied="\n".join(
                f"- {d}" for d in client.denied))
    except TimeoutError as exc:
        turns.append("".join(client.text))
        print(str(exc), file=sys.stderr)
    finally:
        client.close()
    text = "\n\n---\n\n".join(t for t in turns if t.strip())
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
