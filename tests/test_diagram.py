"""Tests for tools/diagram.py (spec defaults, CJK font, trimming)."""

import json
from pathlib import Path

import pytest
from PIL import Image

import diagram

needs_font = pytest.mark.skipif(
    not all((diagram.FONT_DIR / f).is_file()
            for f in set(diagram.FONT_FILES.values())),
    reason="需要微軟正黑體")

SPEC = {
    "panels": [
        {"id": "a", "title": "細粒度專家", "tiles": ["共享專家"],
         "x": 40, "y": 80},
        {"id": "b", "title": "V3-Base", "tone": "dark", "x": 400, "y": 80},
    ],
    "links": [{"from": "a", "to": "b", "label": "沿用"}],
}


def _write(tmp_path: Path, spec: dict) -> Path:
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_spec_defaults(tmp_path: Path) -> None:
    spec = diagram.load_spec(_write(tmp_path, {**SPEC, "theme": {"tile": 20}}))
    assert spec["title"] == " "
    assert spec["legend"] is False
    assert spec["theme"]["tile"] == 20
    assert spec["theme"]["tones"]["dark"] == diagram.RED


def test_load_spec_rejects_non_object(tmp_path: Path) -> None:
    with pytest.raises(diagram.DiagramError):
        diagram.load_spec(_write(tmp_path, [1, 2]))  # type: ignore[arg-type]


def test_trim(tmp_path: Path) -> None:
    png = tmp_path / "x.png"
    img = Image.new("RGB", (200, 100), "white")
    img.paste((0, 0, 0), (90, 40, 110, 60))
    img.save(png)
    diagram.trim(png, pad=5)
    with Image.open(png) as out:
        assert out.size == (30, 30)


def test_slide_points_wide_picture(tmp_path: Path) -> None:
    # 2000x400 PNG = 1000x200 SVG px; width-limited in both slots.
    png = tmp_path / "wide.png"
    Image.new("RGB", (2000, 400), "white").save(png)
    points = diagram.slide_points(png, 20)
    assert points["有要點"] == pytest.approx(20 / 1000 * 7.66 * 72)
    assert points["全寬"] == pytest.approx(20 / 1000 * 12.43 * 72)


def test_slide_points_tall_picture(tmp_path: Path) -> None:
    # A tall picture is height-limited, so both slots give the same size.
    png = tmp_path / "tall.png"
    Image.new("RGB", (400, 1600), "white").save(png)
    points = diagram.slide_points(png, 20)
    assert points["有要點"] == pytest.approx(points["全寬"])
    assert points["全寬"] == pytest.approx(20 / 200 * (4.46 * 400 / 1600) * 72)


def test_size_lines_warns_only_small_slots() -> None:
    summary, warnings = diagram.size_lines(
        Path("a.json"), {"有要點": 7.5, "全寬": 12.2})
    assert summary == "頁面字級：有要點 7.5 pt、全寬 12.2 pt"
    assert len(warnings) == 1 and "有要點" in warnings[0]


@needs_font
def test_cjk_labels_measured(tmp_path: Path) -> None:
    diagram.use_cjk_font()
    from open_diagram.fonts import face

    regular = face("regular")
    assert regular.names("專")[0] != regular.names("�")[0]
    found, points = diagram.problems(_write(tmp_path, SPEC))
    assert found == []
    assert set(points) == set(diagram.SLOTS)


@needs_font
def test_problems_reports_spec_mistake(tmp_path: Path) -> None:
    found, points = diagram.problems(_write(tmp_path, {"panels": [{"x": 0}]}))
    assert found and "flow.json" in found[0]
    assert points == {}
