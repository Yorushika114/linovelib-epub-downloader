"""bilimanga.net 页面解析（纯 bs4，无 IO、无浏览器）。

结构速记（均已实地验证）：
- 搜索：POST /search.html（需真实浏览器完成 search_guard=css/js/redeem 挑战后渲染），
  结果挂在含「條記錄」的 module 内 -> ol.book-ol -> li.book-li -> a.book-layout。
- 详情/目录：/read/{cid}/catalog（桌面端可访问），
  h1=标题、h2=作者行、div.catalog-volume 每卷 + ul.volume-chapters 每章。
- 章节：/read/{cid}/{chid}.html（必须移动端 UA），图片 URL 全部写在 img[data-src]，
  顺序与图片 id 严格递增一致（未经乱序）。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import Chapter, Comic, ComicHit, Volume

BASE = "https://www.bilimanga.net"

# 搜索结果主题词：图片 URL 形如 https://w.motiezw.com/{w}/{cid}/{chid}/{imgid}.avif
_IMGID_RE = re.compile(r"/(\d+)\.(?:avif|jpg|png|webp)(?:\?|$)", re.I)


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def parse_search_results(html: str) -> list[ComicHit]:
    """从搜索结果的渲染后 HTML 解析命中列表。

    只取含「條記錄」的 module，避免把底部推荐位（id=1..8 之类的书）算进来。
    """
    soup = BeautifulSoup(html, "lxml")
    module = None
    for mod in soup.select("div.module"):
        h = mod.select_one("h3.module-title")
        if h and "條記錄" in h.get_text():
            module = mod
            break
    if module is None:
        module = soup  # 兜底：找不到就全页找 book-li

    hits: list[ComicHit] = []
    seen: set[int] = set()
    for a in module.select("ol.book-ol li.book-li a.book-layout"):
        href = a.get("href") or ""
        m = re.search(r"/detail/(\d+)\.html", href)
        if not m:
            continue
        cid = int(m.group(1))
        if cid in seen:
            continue
        seen.add(cid)
        title = _text(a.select_one("h4.book-title"))
        # 作者 <span> 里带一个 SVG 图标（其 <title>作者</title> 会被 _text 误当正文收进去，
        # 导致作者变成「作者 カネツキマサト」）。剔除 SVG 后再取文本，避免前缀污染。
        author_el = a.select_one("span.book-author")
        if author_el:
            for svg in author_el.select("svg"):
                svg.extract()
        author = _text(author_el)
        desc = _text(a.select_one("p.book-desc"))
        hits.append(ComicHit(id=cid, title=title, author=author,
                             url=urljoin(BASE, href), desc=desc))
    return hits


def _parse_author(line: str) -> str:
    """从 h2 的「作者：X、原著：Y、原案：Z」中挑出第一段作者名。"""
    for part in line.split("、"):
        part = part.strip()
        if part.startswith("作者"):
            return part.split("：", 1)[-1].strip()
    return line


def _chapter_title(a) -> str:
    """从目录页章节 <a> 里提取可读章名。

    bilimanga 的卷章列表里，每个 <a class="chapter-li-a"> 一般只有一个
    <span class="chapter-index">�000</span> 指位徽标，**没有真正的章名文本**。
    而且那个徽标前导字形是 GBK 打不出的字符，直接当标题打印就会乱码（用户看到的
    「# 000」）。所以这里：
      - 若 <a> 里除了指位徽标还有别的文字（= 真实章名），取那段；
      - 否则从徽标数字推导「第N话」：#000 => 第1话（0 起算 +1）。
    """
    raw = a.get_text(" ", strip=True)
    idx = a.select_one(".chapter-index")
    if idx is not None:
        badge = idx.get_text(strip=True)
        # 剔掉指位徽标，剩下的才是疑似真实章名
        idx.extract()
        rest = a.get_text(" ", strip=True).strip()
        if rest:
            return rest
        m = re.search(r"(\d+)", badge)
        if m:
            return f"第{int(m.group(1)) + 1}话"
        return raw
    return raw


def parse_catalog(html: str, comic_id: int) -> Comic:
    """解析 /read/{cid}/catalog，得到标题 / 作者 / 卷 / 章。"""
    soup = BeautifulSoup(html, "lxml")
    title = _text(soup.select_one("h1"))
    author = _parse_author(_text(soup.select_one("h2"))) if soup.select_one("h2") else ""

    volumes: list[Volume] = []
    for i, v in enumerate(soup.select("div.catalog-volume"), start=1):
        vt = v.select_one("div.chapter-bar h3") or v.select_one("h3")
        vol_title = _text(vt) or f"第{i}卷"
        chapters: list[Chapter] = []
        for a in v.select("ul.volume-chapters li a.chapter-li-a"):
            href = a.get("href") or ""
            m = re.search(r"/read/(\d+)/(\d+)\.html", href)
            if not m:
                continue
            chapters.append(Chapter(
                id=int(m.group(2)),
                title=_chapter_title(a),
                url=urljoin(BASE, href),
            ))
        if chapters:
            volumes.append(Volume(index=i, title=vol_title, chapters=chapters))

    return Comic(id=comic_id, title=title, author=author, volumes=volumes)


def parse_chapter_image_urls(html: str) -> list[str]:
    """解析章节页渲染后的所有图片 URL，按 DOM 顺序（=阅读顺序，未经乱序）。"""
    soup = BeautifulSoup(html, "lxml")
    order: list[str] = []
    seen: set[str] = set()
    for img in soup.select("img[data-src], img[src]"):
        # data-src 优先，退到 src；跳过纯占位/无图
        url = img.get("data-src") or img.get("src") or ""
        url = url.strip()
        if not url or url.startswith("data:"):
            continue
        if "motiezw.com" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        order.append(url)
    return order


def ordered_image_urls(urls: list[str]) -> list[str]:
    """校验图片顺序是否单调递增（即乱序检测）。

    bilimanga 的 data-src 图片 id 严格递增，故 DOM 顺序即真序。
    如果检测到递减（潜在乱序），返回按 id 排序的结果并标记异常（由调用方告警）。
    返回 (urls, is_monotonic)。
    """
    def img_id(u: str) -> int:
        m = _IMGID_RE.search(u)
        return int(m.group(1)) if m else 0

    ids = [img_id(u) for u in urls]
    monotonic = all(ids[i] < ids[i + 1] for i in range(len(ids) - 1))
    if not monotonic:
        order = sorted(range(len(urls)), key=lambda i: ids[i])
        urls = [urls[i] for i in order]
    return urls, monotonic
