"""抓取網頁文章，轉成 Markdown 快照（給 LLM wiki ingest 用）。

用法（在專案根目錄）：
  uv run python -I tools/web_fetch.py <url> [--out DIR] [--name NAME] [--no-images] [--md]

--md：先試 <url>.md（官方文件常有，比 HTML 乾淨）；回應是 Markdown 就直接用，
      不是（404、回 HTML 等）就退回 HTML 轉換。frontmatter 的 url 記實際抓到的網址。

輸出：
  <out>/<name>.md       文章正文 Markdown，開頭附 frontmatter（url、抓取時間、標題）
  <out>/<name>.html     原始 HTML（留存證據；--md 成功時改存原始 Markdown <name>.source.md）
  <out>/<name>_images/  文章內圖片（--no-images 可略過）
預設 out = .llm-wiki/extract/web/<name>/
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _limits  # noqa: E402, F401  (4 threads, 8 GB; see _limits.py)
import truststore  # noqa: E402
from bs4 import BeautifulSoup, UnicodeDammit
from markdownify import markdownify

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; llm-wiki-fetcher/0.1)"
MAIN_SELECTORS = ["article", "main article", "div.post", "div.post-content", "main", "div#content", "body"]
DROP = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "button"]


def slugify(url: str) -> str:
    path = urlparse(url).path.strip("/").split("/")
    return re.sub(r"[^\w-]", "-", path[-1] if path and path[-1] else urlparse(url).netloc)[:80]


def is_web_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in ("http", "https")


def get(url: str) -> tuple[bytes, str, str | None]:
    """GET（自動跟隨轉址，HTTP 錯誤會丟 HTTPError）；回傳（內容, 最終網址, header 宣告的編碼）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # 用作業系統的憑證清單驗證（內建清單缺 Let's Encrypt Root YE）
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read(), r.geturl(), r.headers.get_content_charset()


# 圖片先處理；連結文字可以包一張圖（徽章：[![b](x.svg)](url)）
_TARGET = r'\(((?:[^()\s]|\([^()\s]*\))+)((?:\s+"[^"]*")?)\)'
MD_IMAGE = re.compile(r'!\[([^\]]*)\]' + _TARGET)
MD_LINK = re.compile(r'(?<!!)\[((?:[^\[\]]|!\[[^\]]*\]\([^)]*\))*)\]' + _TARGET)
# fence 可以縮排、可以在 blockquote 裡；反引號 fence 的 info string 不能含反引號
_QUOTE = r"^(?:[ \t]*>)*[ \t]*"
FENCE_OPEN = re.compile(_QUOTE + r"(`{3,}|~{3,})(.*)$")


