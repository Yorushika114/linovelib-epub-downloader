import re
import sys
from dataclasses import dataclass
from urllib.parse import unquote
from bs4 import BeautifulSoup
from .catalog import parse_novel_page
from .models import Novel

SEARCH_URL = "https://www.linovelib.com/S6/"
# 站点自带搜索接口 /S6/ 对脚本化请求常返回空（结果由前端/WAF 拦截），因此兜底用
# 站外搜索引擎做 site:linovelib.com 解析「书名→编号」。都选国内可访问、可抓取 HTML 的端点。
# 实测 Bing 与 DuckDuckGo 互补：同一书名常只有其中一个命中（Bing 有时静默放宽 site:，
# DDG 有时收不到结果），故做成「站点接口 → Bing → DDG」依次尝试，谁先命中用谁。
BING_SEARCH_URL = "https://cn.bing.com/search"
DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
# 小说落地页 /novel/{nid}.html 偶发被 Cloudflare 机器人挑战(403)拦下，但同样编号的
# 目录页/卷页/章节页全部正常。此时改从目录页解析最小元数据（书名/作者）照常下载。
CATALOG_URL = "https://www.linovelib.com/novel/{nid}/catalog"


class ResolveError(Exception):
    pass


@dataclass
class SearchHit:
    """按书名搜到的一个候选：小说编号 + 结果标题（用于展示与精确匹配）。"""
    id: str
    title: str


def resolve_id(identifier, fetcher):
    identifier = (identifier or "").strip()
    if identifier.isdigit():
        return identifier
    hits = _search_hits(identifier, fetcher)
    if not hits:
        raise ResolveError(
            f"未找到名为「{identifier}」的小说，请改用编号，例如 --novel 3095"
        )
    return _pick_hit(identifier, hits).id


def search_by_name(name, fetcher):
    """按书名解析出小说编号；找不到返回空串。多候选时依交互/TTY 决定选取。"""
    hits = _search_hits(name, fetcher)
    if not hits:
        return ""
    return _pick_hit(name, hits).id


def _search_hits(name, fetcher):
    """返回去重后的候选命中列表；依次尝试站点接口、Bing、DuckDuckGo，谁先命中返回谁。"""
    for probe in (_site_hits, _bing_hits, _ddg_hits):
        try:
            hits = probe(name, fetcher)
        except Exception:
            hits = []
        if hits:
            return _dedupe(hits)
    return []


def _site_hits(name, fetcher):
    html = fetcher.get_html(SEARCH_URL, method="POST",
                            data={"searchkey": name, "t_frmsearch": "1"})
    return _parse_links(html)


def _bing_hits(name, fetcher):
    html = fetcher.get_html(BING_SEARCH_URL, method="GET",
                            params={"q": f"site:linovelib.com {name}"})
    return _parse_bing(html)


def _ddg_hits(name, fetcher):
    html = fetcher.get_html(DDG_SEARCH_URL, method="GET",
                            params={"q": f"site:linovelib.com {name}"})
    return _parse_ddg(html)


def _dedupe(hits):
    seen = set()
    out = []
    for h in hits:
        if h.id in seen:
            continue
        seen.add(h.id)
        out.append(h)
    return out


def _parse_links(html):
    """解析站点结果页里的 /novel/{id}.html 链接为候选，取锚文本当标题。"""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select('a[href*="/novel/"]'):
        href = a.get("href", "")
        m = re.search(r"/novel/(\d+)\.html", href)
        if not m:
            continue
        out.append(SearchHit(m.group(1), a.get_text(" ", strip=True)))
    return out


def _parse_bing(html):
    """解析 Bing 结果页：逐条 b_algo 提取命中与标题。

    Bing 的 <a href> 常是其转向页而非目标 URL，故对整条结果块做 /novel/{id}.html 正则，
    目标编号仍会出现在转向参数或显式 URL 中；标题取 h2 锚文本。
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li.b_algo"):
        m = re.search(r"/novel/(\d+)\.html", str(li))
        if not m:
            continue
        h2 = li.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        out.append(SearchHit(m.group(1), title))
    return out


def _parse_ddg(html):
    """解析 DuckDuckGo HTML 端点结果：逐条 result 提取命中与标题。

    DDG 的 <a class="result__a" href> 常是转向页，目标 URL 藏在其中的 uddg= 参数里且被
    URL 编码，故先按块正则，[urlencode] 失败再对 a.href unquote 后重新匹配。
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    for res in soup.select("div.result"):
        m = re.search(r"/novel/(\d+)\.html", str(res))
        a = res.find("a", class_="result__a") or res.find("a")
        if not m and a is not None:
            m = re.search(r"/novel/(\d+)\.html", unquote(a.get("href", "")))
        if not m:
            continue
        out.append(SearchHit(m.group(1),
                             a.get_text(" ", strip=True) if a is not None else ""))
    return out


def _norm(s):
    # 归一化书名用于精确比较：去掉空白与常见标点，忽略大小写。
    return re.sub(r"[\s　「」『』《》()（）!！?？.。,，:：、·~～-]+", "", s or "").lower()


def _pick_hit(name, hits):
    """在候选里选一个：单一候选直接返回；标题与书名精确吻合优先；仍是多个且可交互则询问；否则取第一条。"""
    if len(hits) == 1:
        return hits[0]
    for h in hits:
        if _norm(h.title) == _norm(name):
            return h
    if sys.stdin.isatty():
        return _ask_choose(name, hits)
    return hits[0]


def _ask_choose(name, hits):
    exact = next((i for i, h in enumerate(hits)
                  if _norm(h.title) == _norm(name)), 0)
    print(f"按书名「{name}」搜到多个候选：")
    for i, h in enumerate(hits, start=1):
        mark = "  (书名吻合)" if i - 1 == exact else ""
        print(f"  [{i}] {h.title}  (id={h.id}){mark}")
    default = exact + 1
    raw = input(f"输入序号（回车选 [{default}]）: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(hits):
        return hits[int(raw) - 1]
    return hits[exact]


def fetch_novel(nid, fetcher):
    try:
        html = fetcher.get_html(f"https://www.linovelib.com/novel/{nid}.html")
        novel = parse_novel_page(html, nid)
        if novel.title:
            return novel
    except Exception:
        pass
    # 落地页被 Cloudflare 挑战拦下(403)，或解析不出书名 → 退回目录页拿最小元数据。
    # 封面由各卷页 og:image 提供（卷页未被拦），小说页封面仅兜底、缺失无碍；
    # 若目录页也失败则在此自然抛出，不静默返回残缺数据。
    return _novel_from_catalog(nid, fetcher)


def _novel_from_catalog(nid, fetcher):
    html = fetcher.get_html(CATALOG_URL.format(nid=nid))
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else _title_from_page(soup)
    return Novel(id=str(nid), title=title or str(nid),
                 author=_author_from_page(soup), cover_url="")


def _title_from_page(soup):
    # 目录页 <title> 形如「书包名小说,免费阅读_作者作品_目录页_哩哩轻小说」，
    # 取「小说」之前的文字作为书名，避免把「小说」二字也带进书名。
    t = soup.title.get_text(strip=True) if soup.title else ""
    return re.split(r"小说", t, 1)[0].strip() if "小说" in t else t


def _author_from_page(soup):
    # 作者通常藏在 <title> 的「_作者作品」段，如「_鸭志田一作品」；取不到则空串，
    # 空作者不影响下载/合成（仅少写 EPUB creator 元数据）。
    t = soup.title.get_text(strip=True) if soup.title else ""
    return re.search(r"_([^_]+)作品", t).group(1) if re.search(r"_([^_]+)作品", t) else ""
