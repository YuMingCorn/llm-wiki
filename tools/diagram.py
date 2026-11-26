"""Draws slide diagrams from open-diagram JSON specs as PNG files.

A spec lives in `slides/<deck>/diagrams/<name>.json` and is written in
open-diagram's JSON format (https://github.com/lukendeasy/open-diagram).
This tool adds what the slides need on top of it:

- Chinese text: labels are drawn with Microsoft JhengHei instead of the
  bundled Inter, which has no CJK glyphs.
- The deck look: flat panels, gray headers, deep red for `"tone": "dark"`,
  no key and no title (the slide has its own). A `theme` object in the spec
  overrides these defaults; `title` and `legend` can be set as well.
- Output: `<name>.png` next to the spec, with the white border trimmed.
- Slide text size: for each picture slot of an `圖片` slide (with 要點, or
  full width) it prints how big the smallest label lands on the slide, and
  warns below `MIN_POINTS`. The warning does not change the exit code.

Usage:
    uv run python -I tools/diagram.py draw <spec.json>...
    uv run python -I tools/diagram.py check <spec.json>...
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

# -I drops the script directory from sys.path; _limits lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _limits  # noqa: E402, F401  (applies the thread and memory caps)

if TYPE_CHECKING:
    from fontTools.ttLib import TTFont
    from open_diagram import Diagram

FONT_DIR = Path(r"C:\Windows\Fonts")
FONT_FILES = {
    "regular": "msjh.ttc",
    "medium": "msjh.ttc",
    "semibold": "msjhbd.ttc",
    "bold": "msjhbd.ttc",
}
RED = "#A51C30"  # the deck's title and band color (slides.py)
THEME = {
    "lid": 0,
    "depth": 0,
    "tones": {"default": "#4A5058", "neutral": "#6A7078", "accent": RED,
              "dark": RED},
    # Slide sizes: the picture is scaled down to fit the slide, so labels
    # start about 1.4 times open-diagram's defaults.
    "panel_title": 22,
    "panel_sub": 17,
    "tile": 20,
    "small": 17,
    "legend": 18,
    "min_text": 16,
    "tile_h": 40,
    "line_h": 20,
    "header_h": 46,
    "header_h_sub": 66,
    # Only shown when a spec sets "legend": true; specs name their own.
    "link_labels": {"flow": "實線", "planned": "虛線"},
}
PAD = 24  # pixels of white kept around the trimmed picture
SCALE = 2.0  # open-diagram writes the PNG at twice the SVG size

# Where an `圖片` slide puts the picture (slides.py, 16:9 deck, with a 重點
# line and a 圖說 caption), in inches. With 要點 the picture shares the
# width with the bullet column.
SLOTS = {"有要點": (7.66, 4.46), "全寬": (12.43, 4.46)}
MIN_POINTS = 10.0  # warn when the smallest label lands below this on a slide


class DiagramError(Exception):
    """A spec that cannot be read or drawn."""


def use_cjk_font(font_dir: Path = FONT_DIR) -> None:
    """Points open-diagram at Microsoft JhengHei.

    open-diagram measures and outlines every label with fontTools, loading
    the faces lazily from `FONT_DIR` and `FILES`. The `.ttc` collections
    need a face number, so the module's `TTFont` is wrapped to pass 0.

    Args:
        font_dir: Folder that holds the font files.

    Raises:
        DiagramError: A font file is missing.
    """
    import open_diagram.fonts as fonts

    missing = [f for f in set(FONT_FILES.values())
               if not (font_dir / f).is_file()]
    if missing:
        raise DiagramError(f"找不到字型 {', '.join(sorted(missing))}"
                           f"（{font_dir}）")
    if not getattr(fonts.TTFont, "_wiki_ttc", False):
        original = fonts.TTFont

        def ttfont(path: str, *args: object,
                   **kwargs: object) -> TTFont:
            if str(path).lower().endswith(".ttc"):
                kwargs.setdefault("fontNumber", 0)
            return original(path, *args, **kwargs)

        ttfont._wiki_ttc = True  # type: ignore[attr-defined]
        fonts.TTFont = ttfont
    fonts.FONT_DIR = str(font_dir)
    fonts.FILES = dict(FONT_FILES)
    fonts._FACES.clear()


def load_spec(path: Path) -> dict[str, Any]:
    """Reads a spec and fills in the deck defaults.

    Args:
        path: The JSON spec.

    Returns:
        The spec with `title`, `legend` and `theme` defaults applied.

    Raises:
        DiagramError: The file is not a JSON object.
    """
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DiagramError(f"{path}：無法讀取 JSON（{e}）") from e
    if not isinstance(spec, dict):
        raise DiagramError(f"{path}：最外層要是 JSON 物件")
    spec.setdefault("title", " ")
    spec.setdefault("legend", False)
    theme = dict(THEME)
    theme.update(spec.get("theme") or {})
    spec["theme"] = theme
    return spec


def build(path: Path) -> Diagram:
    """Turns a spec file into an open-diagram `Diagram`.

    Args:
        path: The JSON spec.

    Returns:
        The diagram, not yet rendered.

    Raises:
        DiagramError: The spec has a mistake open-diagram reports.
    """
    from open_diagram.spec import SpecError, from_spec

    use_cjk_font()
    try:
        return from_spec(load_spec(path))
    except SpecError as e:
        raise DiagramError(f"{path}：{e}") from e


def trim(png: Path, pad: int = PAD) -> None:
    """Crops the white border of a PNG, keeping `pad` pixels.

    Args:
        png: The image, rewritten in place.
        pad: Pixels of white to keep on each side.
    """
    from PIL import Image, ImageChops

    with Image.open(png) as img:
        rgb = img.convert("RGB")
    box = ImageChops.difference(
        rgb, Image.new("RGB", rgb.size, "white")).getbbox()
    if box is None:
        return
    left, top, right, bottom = box
    rgb.crop((max(left - pad, 0), max(top - pad, 0),
              min(right + pad, rgb.width),
              min(bottom + pad, rgb.height))).save(png)


def slide_points(png: Path, min_text: float) -> dict[str, float]:
    """Works out how big the smallest label prints on a slide.

    The picture is scaled to fit the slot, so a wide or tall diagram ends up
    with smaller text than its own `min_text` suggests.

    Args:
        png: The trimmed PNG, written at `SCALE` times the SVG size.
        min_text: The smallest label in SVG pixels.

    Returns:
        Points of the smallest label for each slot in `SLOTS`.
    """
    from PIL import Image

    with Image.open(png) as img:
        width, height = img.size
    points = {}
    for slot, (slot_w, slot_h) in SLOTS.items():
        shown_w = min(slot_w, slot_h * width / height)  # inches on the slide
        points[slot] = min_text * SCALE / width * shown_w * 72
    return points


def size_lines(path: Path, points: dict[str, float]) -> tuple[str, list[str]]:
    """Formats the slide text sizes and the warnings for small ones.

    Args:
        path: The spec, named in the warnings.
        points: Output of `slide_points`.

    Returns:
        A one-line summary and one warning per slot below `MIN_POINTS`.
    """
    summary = "頁面字級：" + "、".join(
        f"{slot} {pt:.1f} pt" for slot, pt in points.items())
    warnings = [f"{path}：警告：放在{slot}的頁，最小字只有 {pt:.1f} pt"
                f"（建議至少 {MIN_POINTS:g} pt；縮短文字，或讓圖的長寬比接近"
                f"版位）"
                for slot, pt in points.items() if pt < MIN_POINTS]
    return summary, warnings


def _render(path: Path, out: Path) -> tuple[list[str], dict[str, float]]:
    """Draws a spec to `out` (a PNG path) and trims it.

    Args:
        path: The JSON spec.
        out: Where the PNG goes; the SVG is written next to it.

    Returns:
        The layout problems and, when there are none, the slide text sizes.
    """
    try:
        diagram = build(path)
    except DiagramError as e:
        return [str(e)], {}
    with tempfile.TemporaryDirectory() as tmp:
        # save() prints a "wrote …" line about the temp files; drop it.
        with contextlib.redirect_stdout(io.StringIO()):
            report = diagram.save(str(Path(tmp) / "out"), scale=SCALE,
                                  strict=False)
        if report.problems:
            return [f"{path}：{p}" for p in report.problems], {}
        made = Path(tmp) / "out.png"
        if not made.is_file():
            return [f"{path}：沒有產生 PNG（open-diagram 需要 Edge 或 "
                    "Chrome；路徑不同時設環境變數 CHROME）"], {}
        shutil.copyfile(made, out)
    trim(out)
    return [], slide_points(out, report.min_text)


def problems(path: Path) -> tuple[list[str], dict[str, float]]:
    """Checks a spec without writing next to it.

    Args:
        path: The JSON spec.

    Returns:
        The layout problems (empty when clean) and the slide text sizes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        return _render(path, Path(tmp) / "check.png")


