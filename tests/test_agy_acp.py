"""Tests for the permission policy of tools/agy_acp.py."""

from pathlib import Path

import agy_acp

ROOT = Path("D:/proj").resolve()
WRITE = [ROOT / ".llm-wiki" / "extract" / "x"]
DOC = "uv run python -I tools/doc.py show raw/a.pdf"
PDF = "uv run python -I tools/pdf_tool.py shot raw/a.pdf --pages 2"
SLIDES = "uv run python -I tools/slides.py"
X = ".llm-wiki/extract/x"


def _decide(tool_call: dict) -> bool:
    return agy_acp.decide(tool_call, cwd=ROOT, write_dirs=WRITE,
                          commands=agy_acp.DEFAULT_COMMANDS)[0]


def _cmd(line: str, **extra: str) -> dict:
    return {"kind": "execute", "rawInput": {"CommandLine": line, **extra}}


def _edit(path: str, **extra: str) -> dict:
    return {"kind": "edit", "rawInput": {"Path": path, **extra},
            "locations": [{"path": path}]}


def test_whitelisted_read_command_allowed() -> None:
    assert _decide(_cmd(DOC + " --pages 1-3"))


def test_unknown_command_denied() -> None:
    assert not _decide(_cmd("git commit -m x"))
    assert not _decide(_cmd("uv --version"))
    assert not _decide(_cmd("uv run python -I tools/doc.py"))
    assert not _decide(_cmd("uv run python -I tools/doc.py\tshow a"))


def test_unsafe_characters_denied() -> None:
    tails = (" | head", "; git push", " > wiki/a.md", " && rm x",
             " $(rm x)", "\ngit push", " (git push)", " @(1)", " $env:X",
             " %PATH%", " ^& calc", " {x}", ' "a b"', " 'a'", " `x`",
             "\x0b", "\u2028")
    for tail in tails:
        assert not _decide(_cmd(DOC + tail)), repr(tail)


def test_out_must_be_in_write_dir() -> None:
    assert _decide(_cmd(f"{PDF} --out {X}/shots"))
    assert _decide(_cmd(f"{PDF} --out={X}/shots"))
    for bad in ("--out wiki/assets/a", "--out=wiki/a", "--o wiki/a",
                "--ou wiki/a", "-owiki/a", "-o=wiki/a", "--out",
                f'--out {X}/""../../a', f"--out {X}/../../a"):
        assert not _decide(_cmd(f"{PDF} {bad}")), bad


def test_pdf_tool_needs_out() -> None:
    assert not _decide(_cmd(PDF))
    assert not _decide(_cmd(
        "uv run python -I tools/pdf_tool.py figures raw/a.pdf"))
    assert _decide(_cmd("uv run python -I tools/pdf_tool.py toc raw/a.pdf"))


def test_slides_targets_checked() -> None:
    assert _decide(_cmd(f"{SLIDES} check slides/s/outline.md"))
    assert _decide(_cmd(f"{SLIDES} build {X}/outline.md"))
    assert not _decide(_cmd(f"{SLIDES} build slides/s/outline.md"))
    assert not _decide(_cmd(f"{SLIDES} build {X}/o.md -owiki/z.pptx"))
    assert not _decide(_cmd(f"{SLIDES} build"))


def test_command_cwd_must_be_root() -> None:
    assert _decide(_cmd(DOC, Cwd=str(ROOT)))
    assert not _decide(_cmd(DOC, Cwd=str(ROOT / "wiki")))


def test_edit_paths() -> None:
    assert _decide(_edit(str(ROOT / X / "a.md")))
    assert _decide(_edit("/" + str(ROOT / X / "a.md").replace("\\", "/")))
    assert _decide(_edit(f"{X}/sub/b.md", toolSummary="write to wiki/a"))
    assert not _decide(_edit(str(ROOT / "wiki/music/a.md")))
    assert not _decide(_edit(f"{X}/../../../wiki/a.md"))
    assert not _decide(_edit(".llm-wiki/extract/xy/a.md"))
    assert not _decide({"kind": "edit", "rawInput": {}})


def test_edit_extra_path_fields_checked() -> None:
    assert not _decide(_edit(f"{X}/a", NewPath="wiki/b"))
    assert not _decide(_edit(f"{X}/a", Destination="wiki/b"))
    chunks = {"kind": "edit", "rawInput": {
        "TargetFile": f"{X}/a.md",
        "ReplacementChunks": [{"TargetContent": "see wiki/x.md",
                               "ReplacementContent": "../../etc"}]}}
    assert _decide(chunks)
    # agy's replace tool: Target/Replacement are text, not paths.
    assert _decide({"kind": "edit", "rawInput": {
        "Path": str(ROOT / X / "ok.txt"), "Replacement": "../a",
        "Target": "ok"}})


def test_reads_limited_to_project() -> None:
    assert _decide({"kind": "read",
                    "rawInput": {"AbsolutePath": str(ROOT / "wiki/a.md")}})
    assert not _decide({"kind": "read", "rawInput": {
        "AbsolutePath": "C:/Users/me/.ssh/id_rsa"}})
    assert not _decide({"kind": "read",
                        "rawInput": {"Directory": "C:/Windows"}})
    assert not _decide({"kind": "read",
                        "rawInput": {"AbsolutePath": str(ROOT / ".env")}})
    assert not _decide({"kind": "read",
                        "rawInput": {"File": str(ROOT / ".mcp.json")}})


