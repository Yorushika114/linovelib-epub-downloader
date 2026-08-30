import re
import time
import pathlib
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .models import ChapterPage, Chapter, ImageAsset

BASE = "https://www.linovelib.com"

# 反爬可能的痕迹：站点在扣留长文时会在正文末尾追加这句；或直接返回无正文的拦截页。
_TRUNC_MARKERS = ("內容加載失敗", "内容加载失败", "請刷新或更換瀏覽器", "请刷新或更换浏览器")


def _page_looks_bad(html, page):
    """判断某页是否被反爬拦截/截断，需要重试。

    三种情形成立其一即为坏页：正文带「内容加载失败」标记；整页没有正文容器
    #TextContent（多是被 Cloudflare/风控拦下）；解析后既无段落也无插图（空页）。
    """
    for m in _TRUNC_MARKERS:
        if m in html:
            return True
    if "TextContent" not in html:
        return True
    return not page.paragraphs and not page.image_urls


def _fetch_page_robust(url, index, fetcher, attempted=0):
    """抓取并解析单页；若判定为坏页则退避重试，最终仍坏时也返回结果，绝不丢页。

    返回的始终是 ChapterPage；重试全部失败时返回最后一页，保证该页在 pages 里
    占位，正文顺序与页数不会被悄悄打乱或缩减（宁可保留，也不静默缺失）。
    """
    html = fetcher.get_html(url)
    page = parse_chapter_page(html, url, index)
    if not _page_looks_bad(html, page):
        return page
    attempted += 1
    if attempted < fetcher.retries:
        time.sleep(2 ** (attempted - 1))
        return _fetch_page_robust(url, index, fetcher, attempted)
    return page


def parse_chapter_page(html, url, index):
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    box = soup.find("div", id="TextContent") or soup.find("div", class_="TextContent")
    paragraphs, images = [], []
    if box:
        paragraphs = [p.get_text("\n", strip=True) for p in box.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        # linovelib 的 pctheme.js 会在正文上「克隆」一遍段落（文本与原文完全相同），
        # 浏览器渲染后这些克隆会随下文一起进入 #TextContent，导致同一句话重复多次、
        # 读到一半像回放。克隆与原文在 DOM 上毫无类/样式/可见性差别，无法靠 CSS 过滤，
        # 只能按「段落文本完全一致」去重（保留首次出现）。这样无论抓取早晚都能还原出
        # 站点真实正文（未注入克隆时的内容），且不改变相对顺序。
        seen = set()
        deduped = []
        for p in paragraphs:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)
        paragraphs = deduped
        for img in box.find_all("img"):
            # 懒加载图：<img src="/images/sloading.svg" data-src="真实URL">。
            # 页面初次取到的是占位 SVG，真实配图在 data-src，故优先取 data-src；
            # 正文插图两类情况：data-src 为空时回退到 src（真实 URL）。
            src = img.get("data-src") or img.get("src") or ""
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = BASE + src
            elif not src.startswith("http"):
                src = urljoin(url, src)
            images.append(src)
    return ChapterPage(index=index, url=url, title=title,
                       paragraphs=paragraphs, image_urls=images)


def _split_header(texts):
    """把章首「重复横幅标题」段（如「～第N败～ 别看我这样，其实很〇〇」，linovelib 常连放两遍）
    从正文里摘出来单独保留。它在参考书里无对应文本，参考对齐器会把它当末命中沉到章尾，
    破坏标题位置；据此我们先把这种「同一短句重复≥2遍且无句末标点」的开头段作为标题保留。

    返回 (banner, body)：banner 保持在章首，body 再交给参考对齐器重排。
    """
    from collections import Counter
    if not texts:
        return [], []
    counts = Counter(texts)
    banner = []
    while texts and counts[texts[0]] >= 2 and len(texts[0]) <= 40 \
            and not any(c in texts[0] for c in "。？！"):
        banner.append(texts.pop(0))
    return banner, texts