def draw(path: Path) -> tuple[list[str], dict[str, float]]:
    """Writes `<name>.png` next to the spec when the layout is clean.

    Args:
        path: The JSON spec.

    Returns:
        The layout problems (the PNG is written only when there are none)
        and the slide text sizes.
    """
    target = path.with_suffix(".png")
    with tempfile.TemporaryDirectory() as tmp:
        found, points = _render(path, Path(tmp) / "draw.png")
        if not found:
            shutil.copyfile(Path(tmp) / "draw.png", target)
    return found, points


def main(argv: list[str] | None = None) -> int:
    """Runs the command line; returns the exit code."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (("draw", "畫成 PNG（版面有問題就不寫檔）"),
                       ("check", "只檢查版面，不寫檔")):
        p = sub.add_parser(name, help=text)
        p.add_argument("specs", type=Path, nargs="+")
    args = parser.parse_args(argv)

    found: list[str] = []
    warnings: list[str] = []
    for spec in args.specs:
        issues, points = (draw if args.cmd == "draw" else problems)(spec)
        if not issues:
            summary, small = size_lines(spec, points)
            name = spec.with_suffix(".png") if args.cmd == "draw" else spec
            print(f"{name}（{summary}）")
            warnings += small
        found += issues
    for line in warnings + found:
        print(line)
    if found:
        print(f"共 {len(found)} 個問題")
        return 1
    if args.cmd == "check":
        print(f"OK：{len(args.specs)} 張圖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
