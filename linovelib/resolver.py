import re
import sys
from dataclasses import dataclass
from urllib.parse import unquote
from bs4 import BeautifulSoup
from .catalog import parse_novel_page
from .models import Novel

SEARCH_URL = "https://www.linovelib.com/S6/"
# 站点自带搜索接口 /S6/?searchkey= 是「客户端渲染 + 需 Cloudflare cookie」：对脚本化
# requests 永远吐空壳（实测长度 0）。只有用真实浏览器（RenderFetcher.search_html）先暖机
# 首页再搜索，才能拿到结果。站外搜索引擎（Bing / DuckDuckGo 的 site:）时好时坏——Bing
# 常静默放宽 site:、DDG 限流、Sogou/360 则对 site: 无结果——因此【本站浏览器搜索为主】，
# 站外引擎仅兜底。都选国内可访问、可抓取 HTML 的端点。
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


def resolve_id(identifier, fetcher, browser=None):
    identifier = (identifier or "").strip()
    if identifier.isdigit():
        return identifier
    hits = _search_hits(identifier, fetcher, browser=browser)
    if not hits:
        raise ResolveError(
            f"未找到名为「{identifier}」的小说，请改用编号，例如 --novel 3095"
        )
    return _pick_hit(identifier, hits).id


def search_by_name(name, fetcher, browser=None):
    """按书名解析出小说编号；找不到返回空串。多候选时依交互/TTY 决定选取。"""
    hits = _search_hits(name, fetcher, browser=browser)
    if not hits:
        return ""
    return _pick_hit(name, hits).id


def search_hits(name, fetcher, browser=None):
    """按书名返回去重候选列表（SearchHit[]），【不做选取】，供前端（WPF）展示与选择。"""
    return _search_hits(name, fetcher, browser=browser)


def is_exact_match(query, title):
    """标题与查询词归一化后是否精确吻合，用于前端标记「书名吻合」候选。"""
    return _norm(title) == _norm(query)



def _search_hits(name, fetcher, browser=None):
    """返回去重后的候选命中列表；依次尝试【浏览器站点搜索】→ 站点接口 → Bing → DuckDuckGo。

    浏览器站点搜索是最可靠、最权威的来源（本站自搜、参考无关）：直接 hit 到本站结果，
    不受站外引擎限流/索引缺失/静默放宽 site: 影响。传入的 browser 为 None（如纯脚本环境、
    无 playwright）时跳过它，退回站外引擎兜底。谁先命中返回谁。
    """
    probes = []
    if browser is not None:
        probes.append(lambda n: _browser_site_hits(n, browser))
    probes += [lambda n: _site_hits(n, fetcher),
               lambda n: _bing_hits(n, fetcher),
               lambda n: _ddg_hits(n, fetcher)]
    for probe in probes:
        try:
            hits = probe(name)
        except Exception:
            hits = []
        if hits:
            return _dedupe(hits)
    return []


def _browser_site_hits(name, browser):
    html = browser.search_html(name)
    return _parse_search_results(html)


def _site_hits(name, fetcher):
    # 脚本化 requests 对 /S6/ 永久空壳；这里仅作一次廉价尝试（给未来服务端渲染留余地），
    # 真正的可靠来源走浏览器 _browser_site_hits。
    html = fetcher.get_html(SEARCH_URL, method="GET",
                            params={"searchkey": name})
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


def _parse_search_results(html):
    """解析【浏览器渲染后】的站点搜索结果页（权威来源）。

    结果项是 div.search-result-list：每个含「封面图 / h2.tit 标题 / 书籍详情」三个指向同名的
    /novel/{id} 链接。页面两侧另有「为您推荐」轮播（果青、在地下城…）也带 /novel/ 链接，
    必须【框定在 search-result-list 内】，否则会把无关书当成候选。标题取 h2.tit a 文本
    （高亮 span 已并入文本，如「无职转生 ～到了异世界就拿出真本事～」）。
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    seen = set()
    for item in soup.select("div.search-result-list"):
        a = item.select_one('a[href*="/novel/"]')
        if a is None:
            continue
        m = re.search(r"/novel/(\d+)\.html", a.get("href", ""))
        if not m:
            continue
        nid = m.group(1)
        if nid in seen:
            continue
        seen.add(nid)
        tit = item.select_one("h2.tit a") or item.select_one("h3 a")
        # 用默认 get_text()（不在元素边界注入空格）：命中词常被包进 <span class="hot">，
        # 若用 get_text(" ")，查询「无职」会被拼成「无职 转生」（高亮边界多出空格）。
        # 默认拼接保真原始空白、且不在边界注入，正是想要的效果。
        title = (tit.get_text() or "").strip() if tit else ""
        if not title:
            # 个别条目标题不在 h2/h3：取第一个「指向本站小说且有文字」的锚点。
            anchor = next((x for x in item.select("a")
                           if re.search(r"/novel/\d+\.html", x.get("href", ""))
                           and (x.get_text() or "").strip()), None)
            title = (anchor.get_text() or "").strip() if anchor else item.get_text(" ", strip=True)
        out.append(SearchHit(nid, title))
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
    try:
        raw = input(f"输入序号（回车选 [{default}]）: ").strip()
    except (EOFError, KeyboardInterrupt):
        # stdin 被判定为 TTY 但实际无法读取（脚本/管道）时，退回默认候选，避免崩溃。
        return hits[exact]
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
