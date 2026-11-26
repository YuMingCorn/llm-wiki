"""Checks that project skills only reference files that exist."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
LOCAL_RE = re.compile(r"`((?:references|assets)/[\w./-]+)`")
REPO_RE = re.compile(r"`((?:\.claude/[\w./-]+|tools/[\w./-]+\.(?:py|md)))`")
IDS = [path.parent.name for path in SKILLS]


def test_skills_found() -> None:
    assert len(SKILLS) >= 3


@pytest.mark.parametrize("skill", SKILLS, ids=IDS)
def test_name_matches_directory(skill: Path) -> None:
    text = skill.read_text(encoding="utf-8")
    assert re.search(rf"^name: {skill.parent.name}$", text, re.M)


@pytest.mark.parametrize("skill", SKILLS, ids=IDS)
def test_referenced_files_exist(skill: Path) -> None:
    text = skill.read_text(encoding="utf-8")
    missing = [p for p in LOCAL_RE.findall(text)
               if not (skill.parent / p).is_file()]
    missing += [p for p in REPO_RE.findall(text) if not (ROOT / p).exists()]
    assert not missing


@pytest.mark.parametrize("skill", SKILLS, ids=IDS)
def test_bundled_files_are_referenced(skill: Path) -> None:
    text = skill.read_text(encoding="utf-8")
    orphans = [
        path.relative_to(skill.parent).as_posix()
        for folder in ("references", "assets")
        for path in (skill.parent / folder).rglob("*")
        if path.is_file()
        and path.relative_to(skill.parent).as_posix() not in text
    ]
    assert not orphans


@pytest.mark.parametrize(
    "agent", sorted((ROOT / ".claude" / "agents").glob("*.md")),
    ids=lambda p: p.stem)
def test_agents_reference_files_that_exist(agent: Path) -> None:
    text = agent.read_text(encoding="utf-8")
    missing = [ref for ref in REPO_RE.findall(text)
               if not (ROOT / ref).exists()]
    assert not missing
