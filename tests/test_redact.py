"""Tests for secret detection. Fake secrets are built by concatenation so this
file itself never contains one (wiki.py commit would refuse it)."""

from pathlib import Path

import redact

GH = "gh" + "p_" + "a1B2" * 9
ANT = "sk-" + "ant-" + "api03-" + "Zx9" * 12
OAI = "sk-" + "proj-" + "Ab1" * 14
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
JWT = "ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 12


def test_block_patterns_and_masking() -> None:
    text = "\n".join([f"token {GH}", ANT, OAI, AWS, PEM, JWT, "plain text"])
    found = redact.scan_text(text, "x.md")
    kinds = [(f.line, f.kind) for f in found]
    assert kinds == [(1, "github_token"), (2, "anthropic_key"), (3, "openai_key"),
                     (4, "aws_access_key"), (5, "private_key"), (6, "jwt")]
    assert all(f.level == "block" for f in found)
    assert all(GH not in f.preview and ANT not in f.preview for f in found)


def test_credential_assignment_warns_but_placeholders_do_not() -> None:
    found = redact.scan_text('password = "hunter2hunter2"\napi_key: <YOUR_KEY>\ntoken=${TOKEN}')
    assert [(f.kind, f.level) for f in found] == [("credential_assignment", "warn")]
    assert redact.blocking(found) == []


def test_ordinary_wiki_text_is_clean() -> None:
    text = "音量 clipping（sk-learn 不是密鑰）；GPT-4 的 token 數是 8k；ASIA 是地名"
    assert redact.scan_text(text) == []


def test_scan_paths_reads_dirs_and_skips_binaries(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(f"x {GH}", encoding="utf-8")
    (tmp_path / "b.png").write_bytes(GH.encode())
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("clean", encoding="utf-8")
    found = redact.scan_paths([tmp_path])
    assert [Path(f.path).name for f in found] == ["a.md"]


def test_mask_text_replaces_block_secrets() -> None:
    assert redact.mask_text(f"k={GH} end") == "k=[REDACTED:github_token] end"


def test_kebab_case_prose_is_not_a_provider_key() -> None:
    assert redact.scan_text("see sk-learn-compatible-estimator-wrapper-for-large-models") == []


def test_credential_names_with_prefix_and_json_quotes_warn() -> None:
    text = '"password": "hunter2hunter2"\nDB_PASSWORD=abcdef12\nclient_secret: s3cr3tvalue'
    assert [f.kind for f in redact.scan_text(text)] == ["credential_assignment"] * 3


def test_scan_file_reads_suffixless_and_non_utf8(tmp_path: Path) -> None:
    (tmp_path / "envfile").write_text(f"K={GH}", encoding="utf-8")
    (tmp_path / "x.pem").write_text(PEM, encoding="utf-8")
    (tmp_path / "u16.md").write_bytes(f"x {GH}".encode("utf-16"))
    names = sorted(Path(f.path).name for f in redact.scan_paths([tmp_path]))
    assert names == ["envfile", "x.pem"]
