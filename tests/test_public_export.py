"""Tests for tools/public_export.py (blank public template)."""

import subprocess

import pytest

import public_export as pe

NOREPLY = "1+someone@users.noreply.github.com"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _make_repo(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", NOREPLY)
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


BASE = {
    "README.md": "# my wiki, 42 pages\n",
    "PURPOSE.md": "my purpose\n",
    "SCHEMA.md": "domain: <domain>\n",
    "tools/a.py": "x = 1\n",
    "tests/test_a.py": "def test(): pass\n",
    ".claude/skills/wiki-query/SKILL.md": "keep\n",
    ".claude/skills/tutor/SKILL.md": "third party\n",
    "wiki/index.md": "- [Rose](music/topics/rose-pruning.md)\n",
    "wiki/music/topics/rose-pruning.md":
        "---\ntitle: Thorn Care\naliases: [剪枝法]\n---\nx\n",
    "wiki/meta/ledgers/source-ledger.json":
        '[{"locator": "raw/sources/Secret Garden Handbook.pdf"}]',
    "wiki/music/sources/2024-kim-rosebush.md": "---\ntitle: k\n---\nx\n",
    "slides/s/outline.md": "deck\n",
    "template/README.md": "# blank template\n",
    "template/PURPOSE.md": "（填寫）\n",
    "template/wiki/index.md": "# Index\n",
}


@pytest.fixture
def repo(tmp_path):
    return _make_repo(tmp_path / "repo", dict(BASE))


def test_export_writes_template_without_wiki_content(repo, tmp_path):
    out = tmp_path / "public"
    stats = pe.export("HEAD", out, repo=repo)
    written = {p.relative_to(out).as_posix() for p in out.rglob("*")
               if p.is_file()}
    assert written == {
        "README.md", "PURPOSE.md", "SCHEMA.md", "tools/a.py",
        "tests/test_a.py", ".claude/skills/wiki-query/SKILL.md",
        "wiki/index.md"}
    assert (out / "README.md").read_text(encoding="utf-8") == \
        "# blank template\n"
    assert (out / "wiki/index.md").read_text(encoding="utf-8") == "# Index\n"
    assert stats["written"] == 7


def test_unclassified_path_stops_export(tmp_path):
    repo = _make_repo(tmp_path / "repo", {**BASE, "notes.txt": "new\n"})
    out = tmp_path / "public"
    with pytest.raises(pe.ExportError, match="notes.txt"):
        pe.export("HEAD", out, repo=repo)
    assert not out.exists()


@pytest.mark.parametrize("leak, needle", [
    ("see wiki/music/concepts/x.md", "music"),
    ("domain: music", "music"),
    ("read rose-pruning first", "rose-pruning"),
    ("用剪枝法", "剪枝法"),
    ("from Secret Garden Handbook", "Secret Garden Handbook"),
    ("mail me" + "@gmail.com", "me@" + "gmail"),
    ("# see Thorn Care", "Thorn Care"),
    ("about Rose  Pruning here", "Rose  Pruning"),
    ("domains: history／music", "music"),
    ("the Rosebush paper", "Rosebush"),
])
def test_leak_check_blocks_strings_from_the_wiki(tmp_path, leak, needle):
    repo = _make_repo(tmp_path / "repo", {**BASE, "tools/b.py": leak})
    out = tmp_path / "public"
    with pytest.raises(pe.ExportError, match=needle):
        pe.export("HEAD", out, repo=repo)
    assert not out.exists()


# Written with a {d} placeholder so this file does not trip the check itself.
@pytest.mark.parametrize("leak", [
    "### 信心分數（{d} 起，決定見 x）",
    "完整解說放講義（{d} 決定）",
    "- 資源上限（使用者 {d} 指定）",
    "MAX = 4  # the user's ceiling ({d})",
    "the confirmation point (decided {d}).",
    "設計理由見 [ADR](docs/{adr}/0003-x.md)",
    "{d} 的 15 題評估",
    "{d} 使用者決定",
    "11 月起：{m} 起",
    "asked for 34 GB on {d}.",
    "valid since {d}",
])
def test_leak_check_blocks_dated_history_notes(tmp_path, leak):
    leak = leak.format(d="2026-11-15", m="2026-11", adr="adr")
    repo = _make_repo(tmp_path / "repo", {**BASE, "tools/b.py": leak})
    with pytest.raises(pe.ExportError, match="tools/b.py"):
        pe.export("HEAD", tmp_path / "public", repo=repo)


def test_leak_check_allows_bare_dates(repo):
    needles = pe.build_needles(pe._read_commit(repo, "HEAD"), repo)
    assert needles.find("created: 2026-11-05\n"
                        "- 2026-11-15｜舊說法；新說法\n"
                        '{"date": "2026-11-16"}\n'
                        'FORMAT_SINCE = "2026-11-23"') == []


def test_adr_folder_is_left_out(tmp_path):
    repo = _make_repo(tmp_path / "repo",
                      {**BASE, "docs/" "adr/0001-x.md": "# x\n"})
    out = tmp_path / "public"
    pe.export("HEAD", out, repo=repo)
    assert not (out / "docs").exists()


def test_leak_check_ignores_cross_and_word_parts(repo):
    files = pe._read_commit(repo, "HEAD")
    needles = pe.build_needles(files, repo)
    assert needles.find("wiki/cross/ and a pre-rose-pruning-x, musical") == []
    assert needles.find("see the Music folder") == ["Music"]


def test_overlay_is_required(tmp_path):
    files = {k: v for k, v in BASE.items() if not k.startswith("template/")}
    repo = _make_repo(tmp_path / "repo", files)
    with pytest.raises(pe.ExportError, match="template/"):
        pe.export("HEAD", tmp_path / "public", repo=repo)


def test_target_must_be_empty_unless_replace(repo, tmp_path):
    out = tmp_path / "public"
    (out / ".git").mkdir(parents=True)
    (out / ".git/keep").write_text("x", encoding="utf-8")
    (out / "old.md").write_text("old", encoding="utf-8")
    with pytest.raises(pe.ExportError, match="--replace"):
        pe.export("HEAD", out, repo=repo)
    pe.export("HEAD", out, replace=True, repo=repo)
    assert not (out / "old.md").exists()
    assert (out / ".git/keep").exists()
    assert (out / "README.md").exists()


def test_target_inside_or_around_repo_is_refused(repo):
    for target in (repo / "public", repo, repo.parent):
        with pytest.raises(pe.ExportError, match="repo"):
            pe.export("HEAD", target, replace=True, repo=repo)
    assert (repo / "README.md").exists()


def test_commit_needs_noreply_identity_and_never_pushes(repo, tmp_path):
    out = tmp_path / "public"
    with pytest.raises(pe.ExportError, match="git repo"):
        pe.commit(out, "m")
    _git(tmp_path, "init", "-q", str(out))
    _git(out, "config", "user.email", "me@example.com")
    pe.export("HEAD", out, replace=True, repo=repo)
    with pytest.raises(pe.ExportError, match="noreply"):
        pe.commit(out, "m")
    _git(out, "config", "user.email", NOREPLY)
    _git(out, "config", "user.name", "t")
    assert "7 files changed" in pe.commit(out, "first")
    assert pe.commit(out, "again") == ""
    log = subprocess.run(["git", "-C", str(out), "log", "--format=%s"],
                         capture_output=True, text=True, check=True).stdout
    assert log.split() == ["first"]
