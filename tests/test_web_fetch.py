"""Tests for web_fetch.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import web_fetch  # noqa: E402


@pytest.mark.parametrize("url,allowed", [
    ("https://example.com/a.png", True),
    ("HTTP://example.com/a.png", True),
    ("file:///D:/my_wiki/.mcp.json", False),
    ("ftp://example.com/a.png", False),
])
def test_only_web_urls_are_fetched(url: str, allowed: bool) -> None:
    assert web_fetch.is_web_url(url) is allowed


class _FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class _FakeResp:
    headers = _FakeHeaders()

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return b"ok"

    def geturl(self) -> str:
        return "https://example.com/final"


def test_get_verifies_with_os_trust_store(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies with the OS trust store, which has Let's Encrypt Root YE."""
    import ssl

    import truststore

    seen: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: float,
                     context: object = None) -> _FakeResp:
        seen["context"] = context
        return _FakeResp()

    monkeypatch.setattr(web_fetch.urllib.request, "urlopen", fake_urlopen)
    got = web_fetch.get("https://example.com/")
    assert got == (b"ok", "https://example.com/final", "utf-8")
    ctx = seen["context"]
    assert isinstance(ctx, truststore.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


@pytest.mark.parametrize("url,want", [
    ("https://code.claude.com/docs/en/hooks",
     "https://code.claude.com/docs/en/hooks.md"),
    ("https://code.claude.com/docs/en/hooks/",
     "https://code.claude.com/docs/en/hooks.md"),
    ("https://x.com/a/b.md", "https://x.com/a/b.md"),
    ("https://x.com/a?x=1#sec", "https://x.com/a.md"),
    ("https://x.com", "https://x.com/index.md"),
])
def test_md_variant(url: str, want: str) -> None:
    assert web_fetch.md_variant(url) == want


@pytest.mark.parametrize("text,ok", [
    ("# Hooks\n\ntext", True),
    ("  <!DOCTYPE html><html>", False),
    ("<html><body>", False),
    ("   \n", False),
])
def test_looks_like_markdown(text: str, ok: bool) -> None:
    assert web_fetch.looks_like_markdown(text) is ok


def test_split_frontmatter() -> None:
    fm = '---\ntitle: "Hooks"\nx: 1\n---\n# H\n'
    assert web_fetch.split_frontmatter(fm) == ("Hooks", "# H\n")
    assert web_fetch.split_frontmatter("# H\n") == ("", "# H\n")
    # An empty title must not swallow the next line.
    empty = "---\ntitle:\ndescription: d\n---\nB"
    assert web_fetch.split_frontmatter(empty)[0] == ""
    assert web_fetch.split_frontmatter("---\n---\nB") == ("", "B")


def test_fetch_markdown_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(body: bytes, charset: str | None = "utf-8") -> None:
        monkeypatch.setattr(web_fetch, "get",
                            lambda u: (body, u, charset))

    fake(b"<!doctype html>")
    assert web_fetch.fetch_markdown("https://x.com/a") is None

    def boom(u: str) -> None:
        raise OSError("404")
    monkeypatch.setattr(web_fetch, "get", boom)
    assert web_fetch.fetch_markdown("https://x.com/a") is None

    # An unknown charset must fall back, not crash the run.
    fake(b"# A", "no-such-codec")
    assert web_fetch.fetch_markdown("https://x.com/a") is None

    fake(b"# A", None)
    assert web_fetch.fetch_markdown("https://x.com/a") == (
        "# A", "https://x.com/a.md", b"# A")


def test_fetch_markdown_normalizes_crlf_and_bom(
        monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"\xef\xbb\xbf---\r\ntitle: T\r\n---\r\n# H\r\n"
    monkeypatch.setattr(web_fetch, "get", lambda u: (raw, u, "utf-8"))
    text, _, got_raw = web_fetch.fetch_markdown("https://x.com/a")
    assert text == "---\ntitle: T\n---\n# H\n"
    assert got_raw == raw  # kept byte-for-byte for <name>.source.md


def test_localize_markdown(monkeypatch: pytest.MonkeyPatch,
                           tmp_path: Path) -> None:
    fetched: list[str] = []

    def fake_get(u: str) -> tuple[bytes, str, str]:
        fetched.append(u)
        return b"PNG", u, "utf-8"
    monkeypatch.setattr(web_fetch, "get", fake_get)
    md = ('[rel](./skills) [abs](https://y.com/z) [anchor](#top) '
          '![fig](/img/a.png "cap") ![bad](file:///etc/passwd)')
    out, n = web_fetch.localize_markdown(md, "https://x.com/docs/en/hooks.md",
                                         tmp_path / "n_images", "n", True)
    assert n == 1
    assert fetched == ["https://x.com/img/a.png"]
    assert "[rel](https://x.com/docs/en/skills)" in out
    assert "[abs](https://y.com/z)" in out
    assert "[anchor](#top)" in out
    assert '![fig](n_images/img01.png "cap")' in out
    assert (tmp_path / "n_images" / "img01.png").read_bytes() == b"PNG"
    assert "![bad](file:///etc/passwd)" in out  # not fetched, left as is

    out2, n2 = web_fetch.localize_markdown("![f](/a.png)", "https://x.com/",
                                           tmp_path, "n", False)
    assert (out2, n2) == ("![f](https://x.com/a.png)", 0)


def test_localize_markdown_badge_and_fence(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_fetch, "get", lambda u: (b"S", u, "utf-8"))
    fence = "```md\n[x](./y) ![i](/i.png)\n```"
    md = f"[![b](/b.svg)](./link)\n\n{fence}\n[z](./z)"
    out, n = web_fetch.localize_markdown(md, "https://x.com/d/p.md",
                                         tmp_path / "n_images", "n", True)
    assert n == 1
    assert "[![b](n_images/img01.svg)](https://x.com/d/link)" in out
    assert fence in out  # code blocks are left alone
    assert "[z](https://x.com/d/z)" in out


@pytest.mark.parametrize("md,kept,changed", [
    # indented fence inside a list item
    ("- item\n\n    ```\n    [in](./i)\n    ```\n[out](./o)",
     "    [in](./i)", "[out](https://x.com/d/o)"),
    # fence inside a blockquote
    ("> ```\n> [in](./i)\n> ```\n[out](./o)",
     "> [in](./i)", "[out](https://x.com/d/o)"),
    # inline ``` at line start is not a fence opener
    ("```inline``` and [a](./a)\n```\ncode [c](./c)\n```",
     "code [c](./c)", "[a](https://x.com/d/a)"),
    # a 4-backtick fence is not closed by ```
    ("````\n```\n[in](./i)\n```\n````\n[out](./o)",
     "[in](./i)", "[out](https://x.com/d/o)"),
    # an unclosed fence runs to the end
    ("[out](./o)\n```\n[in](./i)", "[in](./i)", "[out](https://x.com/d/o)"),
])
def test_localize_markdown_fence_edges(md: str, kept: str,
                                       changed: str) -> None:
    out, _ = web_fetch.localize_markdown(md, "https://x.com/d/p.md",
                                         Path("unused"), "n", False)
    assert kept in out and changed in out


def test_image_url_with_parentheses(monkeypatch: pytest.MonkeyPatch,
                                    tmp_path: Path) -> None:
    fetched: list[str] = []
    monkeypatch.setattr(web_fetch, "get",
                        lambda u: (fetched.append(u) or b"P", u, "utf-8"))
    out, n = web_fetch.localize_markdown("![x](/a_(1).png)", "https://x.com/",
                                         tmp_path / "n_images", "n", True)
    assert fetched == ["https://x.com/a_(1).png"]
    assert out == "![x](n_images/img01.png)" and n == 1
