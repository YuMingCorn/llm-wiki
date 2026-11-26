"""PDF 章節切分與截圖工具（給 LLM wiki ingest 用）。

用法（在專案根目錄）：
  uv run python tools/pdf_tool.py toc   <pdf>
  uv run python tools/pdf_tool.py split <pdf> [--out DIR] [--level 1|2]
  uv run python tools/pdf_tool.py shot  <pdf> --pages 5,12-14 [--clip x0,y0,x1,y1] [--dpi 150] [--out DIR]
  uv run python tools/pdf_tool.py figures <pdf> [--out DIR] [--dpi 150]

章節來源：優先用 PDF 書籤；沒有書籤時，依「粗體 + 羅馬數字」(level 1)、
「粗體編號 + 同列粗體標題」(1 / 3.1 / A，層級看點數)、
「斜體 + 大寫字母.」(level 2)、以及 Introduction/References/Appendix 等關鍵字辨識。
圖表：figures 依圖說位置裁切（Figure 往上找、Table 往下找），split 在圖說處插入
<!-- figure: ... --> 標記；兩者共用 find_captions，檔名一致。
輸出預設在 .llm-wiki/extract/<pdf 檔名>/，raw/ 不會被修改。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I 會拿掉腳本目錄；docsrc 在旁邊
import _limits  # noqa: E402, F401  (4 threads, 8 GB；要在 pymupdf 之前)
import docsrc  # noqa: E402
import pymupdf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROMAN = re.compile(r"^([IVX]+)\.\s+\S")
LETTER = re.compile(r"^([A-H])\.\s+\S")
KEYWORD = re.compile(r"^(Introduction|INTRODUCTION|References|REFERENCES|Appendix|APPENDIX|Conclusion)\b")
NUMBER = re.compile(r"^(\d+(?:\.\d+){0,2}|[A-Z](?:\.\d+){0,2})$")
# 圖說：編號後接 : | . 或整行只有編號（"Table 1"）；內文的 "Table 2 shows ..." 不算
CAPTION = re.compile(r"^(Table|Figure|Fig\.)\s*([IVX]+|\d+|[A-Z]\d*)(?:\s*[:|.—–]|\s*$)")
CAPTION_BOLD = re.compile(r"^(Table|Figure)\s+([IVX]+|\d+|[A-Z]\d*)\b")  # 粗體圖說不要求分隔符號


@dataclass
class Heading:
    level: int
    title: str
    page: int  # 1-based
    y: float


def default_out(pdf: Path) -> Path:
    return ROOT / ".llm-wiki" / "extract" / pdf.stem


def iter_lines(doc):
    """依頁、依閱讀順序產出 (page_no, y0, text, first_span)。"""
    for pno, page in enumerate(doc, start=1):
        for text, line in docsrc.page_lines(page, merge=False):
            yield pno, line["bbox"][1], text, line["spans"][0]


def body_size(doc) -> float:
    from collections import Counter
    c = Counter()
    for _, _, text, span in iter_lines(doc):
        c[round(span["size"], 1)] += len(text)
    return c.most_common(1)[0][0] if c else 10.0  # 掃描檔沒有文字層：給常見內文字級


def detect_headings(doc) -> list[Heading]:
    toc = doc.get_toc(simple=False)
    if toc:
        out = []
        for lvl, title, page, dest in toc:
            y = dest.get("to").y if isinstance(dest, dict) and dest.get("to") else 0.0
            out.append(Heading(lvl, title.strip(), page, y))
        return out

    size = body_size(doc)
    out: list[Heading] = []
    prev = None
    for pno, y, text, span in iter_lines(doc):
        font = span["font"].lower()
        bold = "bold" in font or span["flags"] & 16
        # 編號與標題被拆成同一個 y 的兩行（例如 "3.1" + "Pre-training Data"）
        if prev and bold and text[0].isalpha() and prev[:2] == (pno, round(y)):
            num = prev[2]
            out.append(Heading(num.count(".") + 1, f"{num} {text}", pno, y))
            prev = None
            continue
        prev = (pno, round(y), text) if bold and NUMBER.match(text) else None
        if len(text) > 100 or abs(span["size"] - size) > 0.6 and span["size"] < size:
            continue
        italic = "italic" in font or span["flags"] & 2
        if bold and (ROMAN.match(text) or KEYWORD.match(text)):
            out.append(Heading(1, text, pno, y))
        elif italic and not bold and LETTER.match(text):
            out.append(Heading(2, text, pno, y))
    # 第一個章節之前的內容（標題、摘要、引言）
    if out and (out[0].page > 1 or out[0].y > 0):
        out.insert(0, Heading(1, "Front Matter and Introduction", 1, 0.0))
    return out


def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_]+", "-", s)[:60]


def cmd_toc(args):
    doc = pymupdf.open(args.pdf)
    src = "bookmarks" if doc.get_toc() else "detected"
    print(f"# {Path(args.pdf).name}  pages={doc.page_count}  source={src}")
    for h in detect_headings(doc):
        print(f"{'  ' * (h.level - 1)}- {h.title}  (p.{h.page})")


def cmd_split(args):
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    # 找不到章節（無書籤、標題非粗體）時整份當一節，不要 crash
    heads = [h for h in detect_headings(doc) if h.level <= args.level] or [Heading(1, pdf.stem, 1, 0.0)]
    out = Path(args.out) if args.out else default_out(pdf) / "sections"
    out.mkdir(parents=True, exist_ok=True)

    caps = {(c["page"], round(c["y"], 1)): c for c in find_captions(doc)}
    buckets: list[list[str]] = [[] for _ in heads]
    idx = 0
    last_page = None
    for pno, y, text, _ in iter_lines(doc):
        while idx + 1 < len(heads) and (pno, y) >= (heads[idx + 1].page, heads[idx + 1].y - 0.5):
            idx += 1
        if pno != last_page:
            buckets[idx].append(f"\n<!-- page {pno} -->")
            last_page = pno
        if (c := caps.get((pno, round(y, 1)))) and c["caption"].startswith(text[:20]):
            buckets[idx].append(f"<!-- figure: {c['kind']} {c['id']} p.{pno} -> figures/{c['file']} -->")
        buckets[idx].append(text)

    manifest = []
    for i, (h, lines) in enumerate(zip(heads, buckets)):
        end = heads[i + 1].page if i + 1 < len(heads) else doc.page_count
        name = f"{i:02d}-{slug(h.title)}.md"
        body = "\n".join(lines)
        (out / name).write_text(f"# {h.title}\n\n_pages {h.page}-{end}_\n{body}\n", encoding="utf-8")
        manifest.append({**asdict(h), "end_page": end, "file": name, "chars": len(body)})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for m in manifest:
        print(f"{m['file']:<70} p.{m['page']}-{m['end_page']}  {m['chars']} chars")
    print(f"-> {out}")


def render(page, path: Path, dpi: int, clip=None, rotate: int = 0):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(rotate), clip=clip)
    pix.save(path)
    return path


def cmd_shot(args):
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    out = Path(args.out) if args.out else default_out(pdf) / "shots"
    out.mkdir(parents=True, exist_ok=True)
    clip = pymupdf.Rect(*map(float, args.clip.split(","))) if args.clip else None
    try:
        pages = sorted(docsrc.parse_ranges(args.pages))
    except ValueError as e:
        raise SystemExit(f"--pages 不正確：{e}") from None
    if not pages or pages[-1] > doc.page_count:
        raise SystemExit(f"--pages 超出範圍（共 {doc.page_count} 頁）：{args.pages}")
    for p in pages:
        suffix = "-clip" if clip else ""
        path = render(doc[p - 1], out / f"p{p:03d}{suffix}.png", args.dpi, clip)
        print(path)


def find_captions(doc) -> list[dict]:
    """找出 Table/Figure 圖說：每個編號每頁一筆，跨頁表格的續頁檔名加 -p<頁>。"""
    seen, items = set(), []
    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict", sort=True)["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                span = line["spans"][0] if line["spans"] else None
                bold = span and ("bold" in span["font"].lower() or span["flags"] & 16)
                m = CAPTION.match(text) or (bold and CAPTION_BOLD.match(text))
                if not m:
                    continue
                kind = "Figure" if m.group(1).startswith("Fig") else m.group(1)
                key = (kind, m.group(2))
                if (*key, pno) in seen:
                    continue
                cont = any(k[:2] == key for k in seen)
                seen.add((*key, pno))
                # 圖說段落：整個 block；block 太大（和內文黏在一起）就只取這一行
                rect = pymupdf.Rect(block["bbox"])
                upright = abs(line["dir"][0]) >= abs(line["dir"][1])
                if (rect.height / page.rect.height if upright else rect.width / page.rect.width) > 0.25:
                    rect = pymupdf.Rect(line["bbox"])
                caption = " ".join("".join(s["text"] for s in ln["spans"]) for ln in block["lines"]
                                   if pymupdf.Rect(ln["bbox"]).intersects(rect))
                # 文字方向 (0,-1)：由下往上讀，截圖要轉 90° 才是正的
                rotate = 0 if upright else (90 if line["dir"][1] < 0 else -90)
                items.append({"kind": kind, "id": m.group(2), "page": pno, "y": line["bbox"][1], "rotate": rotate,
                              "caption_rect": tuple(rect), "caption": re.sub(r"\s+", " ", caption).strip()[:200],
                              "file": f"{kind.lower()}-{m.group(2)}{f'-p{pno}' if cont else ''}.png"})
    return items


def _merge(rects: list, gap: float) -> list:
    """把距離 gap 以內的矩形合併成群組。"""
    rects = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed, out = False, []
        for r in rects:
            for i, o in enumerate(out):
                if _near(o, r, gap):
                    out[i] = _union(o, r)  # 寫回串列；Rect 的 |= 不保證原地修改
                    changed = changed or out[i] != o
                    break
            else:
                out.append(pymupdf.Rect(r))
        rects = out
    return rects


def clipped_drawings(page) -> list[dict]:
    """向量繪圖，外框扣掉 clip path（被座標軸裁掉的折線不會把外框撐到內文去）。"""
    out, stack = [], []  # stack: (level, scissor)
    for d in page.get_drawings(extended=True):
        lvl = d.get("level", 0)
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        if d["type"] == "clip":
            sc = pymupdf.Rect(d["scissor"])
            if stack:
                sc = _clamp(sc, stack[-1][1]) or pymupdf.Rect(sc.x0, sc.y0, sc.x0, sc.y0)
            stack.append((lvl, sc))
            continue
        if d["type"] == "group":
            continue
        r = pymupdf.Rect(d["rect"])
        if stack and not (r := _clamp(r, stack[-1][1])):
            continue
        out.append({**d, "rect": r})
    return out


def graphics(page) -> list:
    """頁面上的圖形區塊：點陣圖、向量繪圖群組、偵測到的表格。"""
    full = page.rect.get_area() * 0.8
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    try:
        drawings = clipped_drawings(page)
        rects += list(page.cluster_drawings(drawings=drawings))
        # cluster_drawings 會丟掉零厚度的線（表格的水平框線），另外補回
        # 只補長線：圖裡成千的刻度、格線短線段會讓 _merge 變成 O(n²)
        rects += [d["rect"] for d in drawings
                  if (d["rect"].height == 0 and d["rect"].width >= 40) or (d["rect"].width == 0 and d["rect"].height >= 40)]
    except Exception:  # 繪圖指令異常的頁面
        pass
    try:
        rects += [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
    except Exception:
        pass
    rects = [_clamp(r, page.rect) for r in rects]
    return [r for r in rects if r and (r.width > 1 or r.height > 1) and r.get_area() < full]


def _clamp(r, s):
    """交集；與 Rect 的 & 不同，高度為 0 的水平線不會變成空的。"""
    r = pymupdf.Rect(max(r.x0, s.x0), max(r.y0, s.y0), min(r.x1, s.x1), min(r.y1, s.y1))
    return r if r.x0 <= r.x1 and r.y0 <= r.y1 else None


def _union(a, b):
    """聯集；Rect 的 | 會忽略零厚度的線。"""
    return pymupdf.Rect(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))


def _near(a, b, pad: float) -> bool:
    return a.x0 - pad <= b.x1 and b.x0 <= a.x1 + pad and a.y0 - pad <= b.y1 and b.y0 <= a.y1 + pad


def page_layout(page):
    """crop_rect 用的整頁圖形區塊與文字行；find_tables 很慢，同一頁多個圖說時共用一份。"""
    blocks = _merge(graphics(page), 10)
    lines = [(pymupdf.Rect(ln["bbox"]), "".join(s["text"] for s in ln["spans"]).strip(), ln["spans"][0]["size"])
             for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) if ln["spans"]]
    return blocks, lines


def crop_rect(page, item, body: float, layout=None):
    """依圖說位置找出圖或表的範圍；找不到回傳 None（改截整頁）。

    Figure 的圖在圖說上方、Table 的表在圖說下方，偏好方向找不到再找另一邊。
    找到最近的圖形區塊後往外長：相鄰的圖形區塊（子圖、表格框線）與
    圖內文字（座標軸、圖例、儲存格：短行或小字）都併入，但不越過圖說。
    橫排（旋轉 90°）的圖表圖說是直條：把 x、y 對調後用同一套邏輯，最後再換回來。
    """
    cap = pymupdf.Rect(item["caption_rect"])
    blocks, lines = layout or page_layout(page)
    labels = [r for r, text, size in lines if text and (len(text) < 60 or size < body - 0.5)]
    vertical = cap.height > 3 * cap.width
    if vertical:
        cap, blocks, labels = _t(cap), [_t(r) for r in blocks], [_t(r) for r in labels]

    def same_side(r, above):
        return r.y1 <= cap.y0 + 4 if above else r.y0 >= cap.y1 - 4

    def overlap(r):
        return min(r.x1, cap.x1) - max(r.x0, cap.x0) > 0.3 * min(r.width, cap.width)

    def grow(above, seeds, max_gap):
        gap = lambda r: cap.y0 - r.y1 if above else r.y0 - cap.y1  # noqa: E731
        cands = [r for r in seeds if overlap(r) and same_side(r, above) and gap(r) <= max_gap]
        if not cands:
            return None, 0
        region = pymupdf.Rect(min(cands, key=gap))
        pool = [(r, 12) for r in blocks if same_side(r, above)]
        pool += [(r, 10) for r in labels if same_side(r, above) and not r.intersects(cap)]  # 表格分組間距約 8pt
        n, grew = 0, True
        while grew:
            grew = False
            for r, pad in pool:
                if _near(region, r, pad) and (new := _union(region, r)) != region:
                    region = new
                    n += 1
                    grew = True
        return region, n

    above = item["kind"] == "Figure"
    region = grow(above, blocks, 60)[0] or grow(not above, blocks, 60)[0]
    if region is None:  # 沒有框線的純文字表格：從圖說旁的短行開始長，至少要 4 行才算
        for side in (above, not above):
            region, n = grow(side, labels, 20)
            if region is not None and n >= 3:
                break
            region = None
    if region is None:
        return None
    region |= cap
    if vertical:
        region = _t(region)
    return _clamp(region + (-4, -4, 4, 4), page.rect)


def _t(r):
    """x、y 對調（處理旋轉 90° 的圖表）。"""
    return pymupdf.Rect(r.y0, r.x0, r.y1, r.x1)


def cmd_figures(args):
    """找出 Table/Figure 圖說，裁切出圖或表並輸出清單；裁不出來的整頁截圖並標 crop=page。"""
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    out = Path(args.out) if args.out else default_out(pdf) / "figures"
    out.mkdir(parents=True, exist_ok=True)
    body = body_size(doc)
    items, layouts = [], {}
    for it in find_captions(doc):
        page = doc[it["page"] - 1]
        if it["page"] not in layouts:
            layouts[it["page"]] = page_layout(page)
        clip = crop_rect(page, it, body, layouts[it["page"]])
        render(page, out / it["file"], args.dpi, clip, it["rotate"] if clip else 0)
        items.append({"kind": it["kind"], "id": it["id"], "page": it["page"], "file": it["file"],
                      "crop": "region" if clip else "page", "clip": [round(v, 1) for v in clip] if clip else None,
                      "caption": it["caption"]})
        print(f"{it['kind']} {it['id']:<5} p.{it['page']:<3} {items[-1]['crop']:<6} -> {it['file']}")
    # 圖說印在點陣圖裡（例如 Exhibit）時文字層找不到圖說：大型點陣圖沒被裁到就單獨截下
    for pno, page in enumerate(doc, start=1):
        done = [pymupdf.Rect(i["clip"]) if i["clip"] else page.rect for i in items if i["page"] == pno]
        big = [pymupdf.Rect(i["bbox"]) & page.rect for i in page.get_image_info()]
        big = [r for r in big if r.get_area() >= page.rect.get_area() * 0.08]
        k = 0
        for r in big:
            if any(d.intersects(r) for d in done):
                continue
            done.append(r)
            k += 1
            name = f"image-p{pno}-{k}.png"
            render(page, out / name, args.dpi, r + (-2, -2, 2, 2) & page.rect)
            items.append({"kind": "Image", "id": f"p{pno}-{k}", "page": pno, "file": name,
                          "crop": "image", "clip": [round(v, 1) for v in r], "caption": ""})
            print(f"Image p.{pno:<3} -> {name}")
    (out / "manifest.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {out}")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("toc"); p.add_argument("pdf"); p.set_defaults(fn=cmd_toc)
    p = sub.add_parser("split"); p.add_argument("pdf"); p.add_argument("--out"); p.add_argument("--level", type=int, default=2); p.set_defaults(fn=cmd_split)
    p = sub.add_parser("shot"); p.add_argument("pdf"); p.add_argument("--pages", required=True); p.add_argument("--clip"); p.add_argument("--dpi", type=int, default=150); p.add_argument("--out"); p.set_defaults(fn=cmd_shot)
    p = sub.add_parser("figures"); p.add_argument("pdf"); p.add_argument("--out"); p.add_argument("--dpi", type=int, default=150); p.set_defaults(fn=cmd_figures)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
