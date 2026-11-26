"""Tests for the autopilot hooks (no real repo state is touched)."""

import json
import subprocess
from pathlib import Path

import pytest

import autopilot
import ledger

GH = "gh" + "p_" + "a1B2" * 9


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    raw = tmp_path / "raw/sources"
    (raw / "x_images").mkdir(parents=True)
    (raw / "known.pdf").write_bytes(b"known")
    (raw / "fresh.md").write_text(f"secret {GH}", encoding="utf-8")
    (raw / "x_images" / "img.png").write_bytes(b"png")
    (raw / ".gitkeep").write_text("", encoding="utf-8")
    page = tmp_path / "wiki/music/sources/known.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: source\n---\n# k\n", encoding="utf-8")
    ledger.add_source(tmp_path, "raw/sources/known.pdf", "wiki/music/sources/known.md")
    (tmp_path / "wiki/log.md").write_text("# log\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_new_sources_skip_known_images_and_dotfiles(repo: Path) -> None:
    rows = autopilot.new_sources(repo)
    assert rows == [{"path": "raw/sources/fresh.md", "personal": False, "status": "new",
                     "extracted": False, "deferred": False}]
    (repo / "raw/sources/known.pdf").write_bytes(b"edited")
    changed = {r["path"]: r["status"] for r in autopilot.new_sources(repo)}
    assert changed["raw/sources/known.pdf"] == "changed"


def test_brief_asks_to_start_ingest_and_flags_secrets(repo: Path) -> None:
    text = autopilot.brief(repo)
    assert "raw/sources/fresh.md" in text
    assert "停在第 3 步確認點" in text
    assert "github_token" in text and GH not in text


def test_pre_write_blocks_secrets_only_in_guarded_dirs(repo: Path) -> None:
    wiki_write = {"tool_input": {"file_path": str(repo / "wiki/music/x.md"), "content": GH}}
    code, message = autopilot.hook_pre_write(repo, wiki_write)
    assert code == 2 and "github_token" in message and GH not in message
    other = {"tool_input": {"file_path": str(repo / "tools/x.py"), "content": GH}}
    assert autopilot.hook_pre_write(repo, other) == (0, "")
    edit = {"tool_input": {"file_path": str(repo / "hackmd_note/a.md"),
                           "edits": [{"new_string": GH}]}}
    assert autopilot.hook_pre_write(repo, edit)[0] == 2


def test_post_edit_reports_page_problems(repo: Path) -> None:
    page = repo / "wiki/music/concepts/c.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: c\ntype: concept\n---\n# c\n\n[x](missing.md)\n",
                    encoding="utf-8")
    result = autopilot.hook_post_edit(repo, {"tool_input": {"file_path": str(page)}})
    assert result["decision"] == "block"
    assert "壞連結：missing.md" in result["reason"]
    assert "frontmatter：missing: domain" in result["reason"]
    assert autopilot.hook_post_edit(repo, {"tool_input": {"file_path": str(repo / "wiki/log.md")}}) is None


def test_session_digest_lists_commits_and_pending(repo: Path) -> None:
    autopilot.hook_session_start(repo, {"session_id": "abcdef1234"})
    (repo / "wiki/log.md").write_text("# log\n\n## [2026-11-15] ingest | X\n- y\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "docs(wiki): ingest X")
    (repo / "wiki/music/sources/known.md").write_text("changed", encoding="utf-8")
    path = autopilot.hook_session_end(repo, {"session_id": "abcdef1234"})
    text = path.read_text(encoding="utf-8")
    assert "docs(wiki): ingest X" in text
    assert "[2026-11-15] ingest | X" in text
    assert "wiki/music/sources/known.md" in text
    assert not list((repo / autopilot.SESSIONS).glob(".start-*"))


def test_run_hook_never_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autopilot, "_hook_input", lambda: {"session_id": "s"})
    monkeypatch.setattr(autopilot, "brief", lambda root: 1 / 0)
    assert autopilot.run_hook(repo, "session-start") == 0


def test_hook_cli_reads_stdin(repo: Path) -> None:
    out = subprocess.run(
        ["uv", "run", "python", "-I", str(Path(autopilot.__file__)), "hook", "pre-write"],
        input=json.dumps({"tool_input": {"file_path": "C:/elsewhere/x.md", "content": "x"}}),
        capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0


def test_commit_refuses_and_unstages_secret_files(repo: Path) -> None:
    import wiki

    (repo / "x.pem").write_text("-----BEGIN " + "RSA PRIVATE KEY-----\n", encoding="utf-8")
    (repo / "u16.md").write_bytes("plain".encode("utf-16"))
    with pytest.raises(ValueError, match="密鑰"):
        wiki.commit(repo, "chore(repo): add", ["x.pem", "u16.md"])
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"],
                            capture_output=True, text=True).stdout.split()
    assert "x.pem" not in staged and "u16.md" in staged


def test_post_edit_only_hints_for_broken_links(repo: Path) -> None:
    page = repo / "wiki/music/concepts/ok.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: ok\ntype: concept\ndomain: music\ntags: []\nsources: []\n"
                    "created: 2026-11-15\nupdated: 2026-11-15\nstatus: draft\n---\n"
                    "# ok\n\n[later](later.md)\n", encoding="utf-8")
    result = autopilot.hook_post_edit(repo, {"tool_input": {"file_path": str(page)}})
    assert "decision" not in result
    assert "later.md" in result["hookSpecificOutput"]["additionalContext"]


def test_post_edit_hints_legacy_status_without_blocking(repo: Path) -> None:
    page = repo / "wiki/music/concepts/old.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: old\ntype: concept\ndomain: music\ntags: []\n"
                    "sources: []\ncreated: 2026-11-15\nupdated: 2026-11-15\n"
                    "status: needs-review\n---\n# old\n", encoding="utf-8")
    result = autopilot.hook_post_edit(repo, {"tool_input": {"file_path": str(page)}})
    assert "decision" not in result
    assert "應為 disputed" in result["hookSpecificOutput"]["additionalContext"]


def test_session_id_is_sanitised(repo: Path) -> None:
    assert autopilot._session_name({"session_id": "../../evil"}) == "______evil"


def test_read_and_deferred_sources_are_not_restarted(repo: Path) -> None:
    extract = repo / ".llm-wiki/extract/fresh/extraction.md"
    extract.parent.mkdir(parents=True)
    extract.write_text("# x", encoding="utf-8")
    text = autopilot.brief(repo)
    assert "已讀過、還在等確認" in text and "自動開始 /wiki-ingest" not in text
    autopilot.defer(repo, ["raw/sources/fresh.md"])
    text = autopilot.brief(repo)
    assert "暫緩的來源 1 份" in text and "已讀過" not in text
    autopilot.defer(repo, ["raw/sources/fresh.md"], undo=True)
    assert "已讀過" in autopilot.brief(repo)