def _dedup_keep_order(paragraphs):
    """按「段落文本完全一致」去重，保留首次出现。

    pctheme 在【实时 DOM】里插入的可见克隆段，其文本与某真段完全相同且无任何
    class/data-*/display 标记，只能靠文本去重识别。克隆是多余重复，删掉它既不丢内容、
    又不改变真段相对顺序（真序由浏览器 DOM 保证 → 首见即真段）。
    """
    seen = set()
    out = []
    for p in paragraphs:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _fetch_page_ordered(url, index, fetcher):
    """抓取并解析一页正文（参考无关）：纯 requests + 逐页 Fisher-Yates 反洗牌。

    依赖 Fetcher.get_page_body 返回的已还原正文；页内再按文本去重克隆段。若抓取器
    没有 get_page_body（旧测试抓取器），回退到 _fetch_page_robust 的 HTTP 解析路径。
    """
    get_page_body = getattr(fetcher, "get_page_body", None)
    if not callable(get_page_body):
        return _fetch_page_robust(url, index, fetcher)

    title, paras, imgs = get_page_body(url)
    if paras:
        return ChapterPage(index=index, url=url, title=title,
                           paragraphs=_dedup_keep_order(paras), image_urls=imgs)
    raise RuntimeError("未能读取有序正文，已拒绝写入可能乱序的备用内容")


def iter_page_urls(base_url, fetcher):
    """收集一个章节的分页 URL；至少返回 [base_url]。

    仅跟随指向「同一章节」（同一 cid 的 `_N.html`）的下一页链接，
    避免顺着指向「下一章」（不同 cid）的下一页跑出本章节。
    """
    cid = _chapter_cid(base_url)
    page_re = re.compile(re.escape(cid) + r"(?:_\d+)?\.html$")
    urls = []
    seen = set()
    url = base_url
    while url and url not in seen:
        seen.add(url)
        urls.append(url)
        html = fetcher.get_html(url)
        soup = BeautifulSoup(html, "lxml")
        nxt = None
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if "下一页" in text or "下一頁" in text or "後一頁" in text:
                nxt = a["href"]
                break
        if not nxt:
            break
        nxt = urljoin(url, nxt)
        if not page_re.search(nxt) or nxt in seen:
            break
        url = nxt
    return urls


def _chapter_cid(url):
    base = url.rsplit("/", 1)[-1].split(".html")[0]
    return base.split("_")[0]


def download_chapter(chapter, nid, fetcher, tmpdir, aligner=None):
    """下载章节全部页面，合并正文并落地插图。

    正文走参考无关路径：逐页纯 requests 抓 #TextContent → 站点 Fisher-Yates 逆置换
    还原该页后缀真序（见 Fetcher.get_page_body），页内与章内再按「段落文本完全一致」
    去掉 pctheme 注入的克隆段。服务器按页下发、页码参与 seed，故【每页】独立反向洗牌
    后再按页顺序拼接即可还原整章真序（参考无关，无浏览器、无参考书依赖）。

    aligner 仅作下载后的外部顺序校正（可选）：传入时把合并好的整章段落按参考书出现
    顺序再重排一遍，用于「已经拿到正版参考、想逐卷核对/校正」的场合；主下载流程
    （main.py）不传入，保持纯参考无关。
    """
    tmpdir = pathlib.Path(tmpdir)
    img_dir = tmpdir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    pages = []
    for i, url in enumerate(iter_page_urls(chapter.url, fetcher), start=1):
        pages.append(_fetch_page_ordered(url, i, fetcher))
    chapter.pages = pages
    if pages:
        chapter.title = pages[0].title or chapter.title

    url_to_path = {}
    assets = []
    for page in pages:
        for src in page.image_urls:
            if src in url_to_path:
                continue
            try:
                data = fetcher.get_bytes(src)
                if not fetcher.is_valid_image(data):
                    continue
                ext = _detect_ext(data)
                idx = len(assets) + 1
                name = f"{chapter.id}_{idx}.{ext}"
                (img_dir / name).write_bytes(data)
                url_to_path[src] = f"images/{name}"
                assets.append(ImageAsset(epub_path=f"images/{name}", data=data))
            except Exception:
                continue
    chapter.image_assets = assets

    texts = [para for page in pages for para in page.paragraphs]

    if aligner is not None and texts:
        banner, body = _split_header(list(texts))
        ordered = aligner.align(body)
        texts = banner + ordered
    else:
        # 参考无关路径：逐页已按文本去重，但页与页交界（末段=下页首段）仍可能重复，
        # 章内再统一去重一次，消除跨页克隆，同时保持相对顺序。
        texts = _dedup_keep_order(texts)

    parts = [f"<p>{t}</p>" for t in texts]
    # 插图去重后置于章节末尾（正文重排不影响插图；站点本就是按页尾追加的）。
    seen_img = set()
    for page in pages:
        for src in page.image_urls:
            if src not in url_to_path or src in seen_img:
                continue
            seen_img.add(src)
            parts.append(f'<p><img src="{url_to_path[src]}"/></p>')
    chapter.html = "".join(parts)


def _detect_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "jpg"