def test_command_read_paths_checked() -> None:
    for bad in ("show C:/Users/me/t.txt --lines 1-1", "grep x C:/Users",
                "grep x /D:/", "show .mcp.json --lines 1-5",
                "show .claude/settings.local.json", "show .git/config",
                "quote --from=C:/x/v.md", "show D:foo"):
        line = f"uv run python -I tools/doc.py {bad}"
        assert not _decide(_cmd(line)), bad
    assert not _decide(_cmd(f"{SLIDES} build {X}/o.md --template C:/x/y.pptx"))
    assert not _decide(_cmd(
        f"uv run python -I tools/pdf_tool.py figures C:/a.pdf --out {X}/f"))


def test_hidden_or_unknown_edit_keys_fail_closed() -> None:
    for extra in ({"ContextFile": "wiki/x.md"}, {"path_text": "wiki/z"},
                  {"Out": "wiki"}, {"Folder": "wiki"}, {"X": "wiki x/a"}):
        assert not _decide(_edit(f"{X}/a", **extra)), extra


def test_search_patterns_checked() -> None:
    assert not _decide({"kind": "search", "rawInput": {
        "Query": "x", "Pattern": "C:/Users/**"}})
    assert not _decide({"kind": "search", "rawInput": {
        "SearchPathPattern": "C:/Users/**"}})
    assert _decide({"kind": "search", "rawInput": {
        "Query": "x", "SearchPath": "wiki/music"}})


def test_expanding_path_forms_denied() -> None:
    for text in ("~/.ssh/id_rsa", "%USERPROFILE%/x", "$HOME/x",
                 "file://server/share/x.txt"):
        assert not _decide({"kind": "read",
                            "rawInput": {"AbsolutePath": text}}), text


def test_folders_holding_secrets_denied() -> None:
    for bad in ("grep foo .", "grep foo .claude", "grep foo D:/proj",
                "show credentials.json --lines 1-2", "grep foo .git"):
        line = f"uv run python -I tools/doc.py {bad}"
        assert not _decide(_cmd(line)), bad
    assert _decide(_cmd("uv run python -I tools/doc.py grep foo wiki/music"))
    assert not _decide({"kind": "search", "rawInput": {
        "SearchPath": str(ROOT), "Query": "KEY"}})
    assert not _decide({"kind": "search", "rawInput": {"Query": "KEY"}})


def test_chinese_grep_allowed() -> None:
    assert _decide(_cmd("uv run python -I tools/doc.py grep 注意力 wiki/music"))
    for bad in ("“x”", "（x）", "x\u2013y"):
        line = f"uv run python -I tools/doc.py grep {bad} wiki"
        assert not _decide(_cmd(line)), bad


def test_best_fit_lookalikes_denied() -> None:
    for ch in ("ǀ", "ʺ", "ˆ", "ǃ", "ʼ", "〇"):
        line = f"uv run python -I tools/doc.py grep x{ch} wiki"
        assert not _decide(_cmd(line)), hex(ord(ch))


def test_opaque_dirs_denied() -> None:
    for bad in (".venv", ".venv/lib", "node_modules", "node_modules/p"):
        line = f"uv run python -I tools/doc.py grep x {bad}"
        assert not _decide(_cmd(line)), bad


def test_secret_created_mid_run_is_seen(tmp_path: Path) -> None:
    (tmp_path / "wiki" / "sub").mkdir(parents=True)

    def ok(rel: str) -> bool:
        return agy_acp.decide(
            {"kind": "read", "rawInput": {"AbsolutePath": str(tmp_path / rel)}},
            cwd=tmp_path, write_dirs=[], commands=())[0]

    assert ok("wiki/sub")
    (tmp_path / "wiki" / "sub" / "credentials.json").write_text("{}")
    assert not ok("wiki/sub")
    assert not ok("wiki")
    # Opaque folders nested deeper protect every folder above them.
    (tmp_path / "pkg" / "node_modules" / "p").mkdir(parents=True)
    (tmp_path / "lib" / ".git").mkdir(parents=True)
    (tmp_path / "doc").mkdir()
    assert not ok("pkg")
    assert not ok("lib")
    assert ok("doc")


def test_slides_target_must_be_file() -> None:
    assert not _decide(_cmd(f"{SLIDES} render {X}"))
    assert _decide(_cmd(f"{SLIDES} render {X}/deck.pptx"))


def test_edit_extra_benign_keys() -> None:
    assert _decide(_edit(f"{X}/a.md", toolAction="Edit",
                         TargetLintErrorIds="abc"))
    assert not _decide({"kind": "edit", "rawInput": "x",
                        "locations": [{"path": f"{X}/a"}]})


def test_malformed_requests_denied() -> None:
    assert not _decide({"kind": "edit", "locations": ["wiki/a"],
                        "rawInput": "x"})
    assert not agy_acp.decide("x", cwd=ROOT, write_dirs=WRITE,
                              commands=agy_acp.DEFAULT_COMMANDS)[0]


def test_other_kinds_denied() -> None:
    assert not _decide({"kind": "fetch", "rawInput": {"Url": "https://x"}})
    assert not _decide({"kind": "delete", "rawInput": {"Path": f"{X}/a"}})
    assert not _decide({"kind": "", "rawInput": {}})
