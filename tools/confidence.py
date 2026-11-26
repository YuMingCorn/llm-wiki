"""Computed confidence for each claim on a wiki page (schema §9 信心分數).

Nothing is stored: scores are derived from the page every time, so they
cannot drift from the page the way the deleted claim ledger did.

A claim is one prose line (paragraph line or bullet) of a non-source content
page that cites at least one source page. A line saying 同上 without a link
reuses the previous claim's sources; a line that is only a citation in
parentheses, such as ``（見 [V3](../sources/x.md) p.7）``, becomes the default
for the uncited lines after it until the next heading.

Score (0-1) per claim: every independent source (ledger ``independence_key``)
gives evidence ``tier weight x locator factor x freshness``; the evidence is
combined as 1 - prod(1 - 0.7 * e). Markers then cap the score: 推論 at 0.5,
待查證 at 0.1, a ``disputed`` page multiplies by 0.7. Source age plays no
part; time enters only through ``refresh_due`` and supersession.

Usage:
  uv run python -I tools/confidence.py <page ...> [--below X] [--json]
  uv run python -I tools/confidence.py --summary [<page ...>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402
import _paths  # noqa: E402
import ledger  # noqa: E402
import lifecycle  # noqa: E402
import lint  # noqa: E402

TIER_WEIGHT = {"primary": 1.0, "secondary": 0.6, "tertiary": 0.4}
UNKNOWN_TIER_WEIGHT = 0.4
NO_LOCATOR_FACTOR = 0.8
OVERDUE_FACTOR = 0.5
PER_SOURCE = 0.7
CAP_INFERENCE, CAP_UNVERIFIED, CONTESTED_FACTOR = 0.5, 0.1, 0.7

LOCATOR_RE = re.compile(
    r"p\.\s?\d|pp\.\s?\d|§\s?\d|Table\s?\d|Figure\s?\d|Fig\.\s?\d|Eq\.\s?\d"
    r"|式\s?\d|表\s?\d|圖\s?\d|Appendix|附錄|\b[A-H]\.\d")
CITATION_ONLY_RE = re.compile(r"^\s*[（(]\s*見.*[）)]\s*$")
# Explanations, examples, quotes, tables, images, italic captions, display
# formulas.
SKIP_RE = re.compile(
    r"^\s*(?:解說：|示例（整理者構造）|>|\||!\[|\*[^*\s]|\$\$)")
# A short lead-in ending in a colon introduces the formula or list below it.
LEAD_IN_MAX_CHARS = 60
# 推論 as a marker (推論：…, also after 。；), not the word in 推論時
# (inference time).
INFERENCE_RE = re.compile(
    r"(?:^|[-*+]\s+|\d+\.\s+|[（(：。；;]|\*\*)推論(?:\*\*)?[：:]")
HEADING_RE = re.compile(r"^(#{1,6})\s")
# Sections that set sources against each other: several sources on one line
# there mean disagreement, not support (e.g. a section titled 仍有分歧的地方).
DISPUTE_RE = re.compile(r"分歧|矛盾|不一致|爭議|衝突|互相")
SOURCE_DIR_RE = re.compile(r"^wiki/(?:[a-z]+/)?sources/")


@dataclass
class Claim:
    """One scored line of a page."""

    page: str
    line: int
    text: str
    sources: list[str]
    independent: int
    score: float
    label: str
    flags: list[str] = field(default_factory=list)


def _label(score: float, flags: list[str], tiers: set[str],
           independent: int) -> str:
    """Maps a claim to the wording rule of /wiki-query step 3."""
    if "superseded_section" in flags:
        return "已被取代（只當歷史）"
    if "unverified" in flags:
        return "待查證（沒有證據）"
    if "inference" in flags:
        return "推論（暫定）"
    if "contested" in flags:
        return "有矛盾（並陳各方）"
    if "disputed_section" in flags:
        return "有分歧（並陳各方）"
    if "superseded_source" in flags:
        return "來源已被取代（改引取代者）"
    if "overdue_source" in flags:
        return "來源過期（附到期日）"
    if tiers and tiers <= {"secondary", "tertiary"}:
        return "只有二手來源"
    if independent < 2:
        return "單一來源"
    return "多來源支持" if score >= 0.85 else "有來源"


class Scorer:
    """Scores claims against one loaded wiki and ledger."""

    def __init__(self, root: Path) -> None:
        """Loads the wiki, ledger keys and stale-source states."""
        self.wiki = lint.load(root)
        self.states = lifecycle.source_states(root)
        self.keys: dict[str, str] = {}
        for record in ledger._load(root, ledger.SOURCE_LEDGER):
            self.keys.setdefault(
                record["page"], record.get("independence_key") or
                record["page"])

    def _evidence(self, source: str, has_locator: bool) -> tuple[float, str]:
        page = self.wiki.pages.get(source)
        tier = (page.frontmatter or {}).get("tier") if page else None
        weight = TIER_WEIGHT.get(tier, UNKNOWN_TIER_WEIGHT)
        if not has_locator:
            weight *= NO_LOCATOR_FACTOR
        state = self.states.get(source, {}).get("reason")
        if state == "superseded":
            weight = 0.0
        elif state == "refresh_due":
            weight *= OVERDUE_FACTOR
        return weight, tier or "unknown"

    def _score(self, key: str, text: str, sources: list[str],
               contested: bool, in_superseded: bool,
               inherited_locator: bool = False,
               disputed: bool = False) -> Claim:
        flags: list[str] = []
        has_locator = inherited_locator or bool(LOCATOR_RE.search(text))
        best: dict[str, float] = {}
        tiers: set[str] = set()
        for source in sources:
            evidence, tier = self._evidence(source, has_locator)
            tiers.add(tier)
            group = self.keys.get(source, source)
            best[group] = max(best.get(group, 0.0), evidence)
            reason = self.states.get(source, {}).get("reason")
            if reason:
                flags.append("superseded_source" if reason == "superseded"
                             else "overdue_source")
        remaining = 1.0
        for evidence in best.values():
            remaining *= 1 - PER_SOURCE * evidence
        score = 1 - remaining
        if INFERENCE_RE.search(text.strip()):
            flags.append("inference")
            score = min(score, CAP_INFERENCE)
        if "待查證" in text:
            flags.append("unverified")
            score = min(score, CAP_UNVERIFIED)
        if contested:
            flags.append("contested")
            score *= CONTESTED_FACTOR
        elif disputed:
            flags.append("disputed_section")
            score *= CONTESTED_FACTOR
        if in_superseded:
            flags.append("superseded_section")
            score = 0.0
        flags = sorted(set(flags))
        independent = sum(1 for e in best.values() if e > 0)
        return Claim(key, 0, text.strip()[:120], sources, independent,
                     round(score, 2),
                     _label(score, flags, tiers, independent), flags)

    def claims(self, key: str) -> list[Claim]:
        """Scores every claim line of one content page.

        Raises:
            ValueError: The page is not a non-source content page.
        """
        page = self.wiki.pages.get(key)
        if page is None:
            raise ValueError(f"找不到內容頁（要寫 repo 相對路徑）：{key}")
        if page.type == "source":
            raise ValueError(f"source 頁不計分，請給 concept 等頁：{key}")
        by_line: dict[int, list[str]] = {}
        for link in page.links:
            if SOURCE_DIR_RE.match(link.resolved) and not link.is_image:
                by_line.setdefault(link.line, []).append(link.resolved)
        text = (self.wiki.root / key).read_text(encoding="utf-8-sig")
        contested = ((page.frontmatter or {}).get("status")
                     in _common.DISPUTED_STATUSES)
        out: list[Claim] = []
        # (sources, has locator) of the section citation and last claim; the
        # section citation covers deeper subheadings until one of its level.
        default: tuple[list[str], bool] = ([], False)
        default_level = 0
        previous: tuple[list[str], bool] = ([], False)
        in_superseded = False
        disputed = False
        fence = False
        current_level = 0
        body_start = len(text) - len(page.body)
        first_line = text[:body_start].count("\n") + 1
        for number, line in enumerate(page.body.split("\n"), first_line):
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
                continue
            if fence or not line.strip():
                continue
            heading = HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                current_level = level
                if level <= default_level or default_level == 0:
                    default, default_level = ([], False), 0
                if level <= 2:
                    # Same boundary as lifecycle's section parser.
                    previous = ([], False)
                    in_superseded = bool(re.match(
                        rf"^##\s+{lifecycle.SUPERSEDED_SECTION}\s*$", line))
                    disputed = bool(DISPUTE_RE.search(line))
                elif DISPUTE_RE.search(line):
                    disputed = True
                continue
            if SKIP_RE.match(line) or (
                    line.rstrip().endswith(("：", ":"))
                    and len(line.strip()) <= LEAD_IN_MAX_CHARS):
                continue
            sources = list(dict.fromkeys(by_line.get(number, [])))
            locator = bool(LOCATOR_RE.search(line))
            if sources and CITATION_ONLY_RE.match(line):
                default, default_level = (sources, locator), current_level
                continue
            inherited = False
            if not sources:
                if "同上" in line and previous[0]:
                    sources, inherited = previous
                elif default[0]:
                    sources, inherited = default
                elif "待查證" not in line and not INFERENCE_RE.search(
                        line.strip()):
                    continue
            claim = self._score(key, line, sources, contested, in_superseded,
                                inherited, disputed)
            claim.line = number
            out.append(claim)
            if by_line.get(number):
                previous = (sources, locator)
        return out


def summary(scorer: Scorer, keys: Sequence[str]) -> list[dict]:
    """Aggregates claims per page: count, mean and the weakest share."""
    out = []
    for key in keys:
        claims = scorer.claims(key)
        if not claims:
            continue
        scores = [c.score for c in claims]
        out.append({
            "page": key,
            "claims": len(claims),
            "mean": round(sum(scores) / len(scores), 2),
            "below_0.5": sum(1 for s in scores if s < 0.5),
        })
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; exit 1 when a page is not a scoreable content page."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--below", type=float,
                    help="only list claims scoring below this")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true",
                    help="one line per page (all content pages if none)")
    args = ap.parse_args(argv)
    scorer = Scorer(_paths.ROOT)
    keys = [Path(p).as_posix().removeprefix("./") for p in args.pages]
    try:
        if args.summary:
            keys = keys or sorted(
                k for k, p in scorer.wiki.pages.items() if p.type != "source")
            rows = summary(scorer, keys)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                for r in rows:
                    print(f"{r['mean']:.2f}\t{r['claims']}\t"
                          f"{r['below_0.5']}\t{r['page']}")
            return 0
        if not keys:
            ap.error("要指定頁面，或用 --summary")
        claims = [c for k in keys for c in scorer.claims(k)
                  if args.below is None or c.score < args.below]
    except ValueError as err:
        print(f"錯誤：{err}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([asdict(c) for c in claims], ensure_ascii=False,
                         indent=1))
    else:
        for c in claims:
            print(f"{c.score:.2f}\t{c.label}\t{c.page}:{c.line}\t{c.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
