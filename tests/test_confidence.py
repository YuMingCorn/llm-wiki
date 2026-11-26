"""Tests for computed claim confidence."""

import json
from pathlib import Path

import pytest

import confidence
import ledger

SOURCES = {
    "p1": "primary", "p2": "primary", "p2b": "primary", "s1": "secondary",
    "old": "primary", "late": "primary",
}
BODY = """# C

- 甲（見 [P1](../sources/p1.md) p.3）
- 乙（見 [P1](../sources/p1.md) p.3、[P2](../sources/p2.md) Table 2）
- 丙（同上）
- 丁（見 [P2](../sources/p2.md) p.1、[P2b](../sources/p2b.md) p.4）
- 戊（見 [S1](../sources/s1.md)）
- 推論：己（依據 [P1](../sources/p1.md) p.3）
- 推論時的記憶體很小（見 [P1](../sources/p1.md) p.3）
- 庚待查證
- 辛（見 [Old](../sources/old.md) p.2）
- 壬（見 [Late](../sources/late.md) p.2）
- 沒有出處的敘述
- 癸。推論：句中的推論（見 [P1](../sources/p1.md) p.3）
![圖](../../assets/x.png)

## 機制

（見 [P2](../sources/p2.md) 式 1–3，p.7）

- 子項沒有連結
最大化下式：
$$L = x$$

### 細節

- 子標題下也沿用

## 已被取代的說法

- 2026-11-01｜舊說法（見 [Old](../sources/old.md) p.2）；新說法見 [P1](../sources/p1.md)

### 子

- 子節裡的舊說法（見 [P1](../sources/p1.md) p.3）
"""


@pytest.fixture
def scorer(tmp_path: Path) -> confidence.Scorer:
    for name, tier in SOURCES.items():
        path = tmp_path / f"wiki/music/sources/{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: source\ntier: {tier}\nsources: []\n---\n# {name}\n", encoding="utf-8")
    concept = tmp_path / "wiki/music/concepts/c.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("---\ntype: concept\nstatus: draft\nsources: []\n---\n" + BODY, encoding="utf-8")
    records = [{"page": f"wiki/music/sources/{n}.md", "independence_key": f"wiki/music/sources/{n}.md",
                "review_state": "active", "refresh_due": None} for n in SOURCES]
    records[2]["independence_key"] = "wiki/music/sources/p2.md"  # p2b is the same study as p2
    records[4].update(review_state="superseded", superseded_by="wiki/music/sources/p1.md")
    records[5]["refresh_due"] = "2000-01-01"
    path = tmp_path / ledger.SOURCE_LEDGER
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(records), encoding="utf-8")
    return confidence.Scorer(tmp_path)


def _find(scorer: confidence.Scorer, fragment: str) -> confidence.Claim:
    return next(c for c in scorer.claims("wiki/music/concepts/c.md") if fragment in c.text)


def test_scores_and_labels(scorer: confidence.Scorer) -> None:
    assert (_find(scorer, "甲").score, _find(scorer, "甲").label) == (0.7, "單一來源")
    assert (_find(scorer, "乙").score, _find(scorer, "乙").label) == (0.91, "多來源支持")
    assert _find(scorer, "丙").sources == _find(scorer, "乙").sources
    # p2 and p2b share an independence key, so they count once.
    assert (_find(scorer, "丁").independent, _find(scorer, "丁").score) == (1, 0.7)
    assert (_find(scorer, "戊").score, _find(scorer, "戊").label) == (0.34, "只有二手來源")
    assert (_find(scorer, "己").score, _find(scorer, "己").label) == (0.5, "推論（暫定）")


def test_inference_word_is_not_a_marker(scorer: confidence.Scorer) -> None:
    lines = {c.text: c for c in scorer.claims("wiki/music/concepts/c.md")}
    claim = next(c for t, c in lines.items() if "推論時" in t)
    assert "inference" not in claim.flags and claim.score == 0.7


def test_unverified_stale_and_skipped_lines(scorer: confidence.Scorer) -> None:
    texts = {c.text: c for c in scorer.claims("wiki/music/concepts/c.md")}
    unverified = next(c for t, c in texts.items() if "庚" in t)
    assert (unverified.score, unverified.label) == (0.0, "待查證（沒有證據）")
    old = next(c for t, c in texts.items() if "辛" in t)
    assert (old.score, old.label) == (0.0, "來源已被取代（改引取代者）")
    late = next(c for t, c in texts.items() if "壬" in t)
    assert (late.score, late.label) == (0.35, "來源過期（附到期日）")
    assert not any("沒有出處" in t or "![" in t for t in texts)


def test_section_citation_is_inherited_and_superseded_section_is_zero(scorer: confidence.Scorer) -> None:
    texts = {c.text: c for c in scorer.claims("wiki/music/concepts/c.md")}
    child = next(c for t, c in texts.items() if "子項" in t)
    assert child.sources == ["wiki/music/sources/p2.md"] and child.score == 0.7
    history = next(c for t, c in texts.items() if "舊說法" in t)
    assert (history.score, history.label) == (0.0, "已被取代（只當歷史）")


def test_contested_page_is_discounted(scorer: confidence.Scorer, tmp_path: Path) -> None:
    path = tmp_path / "wiki/music/concepts/c.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: draft", "status: disputed"),
                    encoding="utf-8")
    claim = _find(confidence.Scorer(tmp_path), "甲")
    assert (claim.score, claim.label) == (0.49, "有矛盾（並陳各方）")


def test_source_page_is_rejected(scorer: confidence.Scorer) -> None:
    with pytest.raises(ValueError):
        scorer.claims("wiki/music/sources/p1.md")


def test_mid_line_inference_formulas_and_subsections(scorer: confidence.Scorer) -> None:
    texts = {c.text: c for c in scorer.claims("wiki/music/concepts/c.md")}
    assert next(c for t, c in texts.items() if "癸" in t).label == "推論（暫定）"
    assert not any("$$" in t or "最大化下式" in t for t in texts)
    assert next(c for t, c in texts.items() if "子標題下" in t).sources == ["wiki/music/sources/p2.md"]
    assert next(c for t, c in texts.items() if "子節裡" in t).score == 0.0


def test_disagreement_section_is_not_multi_source_support(tmp_path: Path, scorer: confidence.Scorer) -> None:
    path = tmp_path / "wiki/music/concepts/c.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n## 仍有分歧的地方\n\n"
                    "- 甲說 A（見 [P1](../sources/p1.md) p.3）；乙說 B（見 [P2](../sources/p2.md) p.4）\n",
                    encoding="utf-8")
    claim = _find(confidence.Scorer(tmp_path), "甲說")
    assert claim.label == "有分歧（並陳各方）" and claim.score == round(0.91 * 0.7, 2)