def split_fences(md: str) -> list[tuple[bool, str]]:
    """切成（是否在程式碼區塊內, 文字）；沒有關閉的 fence 一直算到文件結尾。"""
    parts: list[tuple[bool, str]] = []
    buf: list[str] = []
    lines = md.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i].rstrip("\r\n"))
        if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
            buf.append(lines[i])
            i += 1
            continue
        if buf:
            parts.append((False, "".join(buf)))
            buf = []
        mark = m.group(1)
        close = re.compile(_QUOTE + re.escape(mark[0]) + "{" + str(len(mark))
                           + r",}[ \t]*$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j].rstrip("\r\n")):
            j += 1
        parts.append((True, "".join(lines[i:j + 1])))
        i = j + 1
    if buf:
        parts.append((False, "".join(buf)))
    return parts


def md_variant(url: str) -> str:
    """回傳 url 的 .md 版本網址（去掉查詢與錨點；已是 .md 就原樣）。"""
    u = urlparse(url)
    path = u.path.rstrip("/") or "/index"
    if not path.lower().endswith(".md"):
        path += ".md"
    return u._replace(path=path, query="", fragment="").geturl()


def looks_like_markdown(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return bool(head) and not head.startswith(("<!doctype", "<html", "<?xml"))


def fetch_markdown(url: str) -> tuple[str, str, bytes] | None:
    """抓 url 的 .md 版本；拿不到或不是 Markdown 就回 None。

    回傳（換行統一成 LF 的文字, 最終網址, 原始位元組）。
    """
    try:
        content, final_url, charset = get(md_variant(url))
        text = content.decode(charset or "utf-8", errors="replace")
    except (OSError, ValueError, LookupError, http.client.HTTPException) as e:
        # LookupError：header 宣告了不存在的編碼；IncompleteRead 不是 OSError
        print(f"[md] {md_variant(url)}: {e}，改抓 HTML")
        return None
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not looks_like_markdown(text):
        print(f"[md] {final_url} 回的不是 Markdown，改抓 HTML")
        return None
    return text, final_url, content


def split_frontmatter(text: str) -> tuple[str, str]:
    """拆掉來源自帶的 YAML frontmatter，回傳（其中的 title, 正文）。"""
    m = re.match(r"---\r?\n(.*?)\r?\n?---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return "", text
    t = re.search(r"^title:[ \t]*(\S.*)$", m.group(1), re.M)
    return (t.group(1).strip().strip("'\"") if t else ""), text[m.end():]


def save_image(src: str, base: str, img_dir: Path, name: str, n: int) -> str | None:
    """下載一張圖存成 img{n:02d}；只接受 http/https。成功回傳相對路徑。"""
    full = urljoin(base, src)
    if not is_web_url(full):  # 網頁內容不可信：file:// 會把本機檔案複製進快照
        print(f"[skip image] {full}: 只接受 http/https")
        return None
    try:
        data = get(full)[0]
    except (OSError, ValueError) as e:  # HTTPError/URLError/逾時都是 OSError；怪網址是 ValueError
        print(f"[skip image] {full}: {e}")
        return None
    img_dir.mkdir(exist_ok=True)
    ext = Path(urlparse(full).path).suffix or ".img"
    fname = f"img{n:02d}{ext}"
    (img_dir / fname).write_bytes(data)
    return f"{name}_images/{fname}"


def localize_markdown(md: str, base: str, img_dir: Path, name: str,
                      images: bool) -> tuple[str, int]:
    """相對連結改絕對網址；圖片下載到 img_dir（images=False 時只改成絕對網址）。

    程式碼區塊（``` 或 ~~~）裡的內容不動。
    """
    n_img = 0

    def image(m: re.Match) -> str:
        nonlocal n_img
        text, target, title = m.groups()
        if target.startswith(("#", "data:")):
            return m.group(0)
        if images:
            local = save_image(target, base, img_dir, name, n_img + 1)
            if local:
                n_img += 1
                return f"![{text}]({local}{title})"
        return f"![{text}]({urljoin(base, target)}{title})"

    def link(m: re.Match) -> str:
        text, target, title = m.groups()
        if target.startswith("#"):
            return m.group(0)
        return f"[{text}]({urljoin(base, target)}{title})"

    out = [text if code else MD_LINK.sub(link, MD_IMAGE.sub(image, text))
           for code, text in split_fences(md)]
    return "".join(out), n_img


def pick_main(soup: BeautifulSoup):
    for sel in MAIN_SELECTORS:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 500:
            return node
    return soup.body or soup


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out")
    ap.add_argument("--name")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--md", action="store_true", help="先試 <url>.md，失敗再抓 HTML")
    args = ap.parse_args()

    name = args.name or slugify(args.url)
    out = Path(args.out) if args.out else ROOT / ".llm-wiki" / "extract" / "web" / name
    out.mkdir(parents=True, exist_ok=True)

    got = fetch_markdown(args.url) if args.md else None
    if got:
        text, final_url, raw = got
        (out / f"{name}.source.md").write_bytes(raw)  # 原樣留存
        title, body = split_frontmatter(text)
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        title = title or (h1.group(1).strip() if h1 else name)
        body, n_img = localize_markdown(body, final_url, out / f"{name}_images", name,
                                        not args.no_images)
        write_snapshot(out, name, final_url, title, "", n_img, body.strip())
        return

    content, final_url, charset = get(args.url)
    # header 沒寫編碼時由 UnicodeDammit 依 <meta charset> 與內容判斷（例如 Big5 網頁）
    html = UnicodeDammit(content, [charset] if charset else [], is_html=True).unicode_markup
    (out / f"{name}.html").write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("meta", property="og:title") or {}).get("content") or (soup.title and soup.title.string) or name
    date = (soup.find("meta", property="article:published_time") or {}).get("content", "")
    node = pick_main(soup)
    for tag in node.find_all(DROP):
        tag.decompose()

    # 相對連結在 Obsidian 點了會在 vault 根目錄建空檔；轉絕對網址，錨點保留
    for a in node.find_all("a", href=True):
        if not a["href"].startswith("#"):
            a["href"] = urljoin(final_url, a["href"])

    n_img = 0
    if not args.no_images:
        img_dir = out / f"{name}_images"
        for img in node.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src or src.startswith("data:"):
                continue
            local = save_image(src, final_url, img_dir, name, n_img + 1)
            if not local:
                continue
            n_img += 1
            img["src"] = local
            img["data-original-src"] = urljoin(final_url, src)

    body = markdownify(str(node), heading_style="ATX", bullets="-", code_language_callback=lambda el: (el.get("class") or [""])[0].replace("language-", ""))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    write_snapshot(out, name, final_url, title, date, n_img, body)


def write_snapshot(out: Path, name: str, url: str, title: str, date: str,
                   n_img: int, body: str) -> None:
    fetched = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    # JSON 字串也是合法 YAML；Python repr 遇到同時有 ' 和 " 的標題會產生壞掉的 YAML
    front = f"---\nurl: {url}\ntitle: {json.dumps(title.strip(), ensure_ascii=False)}\npublished: {date}\nfetched: {fetched}\nimages: {n_img}\n---\n\n"
    md_path = out / f"{name}.md"
    md_path.write_text(front + body + "\n", encoding="utf-8")
    print(f"title: {title.strip()}\npublished: {date}\nchars: {len(body)}  images: {n_img}\n-> {md_path}")


if __name__ == "__main__":
    main()
